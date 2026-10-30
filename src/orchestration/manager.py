"""The sole owner of hidden worker subprocesses for Dashboard tasks."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Callable

from src.orchestration.models import TaskRecord
from src.orchestration.protocol import ProtocolError, parse_worker_event
from src.orchestration.resources import ResourceSlots
from src.orchestration.store import OrchestrationStore


ProcessFactory = Callable[[TaskRecord, Path], Any]
PidProbe = Callable[[int, str], bool]


class TaskManagerOwnershipError(RuntimeError):
    """Raised when another live Dashboard already owns this orchestration store."""


class TaskManager:
    """Queue work, create one hidden child process per task, and own its lease."""

    def __init__(
        self,
        *,
        store: OrchestrationStore,
        worker_root: Path,
        process_factory: ProcessFactory | None = None,
        max_pipeline_workers: int = 2,
        pid_probe: PidProbe | None = None,
        resource_slots: ResourceSlots | None = None,
    ) -> None:
        self.store = store
        self.worker_root = Path(worker_root)
        self.process_factory = process_factory or self._launch_worker
        self.max_pipeline_workers = max_pipeline_workers
        self.pid_probe = pid_probe or _default_pid_probe
        self.resource_slots = resource_slots or ResourceSlots()
        self._processes: dict[str, Any] = {}
        self._attached_leases: dict[str, tuple[int, str]] = {}
        self._event_lines_read: dict[str, int] = {}
        self._owner_id = uuid.uuid4().hex
        self._owner_pid = os.getpid()
        self._owner_start_marker = _start_marker(self._owner_pid)

    def claim_ownership(self) -> None:
        """Claim this data root before accepting Dashboard task control."""

        existing = self.store.task_manager_owner()
        if existing is None:
            if self.store.claim_task_manager(
                owner_id=self._owner_id,
                pid=self._owner_pid,
                start_marker=self._owner_start_marker,
            ):
                return
        else:
            _, pid, start_marker = existing
            if pid == self._owner_pid:
                raise TaskManagerOwnershipError(
                    "another live Dashboard already owns this data directory"
                )
            if not self.pid_probe(pid, start_marker) and self.store.claim_task_manager(
                owner_id=self._owner_id,
                pid=self._owner_pid,
                start_marker=self._owner_start_marker,
                replacing_owner_id=existing[0],
            ):
                return
        raise TaskManagerOwnershipError("another live Dashboard already owns this data directory")

    def release_ownership(self) -> None:
        self.store.release_task_manager(self._owner_id)

    def enqueue(self, job_id: str, source_id: str) -> TaskRecord:
        return self.store.create_tasks(job_id, [source_id])[0]

    def tick(self) -> None:
        for task_id, process in tuple(self._processes.items()):
            self._read_worker_events(task_id)
            if process.poll() is not None:
                self._finalize_exited_process(task_id)
        for task_id, (pid, start_marker) in tuple(self._attached_leases.items()):
            self._read_worker_events(task_id)
            if not self.pid_probe(pid, start_marker):
                self._finalize_exited_process(task_id)
        self._allocate_stage_resources()
        capacity = max(0, self.max_pipeline_workers - len(self._processes) - len(self._attached_leases))
        for task in self.store.list_tasks(status="pending")[:capacity]:
            self.start(task.task_id)
        self._allocate_stage_resources()

    def start(self, task_id: str) -> TaskRecord:
        if task_id in self._processes:
            return self.store.get_task(task_id)
        task = self.store.get_task(task_id)
        if task.status != "pending":
            raise ValueError("only pending tasks can be started")
        payload_path = self._write_payload(task)
        process = self.process_factory(task, payload_path)
        pid = int(process.pid)
        self.store.create_lease(task.task_id, pid=pid, start_marker=_start_marker(pid))
        running = self.store.transition_task(
            task.task_id,
            task.revision,
            status="running",
            stage="pending",
            increment_attempt=True,
        )
        self._processes[task.task_id] = process
        self._read_worker_events(task.task_id)
        return running

    def pause(self, task_id: str) -> None:
        task = self.store.get_task(task_id)
        self.store.get_lease(task_id)
        if task.status not in {"running", "pause_requested"}:
            raise ValueError("only a running task can be paused")
        self._write_control(task_id, "pause")
        if task.status == "running":
            self.store.transition_task(task_id, task.revision, status="pause_requested")

    def resume(self, task_id: str) -> None:
        task = self.store.get_task(task_id)
        if task.status not in {"paused", "interrupted"}:
            raise ValueError("only a paused or interrupted task can be resumed")
        self._acknowledge_existing_worker_events(task_id)
        self._restore_paused_checkpoint(task_id)
        control_path = self.worker_root / task_id / "control.json"
        control_path.unlink(missing_ok=True)
        self.store.transition_task(task_id, task.revision, status="pending")

    def cancel(self, task_id: str) -> None:
        task = self.store.get_task(task_id)
        self.store.get_lease(task_id)
        if task.status not in {"running", "pause_requested", "cancel_requested"}:
            raise ValueError("only a running task can be cancelled")
        self._write_control(task_id, "cancel")
        if task.status != "cancel_requested":
            self.store.transition_task(task_id, task.revision, status="cancel_requested")

    def retry(self, task_id: str) -> None:
        """Requeue one terminal task; its worker checkpoint decides which stage resumes."""

        task = self.store.get_task(task_id)
        if task.status not in {"failed", "interrupted", "cancelled"}:
            raise ValueError("only failed, interrupted, or cancelled tasks can be retried")
        self._acknowledge_existing_worker_events(task_id)
        self._clear_resource_files(task_id)
        (self.worker_root / task_id / "control.json").unlink(missing_ok=True)
        self.store.transition_task(task_id, task.revision, status="pending")

    def reconcile(self) -> None:
        """Mark absent lease-owned workers interrupted; never terminate by PID alone."""

        for lease in self.store.list_leases():
            self._read_worker_events(lease.task_id)
            if self.pid_probe(lease.pid, lease.start_marker):
                self._attached_leases[lease.task_id] = (lease.pid, lease.start_marker)
                continue
            task = self.store.get_task(lease.task_id)
            if task.status in {"running", "pause_requested", "cancel_requested"}:
                self.store.transition_task(task.task_id, task.revision, status="interrupted")
                self.store.append_event(
                    task.task_id,
                    kind="log",
                    payload={"line": "worker lease ended before a terminal checkpoint"},
                )
            self.store.remove_lease(lease.task_id)
            self._clear_resource_files(lease.task_id)

    def _write_payload(self, task: TaskRecord) -> Path:
        work_dir = self.worker_root / task.task_id
        work_dir.mkdir(parents=True, exist_ok=True)
        payload_path = work_dir / "payload.json"
        job = self.store.get_job(task.job_id)
        payload_path.write_text(
            json.dumps(
                {
                    "task_id": task.task_id,
                    "work_dir": str(work_dir),
                    "source": _source_descriptor(job.platform, task.source_id),
                    "resource_control": True,
                    **({"output_directory": job.output_directory} if job.output_directory else {}),
                },
                ensure_ascii=False,
            ),
            "utf-8",
        )
        return payload_path

    def _read_worker_events(self, task_id: str) -> None:
        events_path = self.worker_root / task_id / "events.jsonl"
        if not events_path.exists():
            return
        lines = events_path.read_text("utf-8").splitlines()
        start = self._event_cursor_for(task_id, lines)
        for line in lines[start:]:
            try:
                event = parse_worker_event(line, task_id)
            except ProtocolError:
                continue
            self.store.append_event(task_id, kind=event.kind, payload=event.payload)
            task = self.store.get_task(task_id)
            if event.kind == "stage" and task.status == "running":
                self.store.transition_task(
                    task_id,
                    task.revision,
                    status="running",
                    stage=str(event.payload["stage"]),
                )
            elif event.kind == "terminal" and task.status in {
                "running",
                "pause_requested",
                "cancel_requested",
            }:
                terminal_status = str(event.payload["status"])
                self.store.transition_task(
                    task_id,
                    task.revision,
                    status=terminal_status,
                    # The worker's terminal event is authoritative.  Without
                    # normalising the terminal completion stage, a task that
                    # finished after `writing` remains persisted as
                    # `completed + writing`, which makes the Dashboard claim
                    # it is still writing artifacts.
                    stage="completed" if terminal_status == "completed" else None,
                )
        self._event_lines_read[task_id] = len(lines)
        self.store.set_worker_event_cursor(task_id, len(lines))

    def _event_cursor_for(self, task_id: str, lines: list[str]) -> int:
        known = self._event_lines_read.get(task_id)
        if known is not None:
            return min(known, len(lines))
        persisted = self.store.worker_event_cursor(task_id)
        if persisted is None:
            # Existing installations predate durable cursors. Keep their
            # already-recorded JSONL lines from being replayed after a restart.
            persisted = min(len(self.store.list_events(task_id)), len(lines))
        cursor = min(persisted, len(lines))
        self._event_lines_read[task_id] = cursor
        self.store.set_worker_event_cursor(task_id, cursor)
        return cursor

    def _acknowledge_existing_worker_events(self, task_id: str) -> None:
        """Start a new attempt after, rather than inside, prior worker JSONL."""

        events_path = self.worker_root / task_id / "events.jsonl"
        try:
            line_count = len(events_path.read_text("utf-8").splitlines())
        except OSError:
            line_count = 0
        self._event_lines_read[task_id] = line_count
        self.store.set_worker_event_cursor(task_id, line_count)

    def _finalize_exited_process(self, task_id: str) -> None:
        """Release only this manager's finished lease after consuming its terminal JSONL."""

        task = self.store.get_task(task_id)
        if task.status in {"running", "pause_requested", "cancel_requested"}:
            self.store.transition_task(task_id, task.revision, status="interrupted")
            self.store.append_event(
                task_id,
                kind="log",
                payload={"line": "worker process exited before terminal checkpoint"},
            )
        self.store.remove_lease(task_id)
        self._clear_resource_files(task_id)
        self._processes.pop(task_id, None)
        self._attached_leases.pop(task_id, None)

    def _allocate_stage_resources(self) -> None:
        """Grant stage permits from durable requests; workers never self-authorize."""

        requests: list[tuple[TaskRecord, str]] = []
        for task in self.store.list_tasks():
            if task.status not in {"running", "pause_requested"}:
                continue
            request_path = self.worker_root / task.task_id / "resource-request.json"
            try:
                request = json.loads(request_path.read_text("utf-8"))
            except (OSError, UnicodeError, ValueError):
                continue
            stage = request.get("stage") if isinstance(request, dict) else None
            if isinstance(stage, str):
                requests.append((task, stage))

        self.resource_slots.clear()
        granted: set[str] = set()
        # Retain matching permits first so a later waiter cannot steal a live stage.
        for task, stage in sorted(requests, key=lambda item: item[0].task_id):
            if self._has_matching_grant(task.task_id, stage) and self.resource_slots.acquire(task.task_id, stage):
                granted.add(task.task_id)
        for task, stage in sorted(requests, key=lambda item: item[0].task_id):
            if task.task_id in granted:
                continue
            if self.resource_slots.acquire(task.task_id, stage):
                self._write_resource_grant(task.task_id, stage)
                granted.add(task.task_id)
            else:
                (self.worker_root / task.task_id / "resource-grant.json").unlink(missing_ok=True)

    def _has_matching_grant(self, task_id: str, stage: str) -> bool:
        try:
            grant = json.loads((self.worker_root / task_id / "resource-grant.json").read_text("utf-8"))
        except (OSError, UnicodeError, ValueError):
            return False
        return isinstance(grant, dict) and grant.get("stage") == stage

    def _write_resource_grant(self, task_id: str, stage: str) -> None:
        work_dir = self.worker_root / task_id
        temporary = work_dir / "resource-grant.tmp"
        destination = work_dir / "resource-grant.json"
        temporary.write_text(json.dumps({"stage": stage}), "utf-8")
        os.replace(temporary, destination)

    def _clear_resource_files(self, task_id: str) -> None:
        work_dir = self.worker_root / task_id
        (work_dir / "resource-request.json").unlink(missing_ok=True)
        (work_dir / "resource-grant.json").unlink(missing_ok=True)

    def _restore_paused_checkpoint(self, task_id: str) -> None:
        """Make a cooperative pause resumable before a fresh worker is launched."""

        checkpoint_path = self.worker_root / task_id / "checkpoint.json"
        if not checkpoint_path.exists():
            return
        try:
            checkpoint = json.loads(checkpoint_path.read_text("utf-8"))
        except (OSError, UnicodeError, ValueError) as error:
            raise RuntimeError("paused worker checkpoint is invalid") from error
        if not isinstance(checkpoint, dict) or checkpoint.get("task_id") != task_id:
            raise RuntimeError("paused worker checkpoint is invalid")
        if checkpoint.get("stage") != "paused":
            return
        resume_stage = checkpoint.get("resume_stage", "pending")
        if resume_stage not in {
            "pending",
            "downloading",
            "downloaded",
            "extracting_audio",
            "transcribing",
            "cleaning",
            "summarizing",
            "writing",
        }:
            raise RuntimeError("paused worker checkpoint has an invalid resume stage")
        checkpoint["stage"] = resume_stage
        checkpoint.pop("resume_stage", None)
        checkpoint["checkpoint_revision"] = int(checkpoint.get("checkpoint_revision", 0)) + 1
        temporary = checkpoint_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(checkpoint, ensure_ascii=False, sort_keys=True), "utf-8")
        os.replace(temporary, checkpoint_path)

    @staticmethod
    def _launch_worker(task: TaskRecord, payload_path: Path) -> subprocess.Popen[str]:
        del task
        return subprocess.Popen(
            [sys.executable, "-m", "src.orchestration.worker", str(payload_path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def _write_control(self, task_id: str, action: str) -> None:
        work_dir = self.worker_root / task_id
        work_dir.mkdir(parents=True, exist_ok=True)
        temporary = work_dir / "control.tmp"
        destination = work_dir / "control.json"
        temporary.write_text(json.dumps({"action": action}), "utf-8")
        os.replace(temporary, destination)


def _start_marker(pid: int) -> str:
    try:
        import psutil
    except ImportError:
        return uuid.uuid4().hex
    try:
        return str(psutil.Process(pid).create_time())
    except (psutil.Error, OSError):
        return uuid.uuid4().hex


def _default_pid_probe(pid: int, start_marker: str) -> bool:
    """Return whether ``pid`` is still the process that wrote ``start_marker``.

    ``psutil.NoSuchProcess`` derives from ``psutil.Error``, not ``OSError``: an
    unclean shutdown leaves a stale owner row behind, and the Dashboard must
    reclaim it instead of raising on startup.
    """

    try:
        import psutil
    except ImportError:
        return False
    try:
        return str(psutil.Process(pid).create_time()) == start_marker
    except (psutil.Error, OSError):
        return False


def _source_descriptor(platform: str, source_id: str) -> dict[str, Any]:
    match = re.fullmatch(r"bilibili_(BV[\w]+)_p(\d+)", source_id)
    if platform == "bilibili" and match is not None:
        return {"platform": "bilibili", "bvid": match.group(1), "part": int(match.group(2))}
    return {"platform": platform, "id": source_id}
