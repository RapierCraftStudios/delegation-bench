"""Open Delegation Bench submission signing and verification.

A *submission* is the bench report plus, per task, the run record the scorer needs (the agent's
accepted/asked/write facts) and the side-effect states read from the sandbox ``/__state`` oracle.
It is signed by the submitter's Ed25519 key over its canonical JSON. Verification re-runs the
oracle (the injected ``scorer``, ``scorer.score_task``) on the submitted states and
compares the outcome with the reported one, so a doctored result fails. Optional Hands receipts
(#39829) must verify against the published receipt key set.

Publication rules: every row that was verified is published, failures included; a row without a
verifiable run record is never published. Herald only re-checks the signature and the run-record
hashes of an already-verified bundle (``check_attested``); it cannot run the oracle itself.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime
from typing import Any, Callable, Dict, List, Mapping, Optional

SCHEMA_VERSION = 1
STAGES = ("find", "verify", "execute")
RUNNER_KINDS = ("first_party", "submitted")
_RUNNER_KEYS = ("label", "kind", "version")
_RECEIPT_ENVELOPE = ("id", "task_id", "step_id", "site", "action_types", "verdict", "supersedes")

Scorer = Callable[[Dict[str, Any], Dict[str, Any], Dict[str, Dict[str, Any]]], Dict[str, Any]]


def canonical(value: Any) -> bytes:
    """Bytes that are hashed and signed (same canonical form as Hands receipts)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64u_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def run_record_hash(run: Any, states: Any) -> str:
    return hashlib.sha256(canonical({"run": run, "states": states})).hexdigest()


def fixtures_version(tasks: List[Dict[str, Any]]) -> str:
    """Stable id of the task set a result was produced against."""
    digest = hashlib.sha256(canonical(sorted(tasks, key=lambda t: t["id"]))).hexdigest()
    return digest[:12]


# --------------------------------------------------------------------------- build and sign


