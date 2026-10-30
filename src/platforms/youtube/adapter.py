"""YouTube source adapter backed by yt-dlp.

yt-dlp is already a runtime dependency (Bilibili audio download uses it), so this
adapter adds a third platform without new requirements or browser automation.
Enumeration uses ``--flat-playlist --dump-json``; download extracts audio to WAV,
which is what the shared ASR stage consumes.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from src.distillation.progress import TransferProgress
from src.platforms.errors import (
    PlatformDownloadError,
    PlatformEnumerationError,
    TargetResolutionError,
)
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

_CHANNEL_PATH_RE = re.compile(
    r"^/(?P<kind>channel/(?P<channel_id>UC[\w-]+)|@(?P<handle>[\w.\-]+)|c/(?P<custom>[\w.\-]+)|user/(?P<user>[\w.\-]+))(?:/.*)?$"
)
_VIDEO_QUERY_KEYS = ("v",)
_SHORT_HOSTS = frozenset({"youtu.be", "www.youtu.be"})
_MAIN_HOSTS = frozenset(
    {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}
)
_EXPLICIT_RE = re.compile(r"^youtube:(?P<rest>.+)$", re.IGNORECASE)


def _first(values: Any) -> str:
    if isinstance(values, list) and values:
        return str(values[0])
    return "" if values is None else str(values)


class YoutubeAdapter:
    """Read-only public YouTube adapter: no login, no cookies."""

    descriptor = PlatformDescriptor(
        name="youtube",
        url_patterns=(
            r"https?://(?:www\.)?youtube\.com/@[\w.\-]+",
            r"https?://(?:www\.)?youtube\.com/(?:channel/c|user/)[\w\-]+",
            r"https?://(?:www\.)?youtube\.com/watch\?v=[\w-]+",
            r"https?://youtu\.be/[\w-]+",
        ),
        item_types=frozenset({ItemType.VIDEO}),
        requires_browser=False,
        requires_auth=False,
        commands=("status", "creator"),
    )

    def __init__(
        self,
        *,
        runner: Callable[[list[str]], str] | None = None,
        ytdlp_bin: str = "yt-dlp",
        max_items: int = 200,
    ):
        self._runner = runner or self._run_ytdlp
        self._ytdlp_bin = ytdlp_bin
        self._max_items = max_items

    # -- yt-dlp boundary -------------------------------------------------
    def _run_ytdlp(self, args: list[str]) -> str:
        command = [self._ytdlp_bin, *args]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=900,
                check=False,
            )
        except FileNotFoundError:
            # ``pip install yt-dlp`` may land outside PATH; fall back to the module.
            command = [sys.executable, "-m", "yt_dlp", *args]
            try:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=900,
                    check=False,
                )
            except FileNotFoundError as exc:
                raise PlatformEnumerationError(
                    "yt-dlp is not installed; run `pip install -r requirements.txt`"
                ) from exc
        except subprocess.TimeoutExpired as exc:
            raise PlatformEnumerationError("yt-dlp timed out") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or "").strip().splitlines()
            message = detail[-1] if detail else "unknown yt-dlp error"
            raise PlatformEnumerationError(f"yt-dlp failed: {message}")
        return completed.stdout

    # -- adapter contract ------------------------------------------------
    def matches(self, target: str) -> bool:
        text = target.strip()
        if _EXPLICIT_RE.match(text):
            return True
        parts = urlsplit(text if "://" in text else f"https://{text}")
        host = parts.hostname or ""
        if host in _SHORT_HOSTS:
            return bool(parts.path.strip("/"))
        if host not in _MAIN_HOSTS:
            return False
        if parts.path in {"/watch", "/playlist"}:
            return "v" in parse_qs(parts.query) or "list" in parse_qs(parts.query)
        return bool(_CHANNEL_PATH_RE.match(parts.path))

    def auth_status(self) -> AuthStatus:
        return AuthStatus("not_required", "YouTube 公开内容无需登录")

    def authenticate(self, *, headful: bool) -> None:
        """Public content needs no credential; nothing to do."""

    def resolve(self, target: str) -> ResolvedTarget:
        text = target.strip()
        explicit = _EXPLICIT_RE.match(text)
        if explicit:
            text = explicit.group("rest").strip()
        parts = urlsplit(text if "://" in text else f"https://{text}")
        host = parts.hostname or ""
        path = parts.path.rstrip("/") or "/"

        if host in _SHORT_HOSTS:
            video_id = path.strip("/")
            if not video_id:
                raise TargetResolutionError("YouTube short link is missing a video id")
            return self._video_target(video_id, f"https://www.youtube.com/watch?v={video_id}", text)

        if host not in _MAIN_HOSTS:
            raise TargetResolutionError(
                "YouTube 目标需为 @handle、/channel/UC…、/c/…、/user/… 或视频链接"
            )

        if path == "/watch":
            video_id = _first(parse_qs(parts.query).get("v"))
            if not video_id:
                raise TargetResolutionError("YouTube watch link is missing the v parameter")
            return self._video_target(video_id, f"https://www.youtube.com/watch?v={video_id}", text)

        match = _CHANNEL_PATH_RE.match(path)
        if not match:
            raise TargetResolutionError(
                "YouTube 目标需为 @handle、/channel/UC…、/c/…、/user/… 或视频链接"
            )
        creator_id = (
            match.group("channel_id")
            or match.group("handle")
            or match.group("custom")
            or match.group("user")
        )
        return ResolvedTarget(
            platform="youtube",
            creator_id=creator_id,
            canonical_url=f"https://www.youtube.com{path}",
            original_target=target,
            target_type="creator",
        )

    def get_creator(self, target: ResolvedTarget) -> SourceCreator:
        if target.target_type == "video":
            return SourceCreator(
                platform="youtube",
                creator_id=target.creator_id,
                display_name=str(target.metadata.get("title") or target.creator_id),
                canonical_url=target.canonical_url,
            )
        return SourceCreator(
            platform="youtube",
            creator_id=target.creator_id,
            display_name=target.creator_id,
            canonical_url=target.canonical_url,
        )

    def iter_items(
        self,
        creator: SourceCreator,
        *,
        checkpoint: EnumerationCheckpoint | None,
    ) -> Iterator[EnumerationPage]:
        seen = set(checkpoint.seen_ids) if checkpoint else set()

        if creator.creator_id and creator.canonical_url.endswith(f"v={creator.creator_id}"):
            item = self._video_item(creator.creator_id, creator.display_name)
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

        output = self._runner(
            [
                "--flat-playlist",
                "--dump-json",
                "--ignore-errors",
                "--no-warnings",
                "--playlist-end",
                str(self._max_items),
                creator.canonical_url,
            ]
        )
        items = tuple(
            item
            for item in (self._map_entry(entry) for entry in _iter_json_lines(output))
            if item is not None and item.item_id not in seen
        )
        next_seen = frozenset(seen | {item.item_id for item in items})
        yield EnumerationPage(
            items=items,
            checkpoint=EnumerationCheckpoint(
                cursor=None,
                seen_ids=next_seen,
                complete=True,
                expected_count=len(next_seen),
            ),
            has_more=False,
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
        destination.mkdir(parents=True, exist_ok=True)
        started = datetime.now(timezone.utc)
        progress(
            TransferProgress(
                source_id=item.source_id,
                completed_bytes=0,
                total_bytes=None,
                bytes_per_second=0.0,
                timestamp=started,
            )
        )
        try:
            self._runner(
                [
                    "-x",
                    "--audio-format",
                    "wav",
                    "--audio-quality",
                    "0",
                    "--no-playlist",
                    "-o",
                    str(destination / "%(id)s.%(ext)s"),
                    item.canonical_url,
                ]
            )
        except PlatformEnumerationError as exc:
            raise PlatformDownloadError(f"YouTube 下载失败：{exc}") from exc

        audio_path = destination / f"{item.item_id}.wav"
        if not audio_path.exists():
            candidates = sorted(
                destination.glob(f"{item.item_id}.*"),
                key=lambda path: path.stat().st_size,
                reverse=True,
            )
            if not candidates:
                raise PlatformDownloadError(
                    f"YouTube 未产出音频文件：{item.item_id}"
                )
            audio_path = candidates[0]

        size = audio_path.stat().st_size
        progress(
            TransferProgress(
                source_id=item.source_id,
                completed_bytes=size,
                total_bytes=size,
                bytes_per_second=0.0,
                timestamp=datetime.now(timezone.utc),
            )
        )
        return DownloadedAssets(audio_path=audio_path)

    # -- mapping ---------------------------------------------------------
    def _video_target(
        self, video_id: str, canonical_url: str, original: str
    ) -> ResolvedTarget:
        return ResolvedTarget(
            platform="youtube",
            creator_id=video_id,
            canonical_url=canonical_url,
            original_target=original,
            target_type="video",
            metadata={"title": video_id},
        )

    def _video_item(self, video_id: str, title: str) -> SourceItem:
        return SourceItem(
            platform="youtube",
            item_id=video_id,
            creator_id=video_id,
            item_type=ItemType.VIDEO,
            title=title or video_id,
            description="",
            canonical_url=f"https://www.youtube.com/watch?v={video_id}",
        )

    def _map_entry(self, entry: dict[str, Any]) -> SourceItem | None:
        video_id = str(entry.get("id") or "").strip()
        if not video_id:
            return None
        title = str(entry.get("title") or video_id)
        channel_id = str(entry.get("channel_id") or entry.get("uploader_id") or "")
        duration = entry.get("duration")
        timestamp = entry.get("timestamp")
        published_at = None
        if isinstance(timestamp, (int, float)) and timestamp > 0:
            published_at = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        return SourceItem(
            platform="youtube",
            item_id=video_id,
            creator_id=channel_id or video_id,
            item_type=ItemType.VIDEO,
            title=title,
            description=str(entry.get("description") or ""),
            canonical_url=f"https://www.youtube.com/watch?v={video_id}",
            published_at=published_at,
            duration_seconds=float(duration) if isinstance(duration, (int, float)) else None,
        )


def _iter_json_lines(output: str) -> Iterator[dict[str, Any]]:
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            yield payload
