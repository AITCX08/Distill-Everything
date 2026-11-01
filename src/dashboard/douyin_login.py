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

_LOGIN_PANEL = "#douyin_login_comp_flat_panel"
_QR_SELECTORS = (
    f'{_LOGIN_PANEL} img[src^="data:image"]',
    '[data-e2e="login-qrcode"] img',
    'img[src^="data:image/png;base64"]',
    '[class*="qrcode"] canvas',
)

# 抖音首页默认把「登录」放在顶部导航里，且文字节点没有稳定选择器；
# 用 JS 按文本精确匹配比 button:has-text 稳（后者会先命中隐藏节点）。
_CLICK_LOGIN_JS = """() => {
  const nodes = [...document.querySelectorAll('*')].filter(
    (el) => (el.textContent || '').trim() === '登录' && el.children.length === 0
  );
  const visible = nodes.find((el) => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  });
  if (!visible) return false;
  (visible.closest('button, [role=button], div') || visible).click();
  return true;
}"""

_MARK_QR_JS = """() => {
  const panel = document.querySelector('%s') || document;
  const images = [...panel.querySelectorAll('img')].filter((el) =>
    String(el.getAttribute('src') || '').startsWith('data:image')
  );
  let best = null;
  for (const el of images) {
    const rect = el.getBoundingClientRect();
    if (rect.width < 120 || rect.height < 120) continue;
    const area = rect.width * rect.height;
    if (!best || area > best.area) best = {el, area};
  }
  if (!best) return false;
  best.el.setAttribute('data-qr-candidate', '1');
  return true;
}""" % _LOGIN_PANEL
_SWITCH_TO_QR_JS = """() => {
  const panel = document.querySelector('%s');
  if (!panel) return false;
  const target = [...panel.querySelectorAll('*')].find((el) => {
    const text = (el.textContent || '').trim();
    return /扫码登录/.test(text) && text.length < 8;
  });
  if (!target) return false;
  target.click();
  return true;
}""" % _LOGIN_PANEL


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


_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_MIN_QR_PIXELS = 100


def _is_real_qr_png(png: bytes | None) -> bool:
    """Reject the small icons that also use ``data:image`` sources."""

    if not png or len(png) < 24 or not png.startswith(_PNG_SIGNATURE):
        return False
    width = int.from_bytes(png[16:20], "big")
    height = int.from_bytes(png[20:24], "big")
    return width >= _MIN_QR_PIXELS and height >= _MIN_QR_PIXELS

# 登录只需要 JS/XHR；首页的视频流、封面图和字体会把单实例内存推到 ~900 MB。
# 二维码是客户端生成的 data: URI，不经过网络路由，因此屏蔽网络图片不影响取码。
_HEAVY_RESOURCES = frozenset({"media", "texttrack"})


_RENDERER_TRIM_MB = 150


def trim_heavy_renderers(profile_dir: Any, *, threshold_mb: int = _RENDERER_TRIM_MB) -> int:
    """Terminate the renderer that hosted the feed page.

    抖音首页会把渲染进程推到 ~500 MB，且关掉页面后 Chrome 仍保留该进程。
    登录轮询只需要 cookie，杀掉这个渲染进程不会影响登录态（cookie 在网络进程里），
    实测单实例内存从 ~760 MB 降到 ~250 MB。
    """

    try:
        import psutil
    except ImportError:
        return 0

    marker = str(profile_dir)
    threshold = threshold_mb * 1024 * 1024
    killed = 0
    for proc in psutil.process_iter(["pid", "cmdline", "memory_info"]):
        try:
            cmdline = proc.info["cmdline"] or []
            private = proc.info["memory_info"].private
        except psutil.Error:
            continue
        if not any(marker in part for part in cmdline):
            continue
        if not any(part.startswith("--type=renderer") for part in cmdline):
            continue
        if private < threshold:
            continue
        try:
            proc.terminate()
            killed += 1
        except psutil.Error:
            continue
    return killed

