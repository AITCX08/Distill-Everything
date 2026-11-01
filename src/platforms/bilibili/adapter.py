"""Adapt the existing Bilibili crawler to platform-neutral contracts."""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import tempfile
from collections.abc import Callable, Iterator, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import requests

from src.crawl.audio_download import (
    download_audio_with_progress,
    generate_cookies_file,
    parse_duration_str,
)
from src.crawl.auth import get_credential, run_qrcode_login, save_credential
from src.crawl.video_list import fetch_user_videos
from src.distillation.progress import TransferProgress
from src.platforms.bilibili.favorites import BilibiliFavorites
from src.platforms.errors import PlatformDownloadError, TargetResolutionError
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


_SPACE_URL_RE = re.compile(
    r"^https?://space\.bilibili\.com/(?P<uid>\d+)(?:[/?#].*)?$",
    re.IGNORECASE,
)
_EXPLICIT_UID_RE = re.compile(r"^bilibili:(?P<uid>\d+)$", re.IGNORECASE)
_BARE_BVID_RE = re.compile(r"^(?P<bvid>BV[0-9A-Za-z]+)$", re.IGNORECASE)
_BARE_AV_RE = re.compile(r"^av(?P<aid>\d+)$", re.IGNORECASE)
_B23_HOSTS = frozenset({"b23.tv", "www.b23.tv"})
_BILIBILI_VIDEO_HOSTS = frozenset(
    {"bilibili.com", "www.bilibili.com", "m.bilibili.com"}
)
_ALLOWED_REDIRECT_HOSTS = _B23_HOSTS | _BILIBILI_VIDEO_HOSTS
_VIDEO_PATH_RE = re.compile(
    r"^/video/(?P<identifier>BV[0-9A-Za-z]+|av\d+)(?:/)?$",
    re.IGNORECASE,
)
_FAV_URL_RE = re.compile(
    r"^https?://space\.bilibili\.com/(?P<mid>\d+)/favlist\?(?:[^#]*&)?fid=(?P<fid>\d+)",
    re.IGNORECASE,
)
_FAV_DIRECT_RE = re.compile(r"^bilibili:fav:(?P<fid>\d+)$", re.IGNORECASE)
_BILIBILI_VIEW_API = "https://api.bilibili.com/x/web-interface/view"
_HTTP_TIMEOUT_SECONDS = 10
_MAX_REDIRECTS = 4


def legacy_source_id(value: str) -> str:
    """Qualify a legacy BVID without double-prefixing new identifiers."""

    return value if value.startswith("bilibili_") else f"bilibili_{value}"


def _favorite_target(target: str) -> tuple[str, str] | None:
    """Return ``(fid, mid)`` for a favorite-folder target, ``mid`` may be empty."""

    direct = _FAV_DIRECT_RE.match(target)
    if direct:
        return direct.group("fid"), ""
    url = _FAV_URL_RE.match(target)
    if url:
        return url.group("fid"), url.group("mid")
    return None