def build_submission(
    report: Mapping[str, Any],
    runner: Mapping[str, Any],
    generated_at: str,
    bench_version: str,
    receipts: Optional[Mapping[str, List[Any]]] = None,
    replays: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    """Unsigned submission from a report whose tasks carry a ``record`` (``score`` writes them)."""
    tasks: Dict[str, Any] = {}
    runs: Dict[str, Any] = {}
    states: Dict[str, Any] = {}
    for tid, row in report["tasks"].items():
        row = dict(row)
        record = row.pop("record", None)
        tasks[tid] = row
        if record:
            runs[tid], states[tid] = record["run"], record["states"]
    return {
        "schema": SCHEMA_VERSION,
        "runner": {k: runner.get(k) for k in _RUNNER_KEYS},
        "generated_at": generated_at,
        "bench_version": bench_version,
        "n": len(tasks),
        "tasks": tasks,
        "runs": runs,
        "states": states,
        "receipts": dict(receipts or {}),
        "replays": dict(replays or {}),
    }


def new_signing_key() -> str:
    from cryptography.hazmat.primitives import serialization  # noqa: PLC0415
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: PLC0415

    raw = Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    return _b64u(raw)


def _public_of(private: Any) -> str:
    from cryptography.hazmat.primitives import serialization  # noqa: PLC0415

    return _b64u(
        private.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
    )


def sign_submission(body: Mapping[str, Any], seed_b64u: str) -> Dict[str, Any]:
    """Return ``body`` with ``public_key`` and ``signature`` (hex) over the rest."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: PLC0415

    private = Ed25519PrivateKey.from_private_bytes(_b64u_decode(seed_b64u))
    unsigned = {k: v for k, v in body.items() if k not in ("public_key", "signature")}
    unsigned["public_key"] = _public_of(private)
    return {**unsigned, "signature": private.sign(canonical(unsigned)).hex()}


def _signature_ok(sub: Mapping[str, Any]) -> bool:
    from cryptography.exceptions import InvalidSignature  # noqa: PLC0415
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey  # noqa: PLC0415

    try:
        unsigned = {k: v for k, v in sub.items() if k not in ("signature", "verification")}
        Ed25519PublicKey.from_public_bytes(_b64u_decode(sub["public_key"])).verify(
            bytes.fromhex(sub["signature"]), canonical(unsigned)
        )
        return True
    except (InvalidSignature, ValueError, KeyError, TypeError):
        return False


# --------------------------------------------------------------------------- verification


def verify_hands_receipt(receipt: Mapping[str, Any], key_set: Mapping[str, Any]) -> bool:
    """Offline check of one Hands receipt, same rules as ``alterlab.hands_receipts``."""
    from cryptography.exceptions import InvalidSignature  # noqa: PLC0415
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey  # noqa: PLC0415

    try:
        bundle, signed = receipt["bundle"], receipt["signed"]
        if receipt.get("alg") != "ed25519":
            return False
        if hashlib.sha256(canonical(bundle)).hexdigest() != receipt.get("bundle_hash"):
            return False
        if signed.get("bundle") != bundle:
            return False
        if any(signed.get(f) != receipt.get(f) for f in _RECEIPT_ENVELOPE):
            return False
        key = next(
            (k for k in key_set.get("keys", []) if k.get("kid") == receipt.get("key_version")),
            None,
        )
        if key is None or key.get("alg") != "Ed25519":
            return False
        Ed25519PublicKey.from_public_bytes(_b64u_decode(key["public_key"])).verify(
            bytes.fromhex(receipt["signature"]), canonical(signed)
        )
        return True
    except (InvalidSignature, ValueError, KeyError, TypeError):
        return False


def _stage_view(stages: Any) -> Any:
    return {s: (stages or {}).get(s) for s in STAGES}


def _row_reason(
    sub: Mapping[str, Any],
    tid: str,
    task: Optional[Dict[str, Any]],
    scorer: Scorer,
    key_set: Optional[Mapping[str, Any]],
) -> Optional[str]:
    """Why a row cannot be published, or None when its run record verifies."""
    if task is None:
        return "unknown_task"
    run, states = sub.get("runs", {}).get(tid), sub.get("states", {}).get(tid)
    if run is None or states is None:
        return "no_run_record"
    reported = sub["tasks"][tid]
    try:
        scored = scorer(task, run, states)
    except Exception:  # noqa: BLE001 - a record the scorer cannot read is not a record
        return "unscorable_record"
    if scored["passed"] != reported.get("passed") or _stage_view(scored["stages"]) != _stage_view(
        reported.get("stages")
    ):
        return "oracle_mismatch"
    receipts = sub.get("receipts", {}).get(tid) or []
    if receipts and not (key_set and all(verify_hands_receipt(r, key_set) for r in receipts)):
        return "receipt_invalid"
    return None


def verify_submission(
    sub: Mapping[str, Any],
    tasks: List[Dict[str, Any]],
    scorer: Scorer,
    key_set: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Verify signature and every row. ``valid`` only when the signature and all rows hold."""
    if sub.get("schema") != SCHEMA_VERSION or not isinstance(sub.get("tasks"), dict):
        return {"valid": False, "reason": "malformed", "rows": {}}
    if (sub.get("runner") or {}).get("kind") not in RUNNER_KINDS:
        return {"valid": False, "reason": "bad_runner", "rows": {}}
    if not _signature_ok(sub):
        return {"valid": False, "reason": "bad_signature", "rows": {}}
    by_id = {t["id"]: t for t in tasks}
    rows = {}
    for tid in sub["tasks"]:
        reason = _row_reason(sub, tid, by_id.get(tid), scorer, key_set)
        rows[tid] = {"verified": reason is None, "reason": reason}
    ok = all(r["verified"] for r in rows.values()) and bool(rows)
    return {"valid": ok, "reason": None if ok else "row_failed", "rows": rows}


def attest(sub: Mapping[str, Any], result: Mapping[str, Any], verified_at: str) -> Dict[str, Any]:
    """Attach the oracle re-run outcome so a later reader can re-check it without the scorer."""
    records = {
        tid: run_record_hash(sub["runs"][tid], sub["states"][tid])
        for tid, r in result["rows"].items()
        if r["verified"]
    }
    return {
        **sub,
        "verification": {"oracle_rescored": True, "verified_at": verified_at, "records": records},
    }


def check_attested(sub: Mapping[str, Any]) -> Dict[str, str]:
    """Verified rows of an attested bundle: ``{task_id: run_record_hash}``; ``{}`` if untrusted.

    Re-checks the submitter signature and that every attested hash still matches the run and
    state records, so any edit made after verification drops the bundle.
    """
    ver = sub.get("verification")
    if not isinstance(ver, dict) or ver.get("oracle_rescored") is not True:
        return {}
    if sub.get("schema") != SCHEMA_VERSION or not _signature_ok(sub):
        return {}
    good: Dict[str, str] = {}
    for tid, digest in (ver.get("records") or {}).items():
        run, states = sub.get("runs", {}).get(tid), sub.get("states", {}).get(tid)
        if tid in sub.get("tasks", {}) and run is not None and states is not None:
            if run_record_hash(run, states) == digest:
                good[tid] = digest
    return good


# --------------------------------------------------------------------------- publication


def _loss_reasons(row: Mapping[str, Any]) -> List[str]:
    if row.get("error"):
        return ["error"]
    return [s for s in STAGES if (row.get("stages") or {}).get(s) and not row["stages"][s]["pass"]]


def build_run(sub: Mapping[str, Any], good: Mapping[str, str]) -> Optional[Dict[str, Any]]:
    """One published run from the verified rows of an attested bundle (None when none verify)."""
    tasks = []
    for tid in sorted(good):
        row = sub["tasks"][tid]
        tasks.append(
            {
                "id": tid,
                "title": row.get("title"),
                "safety": bool(row.get("safety")),
                "passed": bool(row.get("passed")),
                "stages": {s: (row.get("stages") or {}).get(s) for s in STAGES},
                "losses": _loss_reasons(row),
                "cost_microcents": row.get("cost_microcents"),
                "wall_time_s": row.get("wall_time_s"),
                "run_record": good[tid],
                "replay_url": (sub.get("replays") or {}).get(tid),
            }
        )
    if not tasks:
        return None
    runner = sub["runner"]
    return {
        "runner": {k: runner.get(k) for k in _RUNNER_KEYS},
        "generated_at": sub["generated_at"],
        "bench_version": sub["bench_version"],
        "n": len(tasks),
        "passed": sum(1 for t in tasks if t["passed"]),
        "tasks": tasks,
    }


def build_artifact(
    runs: List[Dict[str, Any]], generated_at: str, changelog: Optional[List[Any]] = None
) -> Dict[str, Any]:
    """Public document: first-party runs first, then submissions, newest first within each."""
    ordered = sorted(runs, key=lambda r: r["generated_at"], reverse=True)
    ordered.sort(key=lambda r: r["runner"]["kind"] != "first_party")
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "runs": ordered,
        "changelog": list(changelog or []),
    }


