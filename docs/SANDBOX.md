# Hands sandbox world

One stdlib-only container (`Dockerfile`, `docker-compose.yml`) hosting many
resettable fixture sites for the delegation benchmark and the public demo.

```
docker compose -f docker-compose.hands-e2e.yml up --build
python -m sandbox_world --port 8080        # from the repo root, no container
```

## Addressing

- Site by host: `<site>.sandbox.localhost` / `<site>.sandbox.alterlab.io`, or by path prefix `/s/<site>/`.
- Run (tenant): `X-Sandbox-Run: <id>` header, `sbx_run` cookie or `?run=`. Each run has an isolated copy of every
  site; `POST /__reset` clears it and idle runs reset after 15 minutes. A run id is `[A-Za-z0-9_-]{1,64}`.
- Every HTML page carries the sandbox banner and `noindex`; every response has `X-Robots-Tag`.

## `/__state` (side-effect oracle)

`GET /__state` returns `{"run", "site", "state"}`, the server-side truth the benchmark scores against. It is open
in local e2e. With `HANDS_SANDBOX_PUBLIC=1` it answers 404 unless `X-Sandbox-State-Token` equals
`HANDS_SANDBOX_STATE_TOKEN` (and always 404 when no token is configured), so only authorised demo runs read it.

## Sites

`booking` (calendar, JSON API), `subscriptions` (retention flow, plans, renewal), `support` (tickets, mail stub),
`shop-a|b|c` (price, shipping, stock differ; `/tick` drops price), `vendors` (quotes), `invoices` (PDF + drive),
`accounts` (eight accounts, one transient failure), `govform` (multi-page, PDF upload, save/resume, human-only
`/sign` and `/video`), `taxform`, `bank` (password + TOTP + emailed code), `signup` (email verification),
`mail` (mailbox stub), `mailbot` (counterparty negotiation), `typeform`, `traps` (injection, consent
interstitial, conflicting facts, mid-run layout change). `sandbox_world/tasks.py` maps benchmark tasks T01-T25 to
their sites and oracle keys.

## Demo traps (`demo_*` runs only)

The public demo uses run ids `demo_*`. Only those tenants get deterministic traps, so the benchmark
(T01-T25) and the hands-e2e stack are unchanged (`Ctx.demo`):

- `booking`: the first `POST /api/appointments` for a free slot loses a race. Another customer's booking lands
  first, the API still answers `201 "Booked!"`, and `booked_by_you` stays empty.
- `subscriptions`: `POST /cancel/confirm` without `decline_offer=yes` redirects to `/cancelled` ("Your plan is
  cancelled") but keeps the plan (`status: active`, `retention_accepted: true`).
- `support`: a ticket is filed against the latest order (`4822`) while its page echoes the typed order id;
  `POST /tickets/latest/order` re-files it.

The demo runner reads each result back from `/__state`, re-plans when it does not match (`recovery` in
`shared/hands/demo_catalogue.yaml`) and reads back again.

No site opens an outbound connection; mail is an in-memory stub.
