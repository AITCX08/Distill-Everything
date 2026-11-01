from contextlib import contextmanager

from src.platforms.douyin.session import DouyinSession


class FakePage:
    url = "https://www.douyin.com/"

    def goto(self, url, **kwargs):
        self.url = url


class FakeContext:
    def __init__(self, authenticated: bool):
        self.authenticated = authenticated
        self.pages = [FakePage()]


def browser_factory(authenticated: bool, calls: list[dict]):
    @contextmanager
    def factory(profile_dir, *, headless):
        calls.append({"profile_dir": profile_dir, "headless": headless})
        yield FakeContext(authenticated)

    return factory


def test_missing_profile_is_actionable_without_launching_browser(tmp_path):
    calls = []
    session = DouyinSession(tmp_path, browser_factory=browser_factory(True, calls))

    status = session.auth_status()

    assert status.status == "missing"
    assert "扫码登录" in status.message
    assert calls == []
    assert session.profile_dir == tmp_path / "browser" / "douyin"


def test_expired_auth_marks_session_stale(tmp_path):
    calls = []
    session = DouyinSession(tmp_path, browser_factory=browser_factory(False, calls))
    session.profile_dir.mkdir(parents=True)
    (session.profile_dir / "Preferences").write_text("{}", "utf-8")

    status = session.auth_status()

    assert status.status == "expired"
    assert session.stale_flag.exists()
    assert calls[0]["headless"] is True


def test_external_headful_login_clears_stale_marker(tmp_path):
    calls = []
    session = DouyinSession(tmp_path, browser_factory=browser_factory(True, calls))
    session.mark_stale()

    session.authenticate(headful=True)

    assert calls[0]["headless"] is False
    assert not session.stale_flag.exists()
    assert (session.profile_dir / ".profile-initialized").exists()


class FakeChromium:
    def __init__(self, *, fail_bundled=False, fail_channels=()):
        self.fail_bundled = fail_bundled
        self.fail_channels = set(fail_channels)
        self.calls: list[dict] = []

    def launch_persistent_context(self, path, **kwargs):
        self.calls.append(kwargs)
        channel = kwargs.get("channel")
        if channel is None and self.fail_bundled:
            raise RuntimeError("bundled chromium missing")
        if channel in self.fail_channels:
            raise RuntimeError(f"channel {channel} missing")
        return f"context:{channel or 'bundled'}"


def test_launch_prefers_the_bundled_build(tmp_path):
    from src.platforms.douyin.session import launch_persistent_context

    chromium = FakeChromium()

    context = launch_persistent_context(chromium, tmp_path, headless=True)

    assert context == "context:bundled"
    assert len(chromium.calls) == 1
    assert "channel" not in chromium.calls[0]


def test_launch_falls_back_to_installed_chrome(tmp_path):
    """没跑 playwright install 的机器也应该能用系统 Chrome。"""

    from src.platforms.douyin.session import launch_persistent_context

    chromium = FakeChromium(fail_bundled=True)

    context = launch_persistent_context(chromium, tmp_path, headless=True)

    assert context == "context:chrome"
    assert [call.get("channel") for call in chromium.calls] == [None, "chrome"]


def test_launch_reports_a_clear_error_when_nothing_is_available(tmp_path):
    from src.platforms.douyin.session import launch_persistent_context
    from src.platforms.errors import PlatformDependencyError

    chromium = FakeChromium(fail_bundled=True, fail_channels=("chrome", "msedge"))

    try:
        launch_persistent_context(chromium, tmp_path, headless=True)
    except PlatformDependencyError as error:
        assert "playwright install chromium" in str(error)
    else:
        raise AssertionError("expected PlatformDependencyError")
