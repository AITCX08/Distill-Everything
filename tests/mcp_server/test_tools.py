"""Tool-layer tests: no MCP SDK required."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.application.commands import JobView, PreviewResult
from src.distillation.artifacts import ArtifactRecord
from src.distillation.state import ItemState, JobState, ProcessingStatus
from src.mcp_server import tools
from src.platforms.models import AuthStatus, ItemType, PlatformDescriptor


def _job_view(job_id="job-1", revision=3, status="running"):
    return JobView(
        job_id=job_id,
        status=status,
        revision=revision,
        platform="douyin",
        creator_id="creator-1",
        creator_name="Creator",
        outputs=("skill",),
        total_items=2,
        completed_items=1,
        failed_items=0,
        unsupported_items=0,
        updated_at="2026-10-30T00:00:00Z",
    )


class FakeQueries:
    def __init__(self, state):
        self._state = state

    def get(self, job_id):
        if job_id != self._state.job_id:
            raise KeyError(job_id)
        return self._state


class FakeService:
    def __init__(self, *, state=None, creator_name="Creator"):
        self.state = state or _make_state()
        self.queries = FakeQueries(self.state)
        self.calls = []
        self.creator_name = creator_name

    def list_platforms(self):
        descriptor = PlatformDescriptor(
            name="douyin",
            url_patterns=("douyin.com",),
            item_types=frozenset({ItemType.VIDEO, ItemType.GALLERY}),
            requires_browser=True,
            requires_auth=True,
        )
        return ((descriptor, AuthStatus("missing", "在页内扫码登录")),)

    def preview(self, request):
        self.calls.append(("preview", request))
        return PreviewResult(
            fingerprint="fp-1",
            platform=request.platform,
            creator_id="creator-1",
            creator_name=self.creator_name,
            total_items=2,
            processable_items=2,
            auth_status="ready",
        )

    def create(self, request):
        self.calls.append(("create", request))
        return _job_view()

    def list_jobs(self):
        return (_job_view(),)

    def get_job(self, job_id):
        return _job_view(job_id)

    def pause(self, job_id, revision):
        self.calls.append(("pause", job_id, revision))
        return _job_view(job_id, revision + 1, "paused")

    def resume(self, job_id, revision):
        self.calls.append(("resume", job_id, revision))
        return _job_view(job_id, revision + 1, "running")

    def retry_failed(self, job_id, revision):
        self.calls.append(("retry_failed", job_id, revision))
        return _job_view(job_id, revision + 1, "running")


def _make_state(tmp_path: Path | None = None) -> JobState:
    root = tmp_path or Path.cwd()
    transcript = root / "bilibili_BV1.json"
    record = ArtifactRecord(
        path=str(transcript),
        sha256="0" * 64,
        size_bytes=42,
    )
    item = ItemState(
        source_id="bilibili_BV1",
        processing_status=ProcessingStatus.COMPLETED,
        artifacts={"transcript": record},
    )
    return JobState(job_id="job-1", status="running", items={"bilibili_BV1": item})


def test_list_platforms_reports_capabilities_and_auth():
    result = tools.list_platforms(FakeService())

    assert result == [
        {
            "name": "douyin",
            "item_types": ["gallery", "video"],
            "requires_auth": True,
            "requires_browser": True,
            "auth_status": "missing",
            "auth_message": "在页内扫码登录",
        }
    ]


def test_preview_rejects_empty_target_and_unknown_output():
    service = FakeService()

    with pytest.raises(tools.ToolError):
        tools.preview_target(service, "   ")
    with pytest.raises(tools.ToolError):
        tools.preview_target(service, "https://www.douyin.com/user/x", outputs=["pdf"])


def test_preview_returns_the_fingerprint_agents_need():
    service = FakeService()

    result = tools.preview_target(
        service,
        "https://www.douyin.com/collection/7123",
        platform="douyin",
        outputs="skill",
    )

    assert result["fingerprint"] == "fp-1"
    assert result["platform"] == "douyin"
    request = service.calls[0][1]
    assert request.outputs == ("skill",)
    assert request.rag_chunks is False


def test_preview_redacts_secret_like_creator_names():
    service = FakeService(creator_name="SESSDATA=deadbeef")

    result = tools.preview_target(service, "https://www.douyin.com/user/x")

    assert "deadbeef" not in str(result)


def test_create_job_requires_a_fingerprint():
    service = FakeService()

    with pytest.raises(tools.ToolError):
        tools.create_job(service, "https://www.douyin.com/user/x", "")


def test_create_job_passes_outputs_and_rag_flag():
    service = FakeService()

    view = tools.create_job(
        service,
        "https://www.douyin.com/user/x",
        "fp-1",
        platform="douyin",
        outputs=["episodes", "skill"],
        rag_chunks=True,
    )

    assert view["job_id"] == "job-1"
    request = service.calls[0][1]
    assert request.outputs == ("episodes", "skill")
    assert request.rag_chunks is True


def test_list_artifacts_hides_absolute_paths():
    service = FakeService()

    result = tools.list_artifacts(service, "job-1")

    assert result == [
        {
            "source_id": "bilibili_BV1",
            "kind": "transcript",
            "name": "bilibili_BV1.json",
            "size_bytes": 42,
            "sha256": "0" * 64,
        }
    ]
    assert "/" not in result[0]["name"]


def test_read_artifact_returns_content_for_an_existing_kind(tmp_path):
    record_path = tmp_path / "bilibili_BV1.json"
    record_path.write_text('{"full_text": "你好世界"}', encoding="utf-8")
    service = FakeService(state=_make_state(tmp_path))

    result = tools.read_artifact(service, "job-1", "bilibili_BV1", "transcript")

    assert result["truncated"] is False
    assert "你好世界" in result["content"]
    assert result["name"] == "bilibili_BV1.json"


def test_read_artifact_truncates_and_lists_available_kinds(tmp_path):
    record_path = tmp_path / "bilibili_BV1.json"
    record_path.write_text("x" * 5_000, encoding="utf-8")
    service = FakeService(state=_make_state(tmp_path))

    truncated = tools.read_artifact(
        service, "job-1", "bilibili_BV1", "transcript", max_chars=1_000
    )
    assert truncated["truncated"] is True
    assert len(truncated["content"]) == 1_000

    with pytest.raises(tools.ToolError) as error:
        tools.read_artifact(service, "job-1", "bilibili_BV1", "unknown")
    assert "transcript" in str(error.value)


def test_control_job_uses_the_current_revision_by_default():
    service = FakeService()

    tools.control_job(service, "job-1", "pause")

    assert service.calls[-1] == ("pause", "job-1", 3)


def test_control_job_rejects_unknown_actions():
    with pytest.raises(tools.ToolError):
        tools.control_job(FakeService(), "job-1", "delete")


def test_unknown_job_is_reported_as_a_tool_error():
    with pytest.raises(tools.ToolError):
        tools.list_job_items(FakeService(), "missing")
