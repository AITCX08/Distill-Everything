"""Safe local controls for registered checkpointed series runners."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from src.application.process import process_is_alive
from src.series.runtime import SeriesRuntimeStore


class SeriesController:
    def __init__(
        self,
        data_dir: Path,
        *,
        launcher: Callable[[str], int | None],
        worker_is_alive: Callable[[int], bool] | None = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.launcher = launcher
        self.worker_is_alive = worker_is_alive or _worker_is_alive

    def _store(self, bvid: str) -> SeriesRuntimeStore:
        state_path = self.data_dir / "series" / bvid / "state.json"
        if not state_path.is_file():
            raise LookupError("series was not registered locally")
        return SeriesRuntimeStore(state_path.parent)

    def pause(self, bvid: str) -> dict[str, Any]:
        runtime = self._store(bvid)
        current = runtime.load()
        if current.get("status") in {"paused", "pause_requested"}:
            return current
        return runtime.update(status="pause_requested")

    def resume(self, bvid: str) -> dict[str, Any]:
        runtime = self._store(bvid)
        current = runtime.load()
        if current.get("status") == "running" and self._has_live_worker(current):
            return current
        worker_pid = self.launcher(bvid)
        return runtime.update(
            status="running",
            transfer={},
            worker_pid=worker_pid if isinstance(worker_pid, int) and worker_pid > 0 else None,
            last_error=None,
        )

    def reconcile(self) -> int:
        changed = 0
        root = self.data_dir / "series"
        if not root.is_dir():
            return changed
        for state_path in root.glob("*/state.json"):
            runtime = SeriesRuntimeStore(state_path.parent)
            current = runtime.load()
            if current.get("status") == "running" and not self._has_live_worker(current):
                runtime.update(
                    status="paused",
                    transfer={},
                    worker_pid=None,
                    last_error="执行器已停止，可继续任务。",
                )
                changed += 1
        return changed

    def _has_live_worker(self, runtime: dict[str, Any]) -> bool:
        worker_pid = runtime.get("worker_pid")
        return isinstance(worker_pid, int) and worker_pid > 0 and self.worker_is_alive(worker_pid)


def _worker_is_alive(worker_pid: int) -> bool:
    try:
        return process_is_alive(worker_pid)
    except (OSError, SystemError):
        # A failing probe must degrade to "not running", never crash the controller.
        return False
