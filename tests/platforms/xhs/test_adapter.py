"""Xiaohongshu adapter: routing, mapping and download, all offline."""

from __future__ import annotations

import pytest

from src.platforms.models import EnumerationCheckpoint, ItemType
from src.platforms.xhs.adapter import XhsAdapter
from src.platforms.xhs.resolver import parse_xhs_target

NOTE_ID = "65f0a1b2c3d4e5f60718293a"
USER_ID = "5f1a2b3c4d5e6f708192a3b4"


def note_payload(note_id: str = NOTE_ID, *, video: bool = True):
    return {
        "note_id": note_id,
        "title": "一条笔记",
        "desc": "正文",
        "video_url": f"https://cdn.example/{note_id}.mp4" if video else "",
        "images": [] if video else [f"https://cdn.example/{note_id}-1.jpg"],
        "cover": f"https://cdn.example/{note_id}.jpg",
        "author": {"nickname": "作者"},
        "liked": 12,
    }


class FakeSidecar:
    def __init__(self, *, notes=None, pages=None, available=True):
        self.notes = notes or {}
        self.pages = pages or {}
        self.available = available
        self.calls: list[tuple] = []

    def is_available(self):
        return self.available

    def note(self, note_id):
        self.calls.append(("note", note_id))
        return self.notes.get(note_id, note_payload(note_id))

    def user_notes(self, user_id, *, cursor=""):
        self.calls.append(("user_notes", user_id, cursor))
        return self.pages.get(("user", cursor), {"items": [], "has_more": False})

    def user_collect(self, user_id, *, cursor=""):
        self.calls.append(("collect", user_id, cursor))
        return self.pages.get(("collect", cursor), {"items": [], "has_more": False})


def make_adapter(sidecar=None) -> XhsAdapter:
    return XhsAdapter(sidecar or FakeSidecar())


def test_matches_urls_short_links_and_explicit_forms():
    adapter = make_adapter()

    assert adapter.matches(f"https://www.xiaohongshu.com/explore/{NOTE_ID}") is True
    assert adapter.matches("https://xhslink.com/a/AbCdEf") is True
    assert adapter.matches(f"xhs:collect:{USER_ID}") is True
    assert adapter.matches("https://www.bilibili.com/video/BV1") is False


def test_resolve_maps_note_user_and_collect_targets():
    adapter = make_adapter()

    note = adapter.resolve(f"https://www.xiaohongshu.com/explore/{NOTE_ID}")
    user = adapter.resolve(f"https://www.xiaohongshu.com/user/profile/{USER_ID}")
    collect = adapter.resolve(f"xhs:collect:{USER_ID}")

    assert note.target_type == "video" and note.creator_id == NOTE_ID
    assert user.target_type == "creator" and user.creator_id == USER_ID
    assert collect.target_type == "collection"
    assert collect.creator_id == f"{USER_ID}-collect"


def test_creator_keeps_kind_and_value_for_enumeration():
    adapter = make_adapter()
    resolved = adapter.resolve(f"xhs:collect:{USER_ID}")

    creator = adapter.get_creator(resolved)

    assert creator.raw_metadata == {"kind": "collect", "value": USER_ID}
    assert "收藏" in creator.display_name


def test_single_note_enumeration_returns_one_item():
    adapter = make_adapter()
    creator = adapter.get_creator(adapter.resolve(f"xhs:note:{NOTE_ID}"))

    pages = list(adapter.iter_items(creator, checkpoint=None))

    assert len(pages) == 1
    assert pages[0].items[0].source_id == f"xiaohongshu_{NOTE_ID}"
    assert pages[0].checkpoint.complete is True


def test_user_feed_paginates_with_cursors():
    sidecar = FakeSidecar(
        pages={
            ("user", ""): {"items": [note_payload("a" * 24)], "cursor": "next", "has_more": True},
            ("user", "next"): {"items": [note_payload("b" * 24)], "has_more": False},
        }
    )
    adapter = make_adapter(sidecar)
    creator = adapter.get_creator(adapter.resolve(f"xhs:user:{USER_ID}"))

    pages = list(adapter.iter_items(creator, checkpoint=None))

    assert [len(page.items) for page in pages] == [1, 1]
    assert pages[0].has_more is True
    assert pages[1].checkpoint.complete is True
    assert sidecar.calls[0][0] == "user_notes"


