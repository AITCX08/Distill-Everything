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
