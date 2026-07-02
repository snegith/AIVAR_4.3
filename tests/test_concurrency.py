"""Concurrency tests for per-user advisory-lock serialization."""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.models import UserRiskProfileRow
from app.detection.orchestrator import DetectionOrchestrator
from tests.detector_helpers import make_probing_attack_window
from tests.orchestrator_helpers import persist_detection_window


def _run_cycles(session_factory: sessionmaker[Session], user_id: str, cycles: int) -> None:
    orchestrator = DetectionOrchestrator(Settings(risk_alpha=0.6))
    now = datetime.now(UTC)
    for cycle in range(cycles):
        db = session_factory()
        try:
            orchestrator.run(db, user_id, as_of=now + timedelta(minutes=cycle))
            db.commit()
        finally:
            db.close()


def test_concurrent_orchestrator_runs_no_lost_updates(db_engine) -> None:
    """Parallel detection cycles for one user must serialize via advisory lock."""
    user_id = f"concurrency-user-{uuid.uuid4().hex[:8]}"
    connection = db_engine.connect()
    transaction = connection.begin()
    seed_session = sessionmaker(bind=connection, autocommit=False, autoflush=False)()
    persist_detection_window(seed_session, make_probing_attack_window(blocked_count=20), user_id=user_id)
    seed_session.flush()
    transaction.commit()
    connection.close()

    session_factory = sessionmaker(bind=db_engine, autocommit=False, autoflush=False)
    thread_count = 6
    cycles_per_thread = 2
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            _run_cycles(session_factory, user_id, cycles_per_thread)
        except BaseException as exc:  # pragma: no cover - surfaced via errors list
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(thread_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors, f"worker errors: {errors!r}"

    verify_session = session_factory()
    try:
        profile = verify_session.get(UserRiskProfileRow, user_id)
        assert profile is not None
        assert profile.version == thread_count * cycles_per_thread - 1
        assert float(profile.risk_score) > 0.0
    finally:
        verify_session.close()
