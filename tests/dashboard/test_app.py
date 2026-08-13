from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from src.application.queries import JobRepository
from src.application.service import DistillationService
from src.dashboard.app import create_dashboard_app


def make_client(tmp_path: Path) -> TestClient:
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<main>Dashboard shell</main>", encoding="utf-8")
    service = DistillationService(repository=JobRepository(tmp_path / "jobs"))
    return TestClient(
        create_dashboard_app(
            service=service,
            static_dir=static_dir,
            session_secret="test-session-secret",
        )
    )


def test_health_exposes_only_safe_dashboard_compatibility(tmp_path):
    client = make_client(tmp_path)

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "api_version": "v1",
        "static_compatible": True,
        "models": {
            "transcription": {"provider": "未配置", "model": "未配置"},
            "summary": {"provider": "未配置", "model": "未配置"},
        },
    }
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert "style-src 'self' 'unsafe-inline'" in response.headers["content-security-policy"]
    assert "test-session-secret" not in response.text


def test_health_exposes_current_safe_model_names(tmp_path):
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<main>Dashboard shell</main>", encoding="utf-8")
    config = SimpleNamespace(
        funasr=SimpleNamespace(model="paraformer-zh"),
        llm_provider="deepseek",
        deepseek=SimpleNamespace(model="deepseek-chat"),
    )
    source_runner = SimpleNamespace(config=config)
    service = DistillationService(
        repository=JobRepository(tmp_path / "jobs"),
        source_runner=source_runner,
    )
    client = TestClient(create_dashboard_app(service, static_dir, "test"))

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["models"] == {
        "transcription": {"provider": "FunASR", "model": "paraformer-zh"},
        "summary": {"provider": "DeepSeek", "model": "deepseek-chat"},
    }


def test_spa_fallback_never_captures_api_routes(tmp_path):
    client = make_client(tmp_path)

    page = client.get("/jobs/local")
    missing_api = client.get("/api/v1/unknown")

    assert page.status_code == 200
    assert page.text == "<main>Dashboard shell</main>"
    assert missing_api.status_code == 404
