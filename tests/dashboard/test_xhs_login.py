"""小红书页内扫码：coordinator 与 sidecar 登录接口。"""

from __future__ import annotations

import pytest

from src.dashboard.xhs_login import XhsLoginCoordinator
from src.platforms.errors import PlatformEnumerationError
from src.platforms.xhs.sidecar import XhsSidecar


class FakeSidecar:
    def __init__(self, *, ready=True):
        self.ready = ready
        self.started = 0

    def start_login(self):
        self.started += 1
        return {"operation_id": "op-1", "status": "preparing_qr", "message": "正在生成二维码"}

    def login_status(self, operation_id):
        if operation_id != "op-1":
            raise PlatformEnumerationError("404")
        return {"operation_id": "op-1", "status": "waiting_for_scan", "message": "请扫码"}

    def login_qr(self, operation_id):
        if not self.ready:
            raise PlatformEnumerationError("409")
        return b"PNG"


def test_coordinator_maps_sidecar_responses():
    coordinator = XhsLoginCoordinator(FakeSidecar())

    started = coordinator.start()
    status = coordinator.get("op-1")

    assert started.operation_id == "op-1"
    assert started.status == "preparing_qr"
    assert status.status == "waiting_for_scan"
    assert coordinator.qr_png("op-1") == b"PNG"


def test_coordinator_translates_missing_qr_and_session_into_lookup_errors():
    coordinator = XhsLoginCoordinator(FakeSidecar(ready=False))

    with pytest.raises(LookupError):
        coordinator.qr_png("op-1")
    with pytest.raises(LookupError):
        coordinator.get("unknown")


class _Transport:
    def __init__(self, *, fail_qr=False):
        self.calls: list[tuple[str, str]] = []
        self.fail_qr = fail_qr

    def get_json(self, url, params):
        self.calls.append(("GET", url))
        if url.endswith("/xhs/login/op-1"):
            return {"operation_id": "op-1", "status": "waiting_for_scan", "message": "请扫码"}
        return {}

    def post(self, url, params):
        self.calls.append(("POST", url))
        return {"operation_id": "op-1", "status": "preparing_qr", "message": "正在生成二维码"}

    def raw(self, url):
        self.calls.append(("RAW", url))
        if self.fail_qr:
            raise ConnectionError("409")

        class Response:
            content = b"PNG"

            def raise_for_status(self):
                return None

        return Response()


def test_client_exposes_login_endpoints():
    transport = _Transport()
    client = XhsSidecar(
        "http://127.0.0.1:8001",
        http_get=transport.get_json,
        http_post=transport.post,
        http_get_raw=transport.raw,
    )

    assert client.start_login()["status"] == "preparing_qr"
    assert client.login_status("op-1")["status"] == "waiting_for_scan"
    assert client.login_qr("op-1") == b"PNG"
    assert transport.calls[0] == ("POST", "http://127.0.0.1:8001/xhs/login")
    assert transport.calls[2][1].endswith("/xhs/login/op-1/qr")


def test_client_reports_qr_failures_as_enumeration_errors():
    client = XhsSidecar(
        "http://127.0.0.1:8001",
        http_get=_Transport().get_json,
        http_post=_Transport().post,
        http_get_raw=_Transport(fail_qr=True).raw,
    )

    with pytest.raises(PlatformEnumerationError):
        client.login_qr("op-1")
