"""Atomic persistence for artifacts and revisioned job state."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from src.distillation.artifacts import ArtifactValidationError
from src.distillation.state import (
    CURRENT_SCHEMA_VERSION,
    JobState,
    RevisionConflict,
    StateCorruptionError,
    UnsupportedStateVersionError,
    migrate_state,
    utc_now_iso,
)
from src.fsutil import replace_with_retry


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY)
        os.fsync(descriptor)
    except OSError:
        return
    finally:
        if descriptor is not None:
            os.close(descriptor)


def atomic_write_bytes(
    path: Path,
    payload: bytes,
    *,
    validator: Callable[[Path], Any] | None = None,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if validator is not None and not validator(temporary):
            raise ArtifactValidationError(path)
        replace_with_retry(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def atomic_write_json(path: Path, value: Mapping[str, Any]) -> Path:
    payload = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

    def validate(candidate: Path) -> bool:
        try:
            json.loads(candidate.read_text("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return False
        return True

    return atomic_write_bytes(path, payload, validator=validate)


class JobStateStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> JobState:
        try:
            raw = json.loads(self.path.read_text("utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("Job state root must be an object")
            original_version = int(raw.get("schema_version", 0))
            state = JobState.from_dict(migrate_state(raw))
            if original_version < CURRENT_SCHEMA_VERSION:
                atomic_write_json(self.path, state.to_dict())
            return state
        except UnsupportedStateVersionError:
            raise
        except FileNotFoundError:
            raise
        except (OSError, UnicodeError, ValueError, TypeError, KeyError) as exc:
            raise StateCorruptionError(self.path) from exc

    def save(
        self,
        state: JobState,
        *,
        expected_revision: int | None = None,
    ) -> JobState:
        current = self.load() if self.path.exists() else None
        actual_revision = current.revision if current is not None else None
        if expected_revision is not None and expected_revision != actual_revision:
            raise RevisionConflict(expected_revision, actual_revision)
        base_revision = current.revision if current is not None else state.revision
        updated = replace(
            state,
            schema_version=CURRENT_SCHEMA_VERSION,
            revision=base_revision + 1,
            updated_at=utc_now_iso(),
        )
        atomic_write_json(self.path, updated.to_dict())
        return updated

    def recover_item(self, source_id: str) -> JobState:
        from src.distillation.state import recover_item

        state = self.load()
        item = state.items[source_id]
        items = dict(state.items)
        items[source_id] = recover_item(item)
        return self.save(replace(state, items=items), expected_revision=state.revision)
