"""小红书 sidecar：把 Spider_XHS 封装成本项目约定的 3 个 HTTP 接口。

契约见 docs/platform-sources.md §3.3：
  GET /xhs/note?note_id=
  GET /xhs/user/notes?user_id=&cursor=
  GET /xhs/user/collect?user_id=&cursor=
  GET /xhs/session            登录态自检

启动：
  set XHS_COOKIE=...        （或先跑 scripts/xhs_login.py 生成 cookie.txt）
  python scripts/xhs_sidecar.py --port 8001

说明：Spider_XHS 需要登录态（a1 / web_session 等）才能签名访问；未配置 cookie 时
本服务仍然启动，但数据接口会返回 409，提示先登录。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

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
    consumer = video.get("consumer") or video.get("media")
    if isinstance(consumer, dict):
        origin = consumer.get("origin_video_key")
        if isinstance(origin, str) and origin:
            return origin
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
    return {
        "note_id": str(_first(card, "note_id", "id") or note_id),
        "title": str(title or desc)[:120],
        "desc": str(desc or ""),
        "video_url": _video_url(card),
        "images": _images(card),
        "cover": _first(card, "cover") or (card.get("image_list") or [{}])[0].get("url_default", ""),
        "author": {
            "user_id": str(_first(user, "user_id", "id") or ""),
            "nickname": str(_first(user, "nickname", "nick_name") or ""),
        },
        "liked": str(_first(interact, "liked_count") or ""),
        "url": f"https://www.xiaohongshu.com/explore/{note_id}",
    }


def normalize_feed(payload: Any, cursor: str = "") -> dict[str, Any]:
    """把 Spider_XHS 的列表返回统一成 {items, cursor, has_more}。"""

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


def build_app(session: Any):
    from fastapi import FastAPI, HTTPException

    app = FastAPI(title="Distill-Everything XHS sidecar", version="0.1.0")

    def _apis():
        if session is None:
            raise HTTPException(status_code=409, detail="小红书未登录：先运行 scripts/xhs_login.py")
        from apis.xhs_pc_apis import XHS_PC_Apis

        return XHS_PC_Apis(session)

    @app.get("/xhs/session")
    def session_status() -> dict[str, Any]:
        return {"authenticated": session is not None}

    @app.get("/xhs/note")
    def note(note_id: str) -> dict[str, Any]:
        apis = _apis()
        url = f"https://www.xiaohongshu.com/explore/{note_id}"
        payload = apis.get_note_info(url)
        return normalize_note(payload, note_id)

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
        print("小红书：未配置 cookie，数据接口将返回 409（先跑 scripts/xhs_login.py）")

    uvicorn.run(build_app(session), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
