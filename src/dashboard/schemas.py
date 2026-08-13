"""Explicit response schemas for the versioned Dashboard API."""

from typing import Literal

from pydantic import BaseModel, Field


class RuntimeModelResponse(BaseModel):
    provider: str
    model: str


class RuntimeModelsResponse(BaseModel):
    transcription: RuntimeModelResponse
    summary: RuntimeModelResponse


class HealthResponse(BaseModel):
    status: str
    api_version: str
    static_compatible: bool
    models: RuntimeModelsResponse


class OutputDirectoryInput(BaseModel):
    directory: str = Field(min_length=1, max_length=32768)


class OutputDirectoryResponse(BaseModel):
    directory: str


class DirectoryValidationResponse(OutputDirectoryResponse):
    token: str
    expires_at: str


class DirectorySelectionResponse(BaseModel):
    selected: bool
    directory: str | None = None
    token: str | None = None
    expires_at: str | None = None


class PreviewInput(BaseModel):
    target: str = Field(min_length=1, max_length=4096)
    platform: str = "auto"
    outputs: tuple[Literal["episodes", "skill"], ...] = ("episodes", "skill")
    rag_chunks: bool = False


class CreateJobInput(PreviewInput):
    preview_fingerprint: str = Field(min_length=1, max_length=256)
    destination_mode: Literal["default", "override"] = "default"
    destination_token: str | None = Field(default=None, min_length=1, max_length=256)


class RevisionInput(BaseModel):
    expected_revision: int = Field(ge=0)


class TaskCommandInput(RevisionInput):
    command_id: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class BilibiliImportInput(BaseModel):
    bvid: str = Field(min_length=4, max_length=64, pattern=r"^BV[A-Za-z0-9]+$")


class BilibiliImportResponse(BaseModel):
    job_id: str
    created_tasks: int
    completed_tasks: int
    pending_tasks: int


class TaskResponse(BaseModel):
    task_id: str
    job_id: str
    source_id: str
    display_title: str
    part_number: int | None = Field(default=None, ge=1)
    status: str
    stage: str
    revision: int
    attempt: int
    checkpoint_revision: int
    updated_at: str


class PreviewResponse(BaseModel):
    fingerprint: str
    platform: str
    creator_id: str
    creator_name: str
    total_items: int
    processable_items: int
    skipped_items: int
    unsupported_items: int
    auth_status: str


class JobResponse(BaseModel):
    job_id: str
    status: str
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


class JobDetailsResponse(BaseModel):
    job_id: str
    display_title: str
    creator_name: str
    destination: str
    artifact_count: int = Field(ge=0)
    completed_at: str | None = None


class ItemResponse(BaseModel):
    source_id: str
    display_title: str
    part_number: int | None = Field(default=None, ge=1)
    processing_status: str
    retryable: bool
    stage_progress: float
    overall_progress: float
    last_error: str | None
    completed_at: str | None
    updated_at: str
