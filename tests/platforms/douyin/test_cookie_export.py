"""Cookie bridging from the browser profile into the sidecar config."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from src.platforms.douyin.cookie_export import (
    cookies_to_header,
    read_browser_cookie,
    sync,
    write_cookie,
)


def test_cookies_to_header_skips_empty_and_duplicates():
    header = cookies_to_header(
        [
            {"name": "sessionid", "value": "abc"},
            {"name": "ttwid", "value": "xyz"},
            {"name": "sessionid", "value": "duplicate"},
            {"name": "", "value": "noise"},
            {"name": "empty", "value": ""},
        ]
    )

    assert header == "sessionid=abc; ttwid=xyz; empty="


def test_write_cookie_only_touches_the_cookie_line(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        "headers:\n"
        "  # 不要修改 User-Agent\n"
        "  User-Agent: Mozilla/5.0\n"
        "  Referer: https://www.douyin.com/\n"
        "  # 你唯一需要修改的地方就是这里的 Cookie\n"
        "  Cookie: __ac_nonce=old\n"
        "other:\n"
        "  keep: me\n",
        encoding="utf-8",
    )

    write_cookie(config, "sessionid=abc; ttwid=xyz")

    text = config.read_text(encoding="utf-8")
    assert "Cookie: sessionid=abc; ttwid=xyz" in text
    assert "__ac_nonce=old" not in text
    assert "User-Agent: Mozilla/5.0" in text
    assert "# 不要修改 User-Agent" in text
    assert "  keep: me" in text


def test_write_cookie_reports_a_missing_line(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("headers:\n  User-Agent: x\n", encoding="utf-8")

    with pytest.raises(ValueError):
        write_cookie(config, "sessionid=abc")


class FakeContext:
    def __init__(self, cookies):
        self._cookies = cookies

    def cookies(self, _urls):
        return self._cookies


class FakeSession:
    def __init__(self, cookies):
        self._cookies = cookies

    @contextmanager
    def open_context(self, *, headless, task):
        del headless, task
        yield FakeContext(self._cookies)


def test_read_browser_cookie_requires_a_session():
    session = FakeSession([{"name": "ttwid", "value": "x"}])

    with pytest.raises(RuntimeError) as error:
        read_browser_cookie(session)
    assert "source login douyin" in str(error.value)


def test_read_browser_cookie_returns_the_header():
    session = FakeSession(
        [
            {"name": "sessionid", "value": "abc"},
            {"name": "sid_guard", "value": "def"},
        ]
    )

    assert read_browser_cookie(session) == "sessionid=abc; sid_guard=def"


def test_sync_writes_the_sidecar_config(tmp_path):
    sidecar = tmp_path / "sidecar"
    target = sidecar / "crawlers" / "douyin" / "web" / "config.yaml"
    target.parent.mkdir(parents=True)
    target.write_text("headers:\n  Cookie: placeholder\n", encoding="utf-8")
    session = FakeSession([{"name": "sessionid", "value": "abc"}])

    path, count = sync(session, sidecar)

    assert path == target
    assert count == 1
    assert "Cookie: sessionid=abc" in target.read_text(encoding="utf-8")


def test_sync_reports_a_missing_sidecar(tmp_path):
    session = FakeSession([{"name": "sessionid", "value": "abc"}])

    with pytest.raises(FileNotFoundError):
        sync(session, tmp_path / "absent")
