"""Xiaohongshu adapter: notes, user feeds and collections via a sidecar."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

import requests

from src.distillation.progress import TransferProgress
from src.platforms.errors import (
    PlatformAuthenticationError,
    PlatformDownloadError,
    PlatformEnumerationError,
)
from src.platforms.models import (
    AuthStatus,
    DownloadedAssets,
    EnumerationCheckpoint,
    EnumerationPage,
    ItemType,
    PlatformDescriptor,
    ResolvedTarget,
    SourceAsset,
    SourceCreator,
    SourceItem,
)
from src.platforms.xhs.resolver import is_short_link, parse_xhs_target, resolve_target

_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


class XhsAdapter:
    descriptor = PlatformDescriptor(
        name="xiaohongshu",
        url_patterns=(
            r"https?://(?:www\.)?xiaohongshu\.com/explore/[\w]+",
            r"https?://(?:www\.)?xiaohongshu\.com/user/profile/[\w]+",
            r"https?://xhslink\.com/[\w]+",
            r"xhs:(?:note|user|collect):[\w-]+",
        ),
        item_types=frozenset({ItemType.VIDEO, ItemType.GALLERY}),
        requires_browser=False,
        requires_auth=True,
        commands=("status", "creator"),
    )

    def __init__(
        self,
        sidecar: Any,
        *,
        http_get: Callable[..., Any] | None = None,
        resolver: Callable[..., Any] = resolve_target,
    ) -> None:
        self._sidecar = sidecar
        self._http_get = http_get or requests.get
        self._resolver = resolver

    # -- platform contract ----------------------------------------------
    def matches(self, target: str) -> bool:
        text = (target or "").strip()
        if text.lower().startswith("xhs:"):
            return True
        if parse_xhs_target(text) is not None:
            return True
        return is_short_link(text)

    def auth_status(self) -> AuthStatus:
        if self._sidecar is not None and self._sidecar.is_available():
            return AuthStatus("configured", "小红书 sidecar 已连接")
        return AuthStatus("missing", "启动小红书 sidecar 后即可采集（契约见 docs/platform-sources.md 3.3）")

    def authenticate(self, *, headful: bool) -> None:
        del headful
        raise PlatformAuthenticationError(
            "小红书登录在 sidecar 侧完成：配置好 cookie 后重启 sidecar 即可"
        )

    def resolve(self, target: str) -> ResolvedTarget:
        parsed = self._resolver(target)
        target_type = {"note": "video", "user": "creator", "collect": "collection"}[parsed.kind]
        creator_id = (
            f"{parsed.value}-collect" if parsed.kind == "collect" else parsed.value
        )
        return ResolvedTarget(
            platform="xiaohongshu",
            creator_id=creator_id,
            canonical_url=parsed.canonical_url,
            original_target=target,
            target_type=target_type,
            metadata={"kind": parsed.kind, "value": parsed.value},
        )

    def get_creator(self, target: ResolvedTarget) -> SourceCreator:
        kind = str(target.metadata.get("kind") or "user")
        label = {
            "note": "小红书笔记",
            "collect": "小红书收藏",
            "user": "小红书用户",
        }.get(kind, "小红书")
        return SourceCreator(
            platform="xiaohongshu",
            creator_id=target.creator_id,
            display_name=f"{label} {target.creator_id}",
            canonical_url=target.canonical_url,
            raw_metadata={"kind": kind, "value": target.metadata.get("value") or target.creator_id},
        )

    def iter_items(
        self,
        creator: SourceCreator,
        *,
        checkpoint: EnumerationCheckpoint | None,
    ) -> Iterator[EnumerationPage]:
        if self._sidecar is None:
            raise PlatformEnumerationError("小红书 sidecar 未配置")
        kind = str(creator.raw_metadata.get("kind") or "user")
        value = str(creator.raw_metadata.get("value") or creator.creator_id)

        if kind == "note":
            item = self.map_note(self._sidecar.note(value), creator.creator_id)
            yield EnumerationPage(
                items=(item,),
                checkpoint=EnumerationCheckpoint(
                    seen_ids=frozenset({item.item_id}), complete=True, expected_count=1
                ),
                has_more=False,
            )
            return

        cursor = (checkpoint.cursor if checkpoint else None) or ""
        seen = set(checkpoint.seen_ids) if checkpoint else set()
        expected = checkpoint.expected_count if checkpoint else None
        while True:
            fetch = self._sidecar.user_collect if kind == "collect" else self._sidecar.user_notes
            payload = fetch(value, cursor=cursor)
            items: list[SourceItem] = []
            for raw in payload.get("items") or []:
                try:
                    item = self.map_note(raw, creator.creator_id)
                except (KeyError, TypeError, ValueError):
                    continue
                if item.item_id in seen:
                    continue
                seen.add(item.item_id)
                items.append(item)
            has_more = bool(payload.get("has_more"))
            next_cursor = str(payload.get("cursor") or "")
            yield EnumerationPage(
                items=tuple(items),
                checkpoint=EnumerationCheckpoint(
                    cursor=None if not has_more else next_cursor,
                    seen_ids=frozenset(seen),
                    complete=not has_more,
                    expected_count=payload.get("total") or expected,
                ),
                has_more=has_more,
            )
            if not has_more or not next_cursor or next_cursor == cursor:
                return
            cursor = next_cursor

    def refresh_item(self, item: SourceItem) -> SourceItem:
        return item

    # -- mapping ---------------------------------------------------------
    def map_note(self, raw: Mapping[str, Any], creator_id: str) -> SourceItem:
        note_id = str(raw.get("note_id") or "").strip()
        if not note_id:
            raise ValueError("笔记缺少 note_id")
        title = str(raw.get("title") or raw.get("desc") or note_id)
        description = str(raw.get("desc") or "")
        video_url = str(raw.get("video_url") or "").strip()
        images = [str(url) for url in (raw.get("images") or []) if url]
        if video_url:
            item_type = ItemType.VIDEO
            assets = (SourceAsset(kind="video", url=video_url),)
        elif images:
            item_type = ItemType.GALLERY
            assets = tuple(
                SourceAsset(kind="image", url=url, index=index)
                for index, url in enumerate(images)
            )
        else:
            item_type = ItemType.UNKNOWN
            assets = ()
        author = raw.get("author") if isinstance(raw.get("author"), Mapping) else {}
        return SourceItem(
            platform="xiaohongshu",
            item_id=note_id,
            creator_id=creator_id,
            item_type=item_type,
            title=title,
            description=description,
            canonical_url=str(raw.get("url") or f"https://www.xiaohongshu.com/explore/{note_id}"),
            cover_url=str(raw.get("cover") or "") or None,
            assets=assets,
            raw_metadata={"author": author.get("nickname"), "liked": raw.get("liked")},
        )

    # -- fetch -----------------------------------------------------------
    def download_assets(
        self,
        item: SourceItem,
        destination: Path,
        *,
        progress: Callable[[TransferProgress], None],
    ) -> DownloadedAssets:
        if not item.assets:
            raise PlatformDownloadError(f"小红书作品没有可下载素材：{item.item_id}")
        destination.mkdir(parents=True, exist_ok=True)
        video_path: Path | None = None
        image_paths: list[Path] = []
        for asset in item.assets:
            suffix = ".mp4" if asset.kind == "video" else ".jpg"
            name = f"{item.item_id}{suffix}" if asset.kind == "video" else f"{item.item_id}-{asset.index}{suffix}"
            target = destination / name
            try:
                response = self._http_get(asset.url, stream=True, timeout=60, headers=_HEADERS)
                response.raise_for_status()
                with target.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=256 * 1024):
                        if chunk:
                            handle.write(chunk)
            except Exception as exc:
                raise PlatformDownloadError(f"小红书素材下载失败：{exc}") from exc
            if asset.kind == "video":
                video_path = target
            else:
                image_paths.append(target)
        size = sum(path.stat().st_size for path in ([video_path] if video_path else image_paths))
        progress(
            TransferProgress(
                source_id=item.source_id,
                completed_bytes=size,
                total_bytes=size,
                bytes_per_second=0.0,
                timestamp=datetime.now(timezone.utc),
            )
        )
        return DownloadedAssets(
            video_path=video_path,
            image_paths=tuple(image_paths),
        )
