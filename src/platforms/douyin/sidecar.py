"""Client for a Douyin sidecar service (Evil0ctal API, v3/v4 layout).

Running the signature-heavy Douyin crawler as a separate process keeps this
project free of its pinned dependencies (``pydantic==2.9.*``, ``black``,
``pyexecjs``). Endpoint paths are overridable because v5 renamed them to
``/content/*``.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

import requests

from src.platforms.errors import PlatformEnumerationError

DEFAULT_TIMEOUT = 30.0

# Verified against Evil0ctal/Douyin_TikTok_Download_API at tag V4.1.2
# (app/api/endpoints/douyin_web.py); the router prefix comes from the app.
DEFAULT_PATHS: Mapping[str, str] = {
    "one_video": "/api/douyin/web/fetch_one_video",
    "user_post": "/api/douyin/web/fetch_user_post_videos",
    "user_like": "/api/douyin/web/fetch_user_like_videos",
    "user_collection": "/api/douyin/web/fetch_user_collection_videos",
    "user_mix": "/api/douyin/web/fetch_user_mix_videos",
    "profile": "/api/douyin/web/handler_user_profile",
    "probe": "/openapi.json",
}


class DouyinSidecar:
    """Thin HTTP client: resolve ids, page posts/collections, fetch one video."""

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

    # -- transport -------------------------------------------------------
    def _default_get(self, url: str, params: Mapping[str, Any]) -> Any:
        response = requests.get(url, params=dict(params), timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def _request(self, key: str, params: Mapping[str, Any] | None = None) -> Any:
        path = self._paths.get(key)
        if not path:
            raise PlatformEnumerationError(f"sidecar 未配置 {key!r} 路径")
        url = f"{self.base_url}{path}"
        try:
            payload = self._http_get(url, params or {})
        except Exception as exc:
            raise PlatformEnumerationError(f"抖音 sidecar 请求失败（{key}）：{exc}") from exc
        return _unwrap(payload, key)

    # -- capability ------------------------------------------------------
    def is_available(self) -> bool:
        try:
            self._http_get(f"{self.base_url}{self._paths['probe']}", {})
        except Exception:
            return False
        return True

    def one_video(self, aweme_id: str) -> dict[str, Any]:
        return self._request("one_video", {"aweme_id": aweme_id})

    def user_posts(self, sec_user_id: str, *, cursor: int = 0, count: int = 20) -> dict[str, Any]:
        return self._request(
            "user_post",
            {"sec_user_id": sec_user_id, "max_cursor": cursor, "count": count},
        )

    def user_likes(self, sec_user_id: str, *, cursor: int = 0, count: int = 20) -> dict[str, Any]:
        return self._request(
            "user_like",
            {"sec_user_id": sec_user_id, "max_cursor": cursor, "counts": count},
        )

    def user_collection(self, cookie: str, *, cursor: int = 0, count: int = 20) -> dict[str, Any]:
        """「我的收藏」走 cookie，不接受 sec_user_id（V4 接口定义如此）。"""

        return self._request(
            "user_collection",
            {"cookie": cookie, "max_cursor": cursor, "counts": count},
        )

    def user_mix(self, mix_id: str, *, cursor: int = 0, count: int = 20) -> dict[str, Any]:
        return self._request(
            "user_mix",
            {"mix_id": mix_id, "max_cursor": cursor, "count": count},
        )

    def profile(self, sec_user_id: str) -> dict[str, Any]:
        return self._request("profile", {"sec_user_id": sec_user_id})


def _unwrap(payload: Any, key: str) -> dict[str, Any]:
    """Accept ``{code, data}`` envelopes and bare payloads alike."""

    if isinstance(payload, Mapping):
        code = payload.get("code")
        if code is not None and code not in (0, 200, "0", "200"):
            raise PlatformEnumerationError(
                f"抖音 sidecar 返回错误（{key}）：code={code} {payload.get('message') or payload.get('msg') or ''}".strip()
            )
        data = payload.get("data")
        if isinstance(data, Mapping):
            return dict(data)
        return dict(payload)
    raise PlatformEnumerationError(f"抖音 sidecar 返回了非 JSON 内容（{key}）")
