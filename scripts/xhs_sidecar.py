"""小红书 sidecar：把 Spider_XHS 封装成本项目约定的 HTTP 接口。

数据接口（契约见 docs/platform-sources.md §3.3）：
  GET /xhs/note?note_id=
  GET /xhs/user/notes?user_id=&cursor=
  GET /xhs/user/collect?user_id=&cursor=
  GET /xhs/session

登录接口（供 Dashboard 页内扫码）：
  POST /xhs/login                    启动一次扫码登录，返回 operation_id
  GET  /xhs/login/{op}               查询状态
  GET  /xhs/login/{op}/qr            取二维码 PNG

启动：
  python scripts/xhs_sidecar.py --port 8001
"""

from __future__ import annotations

import argparse
import io
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
XHS_ROOT = ROOT / ".local-artifacts" / "xhs-sidecar"
COOKIE_FILE = XHS_ROOT / "cookie.txt"

if str(XHS_ROOT) not in sys.path:
    sys.path.insert(0, str(XHS_ROOT))


def _load_cookie() -> str:
    value = os.getenv("XHS_COOKIE", "").strip()
    if value:
        return value
    if COOKIE_FILE.exists():
        return COOKIE_FILE.read_text(encoding="utf-8").strip()
    return ""


def _first(mapping: Any, *keys: str, default: Any = None) -> Any:
    if not isinstance(mapping, dict):
        return default
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    return default


def _note_card(payload: Any) -> dict[str, Any]:
    """从 Spider_XHS 的多种返回结构里取到 note_card。"""

    if not isinstance(payload, dict):
        return {}
    items = payload.get("items")
    if isinstance(items, list) and items:
        first = items[0]
        if isinstance(first, dict):
            card = first.get("note_card") or first.get("note") or first
            if isinstance(card, dict):
                return card
    card = payload.get("note_card") or payload.get("note") or payload
    return card if isinstance(card, dict) else {}


def _video_url(card: dict[str, Any]) -> str:
    video = card.get("video")
    if not isinstance(video, dict):
        return ""
    for key in ("url", "master_url"):
        value = video.get(key)
        if isinstance(value, str) and value:
            return value
    stream = video.get("media", {}).get("stream", {}) if isinstance(video.get("media"), dict) else {}
    for codec in ("h264", "h265", "av1"):
        entries = stream.get(codec) if isinstance(stream, dict) else None
        if isinstance(entries, list) and entries:
            url = entries[0].get("master_url") or entries[0].get("url")
            if isinstance(url, str) and url:
                return url
    return ""


def _images(card: dict[str, Any]) -> list[str]:
    images: list[str] = []
    for entry in card.get("image_list") or []:
        if not isinstance(entry, dict):
            continue
        url = entry.get("url_default") or entry.get("url")
        if isinstance(url, str) and url:
            images.append(url)
    return images


def normalize_note(payload: Any, note_id: str) -> dict[str, Any]:
    card = _note_card(payload)
    user = card.get("user") if isinstance(card.get("user"), dict) else {}
    interact = card.get("interact_info") if isinstance(card.get("interact_info"), dict) else {}
    title = _first(card, "title", "display_title") or ""
    desc = _first(card, "desc", "description") or title
    image_list = card.get("image_list") or [{}]
    return {
        "note_id": str(_first(card, "note_id", "id") or note_id),
        "title": str(title or desc)[:120],
        "desc": str(desc or ""),
        "video_url": _video_url(card),
        "images": _images(card),
        "cover": _first(card, "cover") or (image_list[0].get("url_default", "") if image_list else ""),
        "author": {
            "user_id": str(_first(user, "user_id", "id") or ""),
            "nickname": str(_first(user, "nickname", "nick_name") or ""),
        },
        "liked": str(_first(interact, "liked_count") or ""),
        "url": f"https://www.xiaohongshu.com/explore/{note_id}",
    }


def normalize_feed(payload: Any, cursor: str = "") -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    raw_items = payload if isinstance(payload, list) else []
    if isinstance(payload, dict):
        raw_items = payload.get("items") or payload.get("notes") or payload.get("data") or []
    if isinstance(raw_items, dict):
        raw_items = raw_items.get("items") or []
    for entry in raw_items if isinstance(raw_items, list) else []:
        card = _note_card(entry if isinstance(entry, dict) else {})
        note_id = str(_first(card, "note_id", "id") or "")
        if note_id:
            items.append(normalize_note({"note_card": card}, note_id))
    return {"items": items, "cursor": cursor, "has_more": False}


def _qr_png(text: str) -> bytes:
    import qrcode

    image = qrcode.make(text)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class LoginState:
    """扫码登录的进程内状态（同一时刻只允许一个登录）。"""

    def __init__(self, session: Any) -> None:
        self.session = session
        self.lock = threading.Lock()
        self.operations: dict[str, dict[str, Any]] = {}
        self.active: str | None = None

    def snapshot(self, operation_id: str) -> dict[str, Any]:
        operation = self.operations.get(operation_id)
        if operation is None:
            raise KeyError(operation_id)
        return {
            "operation_id": operation_id,
            "status": operation["status"],
            "message": operation["message"],
        }

    def update(self, operation_id: str, *, status: str, message: str, qr: bytes | None = None) -> None:
        with self.lock:
            operation = self.operations[operation_id]
            operation["status"] = status
            operation["message"] = message
            if qr is not None:
                operation["qr"] = qr
            if status not in {"preparing_qr", "waiting_for_scan", "waiting_for_confirmation"}:
                if self.active == operation_id:
                    self.active = None


