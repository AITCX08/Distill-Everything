"""High-level Douyin adapter composed from browser session primitives."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping
from urllib.parse import urlparse

from src.platforms.douyin.resolver import (
    DouyinResolver,
    extract_collection_target,
    extract_sec_uid,
    extract_target_url,
)
from src.platforms.douyin.enumerator import DouyinBrowserRoute, DouyinEnumerator
from src.platforms.douyin.downloader import DouyinDownloader
from src.platforms.douyin.enumerator import map_aweme
from src.platforms.models import (
    AuthStatus,
    DownloadedAssets,
    EnumerationCheckpoint,
    EnumerationPage,
    ItemType,
    PlatformDescriptor,
    ResolvedTarget,
    SourceCreator,
    SourceItem,
)


_DIRECT_RE = re.compile(r"^douyin:[A-Za-z0-9_.-]+$", re.IGNORECASE)


class DouyinAdapter:
    descriptor = PlatformDescriptor(
        name="douyin",
        url_patterns=(
            r"https?://(?:v\.)?douyin\.com/",
            r"https?://(?:www\.)?douyin\.com/collection/\d+",
            r"douyin:collection:\d+",
        ),
        item_types=frozenset({ItemType.VIDEO, ItemType.GALLERY}),
        requires_browser=True,
        requires_auth=True,
        commands=("status", "login", "creator"),
    )

    def __init__(
        self,
        session,
        *,
        resolver_factory: Callable = DouyinResolver,
        enumerator=None,
        downloader=None,
        creator_loader: Callable[[ResolvedTarget], SourceCreator] | None = None,
    ) -> None:
        self.session = session
        self._resolver_factory = resolver_factory
        self._enumerator = enumerator or DouyinEnumerator(DouyinBrowserRoute(session))
        self._downloader = downloader or DouyinDownloader(refresh_item=self._refresh_from_browser)
        self._creator_loader = creator_loader

    def matches(self, target: str) -> bool:
        if _DIRECT_RE.fullmatch(target.strip()):
            return True
        if extract_collection_target(target.strip()):
            return True
        url = extract_target_url(target)
        if not url:
            return False
        if extract_collection_target(url):
            return True
        hostname = (urlparse(url).hostname or "").lower()
        return hostname == "douyin.com" or hostname.endswith(".douyin.com")

    def auth_status(self) -> AuthStatus:
        return self.session.auth_status()

    def authenticate(self, *, headful: bool) -> None:
        self.session.authenticate(headful=headful)

    def resolve(self, target: str) -> ResolvedTarget:
        collection = self._resolver_factory(None).resolve_collection(target)
        if collection is not None:
            return collection

        direct = _DIRECT_RE.fullmatch(target.strip())
        url = extract_target_url(target)
        if direct or (url and extract_sec_uid(url)):
            return self._resolver_factory(None).resolve_share_url(target)
        with self.session.open_page(
            headless=self.session.acquisition_headless,
            task="resolve",
        ) as page:
            return self._resolver_factory(page).resolve_share_url(target)

    def get_creator(self, target: ResolvedTarget) -> SourceCreator:
        if target.target_type == "collection":
            label = (
                "我的收藏"
                if target.creator_id.endswith("-collect")
                else f"抖音合集 {target.creator_id.split('-')[0]}"
            )
            return SourceCreator(
                platform="douyin",
                creator_id=target.creator_id,
                display_name=label,
                canonical_url=target.canonical_url,
                raw_metadata={"target_type": "collection"},
            )
        if self._creator_loader is not None:
            return self._creator_loader(target)
        profile: dict[str, Any] = {}
        with self.session.open_page(
            headless=self.session.acquisition_headless,
            task="creator-profile",
        ) as page:
            def capture(response: Any) -> None:
                if "/user/profile/other/" not in str(getattr(response, "url", "")):
                    return
                try:
                    payload = response.json()
                except Exception:
                    return
                user = payload.get("user") if isinstance(payload, Mapping) else None
                if isinstance(user, Mapping):
                    profile.update(user)

            page.on("response", capture)
            page.goto(target.canonical_url, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(1_000)

        avatar = profile.get("avatar_larger") or profile.get("avatar_medium") or {}
        avatar_urls = avatar.get("url_list", ()) if isinstance(avatar, Mapping) else ()
        return SourceCreator(
            platform="douyin",
            creator_id=target.creator_id,
            display_name=str(profile.get("nickname") or f"Douyin {target.creator_id[:12]}"),
            canonical_url=target.canonical_url,
            avatar_url=str(avatar_urls[0]) if avatar_urls else None,
        )

    def iter_items(
        self,
        creator: SourceCreator,
        *,
        checkpoint: EnumerationCheckpoint | None,
    ) -> Iterator[EnumerationPage]:
        if self._enumerator is None:
            raise RuntimeError("Douyin item enumerator is not configured")
        iterator = getattr(self._enumerator, "iter_pages", None)
        if iterator is not None:
            yield from iterator(creator, checkpoint)
        else:
            yield from self._enumerator.iter_items(creator, checkpoint=checkpoint)

    def refresh_item(self, item: SourceItem) -> SourceItem:
        return self._downloader.refresh_item(item)

    def _refresh_from_browser(self, item: SourceItem) -> SourceItem:
        refreshed: list[SourceItem] = []
        with self.session.open_page(
            headless=self.session.acquisition_headless,
            task="refresh-media",
        ) as page:
            def capture(response: Any) -> None:
                if "/aweme/detail/" not in str(getattr(response, "url", "")):
                    return
                try:
                    payload = response.json()
                except Exception:
                    return
                raw = payload.get("aweme_detail") if isinstance(payload, Mapping) else None
                if isinstance(raw, Mapping):
                    refreshed.append(map_aweme(raw, item.creator_id))

            page.on("response", capture)
            page.goto(item.canonical_url, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(1_000)
        return refreshed[-1] if refreshed else item

    def download_assets(
        self,
        item: SourceItem,
        destination: Path,
        *,
        progress,
    ) -> DownloadedAssets:
        return self._downloader.download_assets(item, destination, progress=progress)