def test_collect_target_uses_the_collect_endpoint():
    sidecar = FakeSidecar(
        pages={("collect", ""): {"items": [note_payload("c" * 24)], "has_more": False}}
    )
    adapter = make_adapter(sidecar)
    creator = adapter.get_creator(adapter.resolve(f"xhs:collect:{USER_ID}"))

    list(adapter.iter_items(creator, checkpoint=None))

    assert sidecar.calls[0][0] == "collect"


def test_mapping_distinguishes_video_and_gallery_notes():
    adapter = make_adapter()

    video = adapter.map_note(note_payload("v" * 24, video=True), "creator")
    gallery = adapter.map_note(note_payload("g" * 24, video=False), "creator")

    assert video.item_type is ItemType.VIDEO
    assert video.assets[0].kind == "video"
    assert gallery.item_type is ItemType.GALLERY
    assert gallery.assets[0].kind == "image"


def test_download_writes_video_and_images(tmp_path):
    class Response:
        def __init__(self, payload: bytes):
            self.payload = payload

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            yield self.payload

    adapter = XhsAdapter(FakeSidecar(), http_get=lambda url, **kwargs: Response(b"x" * 10))
    item = adapter.map_note(note_payload("d" * 24, video=True), "creator")
    events = []

    assets = adapter.download_assets(item, tmp_path, progress=events.append)

    assert assets.video_path is not None and assets.video_path.exists()
    assert assets.video_path.stat().st_size == 10
    assert events[-1].completed_bytes == 10


def test_missing_sidecar_reports_a_clear_error():
    adapter = XhsAdapter(None)
    creator = adapter.get_creator(adapter.resolve(f"xhs:user:{USER_ID}"))

    with pytest.raises(Exception) as error:
        list(adapter.iter_items(creator, checkpoint=None))
    assert "sidecar" in str(error.value)


def test_checkpoint_skips_known_notes():
    sidecar = FakeSidecar(
        pages={("user", ""): {"items": [note_payload("e" * 24)], "has_more": False}}
    )
    adapter = make_adapter(sidecar)
    creator = adapter.get_creator(adapter.resolve(f"xhs:user:{USER_ID}"))

    pages = list(
        adapter.iter_items(
            creator,
            checkpoint=EnumerationCheckpoint(seen_ids=frozenset({"e" * 24})),
        )
    )

    assert pages[0].items == ()


class _SessionTransport:
    def __init__(self, authenticated, *, fail=False):
        self.authenticated = authenticated
        self.fail = fail

    def __call__(self, url, _params):
        if self.fail:
            raise ConnectionError("down")
        if url.endswith("/xhs/session"):
            return {"authenticated": self.authenticated}
        return {}


def test_sidecar_reports_authentication_state():
    from src.platforms.xhs.sidecar import XhsSidecar

    logged_in = XhsSidecar("http://127.0.0.1:8001", http_get=_SessionTransport(True))
    logged_out = XhsSidecar("http://127.0.0.1:8001", http_get=_SessionTransport(False))

    assert logged_in.is_authenticated() is True
    assert logged_out.is_authenticated() is False


def test_adapter_status_distinguishes_connected_from_logged_in():
    from src.platforms.xhs.adapter import XhsAdapter

    class _Sidecar:
        def __init__(self, authenticated):
            self.authenticated = authenticated

        def is_available(self):
            return True

        def is_authenticated(self):
            return self.authenticated

    logged_out = XhsAdapter(_Sidecar(False)).auth_status()
    logged_in = XhsAdapter(_Sidecar(True)).auth_status()

    assert logged_out.status == "missing"
    assert "xhs_login" in logged_out.message
    assert logged_in.status == "configured"
