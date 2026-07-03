"""Tests for optional Langfuse v2 trace mirroring."""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.dependencies import get_db
from app.integrations.langfuse_mirror import LangfuseMirror, MirrorPayload
from app.main import create_app

PROBE_PROMPT = "What is chess?"


@pytest.fixture
def api_client(db_engine, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    """Test client with stub LLM and committed Postgres writes."""
    monkeypatch.setenv("LLM_DRY_RUN", "true")
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    get_settings.cache_clear()
    session_factory = sessionmaker(bind=db_engine, autocommit=False, autoflush=False)

    def override_get_db() -> Generator[Session, None, None]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()
    get_settings.cache_clear()


class _FakeGeneration:
    def end(self, **_: object) -> None:
        return None


class _FakeTrace:
    def __init__(self) -> None:
        self.id = "trace-abc-123"

    def generation(self, **_: object) -> _FakeGeneration:
        return _FakeGeneration()

    def update(self, **_: object) -> None:
        return None


class _FakeLangfuseClient:
    def __init__(self, **_: object) -> None:
        self.trace_calls = 0

    def trace(self, **_: object) -> _FakeTrace:
        self.trace_calls += 1
        return _FakeTrace()

    def flush(self) -> None:
        return None


def _payload() -> MirrorPayload:
    return MirrorPayload(
        interaction_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        user_id="mirror-user",
        prompt="hello",
        response="world",
        guardrail_outcome="allowed",
        capability_level=1,
        model="stub-local",
        is_degraded=False,
    )


def test_mirror_disabled_returns_none() -> None:
    mirror = LangfuseMirror(
        Settings(langfuse_enabled=False, langfuse_public_key="pk", langfuse_secret_key="sk")
    )
    assert mirror.mirror_interaction(_payload()) is None


def test_mirror_missing_keys_returns_none() -> None:
    mirror = LangfuseMirror(Settings(langfuse_enabled=True, langfuse_public_key=None))
    assert mirror.mirror_interaction(_payload()) is None


def test_mirror_success_returns_trace_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("langfuse.Langfuse", _FakeLangfuseClient)
    mirror = LangfuseMirror(
        Settings(langfuse_enabled=True, langfuse_public_key="pk", langfuse_secret_key="sk")
    )
    assert mirror.mirror_interaction(_payload()) == "trace-abc-123"


def test_mirror_swallows_sdk_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(**_: object) -> Any:
        raise RuntimeError("langfuse down")

    monkeypatch.setattr("langfuse.Langfuse", _boom)
    mirror = LangfuseMirror(
        Settings(langfuse_enabled=True, langfuse_public_key="pk", langfuse_secret_key="sk")
    )
    assert mirror.mirror_interaction(_payload()) is None


def test_post_event_langfuse_disabled_returns_null_trace_id(api_client: TestClient) -> None:
    """With LANGFUSE_ENABLED=false, response omits mirrored trace id."""
    response = api_client.post(
        "/v1/events",
        json={"user_id": f"no-langfuse-{uuid.uuid4().hex[:8]}", "prompt": PROBE_PROMPT},
    )
    assert response.status_code == 202
    assert response.json()["langfuse_trace_id"] is None


def test_post_event_with_mocked_mirror_stores_trace_id(
    db_engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Events route persists langfuse_trace_id when mirror succeeds."""
    monkeypatch.setenv("LLM_DRY_RUN", "true")
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    get_settings.cache_clear()

    monkeypatch.setattr(
        "app.api.events.mirror_interaction_to_langfuse",
        lambda payload, settings=None: "trace-mocked-001",
    )

    session_factory = sessionmaker(bind=db_engine, autocommit=False, autoflush=False)

    def override_get_db() -> Generator[Session, None, None]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    user_id = f"langfuse-user-{uuid.uuid4().hex[:8]}"
    with TestClient(app) as client:
        response = client.post(
            "/v1/events",
            json={"user_id": user_id, "prompt": "What is chess?"},
        )
    app.dependency_overrides.clear()
    get_settings.cache_clear()

    assert response.status_code == 202
    body = response.json()
    assert body["langfuse_trace_id"] == "trace-mocked-001"

    with session_factory() as db:
        from app.db.repositories import get_interaction_by_id

        row = get_interaction_by_id(db, uuid.UUID(body["interaction_id"]))
        assert row is not None
        assert row.langfuse_trace_id == "trace-mocked-001"


def test_langfuse_integration_event_mirrors_trace() -> None:
    """Optional live test: requires Langfuse v2 up and LANGFUSE_INTEGRATION=1."""
    if os.environ.get("LANGFUSE_INTEGRATION") != "1":
        pytest.skip("Set LANGFUSE_INTEGRATION=1 with Langfuse v2 running")

    base_url = os.environ.get("LANGFUSE_TEST_BASE_URL", "http://localhost:8000")
    try:
        ready = httpx.get(f"{base_url.rstrip('/')}/ready", timeout=5.0)
        if ready.status_code != 200:
            pytest.skip(f"API not ready at {base_url}")
    except httpx.HTTPError:
        pytest.skip(f"API not reachable at {base_url}")

    user_id = f"langfuse-live-{uuid.uuid4().hex[:8]}"
    response = httpx.post(
        f"{base_url.rstrip('/')}/v1/events",
        json={"user_id": user_id, "prompt": "Explain tides in simple terms."},
        timeout=30.0,
    )
    assert response.status_code == 202
    body = response.json()
    trace_id = body.get("langfuse_trace_id")
    assert trace_id, "expected mirrored trace id when LANGFUSE_ENABLED=true"

    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY", "pk-lf-placeholder")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY", "sk-lf-placeholder")
    host = os.environ.get("LANGFUSE_HOST", "http://localhost:3000").rstrip("/")
    trace_response = httpx.get(
        f"{host}/api/public/traces/{trace_id}",
        auth=(public_key, secret_key),
        timeout=10.0,
    )
    assert trace_response.status_code == 200, trace_response.text
