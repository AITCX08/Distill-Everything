"""内容库 API：登录后读取账号的收藏 / 喜欢 / 发布，并从条目直接建任务。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from src.application.library import SUPPORTED, LibraryService
from src.dashboard.security import require_local_session, require_mutation_security

router = APIRouter(prefix="/api/v1/library", tags=["library"])


def _library(request: Request) -> LibraryService:
    service = getattr(request.app.state, "service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="本地引擎尚未就绪")
    return LibraryService(service)


def _check(platform: str) -> str:
    if platform not in SUPPORTED:
        raise HTTPException(status_code=404, detail=f"不支持的内容库平台：{platform}")
    return platform


class LibraryJobsRequest(BaseModel):
    targets: list[str] = Field(min_length=1, max_length=100)
    outputs: tuple[str, ...] = ("episodes", "skill")
    rag_chunks: bool = False


@router.get("/{platform}", dependencies=[Depends(require_local_session)])
def library_entries(platform: str, request: Request) -> dict[str, Any]:
    library = _library(request)
    _check(platform)
    return {
        "platform": platform,
        "account": library.account(platform),
        "entries": [entry.to_dict() for entry in library.entries(platform)],
    }


@router.get("/{platform}/folders", dependencies=[Depends(require_local_session)])
def library_folders(platform: str, request: Request) -> dict[str, Any]:
    library = _library(request)
    _check(platform)
    return {"platform": platform, "folders": library.folders(platform)}


@router.get("/{platform}/items", dependencies=[Depends(require_local_session)])
def library_items(
    platform: str,
    kind: str,
    request: Request,
    cursor: str = "",
    folder: str = "",
) -> dict[str, Any]:
    library = _library(request)
    _check(platform)
    try:
        return library.items(platform, kind, cursor=cursor, folder=folder)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post(
    "/{platform}/jobs",
    dependencies=[Depends(require_local_session), Depends(require_mutation_security)],
)
def library_jobs(platform: str, payload: LibraryJobsRequest, request: Request) -> dict[str, Any]:
    library = _library(request)
    _check(platform)
    try:
        job_ids = library.create_jobs(
            platform,
            payload.targets,
            outputs=payload.outputs,
            rag_chunks=payload.rag_chunks,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"platform": platform, "job_ids": job_ids}
