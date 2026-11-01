"""Bilibili favorite folders: offline fixtures, no network, no SDK."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.platforms.bilibili.adapter import BilibiliAdapter
from src.platforms.bilibili.favorites import BilibiliFavorites, is_playable_media
from src.platforms.errors import PlatformEnumerationError, TargetResolutionError
from src.platforms.models import SourceCreator

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "bilibili"


def _load(name: str):
    return json.loads((FIXTURES / name).read_text("utf-8"))


class FakeFavorites:
    """Serves the captured pages in order and records the requested page numbers."""

    def __init__(self, pages=None, info=None):
        self.pages = pages if pages is not None else {
            "1": _load("fav_list_page1.json"),
            "2": _load("fav_list_page2.json"),
        }
        self.info = info if info is not None else _load("fav_folder_info.json")
        self.requested_pages: list[str] = []

    def folder_info(self, media_id):
        return BilibiliFavorites(http_get=lambda url, params: self.info).folder_info(media_id)

    def iter_pages(self, media_id, *, page_size=20):
        for page in ("1", "2"):
            payload = self.pages[page]
            self.requested_pages.append(page)
            data = payload["data"]
            medias = tuple(item for item in data["medias"] if is_playable_media(item))
            yield medias, bool(data["has_more"])


def make_favorites(pages=None, info=None) -> BilibiliFavorites:
    pages = pages if pages is not None else {
        "1": _load("fav_list_page1.json"),
        "2": _load("fav_list_page2.json"),
    }
    info = info if info is not None else _load("fav_folder_info.json")

    def fake_get(url, params):
        if "folder/info" in url:
            return info
        return pages[str(params["pn"])]

    return BilibiliFavorites(http_get=fake_get)


def make_adapter(favorites=None) -> BilibiliAdapter:
    config = SimpleNamespace(bilibili=SimpleNamespace(sessdata=""))
    return BilibiliAdapter(config, favorites=favorites or FakeFavorites())


def test_folder_info_maps_title_count_and_owner():
    folder = make_favorites().folder_info("63692673")

    assert folder.media_id == "63692673"
    assert folder.media_count == 307
    assert folder.owner == "DIYgod"


def test_deleted_entries_are_filtered_by_attr():
    assert is_playable_media({"bvid": "BV1", "attr": 0}) is True
    assert is_playable_media({"bvid": "BV1", "attr": 1}) is False
    assert is_playable_media({"bvid": "", "attr": 0}) is False


def test_pagination_follows_has_more_and_drops_dead_videos():
    pages = list(make_favorites().iter_pages("63692673"))

    assert len(pages) == 2
    first_ids = [media["bvid"] for media in pages[0][0]]
    assert first_ids == ["BV1wzfcBQEE6", "BV1hzqrBtEMP"]  # BV1qbL56CE1D 已失效
    assert pages[0][1] is True
    assert pages[1][1] is False


def test_private_or_missing_folder_is_a_clear_resolution_error():
    empty = {"code": 0, "message": "OK", "data": None}

    with pytest.raises(TargetResolutionError):
        make_favorites(info=empty).folder_info("1")


def test_error_codes_surface_as_enumeration_errors():
    failure = {"code": -404, "message": "啥都木有", "data": None}

    with pytest.raises(PlatformEnumerationError) as error:
        make_favorites(info=failure).folder_info("1")
    assert "-404" in str(error.value)


@pytest.mark.parametrize(
    "target",
    [
        "https://space.bilibili.com/2267573/favlist?fid=63692673",
        "https://space.bilibili.com/2267573/favlist?fid=63692673&ftype=create",
        "bilibili:fav:63692673",
    ],
)
def test_adapter_matches_favorite_targets(target):
    assert make_adapter().matches(target) is True


def test_adapter_resolves_favorite_target_to_a_collection():
    resolved = make_adapter().resolve(
        "https://space.bilibili.com/2267573/favlist?fid=63692673&ftype=create"
    )

    assert resolved.target_type == "collection"
    assert resolved.creator_id == "fav-63692673"
    assert resolved.metadata == {"fid": "63692673"}
    assert "fid=63692673" in resolved.canonical_url


def test_creator_carries_the_folder_id_for_enumeration():
    adapter = make_adapter()
    resolved = adapter.resolve("bilibili:fav:63692673")

    creator = adapter.get_creator(resolved)

    assert creator.creator_id == "fav-63692673"
    assert creator.raw_metadata["fid"] == "63692673"
    assert "收藏夹" in creator.display_name


def test_iter_items_maps_favorite_entries_onto_the_shared_item_contract():
    adapter = make_adapter()
    creator = SourceCreator(
        platform="bilibili",
        creator_id="fav-63692673",
        display_name="B站收藏夹 63692673",
        canonical_url="https://space.bilibili.com/2267573/favlist?fid=63692673",
        raw_metadata={"fid": "63692673", "target_type": "collection"},
    )

    pages = list(adapter.iter_items(creator, checkpoint=None))

    assert len(pages) == 2
    first = pages[0].items[0]
    assert first.source_id == "bilibili_BV1wzfcBQEE6"
    assert first.title == "春节走亲戚，亲戚问我有什么才艺…"
    assert first.canonical_url == "https://www.bilibili.com/video/BV1wzfcBQEE6"
    assert first.duration_seconds == 32.0
    assert first.published_at is not None
    assert pages[0].checkpoint.expected_count == 307
    assert pages[0].has_more is True
    assert pages[1].checkpoint.complete is True


def test_iter_items_skips_items_already_in_the_checkpoint():
    from src.platforms.models import EnumerationCheckpoint

    adapter = make_adapter()
    creator = SourceCreator(
        platform="bilibili",
        creator_id="fav-63692673",
        display_name="B站收藏夹 63692673",
        canonical_url="https://space.bilibili.com/2267573/favlist?fid=63692673",
        raw_metadata={"fid": "63692673"},
    )

    pages = list(
        adapter.iter_items(
            creator,
            checkpoint=EnumerationCheckpoint(seen_ids=frozenset({"BV1wzfcBQEE6"})),
        )
    )

    assert [item.item_id for item in pages[0].items] == ["BV1hzqrBtEMP"]
