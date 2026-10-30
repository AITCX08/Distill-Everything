from fastapi.testclient import TestClient

from src.application.queries import JobRepository
from src.application.service import DistillationService
from src.dashboard.app import create_dashboard_app
from src.dashboard.bilibili_login import BilibiliLoginSnapshot
from src.platforms.models import AuthStatus, ItemType, PlatformDescriptor


class FakeAdapter:
    descriptor = PlatformDescriptor(
        name="douyin",
        url_patterns=("douyin.com",),
        item_types=frozenset({ItemType.VIDEO}),
        requires_browser=True,
        requires_auth=True,
    )

    def auth_status(self):
        return AuthStatus("missing", "Scan in external Chromium")

    def authenticate(self, *, headful):
        assert headful is True


class FakeBilibiliAdapter:
    descriptor = PlatformDescriptor(
        name="bilibili",
        url_patterns=("bilibili.com",),
        item_types=frozenset({ItemType.VIDEO}),
        requires_browser=True,
        requires_auth=True,
    )

    def auth_status(self):
        return AuthStatus("missing", "Run the Bilibili login command")

    def save_dashboard_credential(self, credential, buvid3):
        del credential, buvid3


class FakeBilibiliLogin:
    def start(self, save_credential):
        assert callable(save_credential)
        return BilibiliLoginSnapshot("bili-op", "waiting_for_scan", "等待扫码")

    def get(self, operation_id):
        assert operation_id == "bili-op"
        return BilibiliLoginSnapshot(operation_id, "waiting_for_confirmation", "请在手机上确认登录")

    def qr_png(self, operation_id):
        assert operation_id == "bili-op"
        return b"png-bytes"


class FakePlatforms:
    def __init__(self):
        self.adapter = FakeAdapter()
        self.bilibili = FakeBilibiliAdapter()

    def list_descriptors(self):
        return (self.adapter.descriptor, self.bilibili.descriptor)

    def get(self, name):
        return {"douyin": self.adapter, "bilibili": self.bilibili}[name]


def test_platform_listing_and_login_response_never_expose_credentials(tmp_path):
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("dashboard", encoding="utf-8")
    service = DistillationService(
        repository=JobRepository(tmp_path / "jobs"), platform_manager=FakePlatforms()
    )
    client = TestClient(create_dashboard_app(service, static_dir, "test"))
    client.get("/api/v1/health")
    headers = {"Origin": "http://testserver", "X-Distill-CSRF": client.cookies["distill_csrf"]}

    listed = client.get("/api/v1/platforms")
    login = client.post("/api/v1/platforms/douyin/login", headers=headers)

    assert listed.json()[0]["auth_status"] == "missing"
    assert login.status_code == 200
    assert login.json()["status"] == "opening_browser"
    assert "cookie" not in login.text.lower()
    assert "credential" not in login.text.lower()


def test_bilibili_login_exposes_a_local_qr_session_without_opening_a_browser(tmp_path):
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("dashboard", encoding="utf-8")
    service = DistillationService(
        repository=JobRepository(tmp_path / "jobs"), platform_manager=FakePlatforms()
    )
    app = create_dashboard_app(service, static_dir, "test")
    app.state.bilibili_login = FakeBilibiliLogin()
    client = TestClient(app)
    client.get("/api/v1/health")
    headers = {"Origin": "http://testserver", "X-Distill-CSRF": client.cookies["distill_csrf"]}

    started = client.post("/api/v1/platforms/bilibili/login", headers=headers)
    state = client.get("/api/v1/platforms/bilibili/login/bili-op")
    qr = client.get("/api/v1/platforms/bilibili/login/bili-op/qr")

    assert started.status_code == 200
    assert started.json() == {
        "operation_id": "bili-op",
        "platform": "bilibili",
        "status": "waiting_for_scan",
        "message": "等待扫码",
        "qr_url": "/api/v1/platforms/bilibili/login/bili-op/qr",
    }
    assert state.json()["status"] == "waiting_for_confirmation"
    assert qr.headers["content-type"] == "image/png"
    assert qr.content == b"png-bytes"


class FakeDouyinLogin:
    def start(self):
        return BilibiliLoginSnapshot("dy-op", "waiting_for_scan", "请使用抖音 App 扫码")

    def get(self, operation_id):
        assert operation_id == "dy-op"
        return BilibiliLoginSnapshot(operation_id, "succeeded", "登录成功")

    def qr_png(self, operation_id):
        assert operation_id == "dy-op"
        return b"douyin-png"


def test_douyin_login_serves_the_qr_inside_the_page(tmp_path):
    """抖音也走页内弹窗：不再返回 opening_browser，二维码由本机接口提供。"""

    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("dashboard", encoding="utf-8")
    service = DistillationService(
        repository=JobRepository(tmp_path / "jobs"), platform_manager=FakePlatforms()
    )
    app = create_dashboard_app(service, static_dir, "test")
    app.state.qr_logins = {"douyin": FakeDouyinLogin()}
    client = TestClient(app)
    client.get("/api/v1/health")
    headers = {"Origin": "http://testserver", "X-Distill-CSRF": client.cookies["distill_csrf"]}

    started = client.post("/api/v1/platforms/douyin/login", headers=headers)
    state = client.get("/api/v1/platforms/douyin/login/dy-op")
    qr = client.get("/api/v1/platforms/douyin/login/dy-op/qr")

    assert started.status_code == 200
    assert started.json() == {
        "operation_id": "dy-op",
        "platform": "douyin",
        "status": "waiting_for_scan",
        "message": "请使用抖音 App 扫码",
        "qr_url": "/api/v1/platforms/douyin/login/dy-op/qr",
    }
    assert state.json()["status"] == "succeeded"
    assert qr.headers["content-type"] == "image/png"
    assert qr.content == b"douyin-png"
