import pytest

from src.platforms.douyin.adapter import DouyinAdapter
from src.platforms.douyin.resolver import DouyinResolver, extract_sec_uid
from src.platforms.errors import TargetResolutionError


class FakeResponse:
    def __init__(self, url, payload):
        self.url = url
        self._payload = payload

    def json(self):
        return self._payload


class FakePage:
    def __init__(self, final_url, response=None):
        self.url = "about:blank"
        self.final_url = final_url
        self.response = response
        self.handlers = {}

    def on(self, event, handler):
        self.handlers[event] = handler

    def goto(self, url, **kwargs):
        self.url = self.final_url
        if self.response:
            self.handlers["response"](self.response)


def test_extract_sec_uid_only_accepts_creator_urls():
    assert extract_sec_uid("https://www.douyin.com/user/MS4wLjAB_x-1?foo=1") == "MS4wLjAB_x-1"
    assert extract_sec_uid("https://www.douyin.com/video/123") is None


def test_share_url_resolves_final_url_and_sec_uid():
    page = FakePage("https://www.douyin.com/user/MS4wLjAB_x-1?from=share")

    result = DouyinResolver(page).resolve_share_url("https://v.douyin.com/abc123/")

    assert result.platform == "douyin"
    assert result.creator_id == "MS4wLjAB_x-1"
    assert result.canonical_url == "https://www.douyin.com/user/MS4wLjAB_x-1"
    assert result.original_target == "https://v.douyin.com/abc123/"


def test_resolver_can_capture_sec_uid_from_api_response():
    response = FakeResponse(
        "https://www.douyin.com/aweme/v1/web/aweme/detail/",
        {"aweme_detail": {"author": {"sec_uid": "MS4wFromApi"}}},
    )
    page = FakePage("https://www.douyin.com/video/123", response=response)

    result = DouyinResolver(page).resolve_share_url("3.14 复制打开 https://v.douyin.com/abc/")

    assert result.creator_id == "MS4wFromApi"


def test_unresolvable_share_url_is_actionable():
    page = FakePage("https://www.douyin.com/video/123")

    with pytest.raises(TargetResolutionError, match="sec_uid"):
        DouyinResolver(page).resolve_share_url("https://v.douyin.com/abc/")


def test_adapter_matches_share_text_and_direct_creator_url(tmp_path):
    adapter = DouyinAdapter(session=object())

    assert adapter.matches("https://www.douyin.com/user/MS4w")
    assert adapter.matches("复制打开 https://v.douyin.com/abc/")
    assert not adapter.matches("https://www.bilibili.com/video/BV1")


def test_extract_collection_target_supports_three_shapes():
    from src.platforms.douyin.resolver import extract_collection_target

    assert extract_collection_target("https://www.douyin.com/collection/7123") == (
        "7123-collection",
        "https://www.douyin.com/collection/7123",
    )
    assert extract_collection_target("douyin:collection:8899") == (
        "8899-collection",
        "https://www.douyin.com/collection/8899",
    )
    assert extract_collection_target(
        "https://www.douyin.com/user/MS4wLjAB_x?showTab=collect"
    ) == ("MS4wLjAB_x-collect", "https://www.douyin.com/user/MS4wLjAB_x?showTab=collect")
    assert extract_collection_target("https://www.douyin.com/user/MS4wLjAB_x") is None


def test_adapter_resolves_collection_without_opening_a_browser():
    adapter = DouyinAdapter(None)

    resolved = adapter.resolve("https://www.douyin.com/collection/7123")
    creator = adapter.get_creator(resolved)

    assert resolved.platform == "douyin"
    assert resolved.target_type == "collection"
    assert resolved.creator_id == "7123-collection"
    assert creator.display_name == "抖音合集 7123"
    assert adapter.matches("https://www.douyin.com/collection/7123") is True


def test_collect_tab_resolves_to_my_favourites_target():
    adapter = DouyinAdapter(None)

    resolved = adapter.resolve("https://www.douyin.com/user/self?showTab=collect")

    assert resolved.target_type == "collection"
    assert resolved.creator_id == "self-collect"
    assert adapter.get_creator(resolved).display_name == "我的收藏"