def block_heavy_resources(context: Any) -> None:
    """Abort images/media/fonts so the login context stays light."""

    def handler(route: Any) -> None:
        try:
            if route.request.resource_type in _HEAVY_RESOURCES:
                route.abort()
            else:
                route.continue_()
        except Exception:
            pass

    try:
        context.route("**/*", handler)
    except Exception:
        pass

def _click_login_trigger(page: Any) -> bool:
    """Mount the login modal; a missing entry is fine (it may already be open)."""

    try:
        return bool(page.evaluate(_CLICK_LOGIN_JS))
    except Exception:
        return False


def _switch_to_qr_tab(page: Any) -> bool:
    """Douyin opens on the phone-login panel; the QR lives behind a tab."""

    try:
        return bool(page.evaluate(_SWITCH_TO_QR_JS))
    except Exception:
        return False


def capture_login_qr(page: Any, *, attempts: int = 18, wait_ms: int = 600) -> bytes | None:
    """Return a PNG screenshot of the Douyin login QR, or ``None``.

    The panel contains several small ``data:image`` icons (refresh, logo), so the
    largest qualifying image is marked first instead of taking the first match.
    """

    _click_login_trigger(page)
    try:
        page.wait_for_selector(_LOGIN_PANEL, state="attached", timeout=8_000)
    except Exception:
        pass
    page.wait_for_timeout(800)
    page.wait_for_timeout(1_500)
    _switch_to_qr_tab(page)
    for _ in range(attempts):
        try:
            if page.evaluate(_MARK_QR_JS):
                locator = page.locator('[data-qr-candidate="1"]').first
                if locator.count():
                    png = locator.screenshot(timeout=4_000)
                    if _is_real_qr_png(png):
                        return bytes(png)
        except Exception:
            pass
        for selector in _QR_SELECTORS:
            try:
                locator = page.locator(selector).first
                if locator.count() and locator.is_visible():
                    png = locator.screenshot(timeout=4_000)
                    if _is_real_qr_png(png):
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
        """Headless first (stealth args get past the captcha wall), headful fallback.

        只按顺序尝试，同一时刻最多一个浏览器上下文。
        """

        for headless in (True, False):
            with self._session.open_context(
                headless=headless, task="dashboard-login", offscreen=not headless
            ) as context:
                from src.platforms.douyin.session import has_session_cookie

                # 旧 profile 里的 bd_ticket_guard_* 会让二维码一直停在占位图；
                # 未登录时先清干净再开面板（已登录则不动，避免把人踢下线）。
                if not has_session_cookie(context):
                    try:
                        context.clear_cookies()
                    except Exception:
                        pass
                block_heavy_resources(context)
                page = self._session.first_page(context)
                page.goto(self._page_url, wait_until="domcontentloaded", timeout=30_000)
                page.wait_for_timeout(2_500)
                png = self._qr_capture(page)
                if png:
                    self._update(
                        operation_id,
                        status="waiting_for_scan",
                        message="请使用抖音 App 扫码",
                        qr_png=png,
                    )
                    self._wait_for_scan(operation_id, context, page)
                    return

        self._update(
            operation_id,
            status="failed",
            message="无法在页面内获取抖音二维码（页面可能被验证码拦截）；请重试，或在终端运行 `python main.py source login douyin`。",
        )

    def _wait_for_scan(self, operation_id: str, context: Any, page: Any) -> None:
        # 二维码已拿到，首页的视频流不再需要。单纯 goto("about:blank") 不会让渲染进程退出，
        # 因此新建空白页再关掉旧页，让承载视频流的渲染进程被回收。
        # 登录态在 cookie 里，轮询认证不受影响。
        try:
            fresh = context.new_page()
            try:
                page.close()
            except Exception:
                pass
            page = fresh
            trim_heavy_renderers(self._session.profile_dir)
        except Exception:
            pass

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
