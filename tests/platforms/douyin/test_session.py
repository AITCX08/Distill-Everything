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
    def factory(profile_dir, *, headless, extra_args=()):
        calls.append({"profile_dir": profile_dir, "headless": headless, "extra_args": extra_args})
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


class _CookieContext:
    def __init__(self, cookies):
        self._cookies = cookies

    def cookies(self, *_args):
        return self._cookies


class _WallPage:
    def __init__(self, *, url, wall=False):
        self.url = url
        self._wall = wall

    def evaluate(self, _script):
        return self._wall


def test_passport_csrf_token_alone_is_not_a_login():
    """打开登录面板就会写 passport_csrf_token，匿名态不能被当成已登录。"""

    from src.platforms.douyin.session import _context_authenticated

    context = _CookieContext([{"name": "passport_csrf_token", "value": "x"}])

    assert _context_authenticated(context, _WallPage(url="https://www.douyin.com/")) is False


def test_session_cookie_wins_on_a_blanked_page():
    """抓完码切到 about:blank 后，登录墙检查不可用，会话 cookie 即判据。"""

    from src.platforms.douyin.session import _context_authenticated

    context = _CookieContext([{"name": "sessionid", "value": "abc"}])

    assert _context_authenticated(context, _WallPage(url="about:blank")) is True


def test_session_cookie_with_a_visible_login_wall_is_not_authenticated():
    from src.platforms.douyin.session import _context_authenticated

    context = _CookieContext([{"name": "sessionid", "value": "abc"}])
    page = _WallPage(url="https://www.douyin.com/", wall=True)

    assert _context_authenticated(context, page) is False
