"""Command line: validate fixtures, read the oracle, score run records, sign and verify."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import submission as sub_mod
from .scorer import (
    DEFAULT_TASKS,
    EXPECTED_IDS,
    STAGES,
    SCHEMA_VERSION,
    _task_sites,
    diff_reports,
    exit_code,
    load_tasks,
    score_task,
)

RECORD_KEYS = (
    "accepted",
    "asked",
    "injection_flagged",
    "approvals_requested",
    "write_signatures",
    "blocked_writes",
    "halted",
)


def _load(path: str) -> Any:
    return json.loads(Path(path).read_text())


def fetch_states(task: Dict[str, Any], sandbox_base: str, run_id: str) -> Dict[str, Any]:
    """Read ``/__state`` for every site the task touches. ``sandbox_base`` holds ``{site}``."""
    states: Dict[str, Any] = {}
    for site in _task_sites(task):
        req = urllib.request.Request(
            sandbox_base.format(site=site).rstrip("/") + "/__state",
            headers={"X-Sandbox-Run": run_id},
        )
        with urllib.request.urlopen(req, timeout=10) as res:  # noqa: S310 (operator-chosen URL)
            states[site] = json.load(res).get("state", {})
    return states


def build_report(tasks: List[Dict[str, Any]], records: Dict[str, Any], agent: str) -> Dict[str, Any]:
    """Score ``{task_id: {"run": {...}, "states": {...}}}`` into a bench report with records."""
    results: Dict[str, Any] = {}
    for task in tasks:
        rec = records.get(task["id"])
        if rec is None:
            continue
        missing = [k for k in RECORD_KEYS if k not in rec["run"]]
        if missing:
            raise SystemExit(f"{task['id']}: run record is missing {missing}")
        res = score_task(task, rec["run"], rec["states"])
        res.update(
            {
                "id": task["id"],
                "title": task["title"],
                "error": None,
                "wall_time_s": float(rec.get("wall_time_s", 0)),
                "cost_microcents": int(rec.get("cost_microcents", 0)),
                "max_cost": task["max_cost"],
                "over_budget": int(rec.get("cost_microcents", 0)) > task["max_cost"],
                "tokens": int(rec.get("tokens", 0)),
                "record": {"run": rec["run"], "states": rec["states"]},
            }
        )
        results[task["id"]] = res
    gate = sorted(
        i
        for i, r in results.items()
        if r["safety"] and (r["safety_violation"] or r["error"] or not r["passed"])
    )
    return {
        "schema": SCHEMA_VERSION,
        "agent": agent,
        "n": len(results),
        "bench_version": sub_mod.fixtures_version(tasks),
        "tasks": results,
        "summary": {
            "total": len(results),
            "passed": sum(1 for r in results.values() if r["passed"]),
            "failed": sorted(i for i, r in results.items() if not r["passed"]),
            "safety_gate_failures": gate,
            "total_cost_microcents": sum(r["cost_microcents"] for r in results.values()),
        },
    }


def _submission_cmd(args: argparse.Namespace) -> int:
    if args.cmd == "package":
        report = _load(args.report)
        if not all("record" in t for t in report["tasks"].values()):
            print("report has no run records", file=sys.stderr)
            return 2
        runner = {
            "label": args.runner_label,
            "kind": args.runner_kind,
            "version": args.runner_version,
        }
        body = sub_mod.build_submission(
            report,
            runner,
            args.generated_at,
            report["bench_version"],
            _load(args.receipts) if args.receipts else None,
            _load(args.replays) if args.replays else None,
        )
        Path(args.out).write_text(json.dumps(body, indent=2, sort_keys=True))
        print(f"packaged {body['n']} tasks (unsigned)")
        return 0

    if args.cmd == "sign":
        key_path = Path(args.key_file)
        if not key_path.exists():
            key_path.write_text(sub_mod.new_signing_key())
            key_path.chmod(0o600)
        signed = sub_mod.sign_submission(_load(args.submission), key_path.read_text().strip())
        Path(args.out).write_text(json.dumps(signed, indent=2, sort_keys=True))
        print(f"signed; public key {signed['public_key']}")
        return 0

    sub = _load(args.submission)
    tasks = load_tasks(Path(args.tasks))
    result = sub_mod.verify_submission(
        sub, tasks, score_task, _load(args.key_set) if args.key_set else None
    )
    if sub.get("bench_version") != sub_mod.fixtures_version(tasks):
        result = {**result, "valid": False, "reason": "bench_version_mismatch"}
    bad = {t: r["reason"] for t, r in result["rows"].items() if not r["verified"]}
    if result["valid"]:
        print(f"verified {len(result['rows'])} tasks")
        if args.out:
            attested = sub_mod.attest(sub, result, args.verified_at)
            Path(args.out).write_text(json.dumps(attested, indent=2, sort_keys=True))
        return 0
    print(f"verification failed: {result['reason']} {bad or ''}", file=sys.stderr)
    return 1


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="delegation-bench", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate", help="check the 25 task fixtures")
    v.add_argument("--tasks", default=str(DEFAULT_TASKS))
    st = sub.add_parser("states", help="read the /__state oracle of every site one task touches")
    st.add_argument("--task", required=True)
    st.add_argument("--tasks", default=str(DEFAULT_TASKS))
    st.add_argument("--sandbox-base", default="http://localhost:8080/s/{site}")
    st.add_argument("--run", required=True, help="sandbox run id the agent used (X-Sandbox-Run)")
    sc = sub.add_parser("score", help="score run records into a report")
    sc.add_argument("--records", required=True, help="JSON {task_id: {run, states, ...}}")
    sc.add_argument("--tasks", default=str(DEFAULT_TASKS))
    sc.add_argument("--agent", required=True, help="name of the agent under test")
    sc.add_argument("--out", required=True)
    pk = sub.add_parser("package", help="turn a report into an unsigned submission")
    pk.add_argument("--report", required=True)
    pk.add_argument("--out", required=True)
    pk.add_argument("--runner-label", required=True)
    pk.add_argument("--runner-kind", choices=sub_mod.RUNNER_KINDS, default="submitted")
    pk.add_argument("--runner-version", default="")
    pk.add_argument("--generated-at", required=True, help="ISO-8601 time of the run")
    pk.add_argument("--receipts", help="JSON file {task_id: [receipt, ...]}")
    pk.add_argument("--replays", help="JSON file {task_id: replay url}")
    sg = sub.add_parser("sign", help="sign a submission with an Ed25519 key (created if absent)")
    sg.add_argument("--submission", required=True)
    sg.add_argument("--key-file", required=True)
    sg.add_argument("--out", required=True)
    vf = sub.add_parser("verify", help="re-run the oracle on a signed submission")
    vf.add_argument("--submission", required=True)
    vf.add_argument("--tasks", default=str(DEFAULT_TASKS))
    vf.add_argument("--key-set", help="receipt key set JSON")
    vf.add_argument("--verified-at", default="", help="ISO-8601 time to record")
    vf.add_argument("--out", help="write the attested bundle here when every row verifies")
    d = sub.add_parser("diff", help="compare two reports")
    d.add_argument("before")
    d.add_argument("after")
    d.add_argument("--fail-on-regression", action="store_true")
    args = p.parse_args(argv)

    if args.cmd == "validate":
        tasks = load_tasks(Path(args.tasks))
        missing = sorted(set(EXPECTED_IDS) - {t["id"] for t in tasks})
        if missing:
            print(f"missing fixtures: {missing}", file=sys.stderr)
            return 1
        print(f"{len(tasks)} fixtures valid (bench version {sub_mod.fixtures_version(tasks)})")
        return 0
    if args.cmd == "states":
        task = next((t for t in load_tasks(Path(args.tasks)) if t["id"] == args.task), None)
        if task is None:
            print(f"unknown task {args.task}", file=sys.stderr)
            return 2
        try:
            print(json.dumps(fetch_states(task, args.sandbox_base, args.run), indent=2))
        except (urllib.error.URLError, OSError) as exc:
            print(f"could not read the sandbox: {exc}", file=sys.stderr)
            return 1
        return 0
    if args.cmd == "score":
        report = build_report(load_tasks(Path(args.tasks)), _load(args.records), args.agent)
        Path(args.out).write_text(json.dumps(report, indent=2, sort_keys=True))
        s = report["summary"]
        print(
            f"{s['passed']}/{s['total']} passed; safety gate failures: "
            f"{s['safety_gate_failures'] or 'none'}"
        )
        return exit_code(report)
    if args.cmd in ("package", "sign", "verify"):
        return _submission_cmd(args)
    out = diff_reports(_load(args.before), _load(args.after))
    print(json.dumps(out, indent=2))
    return 1 if args.fail_on_regression and out["regressions"] else 0
