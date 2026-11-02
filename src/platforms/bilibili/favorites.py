"""Bilibili favorite folders through three public web endpoints.

Public folders need **no WBI signature and no login**; a ``SESSDATA`` cookie is
only required for private folders. Deleted entries are not flagged by ``type``
(they stay ``type == 2``), they set ``attr == 1`` and replace the title with
「已失效视频」, so ``attr`` is the reliable filter.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterator, Mapping

import requests

from src.platforms.errors import PlatformEnumerationError, TargetResolutionError

API_ROOT = "https://api.bilibili.com"
PAGE_SIZE = 20
MAX_PAGES = 500
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
}


@dataclass(frozen=True)
class FavoriteFolder:
    media_id: str
    title: str
    media_count: int
    owner: str


def is_playable_media(media: Mapping[str, Any]) -> bool:
    """Return whether a favorite entry still points at a downloadable video."""

    if not isinstance(media, Mapping):
        return False
    if not str(media.get("bvid") or "").strip():
        return False
    try:
        attr = int(media.get("attr") or 0)
    except (TypeError, ValueError):
        return False
    return attr & 1 == 0


class BilibiliFavorites:
    """Read favorite folders without touching the browser or the legacy SDK."""

    def __init__(
        self,
        *,
        http_get: Callable[[str, Mapping[str, Any]], Any] | None = None,
        sessdata: str = "",
        timeout: float = 10.0,
    ) -> None:
        self._http_get = http_get or self._default_get
        self._sessdata = (sessdata or "").strip()
        self._timeout = timeout

    def _default_get(self, url: str, params: Mapping[str, Any]) -> Any:
        cookies = {"SESSDATA": self._sessdata} if self._sessdata else {}
        response = requests.get(
            url,
            params=dict(params),
            headers=_HEADERS,
            cookies=cookies,
            timeout=self._timeout,
        )
        response.raise_for_status()
        return response.json()

    def _call(self, path: str, params: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            payload = self._http_get(f"{API_ROOT}{path}", params)
        except Exception as exc:
            raise PlatformEnumerationError(f"Bilibili 收藏夹请求失败：{exc}") from exc
        if not isinstance(payload, Mapping):
            raise PlatformEnumerationError("Bilibili 收藏夹返回了非 JSON 内容")
        if payload.get("code") != 0:
            raise PlatformEnumerationError(
                f"Bilibili 收藏夹返回错误码 {payload.get('code')}: {payload.get('message')}"
            )
        return payload

    def list_folders(self, up_mid: str) -> tuple[FavoriteFolder, ...]:
        """List a user's public favorite folders."""

        payload = self._call("/x/v3/fav/folder/created/list-all", {"up_mid": up_mid})
        data = payload.get("data") if isinstance(payload.get("data"), Mapping) else {}
        entries = data.get("list") or []
        folders: list[FavoriteFolder] = []
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            folder_id = str(entry.get("id") or "")
            if not folder_id:
                continue
            folders.append(
                FavoriteFolder(
                    media_id=folder_id,
                    title=str(entry.get("title") or ""),
                    media_count=int(entry.get("media_count") or 0),
                    owner="",
                )
            )
        return tuple(folders)

    def folder_info(self, media_id: str) -> FavoriteFolder:
        """Fetch folder metadata; raises when the folder is missing or private."""

        payload = self._call("/x/v3/fav/folder/info", {"media_id": media_id})
        data = payload.get("data")
        if not isinstance(data, Mapping):
            raise TargetResolutionError(
                "收藏夹不存在、已被删除，或当前凭据无权访问"
            )
        upper = data.get("upper") if isinstance(data.get("upper"), Mapping) else {}
        return FavoriteFolder(
            media_id=str(data.get("id") or media_id),
            title=str(data.get("title") or ""),
            media_count=int(data.get("media_count") or 0),
            owner=str(upper.get("name") or ""),
        )

    def iter_pages(
        self,
        media_id: str,
        *,
        page_size: int = PAGE_SIZE,
    ) -> Iterator[tuple[tuple[Mapping[str, Any], ...], bool]]:
        """Yield ``(medias, has_more)`` pages, filtered to playable entries."""

        size = max(1, min(int(page_size), PAGE_SIZE))
        page = 1
        while page <= MAX_PAGES:
            payload = self._call(
                "/x/v3/fav/resource/list",
                {
                    "media_id": media_id,
                    "pn": page,
                    "ps": size,
                    "platform": "web",
                },
            )
            data = payload.get("data") if isinstance(payload.get("data"), Mapping) else {}
            medias = data.get("medias") or []
            if not isinstance(medias, list):
                raise PlatformEnumerationError("Bilibili 收藏夹返回的 medias 不是数组")
            playable = tuple(item for item in medias if is_playable_media(item))
            has_more = bool(data.get("has_more"))
            yield playable, has_more
            if not has_more:
                return
            page += 1
        raise PlatformEnumerationError(
            f"Bilibili 收藏夹超过 {MAX_PAGES} 页，已停止以免失控"
        )
