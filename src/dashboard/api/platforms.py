"""Platform capability and in-page QR login endpoints."""

from threading import Thread
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from src.dashboard.security import require_local_session, require_mutation_security

router = APIRouter(prefix="/api/v1/platforms", tags=["platforms"])


class PlatformResponse(BaseModel):
    name: str
    item_types: tuple[str, ...]
    requires_browser: bool
    requires_auth: bool
    auth_status: str
    auth_message: str


class LoginResponse(BaseModel):
    operation_id: str
    platform: str
    status: str
    message: str | None = None
    qr_url: str | None = None


def _platform_response(descriptor: Any, auth: Any) -> PlatformResponse:
    return PlatformResponse(
        name=descriptor.name,
        item_types=tuple(sorted(item.value for item in descriptor.item_types)),
        requires_browser=descriptor.requires_browser,
        requires_auth=descriptor.requires_auth,
        auth_status=auth.status,
        auth_message=auth.message,
    )


def _qr_coordinator(request: Request, platform: str):
    """Return the in-page QR coordinator for a platform, if it has one."""

    if platform == "bilibili":
        return getattr(request.app.state, "bilibili_login", None)
    logins = request.app.state.qr_logins
    if platform in logins:
        return logins[platform]
    if platform == "douyin":
        from src.dashboard.douyin_login import DouyinLoginCoordinator

        try:
            adapter = request.app.state.service.platform_manager.get(platform)
        except Exception:
            return None
        session = getattr(adapter, "session", None)
        if session is None:
            return None
        logins[platform] = DouyinLoginCoordinator(session)
        return logins[platform]
    return None


@router.get("", response_model=tuple[PlatformResponse, ...])
def list_platforms(request: Request):
    return tuple(
        _platform_response(descriptor, auth)
        for descriptor, auth in request.app.state.service.list_platforms()
    )


@router.get("/{platform}/auth", response_model=PlatformResponse)
def platform_auth(platform: str, request: Request):
    for descriptor, auth in request.app.state.service.list_platforms():
        if descriptor.name == platform:
            return _platform_response(descriptor, auth)
    raise HTTPException(status_code=404, detail="platform not found")


@router.post("/{platform}/login", response_model=LoginResponse, dependencies=[Depends(require_mutation_security)])
def login(platform: str, request: Request):
    coordinator = _qr_coordinator(request, platform)
    if coordinator is not None:
        if platform == "bilibili":
            adapter = request.app.state.service.platform_manager.get(platform)
            save_credential = getattr(adapter, "save_dashboard_credential", None)
            if not callable(save_credential):
                raise HTTPException(status_code=409, detail="Bilibili QR login is unavailable")
            snapshot = coordinator.start(save_credential)
        else:
            snapshot = coordinator.start()
        return LoginResponse(
            operation_id=snapshot.operation_id,
            platform=platform,
            status=snapshot.status,
            message=snapshot.message,
            qr_url=f"/api/v1/platforms/{platform}/login/{snapshot.operation_id}/qr",
        )

    # Platforms without an in-page coordinator keep the external-browser flow.
    operation_id = uuid4().hex
    Thread(
        target=request.app.state.service.login_platform,
        kwargs={"platform": platform, "headful": True},
        daemon=True,
        name=f"dashboard-login-{platform}",
    ).start()
    return LoginResponse(operation_id=operation_id, platform=platform, status="opening_browser")


@router.get("/{platform}/login/{operation_id}", dependencies=[Depends(require_local_session)])
def login_status(platform: str, operation_id: str, request: Request):
    coordinator = _qr_coordinator(request, platform)
    if coordinator is None:
        raise HTTPException(status_code=404, detail="platform has no QR login")
    try:
        snapshot = coordinator.get(operation_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return snapshot.__dict__


@router.get("/{platform}/login/{operation_id}/qr", dependencies=[Depends(require_local_session)])
def login_qr(platform: str, operation_id: str, request: Request) -> Response:
    coordinator = _qr_coordinator(request, platform)
    if coordinator is None:
        raise HTTPException(status_code=404, detail="platform has no QR login")
    try:
        png = coordinator.qr_png(operation_id)
    except LookupError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "no-store"})
