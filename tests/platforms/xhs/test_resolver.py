"""Xiaohongshu target parsing: pure string work, no network."""

from __future__ import annotations

import pytest

from src.platforms.errors import TargetResolutionError
from src.platforms.xhs.resolver import (
    expand_short_link,
    is_short_link,
    parse_xhs_target,
    resolve_target,
)

NOTE_ID = "65f0a1b2c3d4e5f60718293a"
USER_ID = "5f1a2b3c4d5e6f708192a3b4"


def test_parses_note_and_user_urls():
    note = parse_xhs_target(f"https://www.xiaohongshu.com/explore/{NOTE_ID}?xsec_token=abc")
    user = parse_xhs_target(f"https://www.xiaohongshu.com/user/profile/{USER_ID}")

    assert note is not None and note.kind == "note" and note.value == NOTE_ID
    assert user is not None and user.kind == "user" and user.value == USER_ID


def test_parses_discovery_item_links():
    target = parse_xhs_target(f"https://www.xiaohongshu.com/discovery/item/{NOTE_ID}")

    assert target is not None and target.kind == "note"


@pytest.mark.parametrize(
    "text,kind",
    [
        (f"xhs:note:{NOTE_ID}", "note"),
        (f"xhs:user:{USER_ID}", "user"),
        (f"xhs:collect:{USER_ID}", "collect"),
    ],
)
def test_parses_explicit_identifiers(text, kind):
    target = parse_xhs_target(text)

    assert target is not None and target.kind == kind


def test_rejects_other_hosts_and_bad_paths():
    assert parse_xhs_target("https://example.com/explore/abc") is None
    assert parse_xhs_target("https://www.xiaohongshu.com/search_result?keyword=x") is None
    assert parse_xhs_target("") is None


def test_short_links_are_recognised_without_network():
    assert is_short_link("https://xhslink.com/a/AbCdEf") is True
    assert is_short_link("https://www.xiaohongshu.com/explore/x") is False


def test_short_link_expansion_follows_the_redirect():
    class Response:
        url = f"https://www.xiaohongshu.com/explore/{NOTE_ID}"

    def fake_head(url, **_kwargs):
        assert url.startswith("https://xhslink.com/")
        return Response()

    target = resolve_target("https://xhslink.com/a/AbCdEf", http_head=fake_head)

    assert target.kind == "note" and target.value == NOTE_ID


def test_short_link_without_redirect_is_an_error():
    class Response:
        url = "https://xhslink.com/a/AbCdEf"

    with pytest.raises(TargetResolutionError):
        expand_short_link("https://xhslink.com/a/AbCdEf", http_head=lambda *a, **k: Response())


def test_share_text_with_embedded_url_is_accepted():
    text = f"看看这个笔记 https://www.xiaohongshu.com/explore/{NOTE_ID}?xsec_token=xyz 复制打开"

    target = resolve_target(text)

    assert target.kind == "note" and target.value == NOTE_ID


def test_empty_target_is_rejected():
    with pytest.raises(TargetResolutionError):
        resolve_target("   ")
