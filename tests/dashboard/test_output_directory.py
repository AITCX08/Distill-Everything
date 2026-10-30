
import pytest

from src.dashboard.output_directory import OutputDirectoryService
from tests.dashboard.test_app import make_client


def _mutation_headers(client):
    client.get("/api/v1/health")
    return {
        "Origin": "http://testserver",
        "X-Distill-CSRF": client.cookies.get("distill_csrf"),
    }


def test_directory_validation_returns_a_session_bound_token(tmp_path):
    service = OutputDirectoryService(tmp_path / "settings.json", session_id="session-a")

    result = service.validate(str(tmp_path / "deliveries"))

    assert result.directory.endswith("deliveries")
    assert service.resolve_token(result.token, session_id="session-a") == tmp_path / "deliveries"
    with pytest.raises(PermissionError):
        service.resolve_token(result.token, session_id="session-b")


def test_directory_validation_rejects_a_filesystem_root(tmp_path):
    service = OutputDirectoryService(tmp_path / "settings.json", session_id="session-a")

    with pytest.raises(ValueError, match="root"):
        service.validate(tmp_path.anchor)


def test_default_directory_persists_without_persisting_tokens(tmp_path):
    settings_path = tmp_path / "settings.json"
    selected = tmp_path / "deliveries"

    first = OutputDirectoryService(settings_path, session_id="session-a")
    assert first.set_default(str(selected)) == selected
    token = first.validate(str(selected)).token

    second = OutputDirectoryService(settings_path, session_id="session-a")

    assert second.get_default() == selected
    with pytest.raises(PermissionError):
        second.resolve_token(token, session_id="session-a")


def test_output_directory_mutations_require_origin_and_csrf(tmp_path):
    client = make_client(tmp_path)

    assert client.put("/api/v1/settings/output-directory", json={"directory": str(tmp_path / "delivery")}).status_code == 403
    assert client.post("/api/v1/directories/validate", json={"directory": str(tmp_path / "delivery")}).status_code == 403
    assert client.post("/api/v1/directories/choose").status_code == 403


def test_output_directory_api_returns_default_for_the_local_session(tmp_path):
    client = make_client(tmp_path)
    client.get("/api/v1/health")

    response = client.get("/api/v1/settings/output-directory")

    assert response.status_code == 200
    assert response.json()["directory"].endswith("output")


def test_directory_chooser_cancellation_has_no_selection(tmp_path):
    client = make_client(tmp_path)
    client.app.state.choose_output_directory = lambda _: None

    response = client.post("/api/v1/directories/choose", headers=_mutation_headers(client))

    assert response.status_code == 200
    assert response.json() == {"selected": False, "directory": None, "token": None, "expires_at": None}


def test_directory_chooser_returns_a_validated_session_token(tmp_path):
    client = make_client(tmp_path)
    chosen = tmp_path / "deliveries"
    client.app.state.choose_output_directory = lambda _: chosen

    response = client.post("/api/v1/directories/choose", headers=_mutation_headers(client))

    assert response.status_code == 200
    assert response.json()["selected"] is True
    assert response.json()["directory"].endswith("deliveries")
    assert client.app.state.output_directories.resolve_token(
        response.json()["token"], session_id=client.app.state.local_session.value
    ) == chosen
