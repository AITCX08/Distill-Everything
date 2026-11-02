"""内容库后端：能力矩阵与条目映射。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.application.library import LibraryItem, LibraryService


class FakeSidecar:
    def __init__(self, *, available=True, authenticated=True, payload=None):
        self._available = available
        self._authenticated = authenticated
        self.payload = payload or {"aweme_list": [], "has_more": 0, "max_cursor": "0"}
        self.calls: list[tuple] = []
        self.user_id = "xhs-user"

    def is_available(self):
        return self._available

    def is_authenticated(self):
        return self._authenticated

    def user_posts(self, sec_uid, *, cursor=0):
        self.calls.append(("posts", sec_uid, cursor))
        return self.payload

    def user_likes(self, sec_uid, *, cursor=0):
        self.calls.append(("likes", sec_uid, cursor))
        return self.payload

    def user_collection(self, cookie, *, cursor=0):
        self.calls.append(("collect", cookie, cursor))
        return self.payload

    def user_notes(self, user_id, *, cursor=""):
        self.calls.append(("notes", user_id, cursor))
        return {"items": [{"note_id": "n1", "title": "笔记", "url": "https://x/n1"}], "has_more": False}

    def user_collect(self, user_id, *, cursor=""):
        self.calls.append(("xhs-collect", user_id, cursor))
        return {"items": [], "has_more": False}


def make_service(*, douyin_sidecar=None, account="MS4wLjABxxx", xhs_sidecar=None):
    douyin = SimpleNamespace(
        sidecar=douyin_sidecar,
        sidecar_dir=None,
        account=SimpleNamespace(sec_uid=account) if account else None,
    )
    xhs = SimpleNamespace(sidecar=xhs_sidecar)
    manager = SimpleNamespace(
        get=lambda name: {"douyin": douyin, "xiaohongshu": xhs}.get(name)
    )
    return LibraryService(SimpleNamespace(platform_manager=manager))


def test_douyin_entries_require_sidecar_account_and_cookie(monkeypatch):
    from src.application import library as module

    monkeypatch.setattr(module, "read_sidecar_cookie", lambda _dir=None: "sessionid=abc")
    service = make_service(douyin_sidecar=FakeSidecar())

    entries = {entry.key: entry for entry in service.entries("douyin")}

    assert entries["posts"].available is True
    assert entries["likes"].available is True
    assert entries["collect"].available is True


def test_douyin_entries_explain_missing_pieces(monkeypatch):
    from src.application import library as module

    monkeypatch.setattr(module, "read_sidecar_cookie", lambda _dir=None: "")
    service = make_service(douyin_sidecar=FakeSidecar(), account=None)

    entries = {entry.key: entry for entry in service.entries("douyin")}

    assert entries["posts"].available is False
    assert "sec_uid" in entries["posts"].reason
    assert entries["collect"].available is False
    assert "cookie" in entries["collect"].reason


def test_douyin_items_map_aweme_payloads(monkeypatch):
    from src.application import library as module

    monkeypatch.setattr(module, "read_sidecar_cookie", lambda _dir=None: "sessionid=abc")
    aweme = {
        "aweme_id": "1001",
        "desc": "一条视频",
        "create_time": 1_700_000_000,
        "author": {"sec_uid": "SEC", "nickname": "作者"},
        "statistics": {"play_count": 1, "digg_count": 1, "comment_count": 0},
        "video": {"duration": 12_500, "play_addr": {"url_list": ["https://cdn/1.mp4"], "data_size": 1}},
    }
    sidecar = FakeSidecar(payload={"aweme_list": [aweme], "has_more": 1, "max_cursor": "40"})
    service = make_service(douyin_sidecar=sidecar)

    result = service.items("douyin", "likes", cursor="0")

    assert sidecar.calls[0] == ("likes", "MS4wLjABxxx", 0)
    assert result["items"][0]["item_id"] == "1001"
    assert result["items"][0]["title"] == "一条视频"
    assert result["has_more"] is True


def test_douyin_collect_requires_cookie(monkeypatch):
    from src.application import library as module

    monkeypatch.setattr(module, "read_sidecar_cookie", lambda _dir=None: "")
    service = make_service(douyin_sidecar=FakeSidecar())

    with pytest.raises(RuntimeError):
        service.items("douyin", "collect")


def test_xhs_entries_follow_sidecar_auth():
    available = make_service(xhs_sidecar=FakeSidecar(authenticated=True))
    logged_out = make_service(xhs_sidecar=FakeSidecar(authenticated=False))

    assert all(entry.available for entry in available.entries("xiaohongshu"))
    assert not any(entry.available for entry in logged_out.entries("xiaohongshu"))


def test_xhs_items_use_the_sidecar_user_id():
    sidecar = FakeSidecar()
    service = make_service(xhs_sidecar=sidecar)

    result = service.items("xiaohongshu", "posts")

    assert sidecar.calls[0] == ("notes", "xhs-user", "")
    assert result["items"][0]["item_id"] == "n1"


def test_bilibili_entries_flag_unavailable_likes(monkeypatch):
    service = make_service()
    monkeypatch.setattr(service, "_bilibili_uid", lambda: "42")

    entries = {entry.key: entry for entry in service.entries("bilibili")}

    assert entries["favorites"].available is True
    assert entries["likes"].available is False
    assert "点赞" in entries["likes"].reason


def test_library_item_serializes():
    item = LibraryItem(item_id="1", title="t", url="u")

    assert item.to_dict() == {
        "item_id": "1",
        "title": "t",
        "url": "u",
        "cover": None,
        "duration": None,
        "published_at": None,
    }
