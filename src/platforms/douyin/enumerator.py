"""Cursor-aware enumeration of creator works from Douyin API responses."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from datetime import datetime, timezone
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

from src.platforms.errors import PlatformEnumerationError
from src.platforms.models import (
    EnumerationCheckpoint,
    EnumerationPage,
    ItemType,
    SourceAsset,
    SourceCreator,
    SourceItem,
)


def _integer(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def _first_url(node: Any) -> str | None:
    if not isinstance(node, Mapping):
        return None
    urls = node.get("url_list") or node.get("urlList") or ()
    return str(urls[0]) if isinstance(urls, (list, tuple)) and urls else None


def _media_type(raw: Mapping[str, Any]) -> ItemType:
    media_type = raw.get("media_type")
    if media_type is None:
        media_type = raw.get("aweme_type")
    if media_type == 2 or raw.get("images"):
        return ItemType.GALLERY
    if media_type == 4 or raw.get("video"):
        return ItemType.VIDEO
    return ItemType.UNKNOWN


def _video_asset(video: Mapping[str, Any]) -> SourceAsset | None:
    play_addr = video.get("play_addr") or video.get("download_addr")
    if not _first_url(play_addr):
        bit_rates = video.get("bit_rate") or ()
        if bit_rates and isinstance(bit_rates[0], Mapping):
            play_addr = bit_rates[0].get("play_addr")
    url = _first_url(play_addr)
    if not url:
        return None
    expected = _integer(play_addr.get("data_size")) if isinstance(play_addr, Mapping) else None
    return SourceAsset(kind="video", url=url, expected_bytes=expected)


def sanitize_raw_metadata(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Retain diagnostics needed for migration without persisting browser secrets."""

    author = raw.get("author") if isinstance(raw.get("author"), Mapping) else {}
    statistics = raw.get("statistics") if isinstance(raw.get("statistics"), Mapping) else {}
    video = raw.get("video") if isinstance(raw.get("video"), Mapping) else {}
    return {
        "aweme_type": raw.get("aweme_type"),
        "media_type": raw.get("media_type"),
        "create_time": raw.get("create_time"),
        "author": {
            "uid": author.get("uid"),
            "sec_uid": author.get("sec_uid"),
            "nickname": author.get("nickname"),
        },
        "statistics": {
            key: statistics.get(key)
            for key in ("play_count", "digg_count", "comment_count", "share_count", "collect_count")
        },
        "video": {"duration": video.get("duration")},
        "image_count": len(raw.get("images") or ()),
    }


def map_aweme(raw: Mapping[str, Any], creator_id: str) -> SourceItem:
    aweme_id = str(raw["aweme_id"])
    item_type = _media_type(raw)
    video = raw.get("video") if isinstance(raw.get("video"), Mapping) else {}
    images = raw.get("images") if isinstance(raw.get("images"), list) else []

    assets: list[SourceAsset] = []
    if item_type is ItemType.VIDEO:
        asset = _video_asset(video)
        if asset:
            assets.append(asset)
    elif item_type is ItemType.GALLERY:
        for index, image in enumerate(images):
            url = _first_url(image)
            if url:
                size = _integer(image.get("data_size")) if isinstance(image, Mapping) else None
                assets.append(SourceAsset("image", url, index=index, expected_bytes=size))

    timestamp = _integer(raw.get("create_time"))
    published_at = (
        datetime.fromtimestamp(timestamp, tz=timezone.utc)
        if timestamp is not None and timestamp > 0
        else None
    )
    duration_ms = _integer(video.get("duration"))
    statistics = raw.get("statistics") if isinstance(raw.get("statistics"), Mapping) else {}
    description = str(raw.get("desc") or "")
    tags = tuple(
        str(tag.get("hashtag_name"))
        for tag in raw.get("text_extra", ())
        if isinstance(tag, Mapping) and tag.get("hashtag_name")
    )
    cover_url = (
        _first_url(video.get("cover"))
        or _first_url(video.get("dynamic_cover"))
        or _first_url(video.get("origin_cover"))
    )
    path_kind = "note" if item_type is ItemType.GALLERY else "video"
    return SourceItem(
        platform="douyin",
        item_id=aweme_id,
        creator_id=creator_id,
        item_type=item_type,
        title=description.splitlines()[0][:120] or f"Douyin {aweme_id}",
        description=description,
        canonical_url=f"https://www.douyin.com/{path_kind}/{aweme_id}",
        published_at=published_at,
        duration_seconds=duration_ms / 1000 if duration_ms is not None else None,
        statistics={
            "views": _integer(statistics.get("play_count")) or 0,
            "likes": _integer(statistics.get("digg_count")) or 0,
            "comments": _integer(statistics.get("comment_count")) or 0,
            "shares": _integer(statistics.get("share_count")) or 0,
            "collects": _integer(statistics.get("collect_count")) or 0,
        },
        cover_url=cover_url,
        tags=tags,
        assets=tuple(assets),
        raw_metadata=sanitize_raw_metadata(raw),
    )


