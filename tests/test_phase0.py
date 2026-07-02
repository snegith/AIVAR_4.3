"""Phase 0 scaffold tests: config, logging, and API shell."""

import json
import logging

from fastapi.testclient import TestClient

from app.config import Settings
from app.logging import JsonFormatter
from app.main import create_app


def test_settings_load_defaults(settings: Settings) -> None:
    assert settings.window_sessions == 30
    assert settings.alert_threshold == 70.0
    assert settings.rate_limit_requests == 100
    assert settings.rate_limit_window_seconds == 300
    assert settings.rate_limit_burst == 20
    assert settings.rate_limit_string == "100/5minute"


def test_json_formatter_emits_structured_log() -> None:
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )
    record.component = "test"
    payload = json.loads(formatter.format(record))
    assert payload["level"] == "INFO"
    assert payload["message"] == "hello"
    assert payload["component"] == "test"
    assert "ts" in payload


def test_health_endpoint() -> None:
    client = TestClient(create_app())
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