def _run_login(state: LoginState, operation_id: str, timeout_seconds: float = 180.0) -> None:
    from apis.xhs_pc_login_apis import XHSLoginApi
    from xhs_utils.xhs_auth import XHSUnifiedAuth

    try:
        login = XHSLoginApi()
        cookies = login.generate_init_cookies()
        ok, message, qr = login.generate_qrcode(cookies)
        if not ok:
            state.update(operation_id, status="failed", message=f"获取二维码失败：{message}")
            return
        cookies = qr["cookies"]
        state.update(
            operation_id,
            status="waiting_for_scan",
            message="请使用小红书 App 扫码",
            qr=_qr_png(qr["qr_url"]),
        )

        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            ok, message, cookies = login.check_qrcode_status(qr["qr_id"], qr["code"], cookies)
            if ok:
                COOKIE_FILE.write_text(cookies, encoding="utf-8")
                state.session = XHSUnifiedAuth.from_cookie(cookies)
                state.update(operation_id, status="succeeded", message="登录成功，登录态已保存在本机。")
                return
            state.update(operation_id, status="waiting_for_scan", message=message or "请使用小红书 App 扫码")
            time.sleep(2.0)

        state.update(operation_id, status="expired", message="二维码已过期，请重新发起登录。")
    except Exception as exc:  # 任何异常都要让前端看到原因
        state.update(operation_id, status="failed", message=f"小红书扫码登录失败：{exc}")


def build_app(state: LoginState):
    from fastapi import FastAPI, HTTPException, Response

    app = FastAPI(title="Distill-Everything XHS sidecar", version="0.2.0")

    def _apis():
        if state.session is None:
            raise HTTPException(status_code=409, detail="小红书未登录：先运行 scripts/xhs_login.py")
        from apis.xhs_pc_apis import XHS_PC_Apis

        return XHS_PC_Apis(state.session)

    @app.get("/xhs/session")
    def session_status() -> dict[str, Any]:
        if state.session is None:
            return {"authenticated": False, "user_id": ""}
        user_id = ""
        try:
            from apis.xhs_pc_apis import XHS_PC_Apis

            payload = XHS_PC_Apis(state.session).get_user_me()
            data = payload.get("data") if isinstance(payload, dict) else None
            source = data if isinstance(data, dict) else payload
            if isinstance(source, dict):
                user_id = str(source.get("user_id") or source.get("userId") or "")
        except Exception:
            user_id = ""
        return {"authenticated": True, "user_id": user_id}

    @app.post("/xhs/login")
    def login_start() -> dict[str, Any]:
        with state.lock:
            if state.active:
                return state.snapshot(state.active)
            operation_id = uuid4().hex
            state.operations[operation_id] = {
                "status": "preparing_qr",
                "message": "正在生成二维码",
                "qr": None,
            }
            state.active = operation_id
        threading.Thread(
            target=_run_login,
            args=(state, operation_id),
            daemon=True,
            name=f"xhs-login-{operation_id[:8]}",
        ).start()
        return state.snapshot(operation_id)

    @app.get("/xhs/login/{operation_id}")
    def login_status(operation_id: str) -> dict[str, Any]:
        try:
            return state.snapshot(operation_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="登录会话不存在") from error

    @app.get("/xhs/login/{operation_id}/qr")
    def login_qr(operation_id: str) -> Response:
        with state.lock:
            operation = state.operations.get(operation_id)
            if operation is None:
                raise HTTPException(status_code=404, detail="登录会话不存在")
            png = operation.get("qr")
        if not png:
            raise HTTPException(status_code=409, detail="二维码仍在生成")
        return Response(content=png, media_type="image/png")

    @app.get("/xhs/note")
    def note(note_id: str) -> dict[str, Any]:
        apis = _apis()
        url = f"https://www.xiaohongshu.com/explore/{note_id}"
        return normalize_note(apis.get_note_info(url), note_id)

    @app.get("/xhs/user/notes")
    def user_notes(user_id: str, cursor: str = "") -> dict[str, Any]:
        apis = _apis()
        payload = apis.get_user_all_notes(f"https://www.xiaohongshu.com/user/profile/{user_id}")
        return normalize_feed(payload, cursor)

    @app.get("/xhs/user/collect")
    def user_collect(user_id: str, cursor: str = "") -> dict[str, Any]:
        apis = _apis()
        payload = apis.get_user_all_collect_note_info(
            f"https://www.xiaohongshu.com/user/profile/{user_id}"
        )
        return normalize_feed(payload, cursor)

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="小红书 sidecar")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()

    import uvicorn

    cookie = _load_cookie()
    session = None
    if cookie:
        from xhs_utils.xhs_auth import XHSUnifiedAuth

        session = XHSUnifiedAuth.from_cookie(cookie)
        print("小红书：已加载 cookie")
    else:
        print("小红书：未登录，可在 Dashboard 页内扫码")

    uvicorn.run(build_app(LoginState(session)), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
