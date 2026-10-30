"""FastAPI application factory for the loopback-only Dashboard."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from src.application.service import DistillationService
from src.application.errors import (
    ApplicationError,
    InvalidJobTransitionError,
    ItemNotRetryableError,
    JobAlreadyExistsError,
    JobNotFoundError,
    PreviewChangedError,
)
from src.dashboard.api.health import router as health_router
from src.dashboard.api.artifacts import reveal_directory, router as artifacts_router
from src.dashboard.api.jobs import router as jobs_router
from src.dashboard.api.platforms import router as platforms_router
from src.dashboard.api.events import router as events_router
from src.dashboard.api.tasks import router as tasks_router
from src.dashboard.api.settings import router as settings_router
from src.dashboard.bilibili_login import BilibiliLoginCoordinator
from src.dashboard.output_directory import OutputDirectoryService, choose_output_directory
from src.dashboard.security import CSRF_COOKIE, SESSION_COOKIE, new_local_session
from src.distillation.state import RevisionConflict

_CSP = "default-src 'self'; style-src 'self' 'unsafe-inline'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
_SUMMARY_PROVIDERS = {
    "claude": ("Anthropic", "anthropic"),
    "openai": ("OpenAI", "openai"),
    "qwen": ("Qwen", "qwen"),
    "deepseek": ("DeepSeek", "deepseek"),
    "ollama": ("Ollama", "ollama"),
}


def _runtime_models(config: object | None) -> dict[str, dict[str, str]]:
    """Return display-safe model names without exposing endpoints or credentials."""

    unavailable = {"provider": "未配置", "model": "未配置"}
    if config is None:
        return {"transcription": unavailable.copy(), "summary": unavailable.copy()}

    funasr = getattr(config, "funasr", None)
    transcription_model = str(getattr(funasr, "model", "") or "未配置")
    provider_key = str(getattr(config, "llm_provider", "") or "").lower()
    provider_label, config_name = _SUMMARY_PROVIDERS.get(provider_key, (provider_key or "未配置", ""))
    summary_config = getattr(config, config_name, None) if config_name else None
    summary_model = str(getattr(summary_config, "model", "") or "未配置")
    return {
        "transcription": {"provider": "FunASR", "model": transcription_model},
        "summary": {"provider": provider_label, "model": summary_model},
    }


def create_dashboard_app(
    service: DistillationService,
    static_dir: Path,
    session_secret: str,
) -> FastAPI:
    """Create a docs-free Dashboard app backed by one application service."""

    del session_secret  # A fresh process-local session is safer than a serialized secret.
    index_path = static_dir / "index.html"
    if not index_path.is_file():
        raise FileNotFoundError(f"Dashboard static index is missing: {index_path}")

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.service = service
    app.state.static_dir = static_dir.resolve()
    app.state.static_compatible = True
    app.state.local_session = new_local_session()
    app.state.reveal_directory = reveal_directory
    app.state.bilibili_login = BilibiliLoginCoordinator()
    app.state.qr_logins = {}
    app.state.series_controller = None
    app.state.task_manager = None
    runner_config = getattr(getattr(service, "source_runner", None), "config", None)
    app.state.runtime_models = _runtime_models(runner_config)
    configured_output_directory = getattr(runner_config, "output_dir", service.repository.root.parent / "output")
    app.state.output_directories = OutputDirectoryService(
        service.repository.root.parent / "dashboard-settings.json",
        session_id=app.state.local_session.value,
        default_directory=Path(configured_output_directory),
    )
    app.state.choose_output_directory = choose_output_directory
    app.include_router(health_router)
    app.include_router(jobs_router)
    app.include_router(platforms_router)
    app.include_router(artifacts_router)
    app.include_router(events_router)
    app.include_router(tasks_router)
    app.include_router(settings_router)

    @app.exception_handler(RevisionConflict)
    async def revision_conflict(_: Request, error: RevisionConflict) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"error": {"code": "revision_conflict", "retryable": True}},
        )

    @app.exception_handler(ApplicationError)
    async def application_error(_: Request, error: ApplicationError) -> JSONResponse:
        status_code = 404 if isinstance(error, JobNotFoundError) else 400
        if isinstance(
            error,
            (JobAlreadyExistsError, InvalidJobTransitionError, ItemNotRetryableError, PreviewChangedError),
        ):
            status_code = 409
        return JSONResponse(
            status_code=status_code,
            content={"error": {"code": error.code, "retryable": status_code == 409}},
        )

    @app.middleware("http")
    async def protect_dashboard(request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", _CSP)
        session = request.app.state.local_session
        if request.cookies.get(SESSION_COOKIE) != session.value:
            response.set_cookie(
                SESSION_COOKIE,
                session.value,
                httponly=True,
                samesite="strict",
                secure=False,
            )
            response.set_cookie(
                CSRF_COOKIE,
                session.csrf_token,
                httponly=False,
                samesite="strict",
                secure=False,
            )
        return response

    @app.get("/{path:path}", include_in_schema=False)
    async def serve_spa(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(status_code=404, detail="API route not found")
        candidate = (app.state.static_dir / path).resolve()
        if candidate.is_file() and candidate.is_relative_to(app.state.static_dir):
            return FileResponse(candidate)
        return FileResponse(index_path)

    return app