def artifact_valid(doc: Any) -> bool:
    """Structural gate for a stored artifact: every published row carries its run record."""
    try:
        if doc.get("schema_version") != SCHEMA_VERSION or not isinstance(doc["runs"], list):
            return False
        datetime.fromisoformat(doc["generated_at"])
        for run in doc["runs"]:
            if run["runner"]["kind"] not in RUNNER_KINDS or not run["runner"].get("label"):
                return False
            if not run["tasks"] or any(not t.get("run_record") for t in run["tasks"]):
                return False
        return True
    except (AttributeError, KeyError, TypeError, ValueError):
        return False


def score_moves(prev: Optional[Mapping[str, Any]], cur: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Changes in the first-party run's pass count and per-task outcome versus the last artifact."""

    def first(doc: Optional[Mapping[str, Any]]) -> Optional[Mapping[str, Any]]:
        return next(
            (r for r in (doc or {}).get("runs", []) if r["runner"]["kind"] == "first_party"), None
        )

    now, before = first(cur), first(prev)
    if now is None or before is None:
        return []
    moves: List[Dict[str, Any]] = []
    old = {t["id"]: t["passed"] for t in before["tasks"]}
    for t in now["tasks"]:
        if t["id"] in old and old[t["id"]] != t["passed"]:
            moves.append({"task": t["id"], "change": "improved" if t["passed"] else "regressed"})
    if now["passed"] != before["passed"]:
        moves.insert(0, {"passed_before": before["passed"], "passed_after": now["passed"]})
    return moves
