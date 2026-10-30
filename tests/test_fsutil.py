"""Atomic replacement must survive Windows sharing violations."""

from __future__ import annotations

import os

import pytest

from src.fsutil import replace_with_retry, unlink_with_retry


def test_retries_transient_permission_errors(tmp_path, monkeypatch):
    source = tmp_path / "source.tmp"
    destination = tmp_path / "destination.json"
    source.write_text("payload", encoding="utf-8")
    calls = {"count": 0}
    real_replace = os.replace

    def flaky_replace(src, dst):
        calls["count"] += 1
        if calls["count"] < 3:
            raise PermissionError("file is held open by another handle")
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", flaky_replace)

    replace_with_retry(source, destination, attempts=5, delay=0)

    assert calls["count"] == 3
    assert destination.read_text("utf-8") == "payload"
    assert not source.exists()


def test_gives_up_after_the_attempt_limit(tmp_path, monkeypatch):
    source = tmp_path / "source.tmp"
    destination = tmp_path / "destination.json"
    source.write_text("payload", encoding="utf-8")

    def always_busy(_src, _dst):
        raise PermissionError("still held open")

    monkeypatch.setattr(os, "replace", always_busy)

    with pytest.raises(PermissionError):
        replace_with_retry(source, destination, attempts=3, delay=0)

def test_unlink_retries_sharing_violations(tmp_path):
    target = tmp_path / "resource-request.json"
    target.write_text("{}", encoding="utf-8")
    calls = {"count": 0}
    real_unlink = type(target).unlink

    def flaky_unlink(self, *args, **kwargs):
        calls["count"] += 1
        if calls["count"] < 3:
            raise PermissionError("[WinError 32] file is held open")
        return real_unlink(self, *args, **kwargs)

    monkeypatch_target = target
    import pathlib

    original = pathlib.Path.unlink
    pathlib.Path.unlink = flaky_unlink
    try:
        unlink_with_retry(monkeypatch_target, attempts=5, delay=0)
    finally:
        pathlib.Path.unlink = original

    assert calls["count"] == 3
    assert not target.exists()


def test_unlink_is_a_no_op_for_missing_files(tmp_path):
    unlink_with_retry(tmp_path / "missing.json", attempts=2, delay=0)
