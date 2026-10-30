import pytest
from fastapi import Depends

from src.dashboard.security import require_mutation_security, validate_host
from tests.dashboard.test_app import make_client


def test_dashboard_host_only_accepts_the_canonical_loopback_address():
    assert validate_host("127.0.0.1") == "127.0.0.1"

    for host in ("0.0.0.0", "localhost", "192.168.1.8", "::1"):
        with pytest.raises(ValueError):
            validate_host(host)


def test_mutation_rejects_foreign_origin_and_invalid_csrf(tmp_path):
    client = make_client(tmp_path)
    app = client.app

    @app.post("/api/v1/test-mutation", dependencies=[Depends(require_mutation_security)])
    def test_mutation():
        return {"ok": True}

    client.get("/api/v1/health")
    csrf = client.cookies.get("distill_csrf")

    foreign = client.post(
        "/api/v1/test-mutation",
        headers={"Origin": "http://evil.example", "X-Distill-CSRF": csrf},
    )
    invalid_csrf = client.post(
        "/api/v1/test-mutation",
        headers={"Origin": "http://testserver", "X-Distill-CSRF": "wrong"},
    )
    accepted = client.post(
        "/api/v1/test-mutation",
        headers={"Origin": "http://testserver", "X-Distill-CSRF": csrf},
    )

    assert foreign.status_code == 403
    assert invalid_csrf.status_code == 403
    assert accepted.json() == {"ok": True}
