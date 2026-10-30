"""Revisioned job commands and domain DTOs."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, replace
from enum import Enum
from typing import Any, Callable

from src.application.errors import (
    InvalidJobTransitionError,
    ItemNotRetryableError,
    PreviewChangedError,
)
from src.application.events import EventHub
from src.application.queries import JobRepository
from src.distillation.state import (
    JobState,
    ProcessingStatus,
    RevisionConflict,
    recover_item,
    utc_now_iso,
)


class JobStatus(str, Enum):
    CREATED = "created"
    QUEUED = "queued"
    RUNNING = "running"
    PAUSE_REQUESTED = "pause_requested"
    PAUSED = "paused"
    PARTIAL = "partial"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class PreviewRequest:
    target: str
    platform: str = "auto"
    outputs: tuple[str, ...] = ("skill",)
    rag_chunks: bool = False


@dataclass(frozen=True)
class PreviewResult:
    fingerprint: str
    platform: str
    creator_id: str
    creator_name: str
    total_items: int
    processable_items: int
    skipped_items: int = 0
    unsupported_items: int = 0
    auth_status: str = "ready"


@dataclass(frozen=True)
class CreateJobRequest:
    target: str
    preview_fingerprint: str
    platform: str = "auto"
    outputs: tuple[str, ...] = ("skill",)
    rag_chunks: bool = False
    job_id: str = ""
    output_directory: str = ""

    def preview_request(self) -> PreviewRequest:
        return PreviewRequest(self.target, self.platform, self.outputs, self.rag_chunks)


@dataclass(frozen=True)
class JobView:
    job_id: str
    status: JobStatus
    revision: int
    platform: str
    creator_id: str
    creator_name: str
    outputs: tuple[str, ...]
    total_items: int
    completed_items: int
    failed_items: int
    unsupported_items: int
    updated_at: str

    @classmethod
    def from_state(cls, state: JobState) -> "JobView":
        items = tuple(state.items.values())
        request_outputs = state.request.get("outputs", ())
        return cls(
            job_id=state.job_id,
            status=JobStatus(state.status),
            revision=state.revision,
            platform=str(state.creator.get("platform", "")),
            creator_id=str(state.creator.get("creator_id", "")),
            creator_name=str(state.creator.get("display_name", "")),
            outputs=tuple(request_outputs),
            total_items=len(items),
            completed_items=sum(
                item.processing_status is ProcessingStatus.COMPLETED for item in items
            ),
            failed_items=sum(
                item.processing_status is ProcessingStatus.FAILED for item in items
            ),
            unsupported_items=sum(
                item.processing_status is ProcessingStatus.UNSUPPORTED for item in items
            ),
            updated_at=state.updated_at,
        )


class JobCommands:
    def __init__(
        self,
        repository: JobRepository,
        events: EventHub,
        *,
        previewer: Callable[[PreviewRequest], PreviewResult] | None = None,
    ) -> None:
        self.repository = repository
        self.events = events
        self.previewer = previewer

    def preview(self, request: PreviewRequest) -> PreviewResult:
        if self.previewer is None:
            raise RuntimeError("No platform preview handler is configured")
        return self.previewer(request)

    def create(self, request: CreateJobRequest) -> JobView:
        preview = self.preview(request.preview_request())
        if preview.fingerprint != request.preview_fingerprint:
            raise PreviewChangedError()
        job_id = request.job_id or uuid.uuid4().hex
        state = JobState(
            job_id=job_id,
            status=JobStatus.QUEUED.value,
            request=asdict(request),
            creator={
                "platform": preview.platform,
                "creator_id": preview.creator_id,
                "display_name": preview.creator_name,
            },
        )
        store = self.repository.register(
            job_id,
            platform=preview.platform,
            creator_id=preview.creator_id,
        )
        saved = store.save(state)
        view = JobView.from_state(saved)
        self._publish(view)
        return view

    def _load_expected(self, job_id: str, expected_revision: int) -> tuple[Any, JobState]:
        store = self.repository.store(job_id)
        state = store.load()
        if state.revision != expected_revision:
            raise RevisionConflict(expected_revision, state.revision)
        return store, state

    def _publish(self, view: JobView) -> None:
        self.events.publish(
            "job.updated",
            {
                "job_id": view.job_id,
                "status": view.status.value,
                "revision": view.revision,
            },
        )

    def _save(self, store, state: JobState, expected_revision: int) -> JobView:
        saved = store.save(state, expected_revision=expected_revision)
        view = JobView.from_state(saved)
        self._publish(view)
        return view

    def pause(self, job_id: str, expected_revision: int) -> JobView:
        store, state = self._load_expected(job_id, expected_revision)
        if state.status in {JobStatus.PAUSE_REQUESTED.value, JobStatus.PAUSED.value}:
            return JobView.from_state(state)
        if state.status not in {JobStatus.RUNNING.value, JobStatus.QUEUED.value}:
            raise InvalidJobTransitionError(state.status, JobStatus.PAUSE_REQUESTED.value)
        return self._save(
            store,
            replace(state, status=JobStatus.PAUSE_REQUESTED.value),
            expected_revision,
        )

    def resume(self, job_id: str, expected_revision: int) -> JobView:
        store, state = self._load_expected(job_id, expected_revision)
        if state.status == JobStatus.RUNNING.value:
            return JobView.from_state(state)
        allowed = {
            JobStatus.PAUSE_REQUESTED.value,
            JobStatus.PAUSED.value,
            JobStatus.PARTIAL.value,
            JobStatus.FAILED.value,
        }
        if state.status not in allowed:
            raise InvalidJobTransitionError(state.status, JobStatus.RUNNING.value)
        return self._save(
            store,
            replace(state, status=JobStatus.RUNNING.value),
            expected_revision,
        )

    def retry_failed(self, job_id: str, expected_revision: int) -> JobView:
        store, state = self._load_expected(job_id, expected_revision)
        items = dict(state.items)
        changed = False
        for source_id, item in items.items():
            if item.processing_status in {ProcessingStatus.FAILED, ProcessingStatus.RETRY_WAIT}:
                items[source_id] = replace(
                    recover_item(item),
                    last_error=None,
                    completed_at=None,
                    updated_at=utc_now_iso(),
                )
                changed = True
        if not changed and state.status == JobStatus.RUNNING.value:
            return JobView.from_state(state)
        return self._save(
            store,
            replace(state, status=JobStatus.RUNNING.value, items=items),
            expected_revision,
        )

    def retry_item(self, job_id: str, source_id: str, expected_revision: int) -> JobView:
        store, state = self._load_expected(job_id, expected_revision)
        item = state.items[source_id]
        if item.processing_status not in {ProcessingStatus.FAILED, ProcessingStatus.RETRY_WAIT}:
            raise ItemNotRetryableError(source_id, item.processing_status.value)
        items = dict(state.items)
        items[source_id] = replace(
            recover_item(item),
            last_error=None,
            completed_at=None,
            updated_at=utc_now_iso(),
        )
        return self._save(
            store,
            replace(state, status=JobStatus.RUNNING.value, items=items),
            expected_revision,
        )
