"""小红书页内扫码：把 Dashboard 的登录请求代理给 xhs sidecar。

小红书登录走它自己的接口（Spider_XHS 在 sidecar 里渲染二维码），所以这里不做
浏览器抓取，只做协议适配，保持与抖音/ B站 相同的 ``start / get / qr_png`` 接口。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.platforms.errors import PlatformEnumerationError


@dataclass(frozen=True)
class XhsLoginSnapshot:
    operation_id: str
    status: str
    message: str


class XhsLoginCoordinator:
    def __init__(self, sidecar: Any) -> None:
        self._sidecar = sidecar

    def start(self) -> XhsLoginSnapshot:
        return _snapshot(self._sidecar.start_login())

    def get(self, operation_id: str) -> XhsLoginSnapshot:
        try:
            return _snapshot(self._sidecar.login_status(operation_id))
        except PlatformEnumerationError as error:
            raise LookupError("登录会话不存在或已过期") from error

    def qr_png(self, operation_id: str) -> bytes:
        try:
            return self._sidecar.login_qr(operation_id)
        except PlatformEnumerationError as error:
            raise LookupError("二维码仍在生成") from error


def _snapshot(payload: Any) -> XhsLoginSnapshot:
    data = payload if isinstance(payload, dict) else {}
    return XhsLoginSnapshot(
        operation_id=str(data.get("operation_id") or ""),
        status=str(data.get("status") or "preparing_qr"),
        message=str(data.get("message") or ""),
    )
