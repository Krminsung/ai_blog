from fastapi.testclient import TestClient

from blogops.core.config import Settings
from blogops.main import create_app


def test_liveness_and_request_id_contract() -> None:
    app = create_app(
        Settings(environment="test", metrics_enabled=False, allowed_hosts="testserver")
    )
    with TestClient(app) as client:
        response = client.get("/health/live", headers={"X-Request-ID": "request-test-123"})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "request-test-123"
    assert response.json()["status"] == "ok"


def test_invalid_client_request_id_is_replaced() -> None:
    app = create_app(
        Settings(environment="test", metrics_enabled=False, allowed_hosts="testserver")
    )
    with TestClient(app) as client:
        response = client.get("/health/live", headers={"X-Request-ID": "bad id"})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"].startswith("req_")
