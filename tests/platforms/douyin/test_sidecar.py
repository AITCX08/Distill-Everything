"""Douyin sidecar client and adapter routing (no network, no browser)."""

from __future__ import annotations

import pytest

from src.platforms.douyin.adapter import DouyinAdapter
from src.platforms.douyin.enumerator import DouyinEnumerator
from src.platforms.douyin.sidecar import DEFAULT_PATHS, DouyinSidecar
from src.platforms.errors import PlatformEnumerationError
from src.platforms.models import SourceCreator


def make_aweme(aweme_id: str, *, desc: str = "作品"):
    return {
        "aweme_id": aweme_id,
        "media_type": 4,
        "desc": desc,
        "create_time": 1_700_000_000,
        "author": {"sec_uid": "SEC_UID", "nickname": "Creator"},
        "statistics": {"play_count": 1, "digg_count": 1, "comment_count": 0},
        "video": {
            "duration": 12_500,
            "play_addr": {
                "url_list": [f"https://cdn.example/{aweme_id}.mp4"],
                "data_size": 1234,
            },
        },
    }


class FakeTransport:
    def __init__(self, payloads):
        self.payloads = payloads
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url, params):
        self.calls.append((url, dict(params)))
        for suffix, payload in self.payloads.items():
            if url.endswith(suffix):
                return payload
        raise AssertionError(f"unexpected url: {url}")


def test_sidecar_unwraps_the_code_data_envelope():
    transport = FakeTransport(
        {"/fetch_one_video": {"code": 200, "data": {"aweme_id": "1", "desc": "x"}}}
    )
    sidecar = DouyinSidecar("http://127.0.0.1:8000", http_get=transport)

    payload = sidecar.one_video("1")

    assert payload["aweme_id"] == "1"
    assert transport.calls[0][1] == {"aweme_id": "1"}


def test_sidecar_reports_error_codes_as_tool_errors():
    transport = FakeTransport(
        {"/fetch_one_video": {"code": 400, "message": "参数错误"}}
    )
    sidecar = DouyinSidecar("http://127.0.0.1:8000", http_get=transport)

    with pytest.raises(PlatformEnumerationError) as error:
        sidecar.one_video("1")
    assert "400" in str(error.value)


def test_sidecar_availability_is_false_when_the_service_is_down():
    def boom(_url, _params):
        raise ConnectionError("refused")

    sidecar = DouyinSidecar("http://127.0.0.1:8000", http_get=boom)

    assert sidecar.is_available() is False


def test_sidecar_user_posts_sends_cursor_and_count():
    transport = FakeTransport({"/fetch_user_post_videos": {"code": 200, "data": {}}})
    sidecar = DouyinSidecar("http://127.0.0.1:8000", http_get=transport)

    sidecar.user_posts("SEC", cursor=40, count=20)

    url, params = transport.calls[0]
    assert url.endswith(DEFAULT_PATHS["user_post"])
    assert params == {"sec_user_id": "SEC", "max_cursor": 40, "count": 20}


class FakeSidecar:
    def __init__(self, pages):
        self.pages = pages
        self.calls: list[tuple[str, object, int]] = []
        self.available = True

    def is_available(self):
        return self.available

    def user_posts(self, sec_user_id, *, cursor=0, count=20):
        self.calls.append(("posts", sec_user_id, cursor))
        return self.pages.get(int(cursor), {"aweme_list": [], "has_more": 0})

    def user_collection(self, sec_user_id, *, cursor=0, count=20):
        self.calls.append(("collection", sec_user_id, cursor))
        return self.pages.get(int(cursor), {"aweme_list": [], "has_more": 0})

    def user_mix(self, mix_id, *, cursor=0, count=20):
        self.calls.append(("mix", mix_id, cursor))
        return self.pages.get(int(cursor), {"aweme_list": [], "has_more": 0})


def make_creator(canonical_url: str, creator_id: str) -> SourceCreator:
    return SourceCreator(
        platform="douyin",
        creator_id=creator_id,
        display_name="Creator",
        canonical_url=canonical_url,
    )


def make_pages():
    return {
        0: {
            "aweme_list": [make_aweme("1001", desc="第一集")],
            "max_cursor": 40,
            "has_more": 1,
        },
        40: {
            "aweme_list": [make_aweme("1002", desc="第二集")],
            "max_cursor": 80,
            "has_more": 0,
        },
    }


def test_creator_targets_use_the_post_endpoint_and_map_items():
    sidecar = FakeSidecar(make_pages())
    adapter = DouyinAdapter(None, sidecar=sidecar)
    creator = make_creator("https://www.douyin.com/user/SEC_UID", "SEC_UID")

    pages = list(adapter.iter_items(creator, checkpoint=None))

    assert sidecar.calls[0] == ("posts", "SEC_UID", 0)
    assert [item.item_id for page in pages for item in page.items] == ["1001", "1002"]
    assert pages[0].items[0].title == "第一集"
    assert pages[-1].has_more is False


def test_collection_links_use_the_mix_endpoint():
    sidecar = FakeSidecar({0: {"aweme_list": [make_aweme("2001")], "has_more": 0}})
    adapter = DouyinAdapter(None, sidecar=sidecar)
    creator = make_creator("https://www.douyin.com/collection/7123", "7123-collection")

    list(adapter.iter_items(creator, checkpoint=None))

    assert sidecar.calls[0] == ("mix", "7123", 0)


def test_collect_tab_uses_the_user_collection_endpoint():
    sidecar = FakeSidecar({0: {"aweme_list": [make_aweme("3001")], "has_more": 0}})
    adapter = DouyinAdapter(None, sidecar=sidecar)
    creator = make_creator(
        "https://www.douyin.com/user/MS4wLjAB_x?showTab=collect", "MS4wLjAB_x-collect"
    )

    list(adapter.iter_items(creator, checkpoint=None))

    assert sidecar.calls[0] == ("collection", "MS4wLjAB_x", 0)


def test_self_collect_falls_back_to_the_browser_route():
    """「我的收藏」只有浏览器里的登录态知道账号是谁，sidecar 拿不到。"""

    class _BrowserStub:
        def iter_pages(self, creator, checkpoint):
            del creator, checkpoint
            yield "browser-page"

    sidecar = FakeSidecar({})
    adapter = DouyinAdapter(None, sidecar=sidecar, enumerator=_BrowserStub())
    creator = make_creator("https://www.douyin.com/user/self?showTab=collect", "self-collect")

    assert list(adapter.iter_items(creator, checkpoint=None)) == ["browser-page"]
    assert sidecar.calls == []


def test_unavailable_sidecar_falls_back_to_the_browser_route():
    class _BrowserStub:
        def iter_pages(self, creator, checkpoint):
            del creator, checkpoint
            yield "browser-page"

    sidecar = FakeSidecar({})
    sidecar.available = False
    adapter = DouyinAdapter(None, sidecar=sidecar, enumerator=_BrowserStub())
    creator = make_creator("https://www.douyin.com/user/SEC_UID", "SEC_UID")

    assert list(adapter.iter_items(creator, checkpoint=None)) == ["browser-page"]


def test_sidecar_enumerator_is_the_shared_paging_engine():
    sidecar = FakeSidecar(make_pages())

    def requester(_creator, cursor):
        return sidecar.user_posts("SEC_UID", cursor=int(cursor))

    enumerator = DouyinEnumerator(requester)
    creator = make_creator("https://www.douyin.com/user/SEC_UID", "SEC_UID")

    pages = list(enumerator.iter_pages(creator, None))

    assert [len(page.items) for page in pages] == [1, 1]
    assert pages[-1].checkpoint.complete is True
