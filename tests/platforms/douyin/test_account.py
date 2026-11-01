"""抖音账号 sec_uid 的解析与持久化。"""

from __future__ import annotations

import json

import pytest

from src.platforms.douyin.account import (
    ACCOUNT_FILE,
    AccountStore,
    capture,
    resolve_sec_uid,
)

SEC_UID = "MS4wLjABAAAA_example_sec_uid_value"


class FakePage:
    def __init__(self, *, url="", storage=None, goto_url=""):
        self.url = url
        self._storage = storage
        self._goto_url = goto_url
        self.visited: list[str] = []

    def goto(self, url, **_kwargs):
        self.visited.append(url)
        if self._goto_url:
            self.url = self._goto_url

    def wait_for_timeout(self, _ms):
        return None

    def evaluate(self, _script):
        return self._storage


def test_store_roundtrip(tmp_path):
    store = AccountStore(tmp_path)

    saved = store.save(SEC_UID)

    assert saved.sec_uid == SEC_UID
    assert (tmp_path / ACCOUNT_FILE).exists()
    loaded = AccountStore(tmp_path).load()
    assert loaded is not None and loaded.sec_uid == SEC_UID
    assert json.loads((tmp_path / ACCOUNT_FILE).read_text("utf-8"))["captured_at"]


def test_store_rejects_invalid_ids(tmp_path):
    with pytest.raises(ValueError):
        AccountStore(tmp_path).save("not-a-sec-uid")


def test_store_ignores_corrupt_files(tmp_path):
    (tmp_path / ACCOUNT_FILE).write_text("{oops", encoding="utf-8")

    assert AccountStore(tmp_path).load() is None


def test_resolve_uses_the_redirected_url():
    page = FakePage(goto_url=f"https://www.douyin.com/user/{SEC_UID}?from_tab_name=main")

    assert resolve_sec_uid(page) == SEC_UID
    assert page.visited == ["https://www.douyin.com/user/self"]


def test_resolve_falls_back_to_local_storage():
    page = FakePage(url="https://www.douyin.com/jingxuan", storage=SEC_UID)

    assert resolve_sec_uid(page) == SEC_UID


def test_resolve_returns_none_when_nothing_is_available():
    page = FakePage(url="https://www.douyin.com/jingxuan", storage="nothing")

    assert resolve_sec_uid(page) is None


def test_capture_persists_the_resolved_account(tmp_path):
    page = FakePage(goto_url=f"https://www.douyin.com/user/{SEC_UID}")

    account = capture(tmp_path, page)

    assert account is not None and account.sec_uid == SEC_UID
    assert AccountStore(tmp_path).load().sec_uid == SEC_UID
