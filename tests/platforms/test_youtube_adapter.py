"""Contract tests for the yt-dlp backed YouTube adapter."""

from __future__ import annotations

import json

import pytest

from src.platforms.models import EnumerationCheckpoint, ItemType
from src.platforms.youtube import YoutubeAdapter
from src.platforms.errors import TargetResolutionError


def make_adapter(payload: str = "") -> tuple[YoutubeAdapter, list[list[str]]]:
    calls: list[list[str]] = []

    def runner(args: list[str]) -> str:
        calls.append(args)
        return payload

    return YoutubeAdapter(runner=runner), calls


@pytest.mark.parametrize(
    "target",
    [
        "https://www.youtube.com/@somecreator",
        "https://youtube.com/channel/UCabc123",
        "https://www.youtube.com/c/some-name",
        "https://www.youtube.com/user/legacy",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ",
        "youtube:@somecreator",
    ],
)
def test_matches_youtube_targets(target):
    adapter, _ = make_adapter()
    assert adapter.matches(target) is True


@pytest.mark.parametrize(
    "target",
    [
        "https://space.bilibili.com/123",
        "https://www.douyin.com/user/SEC",
        "https://example.com/@somecreator",
    ],
)
def test_does_not_match_other_platforms(target):
    adapter, _ = make_adapter()
    assert adapter.matches(target) is False


def test_resolve_channel_target():
    adapter, _ = make_adapter()
    resolved = adapter.resolve("https://www.youtube.com/@somecreator")

    assert resolved.platform == "youtube"
    assert resolved.creator_id == "somecreator"
    assert resolved.target_type == "creator"
    assert resolved.canonical_url == "https://www.youtube.com/@somecreator"


def test_resolve_watch_link_maps_to_video_target():
    adapter, _ = make_adapter()
    resolved = adapter.resolve("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10")

    assert resolved.target_type == "video"
    assert resolved.creator_id == "dQw4w9WgXcQ"
    assert resolved.canonical_url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_resolve_short_link():
    adapter, _ = make_adapter()
    resolved = adapter.resolve("https://youtu.be/dQw4w9WgXcQ")

    assert resolved.target_type == "video"
    assert resolved.creator_id == "dQw4w9WgXcQ"


def test_resolve_rejects_unknown_path():
    adapter, _ = make_adapter()
    with pytest.raises(TargetResolutionError):
        adapter.resolve("https://www.youtube.com/feed/subscriptions")


def test_iter_items_maps_flat_playlist_entries():
    payload = "\n".join(
        [
            json.dumps(
                {
                    "id": "vid00000001",
                    "title": "第一集",
                    "channel_id": "UCcreator",
                    "duration": 615,
                    "timestamp": 1735689600,
                }
            ),
            json.dumps({"id": "vid00000002", "title": "第二集", "channel_id": "UCcreator"}),
            "",
        ]
    )
    adapter, calls = make_adapter(payload)
    creator = adapter.get_creator(adapter.resolve("https://www.youtube.com/@somecreator"))

    pages = list(adapter.iter_items(creator, checkpoint=None))

    assert len(pages) == 1
    items = pages[0].items
    assert [item.item_id for item in items] == ["vid00000001", "vid00000002"]
    assert items[0].title == "第一集"
    assert items[0].duration_seconds == 615.0
    assert items[0].item_type is ItemType.VIDEO
    assert items[0].source_id == "youtube_vid00000001"
    assert pages[0].checkpoint.complete is True
    assert "--flat-playlist" in calls[0]
    assert "--playlist-end" in calls[0]


def test_iter_items_skips_checkpointed_ids():
    payload = json.dumps({"id": "vid00000001", "title": "看过了"})
    adapter, _ = make_adapter(payload)
    creator = adapter.get_creator(adapter.resolve("https://www.youtube.com/@somecreator"))

    pages = list(
        adapter.iter_items(
            creator,
            checkpoint=EnumerationCheckpoint(seen_ids=frozenset({"vid00000001"})),
        )
    )

    assert pages[0].items == ()
    assert pages[0].checkpoint.seen_ids == frozenset({"vid00000001"})


def test_download_creates_wav_asset(tmp_path):
    def runner(args: list[str]) -> str:
        if "-o" in args:
            assert args[args.index("-o") + 1].endswith("%(id)s.%(ext)s")
            (tmp_path / "vid00000001.wav").write_bytes(b"RIFF" + b"\x00" * 64)
        return ""

    adapter = YoutubeAdapter(runner=runner)
    item = adapter._map_entry({"id": "vid00000001", "title": "标题"})
    assert item is not None
    progress_events = []

    assets = adapter.download_assets(item, tmp_path, progress=progress_events.append)

    assert assets.audio_path == tmp_path / "vid00000001.wav"
    assert progress_events[-1].completed_bytes == assets.audio_path.stat().st_size