class BilibiliAdapter:
    """Thin wrapper around Distill-Everything's established Bilibili functions."""

    descriptor = PlatformDescriptor(
        name="bilibili",
        url_patterns=(
            r"https?://space\.bilibili\.com/\d+",
            r"https?://(?:www\.)?b23\.tv/[^\s]+",
            r"https?://(?:www\.|m\.)?bilibili\.com/video/(?:BV|av)[^\s]+",
            r"(?:BV[0-9A-Za-z]{4,}|av\d+)",
        ),
        item_types=frozenset({ItemType.VIDEO}),
        requires_browser=True,
        requires_auth=True,
        commands=("status", "login", "creator"),
    )

    def __init__(
        self,
        config,
        *,
        credential_provider: Callable = get_credential,
        video_fetcher: Callable = fetch_user_videos,
        download_fn: Callable = download_audio_with_progress,
        cookies_factory: Callable = generate_cookies_file,
        login_fn: Callable = run_qrcode_login,
        credential_saver: Callable = save_credential,
        http_request: Callable[..., Any] | None = None,
        favorites: BilibiliFavorites | None = None,
    ):
        self._config = config
        self._credential_provider = credential_provider
        self._video_fetcher = video_fetcher
        self._download_fn = download_fn
        self._cookies_factory = cookies_factory
        self._login_fn = login_fn
        self._credential_saver = credential_saver
        self._http_request = http_request or requests.request
        sessdata = getattr(getattr(config, "bilibili", None), "sessdata", "") or ""
        self._favorites = favorites or BilibiliFavorites(sessdata=sessdata)

    def matches(self, target: str) -> bool:
        text = target.strip()
        return bool(
            _SPACE_URL_RE.match(text)
            or _EXPLICIT_UID_RE.match(text)
            or _FAV_URL_RE.match(text)
            or _FAV_DIRECT_RE.match(text)
            or _is_video_target(text)
        )

    def auth_status(self) -> AuthStatus:
        config = self._config.bilibili
        if config.sessdata:
            return AuthStatus("configured", "Bilibili credentials are configured")
        cache = getattr(self._config, "credentials_cache", None)
        if cache is not None and _has_usable_cached_credential(Path(cache)):
            return AuthStatus("configured", "Bilibili cached credentials are available")
        return AuthStatus("missing", "Run the Bilibili login command")

    def authenticate(self, *, headful: bool) -> None:
        del headful  # The existing QR login owns its browser presentation.
        result = self._login_fn()
        if isinstance(result, tuple) and len(result) == 2:
            credential, buvid3 = result
            self._credential_saver(
                credential,
                buvid3,
                self._config.credentials_cache,
            )

    def save_dashboard_credential(self, credential: Any, buvid3: str) -> None:
        """Persist a credential produced by Dashboard's local QR session."""

        self._credential_saver(credential, buvid3, self._config.credentials_cache)

    def resolve(self, target: str) -> ResolvedTarget:
        target = target.strip()
        favorite = _favorite_target(target)
        if favorite is not None:
            fid, mid = favorite
            return ResolvedTarget(
                platform="bilibili",
                creator_id=f"fav-{fid}",
                canonical_url=(
                    f"https://space.bilibili.com/{mid}/favlist?fid={fid}"
                    if mid
                    else f"https://space.bilibili.com/0/favlist?fid={fid}"
                ),
                original_target=target,
                target_type="collection",
                metadata={"fid": fid},
            )

        match = _SPACE_URL_RE.match(target) or _EXPLICIT_UID_RE.match(target)
        if match:
            creator_id = match.group("uid")
            return ResolvedTarget(
                platform="bilibili",
                creator_id=creator_id,
                canonical_url=f"https://space.bilibili.com/{creator_id}",
                original_target=target,
            )

        if _is_video_target(target):
            bvid, canonical_url, metadata = self._resolve_video_target(target)
            return ResolvedTarget(
                platform="bilibili",
                creator_id=bvid,
                canonical_url=canonical_url,
                original_target=target,
                target_type="video",
                metadata=metadata,
            )

        if not match:
            raise TargetResolutionError(
                "Bilibili targets must use a space.bilibili.com, b23.tv, BV or av URL"
            )

    def get_creator(self, target: ResolvedTarget) -> SourceCreator:
        if target.target_type == "collection":
            fid = str(target.metadata.get("fid") or target.creator_id.removeprefix("fav-"))
            return SourceCreator(
                platform="bilibili",
                creator_id=target.creator_id,
                display_name=f"B站收藏夹 {fid}",
                canonical_url=target.canonical_url,
                raw_metadata={"target_type": "collection", "fid": fid},
            )
        if target.target_type == "video":
            return SourceCreator(
                platform="bilibili",
                creator_id=target.creator_id,
                display_name=str(target.metadata.get("title") or target.creator_id),
                canonical_url=target.canonical_url,
                raw_metadata=target.metadata,
            )
        return SourceCreator(
            platform="bilibili",
            creator_id=target.creator_id,
            display_name=f"Bilibili {target.creator_id}",
            canonical_url=target.canonical_url,
        )

    def iter_items(
        self,
        creator: SourceCreator,
        *,
        checkpoint: EnumerationCheckpoint | None,
    ) -> Iterator[EnumerationPage]:
        fid = str(creator.raw_metadata.get("fid") or "") if creator.raw_metadata else ""
        if fid:
            yield from self._iter_favorite_pages(creator, fid, checkpoint)
            return
        if _is_video_url(creator.canonical_url):
            item = self.map_video(creator.raw_metadata, creator.creator_id)
            yield EnumerationPage(
                items=(item,),
                checkpoint=EnumerationCheckpoint(
                    seen_ids=frozenset({item.item_id}),
                    complete=True,
                    expected_count=1,
                ),
                has_more=False,
            )
            return

        credential, _ = self._credential_provider(self._config)
        seen_ids = set(checkpoint.seen_ids) if checkpoint else set()
        fetched = self._video_fetcher(
            int(creator.creator_id),
            credential,
            existing_bvids=seen_ids,
            max_candidates=0,
        )
        if inspect.isawaitable(fetched):
            fetched = asyncio.run(fetched)

        items = tuple(self.map_video(raw, creator.creator_id) for raw in fetched)
        next_seen = frozenset(seen_ids | {item.item_id for item in items})
        next_checkpoint = EnumerationCheckpoint(
            cursor=None,
            seen_ids=next_seen,
            complete=True,
            expected_count=len(next_seen),
        )
        yield EnumerationPage(items=items, checkpoint=next_checkpoint, has_more=False)

    def map_favorite_media(
        self,
        media: Mapping[str, Any],
        creator_id: str,
    ) -> SourceItem:
        """Map one favorite-folder entry onto the shared item contract."""

        bvid = str(media.get("bvid") or "").strip()
        timestamp = media.get("pubtime")
        published_at = None
        if isinstance(timestamp, (int, float)) and timestamp > 0:
            published_at = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        duration = media.get("duration")
        return SourceItem(
            platform="bilibili",
            item_id=bvid,
            creator_id=creator_id,
            item_type=ItemType.VIDEO,
            title=str(media.get("title") or bvid),
            description=str(media.get("intro") or ""),
            canonical_url=f"https://www.bilibili.com/video/{bvid}",
            published_at=published_at,
            duration_seconds=float(duration) if isinstance(duration, (int, float)) else None,
            cover_url=str(media.get("cover") or "") or None,
            raw_metadata={"target_type": "collection", "fav_title": media.get("title")},
        )

    def _iter_favorite_pages(
        self,
        creator: SourceCreator,
        fid: str,
        checkpoint: EnumerationCheckpoint | None,
    ) -> Iterator[EnumerationPage]:
        seen = set(checkpoint.seen_ids) if checkpoint else set()
        folder = self._favorites.folder_info(fid)
        for medias, has_more in self._favorites.iter_pages(fid):
            items: list[SourceItem] = []
            for media in medias:
                item = self.map_favorite_media(media, creator.creator_id)
                if not item.item_id or item.item_id in seen:
                    continue
                seen.add(item.item_id)
                items.append(item)
            yield EnumerationPage(
                items=tuple(items),
                checkpoint=EnumerationCheckpoint(
                    seen_ids=frozenset(seen),
                    complete=not has_more,
                    expected_count=folder.media_count or None,
                ),
                has_more=has_more,
            )

    def refresh_item(self, item: SourceItem) -> SourceItem:
        return item

    def download_assets(
        self,
        item: SourceItem,
        destination: Path,
        *,
        progress: Callable[[TransferProgress], None],
    ) -> DownloadedAssets:
        credential, buvid3 = self._credential_provider(self._config)
        latest_speed = 0.0

        def report_transfer(
            completed_bytes: int,
            total_bytes: int | None,
            bytes_per_second: float,
        ) -> None:
            nonlocal latest_speed
            latest_speed = max(0.0, bytes_per_second)
            progress(
                TransferProgress(
                    source_id=item.source_id,
                    completed_bytes=completed_bytes,
                    total_bytes=total_bytes,
                    bytes_per_second=latest_speed,
                    timestamp=datetime.now(timezone.utc),
                )
            )

        with tempfile.TemporaryDirectory(prefix="distill_bilibili_") as temp_dir:
            cookie_path = self._cookies_factory(
                credential,
                buvid3,
                Path(temp_dir) / "cookies.txt",
            )
            audio_path = self._download_fn(
                item.item_id,
                destination,
                audio_format="wav",
                cookies_file=cookie_path,
                progress_callback=report_transfer,
            )

        if audio_path is None:
            raise PlatformDownloadError(
                f"Bilibili audio download failed for {item.item_id}"
            )
        audio_path = Path(audio_path)
        size = audio_path.stat().st_size if audio_path.exists() else 0
        progress(
            TransferProgress(
                source_id=item.source_id,
                completed_bytes=size,
                total_bytes=size,
                bytes_per_second=latest_speed,
                timestamp=datetime.now(timezone.utc),
            )
        )
        return DownloadedAssets(audio_path=audio_path)

    def map_video(
        self,
        raw: Mapping[str, Any],
        creator_id: str,
    ) -> SourceItem:
        bvid = _normalize_bvid(raw.get("bvid"))
        if not bvid:
            raise TargetResolutionError("Bilibili metadata did not contain a valid BV id")
        timestamp = raw.get("pubdate")
        published_at = None
        if isinstance(timestamp, (int, float)) and timestamp > 0:
            published_at = datetime.fromtimestamp(timestamp, tz=timezone.utc)

        stat = raw.get("stat")
        stat = stat if isinstance(stat, Mapping) else {}
        duration = raw.get("duration_seconds", raw.get("duration", 0))
        duration_seconds = (
            float(duration)
            if isinstance(duration, (int, float))
            else parse_duration_str(str(duration or "0"))
        )

        return SourceItem(
            platform="bilibili",
            item_id=bvid,
            creator_id=creator_id,
            item_type=ItemType.VIDEO,
            title=str(raw.get("title") or bvid),
            description=str(raw.get("description") or raw.get("desc") or ""),
            canonical_url=f"https://www.bilibili.com/video/{bvid}",
            published_at=published_at,
            duration_seconds=duration_seconds,
            statistics={
                "views": int(raw.get("view_count") or stat.get("view") or 0),
                "comments": int(raw.get("comment_count") or stat.get("reply") or 0),
            },
            cover_url=str(raw.get("cover") or raw.get("pic") or "") or None,
            tags=_normalize_tags(raw.get("tags")),
            raw_metadata=dict(raw),
        )

    def _resolve_video_target(
        self,
        target: str,
    ) -> tuple[str, str, Mapping[str, Any]]:
        """Resolve a direct video target without launching a browser."""

        normalized = _normalize_video_input(target)
        if normalized is None:
            raise TargetResolutionError(
                "Bilibili video target must be a b23.tv link, BV id, av id or video URL"
            )

        source_url, identifier = normalized
        if source_url is not None and _is_b23_url(source_url):
            source_url = self._follow_redirects(source_url)
            parsed = _extract_video_identifier(source_url)
            if parsed is None:
                raise TargetResolutionError(
                    "b23.tv 短链未重定向到可识别的 Bilibili 视频地址"
                )
            identifier = parsed

        kind, value = identifier
        metadata = self._fetch_video_metadata(kind, value)
        bvid = _normalize_bvid(metadata.get("bvid"))
        if not bvid:
            raise TargetResolutionError("Bilibili metadata did not contain a valid BV id")
        return bvid, _canonical_video_url(bvid, source_url), metadata

    def _follow_redirects(self, url: str) -> str:
        current = url
        for _ in range(_MAX_REDIRECTS + 1):
            _validate_redirect_url(current)
            try:
                response = self._http_request(
                    "GET",
                    current,
                    allow_redirects=False,
                    stream=True,
                    timeout=_HTTP_TIMEOUT_SECONDS,
                    headers={"User-Agent": "Distill-Everything/1.0"},
                )
            except requests.RequestException as error:
                raise TargetResolutionError("Bilibili 短链请求失败") from error
            status = int(getattr(response, "status_code", 0) or 0)
            try:
                if status in {301, 302, 303, 307, 308}:
                    location = (getattr(response, "headers", {}) or {}).get("Location")
                    if not location:
                        raise TargetResolutionError("b23.tv 重定向缺少 Location")
                    next_url = urljoin(current, str(location))
                    _validate_redirect_url(next_url)
                    if _extract_video_identifier(next_url) is not None:
                        return next_url
                    current = next_url
                    continue
                if status < 200 or status >= 400:
                    raise TargetResolutionError(f"短链解析失败，HTTP 状态码: {status}")
                return current
            finally:
                close = getattr(response, "close", None)
                if callable(close):
                    close()
        raise TargetResolutionError("短链重定向次数超过安全上限")

    def _fetch_video_metadata(self, kind: str, value: str) -> Mapping[str, Any]:
        try:
            response = self._http_request(
                "GET",
                _BILIBILI_VIEW_API,
                params={"bvid" if kind == "bvid" else "aid": value},
                timeout=_HTTP_TIMEOUT_SECONDS,
                headers={"User-Agent": "Distill-Everything/1.0"},
            )
        except requests.RequestException as error:
            raise TargetResolutionError("Bilibili 元数据请求失败") from error
        status = int(getattr(response, "status_code", 0) or 0)
        if status < 200 or status >= 300:
            raise TargetResolutionError(f"Bilibili 元数据接口失败，HTTP 状态码: {status}")
        try:
            payload = response.json()
        except (TypeError, ValueError, AttributeError) as error:
            raise TargetResolutionError("Bilibili 元数据接口返回了无效 JSON") from error
        if not isinstance(payload, Mapping) or payload.get("code") not in {0, None}:
            message = payload.get("message") if isinstance(payload, Mapping) else ""
            suffix = f": {message}" if message else ""
            raise TargetResolutionError(f"Bilibili 元数据接口拒绝请求{suffix}")
        data = payload.get("data") if isinstance(payload, Mapping) else None
        if not isinstance(data, Mapping):
            raise TargetResolutionError("Bilibili 元数据接口未返回视频数据")
        return dict(data)


