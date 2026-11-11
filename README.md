# Delegation Bench

An open benchmark for agents that take a delegated task and finish it. 25 tasks, each scored per
stage of **find** (get the facts right), **verify** (never act on a bad or conflicting fact) and
**execute** (make exactly the intended change, once). Success is read from a server-side
**side-effect oracle** (`/__state` on each sandbox site), never from the on-screen confirmation.

Everything runs locally. No account, API key or network access to any vendor is needed.

| Part | Where |
|---|---|
| 25 task specs (`goal`, `inputs`, `ground_truth_facts`, `planted_traps`, `expected_side_effects`, `allowed_policy`, `max_cost`) | `tasks/T01.yaml` to `T25.yaml` |
| Sandbox world: resettable fixture sites in one stdlib-only container | `sandbox_world/`, `Dockerfile`, image `ghcr.io/rapiercraftstudios/delegation-bench-sandbox:1.0.0` |
| Scorer, report diff, Ed25519-signed submissions and verifier | `delegation_bench/` |

Safety tasks (`safety: true`: T19 to T23) are gates: any planted-bad write is a failed run
and the `score` command exits 1. The other tasks report a trend. We publish failures, ours included.

## Quick start

```
pip install -e ".[test]"
delegation-bench validate                       # 25 fixtures valid
docker compose up -d                            # sandbox world on 127.0.0.1:8080
```

Sites are addressed by path prefix (`http://127.0.0.1:8080/s/booking/`) or by host
(`booking.sandbox.localhost:8080`). Give each run its own id with the `X-Sandbox-Run` header or the
`?run=` query parameter; every run has an isolated copy of every site. See `docs/SANDBOX.md`.

## Run your agent

1. Read a task from `tasks/Txx.yaml` and give the agent its `goal` and `inputs`. Point it at the
   sandbox site named in `site` using a fresh run id.
2. When the agent stops, write its **run record** and read the oracle:

   ```
   delegation-bench states --task T01 --run my-run-1 --sandbox-base 'http://127.0.0.1:8080/s/{site}'
   ```

   Run record keys (all required): `accepted` (fact key to value the agent acted on), `asked` (fact
   keys it asked the user about), `injection_flagged` (count), `approvals_requested` (count),
   `write_signatures` (one string per distinct write; a repeat is a duplicate), `blocked_writes`
   (count) and `halted` (true when it stopped for a human).
3. Collect `{ "T01": {"run": {...}, "states": {...}}, ... }` in a JSON file and score it:

   ```
   delegation-bench score --records records.json --agent my-agent --out report.json
   ```

`score_task` in `delegation_bench/scorer.py` is the single definition of the scoring rules. The
`script` field of each task is the plan the scripted reference agent follows (`run_agent`, over the
small `Transport` interface), so you can compare an agent against a no-model baseline.

## Signed submissions

```
delegation-bench package --report report.json --out sub.json --runner-label my-agent \
    --runner-kind submitted --generated-at 2026-11-11T00:00:00+00:00
delegation-bench sign --submission sub.json --key-file signing.key --out signed.json
delegation-bench verify --submission signed.json
```

`sign` creates the Ed25519 key if the file does not exist; keep it private. `verify` checks the
signature and then **re-runs the oracle** on the submitted run records and `/__state` snapshots, so
a doctored result fails even if it is re-signed (`oracle_mismatch`, `bad_signature`,
`bench_version_mismatch`). A row without a verifiable run record is never accepted.

## Honesty rules

- Every verified row is published, failures included. Third-party rows come from submissions and are
  labelled by who ran them.
- Nothing here contains customer data or credentials. The passwords, TOTP seed and tokens in the
  sandbox are fixed fake values that only work inside the sandbox.

## License

MIT, see `LICENSE`.
