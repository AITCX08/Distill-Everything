"""Persistent, exclusively owned Playwright session for Douyin."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from src.application.leases import JobLeaseConflict, JobLeaseManager
from src.distillation.store import atomic_write_bytes
from src.platforms.errors import (
    PlatformAuthenticationError,
    PlatformDependencyError,
    PlatformSessionBusyError,
)
from src.platforms.models import AuthStatus


_LOGIN_COOKIES = {"sessionid", "sessionid_ss", "sid_guard", "passport_csrf_token"}
_LOGIN_WALL_CHECK = r"""() => {
    const text = (document.body && document.body.innerText || '').replace(/\s+/g, ' ');
    if (text.includes('登录后即可观看喜欢、收藏的视频')) return true;
    return [...document.querySelectorAll('button, [role="button"]')].some((element) => {
        const value = (element.innerText || element.textContent || '').trim();
        if (value !== '登录') return false;
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return style.display !== 'none' && style.visibility !== 'hidden'
            && rect.width > 0 && rect.height > 0;
    });
}"""


_CHANNELS = ("chrome", "msedge")
_LAUNCH_ARGS = ("--disable-blink-features=AutomationControlled",)


def launch_persistent_context(
    chromium: Any,
    profile_dir: Path,
    *,
    headless: bool,
) -> Any:
    """Return a persistent context, falling back to an installed Chrome/Edge."""

    errors: list[str] = []
    try:
        return chromium.launch_persistent_context(
            str(profile_dir),
            headless=headless,
            args=_LAUNCH_ARGS,
        )
    except Exception as exc:  # bundled Chromium missing or broken
        errors.append(f"bundled={exc}")

    for channel in _CHANNELS:
        try:
            return chromium.launch_persistent_context(
                str(profile_dir),
                headless=headless,
                channel=channel,
                args=_LAUNCH_ARGS,
            )
        except Exception as exc:
            errors.append(f"{channel}={exc}")

    raise PlatformDependencyError(
        "没有可用的 Chromium：请安装 Chrome/Edge，或执行 `playwright install chromium`；"
        f"尝试记录：{'; '.join(errors)[:400]}"
    )


@contextmanager
def _playwright_browser(profile_dir: Path, *, headless: bool) -> Iterator[Any]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise PlatformDependencyError(
            "Douyin requires Playwright; install project dependencies first"
        ) from exc

    playwright = sync_playwright().start()
    context = None
    try:
        context = launch_persistent_context(
            playwright.chromium,
            profile_dir,
            headless=headless,
        )
        yield context
    finally:
        if context is not None:
            context.close()
        playwright.stop()


def _first_page(context: Any) -> Any:
    pages = getattr(context, "pages", ())
    return pages[0] if pages else context.new_page()


def _context_authenticated(context: Any, page: Any) -> bool:
    explicit = getattr(context, "authenticated", None)
    if explicit is not None:
        return bool(explicit)
    try:
        has_cookie = any(
            cookie.get("name") in _LOGIN_COOKIES and cookie.get("value")
            for cookie in context.cookies()
        )
    except Exception:
        return False
    if not has_cookie:
        return False
    try:
        return not bool(page.evaluate(_LOGIN_WALL_CHECK))
    except Exception:
        return has_cookie


class DouyinSession:
    def __init__(
        self,
        data_dir: Path,
        *,
        profile_dir: Path | None = None,
        browser_factory: Callable[..., Any] = _playwright_browser,
        lease_manager: JobLeaseManager | None = None,
        login_timeout: float = 180.0,
        poll_interval: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.data_dir = data_dir
        self.profile_dir = profile_dir or data_dir / "browser" / "douyin"
        self.state_dir = data_dir / "session" / "douyin"
        self.stale_flag = self.state_dir / "login-expired.flag"
        self.profile_marker = self.profile_dir / ".profile-initialized"
        self.acquisition_headless = True
        self._browser_factory = browser_factory
        self._leases = lease_manager or JobLeaseManager(self.state_dir / "locks")
        self._login_timeout = login_timeout
        self._poll_interval = poll_interval
        self._sleep = sleep

    def _has_profile(self) -> bool:
        try:
            return self.profile_dir.exists() and any(self.profile_dir.iterdir())
        except OSError:
            return False

    def mark_stale(self) -> None:
        atomic_write_bytes(self.stale_flag, b"expired\n")

    def mark_authenticated(self) -> None:
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(self.profile_marker, b"1\n")
        self.stale_flag.unlink(missing_ok=True)

    @contextmanager
    def open_context(self, *, headless: bool, task: str) -> Iterator[Any]:
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        try:
            lease = self._leases.acquire("douyin-profile", owner=task)
        except JobLeaseConflict as exc:
            raise PlatformSessionBusyError(str(exc)) from exc
        try:
            with self._browser_factory(self.profile_dir, headless=headless) as context:
                yield context
        finally:
            lease.release()

    @contextmanager
    def open_page(self, *, headless: bool, task: str) -> Iterator[Any]:
        with self.open_context(headless=headless, task=task) as context:
            yield _first_page(context)

    # -- probes reused by the Dashboard QR coordinator --------------------
    def first_page(self, context: Any) -> Any:
        return _first_page(context)

    def is_authenticated(self, context: Any, page: Any) -> bool:
        return _context_authenticated(context, page)

    def auth_status(self) -> AuthStatus:
        if not self._has_profile():
            return AuthStatus("missing", "在「平台与登录」页内扫码登录抖音")
        if self.stale_flag.exists():
            return AuthStatus("expired", "抖音登录已过期，请重新扫码登录")
        try:
            with self.open_context(headless=True, task="auth-probe") as context:
                page = _first_page(context)
                page.goto("https://www.douyin.com/", wait_until="domcontentloaded")
                authenticated = _context_authenticated(context, page)
        except PlatformDependencyError as exc:
            return AuthStatus("unavailable", str(exc))
        if not authenticated:
            self.mark_stale()
            return AuthStatus("expired", "抖音登录已过期，请重新扫码登录")
        self.mark_authenticated()
        return AuthStatus("authenticated", "Douyin browser session is ready")

    def authenticate(self, *, headful: bool = True) -> None:
        deadline = time.monotonic() + self._login_timeout
        with self.open_context(headless=not headful, task="login") as context:
            page = _first_page(context)
            page.goto("https://www.douyin.com/", wait_until="domcontentloaded")
            while time.monotonic() <= deadline:
                if _context_authenticated(context, page):
                    self.mark_authenticated()
                    return
                self._sleep(self._poll_interval)
        self.mark_stale()
        raise PlatformAuthenticationError(
            "Douyin QR login timed out; keep the browser open and scan again"
        )