def _normalize_bvid(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    match = _BARE_BVID_RE.fullmatch(value)
    if not match:
        return None
    return "BV" + value[2:]


def _normalize_tags(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value.strip() else ()
    if not isinstance(value, (list, tuple)):
        return ()
    tags: list[str] = []
    for tag in value:
        if isinstance(tag, Mapping):
            normalized = tag.get("tag_name") or tag.get("name")
        else:
            normalized = tag
        if normalized is not None and str(normalized).strip():
            tags.append(str(normalized))
    return tuple(tags)


def _normalize_video_input(
    target: str,
) -> tuple[str | None, tuple[str, str]] | None:
    value = target.strip()
    bare_bvid = _BARE_BVID_RE.fullmatch(value)
    if bare_bvid:
        return None, ("bvid", _normalize_bvid(value) or value)
    bare_av = _BARE_AV_RE.fullmatch(value)
    if bare_av:
        return None, ("aid", bare_av.group("aid"))

    parsed_value = value
    if "://" not in parsed_value:
        parsed_value = f"https://{parsed_value}"
    try:
        parsed = urlsplit(parsed_value)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if host in _B23_HOSTS:
        if parsed.scheme not in {"http", "https"} or not parsed.path.strip("/"):
            return None
        return parsed_value, ("short", parsed_value)
    if host not in _BILIBILI_VIDEO_HOSTS:
        return None
    identifier = _extract_video_identifier(parsed_value)
    return (parsed_value, identifier) if identifier is not None else None


def _extract_video_identifier(url: str) -> tuple[str, str] | None:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if host not in _BILIBILI_VIDEO_HOSTS:
        return None
    match = _VIDEO_PATH_RE.fullmatch(parsed.path.rstrip("/"))
    if not match:
        return None
    identifier = match.group("identifier")
    if identifier.lower().startswith("bv"):
        return "bvid", _normalize_bvid(identifier) or identifier
    return "aid", identifier[2:]


def _is_b23_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    return (parsed.hostname or "").lower() in _B23_HOSTS


def _is_video_url(url: str) -> bool:
    return _extract_video_identifier(url) is not None


def _is_video_target(target: str) -> bool:
    value = target.strip()
    if _BARE_BVID_RE.fullmatch(value) or _BARE_AV_RE.fullmatch(value):
        return True
    normalized = _normalize_video_input(value)
    return bool(
        normalized is not None
        and (
            normalized[0] is not None
            and _is_b23_url(normalized[0])
            or normalized[1][0] in {"bvid", "aid"}
        )
    )


def _validate_redirect_url(url: str) -> None:
    try:
        parsed = urlsplit(url)
    except ValueError as error:
        raise TargetResolutionError("短链重定向地址格式无效") from error
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or host not in _ALLOWED_REDIRECT_HOSTS:
        raise TargetResolutionError(f"不受信任的重定向主机: {host or '<empty>'}")


def _canonical_video_url(bvid: str, source_url: str | None) -> str:
    suffix = ""
    if source_url:
        try:
            query = urlsplit(source_url).query
        except ValueError:
            query = ""
        if query:
            suffix = f"?{query}"
    return f"https://www.bilibili.com/video/{bvid}{suffix}"


def _has_usable_cached_credential(path: Path) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError):
        return False
    return bool(data.get("sessdata") and data.get("bili_jct"))
