"""Creator-center insights pulled from an already-logged-in browser profile.

The three platforms expose private, weakly-signed endpoints; the only stable
route is the one the console itself uses. Every metric that the page did not
return stays ``None`` — never ``0`` — so "not captured" cannot be mistaken for
"zero views".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

SUPPORTED_PLATFORMS = ("bilibili", "douyin", "xiaohongshu")
METRIC_KEYS = ("views", "likes", "comments", "shares", "followers", "followers_delta")


class InsightsUnavailable(RuntimeError):
    """Raised when the console never emitted the expected XHR."""


class InsightsDisabled(RuntimeError):
    """Raised when the feature flag is off."""


@dataclass(frozen=True)
class InsightsSnapshot:
    platform: str
    captured_at: str
    account: dict[str, Any]
    metrics: dict[str, Any]
    items: tuple[dict[str, Any], ...] = ()
    source: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "captured_at": self.captured_at,
            "account": dict(self.account),
            "metrics": dict(self.metrics),
            "items": [dict(item) for item in self.items],
            "source": dict(self.source),
        }

    def validate(self) -> None:
        if self.platform not in SUPPORTED_PLATFORMS:
            raise ValueError(f"不支持的平台：{self.platform}")
        missing = [key for key in METRIC_KEYS if key not in self.metrics]
        if missing:
            raise ValueError(f"metrics 缺少字段：{', '.join(missing)}")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def pick(payload: Mapping[str, Any], path: str) -> Any:
    """Read a dotted path; return ``None`` when absent (never a default of 0)."""

    current: Any = payload
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def build_metrics(payload: Mapping[str, Any]) -> dict[str, int | None]:
    return {
        "views": _as_int(pick(payload, "views")),
        "likes": _as_int(pick(payload, "likes")),
        "comments": _as_int(pick(payload, "comments")),
        "shares": _as_int(pick(payload, "shares")),
        "followers": _as_int(pick(payload, "followers")),
        "followers_delta": _as_int(pick(payload, "followers_delta")),
    }


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class BrowserInsightsSpec:
    platform: str
    page_url: str
    markers: tuple[str, ...]
    normalize: Callable[[Mapping[str, Any]], InsightsSnapshot]


class InsightsBrowserSession:
    """Minimal persistent-profile session used only for insights capture."""

    def __init__(self, profile_dir: Any, *, headless: bool = True, browser_factory=None) -> None:
        self.profile_dir = profile_dir
        self.headless = headless
        self._browser_factory = browser_factory

    def _factory(self):
        if self._browser_factory is not None:
            return self._browser_factory
        from contextlib import contextmanager

        @contextmanager
        def _playwright(profile_dir, *, headless):
            from playwright.sync_api import sync_playwright

            playwright = sync_playwright().start()
            context = None
            try:
                context = playwright.chromium.launch_persistent_context(
                    str(profile_dir), headless=headless
                )
                yield context
            finally:
                if context is not None:
                    context.close()
                playwright.stop()

        return _playwright

    def open_context(self, *, headless: bool | None = None, task: str = "insights"):
        del task
        factory = self._factory()
        return factory(self.profile_dir, headless=self.headless if headless is None else headless)

    @staticmethod
    def first_page(context: Any) -> Any:
        pages = getattr(context, "pages", ())
        return pages[0] if pages else context.new_page()


def capture(session: Any, spec: BrowserInsightsSpec, *, timeout_ms: int = 30_000, wait_ms: int = 1_500) -> InsightsSnapshot:
    """Open the console page and normalize the first matching XHR payload."""

    captured: list[Mapping[str, Any]] = []

    with session.open_context(headless=True, task=f"insights-{spec.platform}") as context:
        page = session.first_page(context)

        def on_response(response: Any) -> None:
            if captured:
                return
            url = str(getattr(response, "url", ""))
            if not any(marker in url for marker in spec.markers):
                return
            try:
                payload = response.json()
            except Exception:
                return
            if isinstance(payload, Mapping):
                captured.append(payload)

        page.on("response", on_response)
        page.goto(spec.page_url, wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_timeout(wait_ms)

    if not captured:
        raise InsightsUnavailable(
            f"{spec.platform} 创作者中心没有返回预期数据；请先在页内登录并确认后台可访问"
        )
    snapshot = spec.normalize(captured[0])
    snapshot.validate()
    return snapshot
