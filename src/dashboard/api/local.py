"""本地文件蒸馏 API：上传 → 解析/转写 → 建模 → 生成技能文件。"""

from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile

from src.dashboard.security import require_local_session, require_mutation_security
from src.reader.extra_readers import EXTRA_FORMATS
from src.reader.media_reader import MEDIA_EXTENSIONS

router = APIRouter(prefix="/api/v1/local", tags=["local"])

DOCUMENT_EXTENSIONS = {".txt", ".docx", ".pdf"} | set(EXTRA_FORMATS)
ALLOWED_EXTENSIONS = DOCUMENT_EXTENSIONS | MEDIA_EXTENSIONS
MAX_BYTES = 512 * 1024 * 1024
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._\-\u4e00-\u9fff ]+")

_OPERATIONS: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()


def _snapshot(operation_id: str) -> dict[str, Any]:
    with _LOCK:
        operation = _OPERATIONS.get(operation_id)
        if operation is None:
            raise KeyError(operation_id)
        return {
            "operation_id": operation_id,
            "status": operation["status"],
            "message": operation["message"],
            "file_name": operation["file_name"],
            "outputs": operation["outputs"],
        }


def _update(operation_id: str, *, status: str | None = None, message: str | None = None, outputs: dict | None = None) -> None:
    with _LOCK:
        operation = _OPERATIONS.get(operation_id)
        if operation is None:
            return
        if status is not None:
            operation["status"] = status
        if message is not None:
            operation["message"] = message
        if outputs is not None:
            operation["outputs"] = outputs


def _run(operation_id: str, path: Path, config: Any, provider: str | None, title: str) -> None:
    from src.application.local_distill import distill_local_file
    from src.clean.text_processor import create_llm_client

    try:
        llm_client = create_llm_client(provider or config.llm_provider, config)
        if llm_client is None:
            raise RuntimeError("没有可用的 LLM，请先在 .env 配置 API Key")
        result = distill_local_file(
            path,
            config=config,
            llm_client=llm_client,
            title=title,
            progress=lambda message: _update(operation_id, message=message),
        )
        _update(
            operation_id,
            status="done",
            message=f"完成：{result.title}（{result.char_count} 字）",
            outputs={
                "cleaned": result.cleaned_path.name,
                "knowledge": result.knowledge_path.name if result.knowledge_path else "",
                "skill": str(result.skill_path),
            },
        )
    except Exception as error:  # 失败要让界面看到原因
        _update(operation_id, status="failed", message=f"{error}")


@router.post("", dependencies=[Depends(require_local_session), Depends(require_mutation_security)])
def upload(request: Request, file: UploadFile) -> dict[str, Any]:
    config = request.app.state.service.source_runner.config
    name = Path(file.filename or "upload").name
    suffix = Path(name).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"不支持的文件格式：{suffix or '（无后缀）'}")
    safe_name = _SAFE_NAME.sub("_", name) or f"upload{suffix}"
    inbox = Path(config.data_dir) / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    target = inbox / f"{uuid4().hex[:8]}-{safe_name}"

    size = 0
    with target.open("wb") as handle:
        while chunk := file.file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_BYTES:
                handle.close()
                target.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail="文件超过 512 MB 限制")
            handle.write(chunk)

    operation_id = uuid4().hex
    with _LOCK:
        _OPERATIONS[operation_id] = {
            "status": "running",
            "message": "已上传，开始解析…",
            "file_name": safe_name,
            "outputs": {},
        }
    provider = request.query_params.get("llm") or None
    title = Path(name).stem
    threading.Thread(
        target=_run,
        args=(operation_id, target, config, provider, title),
        daemon=True,
        name=f"local-distill-{operation_id[:8]}",
    ).start()
    return _snapshot(operation_id)


@router.get("", dependencies=[Depends(require_local_session)])
def list_operations() -> dict[str, Any]:
    with _LOCK:
        items = [
            {"operation_id": key, "status": value["status"], "file_name": value["file_name"]}
            for key, value in _OPERATIONS.items()
        ]
    return {"operations": items[-20:]}


@router.get("/{operation_id}", dependencies=[Depends(require_local_session)])
def operation_status(operation_id: str) -> dict[str, Any]:
    try:
        return _snapshot(operation_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="任务不存在") from error
