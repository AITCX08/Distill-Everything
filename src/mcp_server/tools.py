"""Agent-facing Distill-Everything tools, independent of the MCP SDK.

Keeping the business logic here has two payoffs: the MCP layer stays a thin
adapter, and these functions are unit-testable without ``mcp`` installed.
Every payload is passed through ``redact_value`` so adapters can never leak
cookies or tokens into an agent transcript.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping

from src.application.commands import CreateJobRequest, PreviewRequest
from src.application.redaction import redact_value

OUTPUT_KINDS = ("episodes", "skill")
CONTROL_ACTIONS = ("pause", "resume", "retry_failed")
MAX_ARTIFACT_CHARS = 200_000


class ToolError(RuntimeError):
    """Agent-facing validation failure (never a credential or traceback)."""


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return value.name
    return value


def _payload(value: Any) -> Any:
    return redact_value(_jsonable(value))


def _normalise_outputs(outputs: Iterable[str] | str | None) -> tuple[str, ...]:
    if outputs is None:
        return ("skill",)
    if isinstance(outputs, str):
        outputs = (outputs,)
    kinds: list[str] = []
    for kind in outputs:
        name = str(kind).strip()
        if name not in OUTPUT_KINDS:
            raise ToolError(f"未知输出类型 {name!r}；可选：{', '.join(OUTPUT_KINDS)}")
        if name not in kinds:
            kinds.append(name)
    if not kinds:
        raise ToolError("outputs 不能为空；可选：episodes, skill")
    return tuple(kinds)


def _require_job(service: Any, job_id: str) -> Any:
    job_id = (job_id or "").strip()
    if not job_id:
        raise ToolError("job_id 不能为空")
    try:
        return service.queries.get(job_id)
    except Exception as exc:  # unknown job / unreadable state
        raise ToolError(f"任务不存在或状态不可读：{job_id}") from exc


def list_platforms(service: Any) -> list[dict[str, Any]]:
    """List adapters with capability flags and (safe) auth status."""

    return _payload(
        [
            {
                "name": descriptor.name,
                "item_types": sorted(item.value for item in descriptor.item_types),
                "requires_auth": descriptor.requires_auth,
                "requires_browser": descriptor.requires_browser,
                "auth_status": auth.status,
                "auth_message": auth.message,
            }
            for descriptor, auth in service.list_platforms()
        ]
    )


def preview_target(
    service: Any,
    target: str,
    *,
    platform: str = "auto",
    outputs: Iterable[str] | str | None = None,
    rag_chunks: bool = False,
) -> dict[str, Any]:
    """Enumerate a creator without writing state; returns the create fingerprint."""

    target = (target or "").strip()
    if not target:
        raise ToolError("target 不能为空")
    request = PreviewRequest(
        target=target,
        platform=(platform or "auto").strip() or "auto",
        outputs=_normalise_outputs(outputs),
        rag_chunks=bool(rag_chunks),
    )
    return _payload(service.preview(request))


def create_job(
    service: Any,
    target: str,
    preview_fingerprint: str,
    *,
    platform: str = "auto",
    outputs: Iterable[str] | str | None = None,
    rag_chunks: bool = False,
    output_directory: str = "",
) -> dict[str, Any]:
    """Create and start a distillation job (requires a preview fingerprint)."""

    target = (target or "").strip()
    if not target:
        raise ToolError("target 不能为空")
    if not (preview_fingerprint or "").strip():
        raise ToolError("缺少 preview_fingerprint；请先调用 distill_preview")
    request = CreateJobRequest(
        target=target,
        preview_fingerprint=preview_fingerprint.strip(),
        platform=(platform or "auto").strip() or "auto",
        outputs=_normalise_outputs(outputs),
        rag_chunks=bool(rag_chunks),
        output_directory=(output_directory or "").strip(),
    )
    return _payload(service.create(request))


def list_jobs(service: Any) -> list[dict[str, Any]]:
    return _payload(list(service.list_jobs()))


def get_job(service: Any, job_id: str) -> dict[str, Any]:
    return _payload(service.get_job(job_id))


def list_job_items(service: Any, job_id: str) -> list[dict[str, Any]]:
    state = _require_job(service, job_id)
    return _payload(
        [
            {
                "source_id": source_id,
                "status": item.processing_status,
                "stage_progress": item.stage_progress,
                "overall_progress": item.overall_progress,
                "last_error": item.last_error,
                "updated_at": item.updated_at,
            }
            for source_id, item in state.items.items()
        ]
    )


def list_artifacts(service: Any, job_id: str) -> list[dict[str, Any]]:
    state = _require_job(service, job_id)
    artifacts: list[dict[str, Any]] = []
    for source_id, item in state.items.items():
        for kind, record in (item.artifacts or {}).items():
            artifacts.append(
                {
                    "source_id": source_id,
                    "kind": kind,
                    "name": Path(record.path).name,
                    "size_bytes": record.size_bytes,
                    "sha256": record.sha256,
                }
            )
    return _payload(artifacts)


def read_artifact(
    service: Any,
    job_id: str,
    source_id: str,
    kind: str,
    *,
    max_chars: int = MAX_ARTIFACT_CHARS,
) -> dict[str, Any]:
    """Return one artifact's text content; large files are truncated explicitly."""

    state = _require_job(service, job_id)
    item = state.items.get(source_id)
    if item is None:
        raise ToolError(f"任务中没有该作品：{source_id}")
    record = (item.artifacts or {}).get(kind)
    if record is None:
        available = ", ".join(sorted(item.artifacts or {})) or "无"
        raise ToolError(f"作品 {source_id} 没有 {kind} 产物；可用：{available}")
    path = Path(record.path)
    if not path.is_file():
        raise ToolError(f"产物文件已不存在：{path.name}")
    text = path.read_text("utf-8", errors="replace")
    limit = max(1_000, int(max_chars))
    truncated = len(text) > limit
    return _payload(
        {
            "source_id": source_id,
            "kind": kind,
            "name": path.name,
            "characters": len(text),
            "truncated": truncated,
            "content": text[:limit],
        }
    )


def control_job(
    service: Any,
    job_id: str,
    action: str,
    *,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    action = (action or "").strip()
    if action not in CONTROL_ACTIONS:
        raise ToolError(f"未知操作 {action!r}；可选：{', '.join(CONTROL_ACTIONS)}")
    view = service.get_job(job_id)
    revision = view.revision if expected_revision is None else int(expected_revision)
    if action == "pause":
        return _payload(service.pause(job_id, revision))
    if action == "resume":
        return _payload(service.resume(job_id, revision))
    return _payload(service.retry_failed(job_id, revision))
