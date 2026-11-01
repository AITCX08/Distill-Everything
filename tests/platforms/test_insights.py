"""Creator-center insights: normalizers, browser capture and the CLI gate."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

import main
from src.platforms.insights.core import (
    InsightsSnapshot,
    InsightsUnavailable,
    capture,
)
from src.platforms.insights.providers import SPECS, normalize_bilibili, normalize_douyin


def test_normalizer_reads_metrics_and_items():
    payload = {
        "data": {
            "mid": 123,
            "nickname": "作者",
            "total_play": 1000,
            "total_like": 50,
            "total_comment": 10,
            "total_share": 5,
            "fans": 800,
            "fans_inc": 3,
            "list": [{"bvid": "BV1", "title": "作品", "view": 300}],
        }
    }

    snapshot = normalize_bilibili(payload)

    assert snapshot.platform == "bilibili"
    assert snapshot.metrics == {
        "views": 1000,
        "likes": 50,
        "comments": 10,
        "shares": 5,
        "followers": 800,
        "followers_delta": 3,
    }
    assert snapshot.items[0]["item_id"] == "BV1"
    snapshot.validate()


def test_missing_metrics_stay_none_instead_of_zero():
    snapshot = normalize_douyin({"data": {"uid": "1"}})

    assert snapshot.metrics["views"] is None
    assert snapshot.metrics["followers"] is None
    snapshot.validate()


def test_snapshot_validation_rejects_unknown_platform_and_missing_keys():
    good = {
        "platform": "bilibili",
        "captured_at": "2026-01-01T00:00:00Z",
        "account": {},
        "metrics": {key: 0 for key in ("views", "likes", "comments", "shares", "followers", "followers_delta")},
    }
    InsightsSnapshot(**good).validate()

    with pytest.raises(ValueError):
        InsightsSnapshot(**{**good, "platform": "weibo"}).validate()
    with pytest.raises(ValueError):
        InsightsSnapshot(**{**good, "metrics": {"views": 1}}).validate()


class FakePage:
    def __init__(self, responses):
        self.responses = responses
        self.handlers = {}
        self.goto_url = None

    def on(self, event, handler):
        self.handlers[event] = handler

    def goto(self, url, **_kwargs):
        self.goto_url = url
        for response in self.responses:
            self.handlers["response"](response)

    def wait_for_timeout(self, _ms):
        return None


class FakeResponse:
    def __init__(self, url, payload):
        self.url = url
        self._payload = payload

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, page):
        self.page = page
        self.tasks: list[str] = []

    @contextmanager
    def open_context(self, *, headless, task):
        self.tasks.append(task)
        yield object()

    def first_page(self, _context):
        return self.page


def test_capture_uses_the_matching_xhr_and_ignores_noise():
    page = FakePage(
        [
            FakeResponse("https://member.bilibili.com/other", {"data": {"total_play": 1}}),
            FakeResponse(
                "https://member.bilibili.com/x/web/data/stat",
                {"data": {"total_play": 42, "nickname": "作者"}},
            ),
        ]
    )
    session = FakeSession(page)

    snapshot = capture(session, SPECS["bilibili"], wait_ms=0)

    assert snapshot.metrics["views"] == 42
    assert session.tasks == ["insights-bilibili"]
    assert page.goto_url == SPECS["bilibili"].page_url


def test_capture_raises_when_the_console_never_answers():
    session = FakeSession(FakePage([FakeResponse("https://example.com/ping", {})]))

    with pytest.raises(InsightsUnavailable):
        capture(session, SPECS["douyin"], wait_ms=0)


def test_cli_refuses_to_run_when_the_feature_flag_is_off(monkeypatch):
    monkeypatch.setattr(
        "src.config.load_config",
        lambda: SimpleNamespace(insights_enabled=False),
    )

    result = CliRunner().invoke(main.cli, ["insights", "--platform", "bilibili"])

    assert result.exit_code != 0
    assert "INSIGHTS_ENABLED" in result.output
