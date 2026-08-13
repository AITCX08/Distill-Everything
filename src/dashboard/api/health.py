"""Safe health endpoint for the Dashboard host."""

from fastapi import APIRouter, Request

from src.dashboard.schemas import HealthResponse

router = APIRouter(prefix="/api/v1", tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    return HealthResponse(
        status="ok",
        api_version="v1",
        static_compatible=request.app.state.static_compatible,
        models=request.app.state.runtime_models,
    )
