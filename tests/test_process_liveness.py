"""Process liveness must never signal the process it is probing."""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from src.application.process import process_is_alive


def test_rejects_non_positive_pids():
    assert process_is_alive(0) is False
    assert process_is_alive(-1) is False


def test_detects_the_current_process():
    assert process_is_alive(os.getpid()) is True


def test_reports_a_dead_process_as_dead():
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait(timeout=30)
    # Give the OS a moment to reap the exit status on slow CI runners.
    for _ in range(50):
        if not process_is_alive(process.pid):
            break
        time.sleep(0.02)
    assert process_is_alive(process.pid) is False


@pytest.mark.skipif(os.name != "nt", reason="Windows-specific CTRL_C hazard")
def test_windows_probe_does_not_call_os_kill(monkeypatch):
    """``os.kill(pid, 0)`` on Windows sends CTRL_C to the whole process group."""

    def explode(*_args, **_kwargs):  # pragma: no cover - must never run
        raise AssertionError("os.kill must not be used as a Windows liveness probe")

    monkeypatch.setattr(os, "kill", explode)

    assert process_is_alive(os.getpid()) is True
    assert process_is_alive(4_000_000) is False
