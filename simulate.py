"""Automated four-persona simulation harness for PS-4.3.

Drives POST /v1/events for personas A–D, asserts risk/pattern/alert outcomes,
runs the inactivity-reset admin test, prints a results table, and exits non-zero
on any assertion failure (CI gate).
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import uuid
from dataclasses import dataclass

import httpx

from sim.personas import (
    boundary_prober,
    data_scraper,
    edge_user,
    normal_user,
    privilege_escalator,
)
from sim.runner import run_persona


@dataclass
class CriterionResult:
    """One assertion row in the results table."""

    persona: str
    criterion: str
    expected: str
    actual: str
    passed: bool


def _admin_headers() -> dict[str, str]:
    admin_key = os.environ.get("ADMIN_KEY", "change-me-in-production")
    return {"X-Admin-Key": admin_key}


def _fetch_config(client: httpx.Client) -> dict[str, float]:
    response = client.get("/v1/config")
    response.raise_for_status()
    body = response.json()
    thresholds = body["thresholds"]
    return {
        "alert_threshold": float(thresholds["alert_threshold"]),
        "watch_threshold": float(thresholds["watch_threshold"]),
    }


def _fetch_risk(client: httpx.Client, user_id: str) -> dict[str, object]:
    response = client.get(f"/v1/users/{user_id}/risk")
    if response.status_code == 404:
        return {"risk_score": 0.0, "status": "normal", "signals": {}}
    response.raise_for_status()
    return response.json()


def _dominant_alert(client: httpx.Client, user_id: str) -> str | None:
    response = client.get("/v1/alerts", params={"user_id": user_id, "limit": 5})
    response.raise_for_status()
    items = response.json().get("items", [])
    if not items:
        return None
    return str(items[0].get("dominant_pattern"))


def _has_alert(client: httpx.Client, user_id: str) -> bool:
    response = client.get("/v1/alerts", params={"user_id": user_id, "limit": 1})
    response.raise_for_status()
    return int(response.json().get("total", 0)) > 0


def _assert_row(
    results: list[CriterionResult],
    *,
    persona: str,
    criterion: str,
    expected: str,
    actual: str,
    passed: bool,
) -> None:
    results.append(
        CriterionResult(
            persona=persona,
            criterion=criterion,
            expected=expected,
            actual=actual,
            passed=passed,
        )
    )


def _verify_must_alert(
    client: httpx.Client,
    results: list[CriterionResult],
    *,
    persona: str,
    user_id: str,
    dominant: str,
    alert_threshold: float,
) -> None:
    risk = _fetch_risk(client, user_id)
    score = float(risk["risk_score"])
    _assert_row(
        results,
        persona=persona,
        criterion="risk_score >= alert_threshold",
        expected=f">= {alert_threshold}",
        actual=str(round(score, 2)),
        passed=score >= alert_threshold,
    )

    pattern = _dominant_alert(client, user_id)
    _assert_row(
        results,
        persona=persona,
        criterion="alert raised",
        expected="true",
        actual=str(_has_alert(client, user_id)),
        passed=_has_alert(client, user_id),
    )
    _assert_row(
        results,
        persona=persona,
        criterion="dominant_pattern",
        expected=dominant,
        actual=str(pattern),
        passed=pattern == dominant,
    )


def _verify_must_not_alert(
    client: httpx.Client,
    results: list[CriterionResult],
    *,
    persona: str,
    user_id: str,
    watch_threshold: float,
) -> None:
    risk = _fetch_risk(client, user_id)
    score = float(risk["risk_score"])
    _assert_row(
        results,
        persona=persona,
        criterion="risk_score < watch_threshold",
        expected=f"< {watch_threshold}",
        actual=str(round(score, 2)),
        passed=score < watch_threshold,
    )
    _assert_row(
        results,
        persona=persona,
        criterion="no alert",
        expected="false",
        actual=str(_has_alert(client, user_id)),
        passed=not _has_alert(client, user_id),
    )


def _run_inactivity_reset_test(
    client: httpx.Client,
    results: list[CriterionResult],
    *,
    user_id: str,
) -> None:
    backdate = client.post(
        f"/v1/admin/users/{user_id}/set_last_event_at",
        headers=_admin_headers(),
        json={"ts": "2020-01-01T00:00:00Z"},
    )
    backdate.raise_for_status()
    recompute = client.post(
        f"/v1/admin/recompute/{user_id}",
        headers=_admin_headers(),
    )
    recompute.raise_for_status()
    risk = recompute.json()
    score = float(risk["risk_score"])
    status = str(risk["status"])
    _assert_row(
        results,
        persona="A_inactivity_reset",
        criterion="risk_score == 0",
        expected="0",
        actual=str(round(score, 2)),
        passed=score == 0.0,
    )
    _assert_row(
        results,
        persona="A_inactivity_reset",
        criterion="status == normal",
        expected="normal",
        actual=status,
        passed=status == "normal",
    )


def _print_table(results: list[CriterionResult]) -> None:
    headers = ("Persona", "Criterion", "Expected", "Actual", "Result")
    rows = [
        (r.persona, r.criterion, r.expected, r.actual, "PASS" if r.passed else "FAIL")
        for r in results
    ]
    widths = [
        max(len(headers[i]), *(len(row[i]) for row in rows)) for i in range(len(headers))
    ]
    line = " | ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    sep = "-+-".join("-" * widths[i] for i in range(len(headers)))
    print(line)
    print(sep)
    for row in rows:
        print(" | ".join(row[i].ljust(widths[i]) for i in range(len(headers))))
    passed = sum(1 for r in results if r.passed)
    print("")
    print(f"Summary: {passed}/{len(results)} criteria passed")


def _wait_for_ready(client: httpx.Client, timeout_seconds: float = 180.0) -> bool:
    """Poll /ready until the API accepts traffic."""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            response = client.get("/ready")
            if response.status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(2.0)
    return False


def main(argv: list[str] | None = None) -> int:
    """Run all personas, print table, return exit code."""
    parser = argparse.ArgumentParser(description="PS-4.3 four-persona simulation harness")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Use stub LLM (set LLM_DRY_RUN on API)",
    )
    parser.add_argument("--request-delay-ms", type=int, default=500)
    args = parser.parse_args(argv)

    os.environ.setdefault("LANGFUSE_ENABLED", "false")
    if args.dry_run:
        os.environ["LLM_DRY_RUN"] = "true"

    run_id = uuid.uuid4().hex[:8]
    plans = [
        boundary_prober(args.seed, run_id=run_id),
        data_scraper(args.seed, run_id=run_id),
        normal_user(args.seed, run_id=run_id),
        privilege_escalator(args.seed, run_id=run_id),
        edge_user(args.seed, run_id=run_id),
    ]

    results: list[CriterionResult] = []
    started = time.perf_counter()

    with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=60.0) as client:
        if not _wait_for_ready(client):
            print(f"ERROR: API not ready at {args.base_url}")
            return 1

        thresholds = _fetch_config(client)

        for plan in plans:
            print(f"Running {plan.name} ({plan.user_id}) ...")
            stats = run_persona(client, plan, request_delay_ms=args.request_delay_ms)
            if stats.errors:
                _assert_row(
                    results,
                    persona=plan.name,
                    criterion="all requests accepted",
                    expected="0 errors",
                    actual=f"{stats.errors} errors",
                    passed=False,
                )
            recompute = client.post(
                f"/v1/admin/recompute/{plan.user_id}",
                headers=_admin_headers(),
            )
            if recompute.status_code != 200:
                _assert_row(
                    results,
                    persona=plan.name,
                    criterion="admin recompute",
                    expected="200",
                    actual=str(recompute.status_code),
                    passed=False,
                )

        _verify_must_alert(
            client,
            results,
            persona="A_boundary_prober",
            user_id=plans[0].user_id,
            dominant="probing",
            alert_threshold=thresholds["alert_threshold"],
        )
        _verify_must_alert(
            client,
            results,
            persona="B_data_scraper",
            user_id=plans[1].user_id,
            dominant="enumeration",
            alert_threshold=thresholds["alert_threshold"],
        )
        _verify_must_not_alert(
            client,
            results,
            persona="C_normal_user",
            user_id=plans[2].user_id,
            watch_threshold=thresholds["watch_threshold"],
        )
        _verify_must_alert(
            client,
            results,
            persona="D_privilege_escalator",
            user_id=plans[3].user_id,
            dominant="escalation",
            alert_threshold=thresholds["alert_threshold"],
        )
        _verify_must_not_alert(
            client,
            results,
            persona="D_edge_user",
            user_id=plans[4].user_id,
            watch_threshold=thresholds["watch_threshold"],
        )
        _run_inactivity_reset_test(client, results, user_id=plans[0].user_id)

    elapsed = time.perf_counter() - started
    print("")
    _print_table(results)
    print(f"Elapsed: {elapsed:.1f}s (delay={args.request_delay_ms}ms per request)")
    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
