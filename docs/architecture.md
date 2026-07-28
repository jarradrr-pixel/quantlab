# Architecture

## Trust model

The organising principle is that **language models never hold authority**. They
produce structured proposals; deterministic code decides.

| Actor | May do | May never do |
|---|---|---|
| LLM agent | Emit validated Pydantic proposals | Hold credentials, write to the database, call the broker, execute shell commands, run raw SQL, approve anything |
| Orchestrator | Move a project between states | Skip a stage, bypass a guard |
| Risk engine | Approve or refuse an order intent | Be overridden by a model |
| Acceptance engine | Pass or fail a backtest | Be overridden by a model |
| Broker adapter | Submit approved orders to a paper account | Receive an unapproved order, hold a live endpoint |
| Operator (human) | Approve, reject, pause, halt | — |

An agent's output reaching the broker requires passing through the schema
validator, the acceptance engine, a human approval, the risk engine and the
kill-switch check. There is no path around any of them.

### Broker adapter (Phase 3)

`app.brokers.alpaca.AlpacaPaperBroker` implements `get_account`,
`list_positions`, `list_open_orders` and `submit_order` against Alpaca's paper
API, and is unit-tested directly. **No route calls `submit_order`.** The risk
engine that would approve an order before it reaches a broker is Phase 2 work
and does not exist yet, so nothing in the console can place an order today —
only verify the connection (`/broker/verify`) and read back positions/open
orders (`/broker/reconcile`). A later phase wires the risk engine's approved
output into `submit_order`.

## Layers

```
app/
├── config.py          Settings. Validates at start-up and refuses ambiguity.
├── logging_config.py  JSON logs; redaction runs on every record.
├── security.py        Argon2id, signed sessions, CSRF.
├── rate_limit.py      Sliding-window login throttle.
├── db/                Engine, session scope, ORM models.
├── core/
│   ├── states.py        The transition table (data, not branches).
│   ├── state_machine.py Orchestrator: table check, guards, audit, commit.
│   └── audit.py         Hash-chained append-only log.
├── brokers/           Broker adapters (Alpaca paper only; see Phase 3 note above).
├── api/               HTTP routes. Thin; no domain logic.
└── web/               Jinja2 console.
```

Domain logic sits in `core/` and is exercised directly by unit tests without an
HTTP client. Routes translate between HTTP and the domain and nothing else,
which is what makes swapping in a React frontend later a routing change rather
than a rewrite.

## The audit chain

Each entry stores `previous_hash` and `entry_hash`, where the digest covers the
sequence number, category, action, actor, project, redacted payload, previous
hash and timestamp, serialised as canonical JSON with sorted keys.

Two properties follow. Editing a payload changes its digest, so verification
fails at that entry. Deleting a row leaves a sequence gap, which verification
also catches. `/audit` replays the chain on every page load and shows the
result.

Timestamps are normalised to UTC before hashing. SQLite has no timezone-aware
column type, so a row written as aware returns naive; without normalisation the
chain would verify on PostgreSQL and fail on SQLite.

Redaction happens *before* hashing, so the chain commits to the redacted form.
A secret that never entered the log cannot be recovered from it.

## Guards

Table membership is necessary but not sufficient. Transitions into
`PAPER_TRADING` additionally require:

1. the kill switch released — an **absent** flag row is read as engaged, so a
   half-initialised database halts rather than trades;
2. an `approvals` row with `decision = 'approved'` and
   `kind = 'enter:PAPER_TRADING'`, written by an authenticated operator.

A blocked transition is itself audited, so refusals are as visible as
successes.

## Fail-closed behaviours

| Situation | Response |
|---|---|
| Kill-switch row missing | Treated as engaged |
| First boot | Kill switch engaged |
| Unknown setting in the environment | Start-up fails |
| `QUANTLAB_TRADING_MODE` not `paper` | Start-up fails |
| Empty symbol allowlist | Start-up fails |
| Position limit above exposure limit | Start-up fails |
| Session cookie tampered or expired | Treated as signed out |
| Database error mid-request | Rolled back, generic message returned |
