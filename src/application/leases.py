"""Cross-platform job execution leases with heartbeat-based stale recovery."""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from src.application.process import process_is_alive
from src.distillation.store import atomic_write_json


_JOB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def _pid_alive(pid: int) -> bool:
    return process_is_alive(pid)


@dataclass(frozen=True)
class LeaseRecord:
    job_id: str
    owner: str
    token: str
    pid: int
    started_at: str
    heartbeat_at: str


class JobLeaseConflict(RuntimeError):
    def __init__(self, record: LeaseRecord):
        super().__init__(
            f"Job {record.job_id} is already owned by {record.owner} (pid {record.pid})"
        )
        self.job_id = record.job_id
        self.owner = record.owner
        self.pid = record.pid
        self.heartbeat_at = record.heartbeat_at


class JobLeaseLost(RuntimeError):
    pass


class JobLease:
    def __init__(self, manager: "JobLeaseManager", record: LeaseRecord):
        self._manager = manager
        self._record = record
        self.released = False

    @property
    def token(self) -> str:
        return self._record.token

    @property
    def owner(self) -> str:
        return self._record.owner

    def heartbeat(self) -> None:
        self._record = self._manager._heartbeat(self._record)

    def release(self) -> None:
        if not self.released:
            self._manager._release(self._record)
            self.released = True

    def __enter__(self) -> "JobLease":
        return self

    def __exit__(self, *args) -> None:
        self.release()


class JobLeaseManager:
    def __init__(
        self,
        root: Path,
        *,
        pid_alive: Callable[[int], bool] = _pid_alive,
        now: Callable[[], datetime] | None = None,
        stale_after: timedelta = timedelta(seconds=30),
    ) -> None:
        self.root = root
        self._pid_alive = pid_alive
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._stale_after = stale_after

    def lease_path(self, job_id: str) -> Path:
        if not _JOB_ID.fullmatch(job_id):
            raise ValueError(f"Invalid job id: {job_id!r}")
        return self.root / f"{job_id}.lease.json"

    def _read(self, path: Path) -> LeaseRecord:
        try:
            value = json.loads(path.read_text("utf-8"))
            return LeaseRecord(**value)
        except (OSError, ValueError, TypeError) as exc:
            raise JobLeaseLost(f"Lease is unreadable: {path}") from exc

    def _write_exclusive(self, path: Path, record: LeaseRecord) -> bool:
        path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        try:
            descriptor = os.open(path, flags, 0o600)
        except FileExistsError:
            return False
        try:
            payload = (json.dumps(asdict(record), ensure_ascii=False, indent=2) + "\n").encode()
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            os.close(descriptor)
        return True

    def _expired(self, record: LeaseRecord) -> bool:
        heartbeat = datetime.fromisoformat(record.heartbeat_at)
        return self._now() - heartbeat > self._stale_after

    def _remove_if_token(self, path: Path, token: str) -> bool:
        try:
            if self._read(path).token != token:
                return False
            path.unlink()
            return True
        except FileNotFoundError:
            return False

    def acquire(self, job_id: str, *, owner: str) -> JobLease:
        path = self.lease_path(job_id)
        now = self._now().isoformat()
        record = LeaseRecord(job_id, owner, uuid.uuid4().hex, os.getpid(), now, now)
        if self._write_exclusive(path, record):
            return JobLease(self, record)

        current = self._read(path)
        recoverable = not self._pid_alive(current.pid) and self._expired(current)
        if not recoverable or not self._remove_if_token(path, current.token):
            raise JobLeaseConflict(current)
        if not self._write_exclusive(path, record):
            raise JobLeaseConflict(self._read(path))
        return JobLease(self, record)

    def _heartbeat(self, record: LeaseRecord) -> LeaseRecord:
        path = self.lease_path(record.job_id)
        current = self._read(path)
        if current.token != record.token:
            raise JobLeaseLost(f"Lease token changed for job {record.job_id}")
        updated = replace(record, heartbeat_at=self._now().isoformat())
        atomic_write_json(path, asdict(updated))
        return updated

    def _release(self, record: LeaseRecord) -> None:
        path = self.lease_path(record.job_id)
        if path.exists() and not self._remove_if_token(path, record.token):
            raise JobLeaseLost(f"Lease token changed for job {record.job_id}")