def _aweme_list(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    value = payload.get("aweme_list") or payload.get("items") or payload.get("data") or []
    if isinstance(value, Mapping):
        value = value.get("aweme_list") or value.get("items") or []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _has_more(payload: Mapping[str, Any]) -> bool:
    value = payload.get("has_more", False)
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no"}
    return bool(value)


def _expected_count(payload: Mapping[str, Any]) -> int | None:
    for key in ("total", "aweme_count", "total_count"):
        value = _integer(payload.get(key))
        if value is not None:
            return value
    return None


_POST_API_MARKERS = ("/aweme/v1/web/aweme/post/",)
# 注意：这里**不包含** /aweme/post/。收藏/合集页面上也可能并发加载作者作品流，
# 混进来会把无关视频算进收藏夹；宁可报错也不要静默混入。
_COLLECTION_API_MARKERS = (
    "/aweme/v1/web/aweme/listcollection/",
    "/aweme/v1/web/aweme/favorite/",
    "/aweme/v1/web/mix/aweme/",
)


def api_markers_for(url: str) -> tuple[str, ...]:
    """Pick the Douyin JSON endpoints that can carry a page's item list.

    创作者主页只信任 ``/aweme/post/``；合集与「我的收藏」页面由其它端点返回，
    且抖音换版本时路径会变，所以这里按页面类型接受一组候选路径。
    """

    lowered = (url or "").lower()
    if "/collection/" in lowered or "showtab=collect" in lowered:
        return _COLLECTION_API_MARKERS
    return _POST_API_MARKERS


class DouyinBrowserRoute:
    """Use browser-generated `/aweme/post/` requests as the signed page transport."""

    def __init__(
        self,
        session,
        *,
        response_timeout_ms: int = 20_000,
        scroll_wait_ms: int = 750,
        api_markers: tuple[str, ...] | None = None,
    ) -> None:
        self.session = session
        self.response_timeout_ms = response_timeout_ms
        self.scroll_wait_ms = scroll_wait_ms
        self._forced_markers = api_markers
        self._api_markers = api_markers or _POST_API_MARKERS
        self._page_context = None
        self._page = None
        self._creator_id: str | None = None
        self._responses: list[tuple[str, Mapping[str, Any]]] = []

    def _capture(self, response: Any) -> None:
        url = str(getattr(response, "url", ""))
        lowered = url.lower()
        if not any(marker in lowered for marker in self._api_markers):
            return
        try:
            payload = response.json()
        except Exception:
            return
        if not isinstance(payload, Mapping):
            return
        query = parse_qs(urlparse(url).query)
        request_cursor = str((query.get("max_cursor") or query.get("cursor") or ["0"])[0])
        self._responses.append((request_cursor, payload))

    def _open(self, creator: SourceCreator) -> None:
        self.close()
        self._api_markers = self._forced_markers or api_markers_for(creator.canonical_url)
        self._page_context = self.session.open_page(
            headless=self.session.acquisition_headless,
            task="enumerate",
        )
        self._page = self._page_context.__enter__()
        self._creator_id = creator.creator_id
        self._page.on("response", self._capture)
        self._page.goto(
            creator.canonical_url,
            wait_until="domcontentloaded",
            timeout=self.response_timeout_ms,
        )

    def _take(self, cursor: str) -> Mapping[str, Any] | None:
        for index, (request_cursor, payload) in enumerate(self._responses):
            if request_cursor == cursor:
                self._responses.pop(index)
                return payload
        return None

    def _trigger_next_page(self) -> None:
        self._page.evaluate("""() => {
            const explicit = document.querySelector('.route-scroll-container');
            const fallback = [...document.querySelectorAll('*')].find((element) => {
                const style = getComputedStyle(element);
                return (style.overflowY === 'auto' || style.overflowY === 'scroll')
                    && element.scrollHeight > element.clientHeight + 100
                    && element.clientWidth > 300 && element.clientHeight > 200;
            });
            const target = explicit || fallback;
            if (target) target.scrollTop = target.scrollHeight;
            else window.scrollTo(0, document.body.scrollHeight);
        }""")
        self._page.wait_for_timeout(self.scroll_wait_ms)

    def __call__(self, creator: SourceCreator, cursor: str) -> Mapping[str, Any]:
        if self._page is None or self._creator_id != creator.creator_id:
            self._open(creator)
        deadline = time.monotonic() + self.response_timeout_ms / 1000
        while time.monotonic() <= deadline:
            payload = self._take(cursor)
            if payload is not None:
                return payload
            self._trigger_next_page()
        raise PlatformEnumerationError(
            f"Douyin did not return any of {list(self._api_markers)} for cursor {cursor}; "
            "login may have expired, or the page type changed"
        )

    def close(self) -> None:
        if self._page_context is not None:
            self._page_context.__exit__(None, None, None)
        self._page_context = None
        self._page = None
        self._creator_id = None
        self._responses.clear()


class DouyinEnumerator:
    def __init__(
        self,
        request_page: Callable[[SourceCreator, str], Mapping[str, Any]],
        *,
        known_boundary_pages: int = 2,
        max_pages: int = 1000,
    ) -> None:
        self.request_page = request_page
        self.known_boundary_pages = known_boundary_pages
        self.max_pages = max_pages

    def iter_pages(
        self,
        creator: SourceCreator,
        checkpoint: EnumerationCheckpoint | None,
    ) -> Iterator[EnumerationPage]:
        previous = checkpoint or EnumerationCheckpoint()
        seen = set(previous.seen_ids)
        incremental_probe = previous.complete
        cursor = "0" if incremental_probe else (previous.cursor or "0")
        expected = previous.expected_count
        known_streak = 0

        try:
            for _ in range(self.max_pages):
                payload = self.request_page(creator, cursor)
                response_expected = _expected_count(payload)
                if response_expected is not None:
                    expected = response_expected

                new_items: list[SourceItem] = []
                for raw in _aweme_list(payload):
                    try:
                        item = map_aweme(raw, creator.creator_id)
                    except (KeyError, TypeError, ValueError):
                        continue
                    if item.item_id in seen:
                        continue
                    seen.add(item.item_id)
                    new_items.append(item)

                known_streak = known_streak + 1 if not new_items else 0
                api_has_more = _has_more(payload)
                next_cursor = str(payload.get("max_cursor", payload.get("cursor", cursor)))
                reached_end = not api_has_more
                reliable_unchanged_total = (
                    incremental_probe
                    and previous.expected_count is not None
                    and response_expected is not None
                    and response_expected <= previous.expected_count
                )
                reached_known_boundary = (
                    reliable_unchanged_total
                    and known_streak >= self.known_boundary_pages
                )
                cursor_stalled = api_has_more and next_cursor == cursor
                complete = reached_end or reached_known_boundary
                next_checkpoint = EnumerationCheckpoint(
                    cursor=None if complete else next_cursor,
                    seen_ids=frozenset(seen),
                    complete=complete,
                    expected_count=expected,
                )
                yield EnumerationPage(
                    items=tuple(new_items),
                    checkpoint=next_checkpoint,
                    has_more=api_has_more and not complete,
                )
                if complete or cursor_stalled:
                    return
                cursor = next_cursor
        finally:
            close = getattr(self.request_page, "close", None)
            if callable(close):
                close()

    def iter_items(
        self,
        creator: SourceCreator,
        *,
        checkpoint: EnumerationCheckpoint | None,
    ) -> Iterator[EnumerationPage]:
        yield from self.iter_pages(creator, checkpoint)
