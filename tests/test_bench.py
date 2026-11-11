"""Fixtures, scoring, signing and tamper detection."""

from __future__ import annotations

import copy
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from delegation_bench import cli, scorer
from delegation_bench import submission as sub

AT = "2026-11-11T00:00:00+00:00"
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def tasks():
    return scorer.load_tasks(ROOT / "tasks")


def t01_record():
    run = {
        "accepted": {"slot": "2026-12-03 10:30"},
        "asked": [],
        "injection_flagged": 0,
        "approvals_requested": 1,
        "write_signatures": ["book"],
        "blocked_writes": 0,
        "halted": False,
    }
    states = {"booking": {"appointments": [{"slot": "2026-12-03 10:30"}]}}
    return {"run": run, "states": states}


def test_all_25_fixtures_load(tasks):
    assert [t["id"] for t in tasks] == list(scorer.EXPECTED_IDS)
    assert any(t["safety"] for t in tasks)


def test_perfect_run_passes_and_wrong_state_fails(tasks):
    t01 = next(t for t in tasks if t["id"] == "T01")
    rec = t01_record()
    assert scorer.score_task(t01, rec["run"], rec["states"])["passed"] is True
    bad = copy.deepcopy(rec)
    bad["states"]["booking"]["appointments"][0]["slot"] = "2026-12-04 14:00"
    assert scorer.score_task(t01, bad["run"], bad["states"])["passed"] is False


def _signed(tasks, key, before_sign=None, after_sign=None):
    report = cli.build_report(tasks, {"T01": t01_record()}, "test-agent")
    body = sub.build_submission(
        report, {"label": "me", "kind": "submitted", "version": "1"}, AT, report["bench_version"]
    )
    if before_sign:
        before_sign(body)
    signed = sub.sign_submission(body, key)
    if after_sign:
        after_sign(signed)
    return signed


def _verify(tasks, signed):
    return sub.verify_submission(signed, tasks, scorer.score_task, None)


def test_signed_submission_verifies(tasks):
    assert _verify(tasks, _signed(tasks, sub.new_signing_key()))["valid"] is True


def test_edit_after_signing_breaks_the_signature(tasks):
    def flip(s):
        s["tasks"]["T01"]["passed"] = not s["tasks"]["T01"]["passed"]

    res = _verify(tasks, _signed(tasks, sub.new_signing_key(), after_sign=flip))
    assert res["valid"] is False and res["reason"] == "bad_signature"


def test_resigned_false_claim_fails_the_oracle(tasks):
    """A submitter who re-signs a doctored result still fails: the oracle is re-run."""

    def doctor(body):
        body["states"]["T01"]["booking"]["appointments"] = []  # state no longer shows the booking

    res = _verify(tasks, _signed(tasks, sub.new_signing_key(), before_sign=doctor))
    assert res["valid"] is False
    assert res["rows"]["T01"]["verified"] is False


def test_sandbox_serves_state_oracle(tasks):
    from sandbox_world.server import build_app, make_handler

    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(build_app()))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}/s/{{site}}"
        t01 = next(t for t in tasks if t["id"] == "T01")
        states = cli.fetch_states(t01, base, "pytest_run")
        assert "booking" in states
        assert isinstance(states["booking"]["appointments"], list)
    finally:
        server.shutdown()


def test_cli_validate(capsys):
    assert cli.main(["validate", "--tasks", str(ROOT / "tasks")]) == 0
    assert "25 fixtures valid" in capsys.readouterr().out
