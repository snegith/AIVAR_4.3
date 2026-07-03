"""Integration test gate for simulate.py against a running API."""

from __future__ import annotations

import os
import subprocess
import sys
import time

import httpx
import pytest


def _wait_for_ready(base_url: str, timeout_seconds: float = 120.0) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            response = httpx.get(f"{base_url.rstrip('/')}/ready", timeout=5.0)
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(2.0)
    pytest.skip(f"API not ready at {base_url}")


@pytest.fixture(scope="module")
def simulation_base_url() -> str:
    """Base URL for simulate.py; defaults to local docker-compose api."""
    return os.environ.get("SIMULATE_BASE_URL", "http://localhost:8000")


def test_simulate_exits_zero(simulation_base_url: str) -> None:
    """simulate.py must pass all persona criteria (CI gate)."""
    _wait_for_ready(simulation_base_url)
    env = os.environ.copy()
    env["LANGFUSE_ENABLED"] = "false"
    env["LLM_DRY_RUN"] = "true"
    delay = env.get("SIMULATE_REQUEST_DELAY_MS", "100")
    result = subprocess.run(
        [
            sys.executable,
            "simulate.py",
            "--base-url",
            simulation_base_url,
            "--dry-run",
            "--request-delay-ms",
            delay,
            "--seed",
            "42",
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=os.path.dirname(os.path.dirname(__file__)),
        check=False,
    )
    print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    assert result.returncode == 0, "simulate.py failed; see table output above"
