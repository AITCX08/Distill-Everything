"""HTTP client for a Xiaohongshu sidecar (Spider_XHS-compatible wrapper).

Spider_XHS ships signed web-API clients (``user_posted`` / ``note/collect/page``
/ ``feed``) but no long-running service, so the sidecar contract below is what we
call. ``scripts/xhs_sidecar.py`` is the reference wrapper.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

import requests

from src.platforms.errors import PlatformEnumerationError

DEFAULT_TIMEOUT = 30.0
DEFAULT_PATHS: Mapping[str, str] = {
    "probe": "/openapi.json",
    "note": "/xhs/note",
    "user_notes": "/xhs/user/notes",
    "user_collect": "/xhs/user/collect",
    "session": "/xhs/session",
    "login": "/xhs/login",
    "login_status": "/xhs/login/{operation_id}",
    "login_qr": "/xhs/login/{operation_id}/qr",
}


class XhsSidecar:
    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        paths: Mapping[str, str] | None = None,
        http_get: Callable[[str, Mapping[str, Any]], Any] | None = None,
        http_post: Callable[[str, Mapping[str, Any]], Any] | None = None,
        http_get_raw: Callable[[str], Any] | None = None,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.timeout = float(timeout)
        self._paths = {**DEFAULT_PATHS, **dict(paths or {})}
        self._http_get = http_get or self._default_get
        self._http_post = http_post or self._default_post
        self._http_get_raw = http_get_raw or self._default_get_raw
        self.user_id = ""

    def _default_get(self, url: str, params: Mapping[str, Any]) -> Any:
        response = requests.get(url, params=dict(params), timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def _default_post(self, url: str, params: Mapping[str, Any]) -> Any:
        response = requests.post(url, params=dict(params), timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def _default_get_raw(self, url: str) -> Any:
        response = requests.get(url, timeout=self.timeout)
        response.raise_for_status()
        return response

    def _request(self, key: str, params: Mapping[str, Any]) -> Mapping[str, Any]:
        path = self._paths.get(key)
        if not path:
            raise PlatformEnumerationError(f"小红书 sidecar 未配置 {key!r} 路径")
        try:
            payload = self._http_get(f"{self.base_url}{path}", params)
        except Exception as exc:
            raise PlatformEnumerationError(f"小红书 sidecar 请求失败（{key}）：{exc}") from exc
        return _unwrap(payload, key)

    def is_available(self) -> bool:
        try:
            self._http_get(f"{self.base_url}{self._paths['probe']}", {})
        except Exception:
            return False
        return True

    def session_status(self) -> Mapping[str, Any]:
        return self._request("session", {})

    def is_authenticated(self) -> bool:
        if not self.is_available():
            return False
        try:
            status = self.session_status()
        except PlatformEnumerationError:
            return False
        self.user_id = str(status.get("user_id") or "")
        return bool(status.get("authenticated"))

    def _request_post(self, key: str, params: Mapping[str, Any]) -> Mapping[str, Any]:
        path = self._paths.get(key)
        if not path:
            raise PlatformEnumerationError(f"小红书 sidecar 未配置 {key!r} 路径")
        try:
            payload = self._http_post(f"{self.base_url}{path}", params)
        except Exception as exc:
            raise PlatformEnumerationError(f"小红书 sidecar 请求失败（{key}）：{exc}") from exc
        return _unwrap(payload, key)

    def start_login(self) -> Mapping[str, Any]:
        return self._request_post("login", {})

    def login_status(self, operation_id: str) -> Mapping[str, Any]:
        path = self._paths["login_status"].format(operation_id=operation_id)
        try:
            payload = self._http_get(f"{self.base_url}{path}", {})
        except Exception as exc:
            raise PlatformEnumerationError(f"小红书 sidecar 请求失败（login_status）：{exc}") from exc
        return _unwrap(payload, "login_status")

    def login_qr(self, operation_id: str) -> bytes:
        path = self._paths["login_qr"].format(operation_id=operation_id)
        try:
            response = self._http_get_raw(f"{self.base_url}{path}")
            response.raise_for_status()
            return bytes(response.content)
        except Exception as exc:
            raise PlatformEnumerationError(f"小红书二维码不可用：{exc}") from exc

    def note(self, note_id: str) -> Mapping[str, Any]:
        return self._request("note", {"note_id": note_id})

    def user_notes(self, user_id: str, *, cursor: str = "") -> Mapping[str, Any]:
        return self._request("user_notes", {"user_id": user_id, "cursor": cursor})

    def user_collect(self, user_id: str, *, cursor: str = "") -> Mapping[str, Any]:
        return self._request("user_collect", {"user_id": user_id, "cursor": cursor})


def _unwrap(payload: Any, key: str) -> Mapping[str, Any]:
    if isinstance(payload, Mapping):
        code = payload.get("code")
        if code is not None and code not in (0, 200, "0", "200"):
            raise PlatformEnumerationError(
                f"小红书 sidecar 返回错误（{key}）：code={code} "
                f"{payload.get('message') or payload.get('msg') or ''}".strip()
            )
        data = payload.get("data")
        if isinstance(data, Mapping):
            return dict(data)
        return dict(payload)
    raise PlatformEnumerationError(f"小红书 sidecar 返回了非 JSON 内容（{key}）")
