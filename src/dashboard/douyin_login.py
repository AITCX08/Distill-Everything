"""In-page Douyin QR login for the local Dashboard.

The CLI keeps its headful-browser flow (``DouyinSession.authenticate``); this
coordinator drives a **headless** persistent context, screenshots the login QR
element and hands the PNG to the page, so the user never has to switch windows.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from threading import Lock, Thread
from typing import Any, Callable
from uuid import uuid4

_LOGIN_TRIGGER_SELECTORS = (
    '[data-e2e="login-button"]',
    '[data-e2e="login"] button',
    'button:has-text("登录")',
    '[role="button"]:has-text("登录")',
)
_QR_SELECTORS = (
    '[data-e2e="login-qrcode"] img',
    '[class*="qrcode"] img',
    '[class*="login"] canvas',
    '[class*="qrcode"] canvas',
    'img[src^="data:image"]',
    'canvas',
)


@dataclass(frozen=True)
class DouyinLoginSnapshot:
    operation_id: str
    status: str
    message: str


@dataclass
class _LoginOperation:
    operation_id: str
    status: str = "preparing_qr"
    message: str = "正在打开抖音登录页"
    qr_png: bytes | None = None

    def snapshot(self) -> DouyinLoginSnapshot:
        return DouyinLoginSnapshot(self.operation_id, self.status, self.message)


def _click_login_trigger(page: Any) -> None:
    """Mount the login modal; a missing button is fine (page may already show it)."""

    for selector in _LOGIN_TRIGGER_SELECTORS:
        try:
            locator = page.locator(selector).first
            if locator.count() and locator.is_visible():
                locator.click(timeout=3_000)
                return
        except Exception:
            continue


def capture_login_qr(page: Any, *, attempts: int = 10, wait_ms: int = 500) -> bytes | None:
    """Return a PNG screenshot of the Douyin login QR, or ``None``."""

    _click_login_trigger(page)
    for _ in range(attempts):
        for selector in _QR_SELECTORS:
            try:
                locator = page.locator(selector).first
                if locator.count() and locator.is_visible():
                    png = locator.screenshot()
                    if png:
                        return bytes(png)
            except Exception:
                continue
        try:
            page.wait_for_timeout(wait_ms)
        except Exception:
            break
    return None


class DouyinLoginCoordinator:
    """Own one Douyin QR session without opening a visible browser window."""

    _ACTIVE_STATUSES = frozenset({"preparing_qr", "waiting_for_scan", "waiting_for_confirmation"})

    def __init__(
        self,
        session: Any,
        *,
        qr_capture: Callable[[Any], bytes | None] = capture_login_qr,
        page_url: str = "https://www.douyin.com/",
        poll_seconds: float = 1.5,
        timeout_seconds: float = 180.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._session = session
        self._qr_capture = qr_capture
        self._page_url = page_url
        self._poll_seconds = poll_seconds
        self._timeout_seconds = timeout_seconds
        self._sleep = sleep
        self._clock = clock
        self._lock = Lock()
        self._operations: dict[str, _LoginOperation] = {}
        self._active_operation_id: str | None = None

    def start(self, save_credential: Callable[[Any], None] | None = None) -> DouyinLoginSnapshot:
        del save_credential  # the persistent profile *is* the credential store
        with self._lock:
            if self._active_operation_id is not None:
                active = self._operations[self._active_operation_id]
                if active.status in self._ACTIVE_STATUSES:
                    return active.snapshot()
            operation = _LoginOperation(operation_id=uuid4().hex)
            self._operations[operation.operation_id] = operation
            self._active_operation_id = operation.operation_id

        Thread(
            target=self._run_in_background,
            args=(operation.operation_id,),
            daemon=True,
            name=f"dashboard-douyin-login-{operation.operation_id[:8]}",
        ).start()
        return operation.snapshot()

    def get(self, operation_id: str) -> DouyinLoginSnapshot:
        with self._lock:
            return self._get_operation(operation_id).snapshot()

    def qr_png(self, operation_id: str) -> bytes:
        with self._lock:
            png = self._get_operation(operation_id).qr_png
        if png is None:
            raise LookupError("二维码仍在生成")
        return png

    # -- internals -------------------------------------------------------
    def _get_operation(self, operation_id: str) -> _LoginOperation:
        try:
            return self._operations[operation_id]
        except KeyError as error:
            raise LookupError("登录会话不存在或已过期") from error

    def _update(
        self,
        operation_id: str,
        *,
        status: str,
        message: str,
        qr_png: bytes | None = None,
    ) -> None:
        with self._lock:
            operation = self._get_operation(operation_id)
            operation.status = status
            operation.message = message
            if qr_png is not None:
                operation.qr_png = qr_png
            if status not in self._ACTIVE_STATUSES and self._active_operation_id == operation_id:
                self._active_operation_id = None

    def _run_in_background(self, operation_id: str) -> None:
        from src.platforms.errors import PlatformDependencyError

        try:
            self._run_login(operation_id)
        except PlatformDependencyError as error:
            self._update(operation_id, status="failed", message=str(error))
        except Exception:
            self._update(
                operation_id,
                status="failed",
                message="抖音扫码登录失败，请重试。",
            )

    def _run_login(self, operation_id: str) -> None:
        with self._session.open_context(headless=True, task="dashboard-login") as context:
            page = self._session.first_page(context)
            page.goto(self._page_url, wait_until="domcontentloaded", timeout=30_000)
            png = self._qr_capture(page)
            if not png:
                self._update(
                    operation_id,
                    status="failed",
                    message="无法在页面内获取抖音二维码；请在终端运行 `python main.py source login douyin` 用外部浏览器登录。",
                )
                return
            self._update(
                operation_id,
                status="waiting_for_scan",
                message="请使用抖音 App 扫码",
                qr_png=png,
            )

            deadline = self._clock() + self._timeout_seconds
            while self._clock() < deadline:
                if self._session.is_authenticated(context, page):
                    self._session.mark_authenticated()
                    self._update(
                        operation_id,
                        status="succeeded",
                        message="登录成功，登录态已保存在本机。",
                    )
                    return
                self._sleep(self._poll_seconds)

        self._update(
            operation_id,
            status="expired",
            message="二维码已过期，请重新发起登录。",
        )
