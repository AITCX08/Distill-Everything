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
}


class XhsSidecar:
    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        paths: Mapping[str, str] | None = None,
        http_get: Callable[[str, Mapping[str, Any]], Any] | None = None,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.timeout = float(timeout)
        self._paths = {**DEFAULT_PATHS, **dict(paths or {})}
        self._http_get = http_get or self._default_get

    def _default_get(self, url: str, params: Mapping[str, Any]) -> Any:
        response = requests.get(url, params=dict(params), timeout=self.timeout)
        response.raise_for_status()
        return response.json()

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
