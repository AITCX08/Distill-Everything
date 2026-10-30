"""In-page Douyin QR login coordinator: no visible browser, same contracts as Bilibili."""

from __future__ import annotations

import time
from contextlib import contextmanager

from src.dashboard.douyin_login import DouyinLoginCoordinator


class FakePage:
    def __init__(self, urls=None):
        self.urls = urls if urls is not None else []
        self.waits = 0

    def goto(self, url, **kwargs):
        self.urls.append(url)

    def wait_for_timeout(self, _ms):
        self.waits += 1


class FakeSession:
    def __init__(self, *, authenticated_after=0, qr=b"PNG"):
        self.authenticated_after = authenticated_after
        self.qr = qr
        self.checks = 0
        self.marked = 0
        self.headless_flags: list[bool] = []
        self.page = FakePage()

    @contextmanager
    def open_context(self, *, headless: bool, task: str):
        self.headless_flags.append(headless)
        yield object()

    def first_page(self, _context):
        return self.page

    def is_authenticated(self, _context, _page):
        self.checks += 1
        return self.checks > self.authenticated_after

    def mark_authenticated(self):
        self.marked += 1


def _wait_until(coordinator, operation_id, predicate, *, timeout=3.0):
    deadline = time.monotonic() + timeout
    snapshot = coordinator.get(operation_id)
    while time.monotonic() < deadline:
        snapshot = coordinator.get(operation_id)
        if predicate(snapshot):
            return snapshot
        time.sleep(0.01)
    return snapshot


def test_login_runs_headless_and_serves_the_qr_png():
    session = FakeSession(authenticated_after=10**6)
    coordinator = DouyinLoginCoordinator(
        session,
        qr_capture=lambda _page: b"PNG-BYTES",
        poll_seconds=0.01,
        timeout_seconds=5,
    )

    started = coordinator.start()

    snapshot = _wait_until(coordinator, started.operation_id, lambda item: item.status == "waiting_for_scan")
    assert snapshot.status == "waiting_for_scan"
    assert coordinator.qr_png(started.operation_id) == b"PNG-BYTES"
    assert session.headless_flags == [True]
    assert session.page.urls == ["https://www.douyin.com/"]
    coordinator._update(started.operation_id, status="cancelled", message="test cleanup")


def test_login_marks_the_profile_authenticated_after_scan():
    session = FakeSession(authenticated_after=0)
    coordinator = DouyinLoginCoordinator(
        session,
        qr_capture=lambda _page: b"PNG-BYTES",
        poll_seconds=0.01,
        timeout_seconds=5,
    )

    started = coordinator.start()
    snapshot = _wait_until(coordinator, started.operation_id, lambda item: item.status == "succeeded")

    assert snapshot.status == "succeeded"
    assert session.marked == 1


def test_missing_qr_falls_back_to_the_cli_flow_without_crashing():
    session = FakeSession()
    coordinator = DouyinLoginCoordinator(
        session,
        qr_capture=lambda _page: None,
        poll_seconds=0.01,
        timeout_seconds=5,
    )

    started = coordinator.start()
    snapshot = _wait_until(coordinator, started.operation_id, lambda item: item.status == "failed")

    assert snapshot.status == "failed"
    assert "source login douyin" in snapshot.message
