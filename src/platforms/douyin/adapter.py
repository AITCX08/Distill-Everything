"""High-level Douyin adapter composed from browser session primitives."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping
from urllib.parse import urlparse

from src.platforms.douyin.resolver import (
    DouyinResolver,
    extract_collection_target,
    extract_likes_target,
    extract_sec_uid,
    extract_target_url,
)
from src.platforms.douyin.cookie_export import (
    DEFAULT_SIDECAR_DIR,
    has_sidecar_cookie,
    read_sidecar_cookie,
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
        sidecar=None,
        sidecar_required: bool = False,
        sidecar_dir=None,
    ) -> None:
        self.session = session
        self._resolver_factory = resolver_factory
        self._enumerator = enumerator or DouyinEnumerator(DouyinBrowserRoute(session))
        self._downloader = downloader or DouyinDownloader(refresh_item=self._refresh_from_browser)
        self._creator_loader = creator_loader
        self._sidecar = sidecar
        self._sidecar_required = bool(sidecar_required)
        self._sidecar_dir = sidecar_dir or DEFAULT_SIDECAR_DIR
        self._sidecar_checked = False
        self._sidecar_ok = False

    @property
    def sidecar(self):
        return self._sidecar

    @property
    def sidecar_dir(self):
        return self._sidecar_dir

    def _sidecar_online(self) -> bool:
        if self._sidecar is None:
            return False
        if not self._sidecar_checked:
            self._sidecar_ok = self._sidecar.is_available()
            self._sidecar_checked = True
        return self._sidecar_ok

    def _sidecar_ready(self) -> bool:
        return self._sidecar_online() or self._sidecar_required

    def _sidecar_requester(self, creator: SourceCreator):
        """Choose the sidecar endpoint for this target, or ``None`` to fall back."""

        url = (creator.canonical_url or "").lower()
        if "/collection/" in url:
            mix_id = url.split("/collection/")[1].split("?")[0].strip("/")
            if not mix_id.isdigit():
                return None
            return lambda _creator, cursor: self._sidecar.user_mix(
                mix_id, cursor=int(cursor or 0)
            )
        if "showtab=like" in url:
            sec_uid = str(creator.creator_id).removesuffix("-likes")
            if not sec_uid or sec_uid == "self":
                return None  # 自己的 sec_uid 需要登录态才知道
            return lambda _creator, cursor: self._sidecar.user_likes(
                sec_uid, cursor=int(cursor or 0)
            )
        if "showtab=collect" in url:
            cookie = read_sidecar_cookie(self._sidecar_dir)
            if not cookie:
                return None  # 「我的收藏」走 cookie，没有登录态就回退浏览器
            return lambda _creator, cursor: self._sidecar.user_collection(
                cookie, cursor=int(cursor or 0)
            )
        sec_uid = str(creator.creator_id)
        if not sec_uid or sec_uid.endswith("-collection"):
            return None
        return lambda _creator, cursor: self._sidecar.user_posts(
            sec_uid, cursor=int(cursor or 0)
        )

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

    @property
    def account(self):
        """Logged-in account (sec_uid), captured right after login."""

        loader = getattr(self.session, "account", None)
        return loader if loader is not None else None

    def auth_status(self) -> AuthStatus:
        if self._sidecar_online():
            if has_sidecar_cookie(self._sidecar_dir):
                return AuthStatus("configured", "抖音 sidecar 已登录")
            return AuthStatus(
                "missing",
                "sidecar 已连接但尚未登录：扫码后运行 python main.py source sync-cookie",
            )
        return self.session.auth_status()

    def authenticate(self, *, headful: bool) -> None:
        self.session.authenticate(headful=headful)

    def resolve(self, target: str) -> ResolvedTarget:
        likes = extract_likes_target(target.strip())
        if likes is not None:
            creator_id, canonical_url = likes
            return ResolvedTarget(
                platform="douyin",
                creator_id=creator_id,
                canonical_url=canonical_url,
                original_target=target,
                target_type="collection",
                metadata={"kind": "likes"},
            )

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
            if target.creator_id.endswith("-likes"):
                label = "我喜欢的视频"
            elif target.creator_id.endswith("-collect"):
                label = "我的收藏"
            else:
                label = f"抖音合集 {target.creator_id.split('-')[0]}"
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
        if self._sidecar_ready():
            requester = self._sidecar_requester(creator)
            if requester is not None:
                yield from DouyinEnumerator(requester).iter_pages(creator, checkpoint)
                return
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
