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
kill-switch check. There is no path around any of them. Order submission,
strategy specification, backtesting and risk review are still triggered only
by an authenticated operator through the console — Phase 4 is the first
agent in the system, and it is scoped narrowly to research: it can propose
findings, never place an order, specify a strategy, or move a project's
state.

### Backtest and acceptance engine (Phase 2)

`app.core.backtest.simulate_sma_crossover` is a long-only SMA crossover
simulator: a bullish cross (fast SMA above slow) opens a full position, a
bearish cross closes it, and — to avoid look-ahead bias — a cross confirmed
using data through bar `i` executes at bar `i + 1`'s open, not bar `i`'s
close. Equity is tracked as a base-100 index so `total_return_pct` includes
mark-to-market on a position still open when the window ends, not just
closed trades.

Out-of-sample split is a fixed 70/30 by bar count: the last 30% of bars are
"out-of-sample," and a trade counts there if its entry falls on or after that
split date. `app.core.backtest.evaluate_acceptance` accepts a backtest iff:

1. the out-of-sample trade count clears the strategy's own
   `minimum_out_of_sample_trades` (a field on `Strategy`, not a global
   default — see the README's roadmap section for why), **and**
2. `total_return_pct` beats `benchmark_return_pct` (buy-and-hold over the
   same window).

### Risk engine (Phase 2)

`app.core.risk` has two entry points, both pure functions:

- `assess_strategy` — run at the `RISK_REVIEW` stage. Checks the strategy's
  symbol/timeframe against `allowed_symbols`/`allowed_timeframes`, and that
  `fast_window < slow_window`.
- `assess_order` — run immediately before every `submit_order` call, using
  live account/position data from whichever broker is active. Checks the
  symbol allowlist, `max_orders_per_day` (counted from that project's `Order`
  rows today), `max_open_positions` (opening a new symbol beyond the limit is
  refused), `max_position_percentage`/`max_total_exposure_percentage` against
  the proposed order's notional, buying power vs `allow_leverage`, and
  short-selling vs `allow_shorting`.

**Known limitation**: `max_daily_loss_percentage` is not enforced by
`assess_order` — it would need day-start equity tracking that doesn't exist
yet. See [docs/security.md](docs/security.md)'s "Known limitations."

### Broker adapters (Phase 2 mock, Phase 3 Alpaca)

`app.brokers.alpaca.AlpacaPaperBroker` and `app.brokers.mock.MockBroker` both
implement `get_account`, `list_positions`, `list_open_orders` and
`submit_order` against the same `BrokerAdapter` interface —
`QUANTLAB_BROKER_BACKEND` (`mock` by default, or `alpaca`) selects which one
`app.deps.get_broker` constructs, and the choice is explicit, never inferred
from credential presence (a typo'd Alpaca key must fail start-up, not
silently degrade to mock simulation).

`MockBroker` holds no external state of its own — it reads and writes the
account-wide `mock_fills` table directly, filling orders synchronously at the
latest cached `market_bars` price (or refusing with `BrokerOrderRejectedError`
if none is cached). `AlpacaPaperBroker` calls the real (paper) API instead.
Either way, `POST /projects/{id}/orders` is the only route that calls
`submit_order`, and only after `risk.assess_order` approves — this is what
makes the broker adapter's "never receive an unapproved order" guarantee
real rather than aspirational.

The project's own internal ledger — the `orders` table, written by the route
after a successful `submit_order` call, never by an adapter directly — is
also what `/broker/reconcile` (Phase 3) diffs broker-reported positions/open
orders against to compute `untracked_position_count`/`untracked_order_count`,
closing the gap Phase 3 originally documented ("no ledger to diff against").

### Research agent (Phase 4)

`app.agents.claude.ClaudeResearchAgent` is the only module in the codebase
that calls an LLM. It has zero imports of `app.db` or SQLAlchemy, holds no
session, and its `research(question)` method returns a `ResearchProposal` —
a plain Pydantic object, never a database row. The route layer
(`app/api/routes_research.py`, ordinary reviewed Python) is what turns a
proposal into `ResearchFinding`/`Citation` rows.

**The citation rule is structural, not conventional.** `ClaimProposal`'s own
`field_validator` refuses to construct a claim with an empty `citations`
list — a bug in the parsing code that builds a proposal cannot smuggle an
uncited assertion through, because the object literally cannot exist without
at least one citation. Citations themselves come from Claude's own
`web_search_20260209` server tool: `TextBlock.citations` is populated by the
API itself when a text segment is grounded in a search result, rather than
asking the model to self-report sources in a separate, unverified structure.
Text segments with no citations are dropped, not persisted as claims — the
count of dropped segments travels with the proposal
(`discarded_uncited_segment_count`) so the route can audit that they existed
instead of the discard being silent.

**Every finding starts `status="pending"`.** Nothing downstream — no other
route, no future automation — may treat a finding as trustworthy until a
human operator explicitly accepts or rejects it via
`/projects/{id}/findings/{id}/review`. This bounds the worst case of a
prompt-injection payload on a fetched web page: at most a misleading
*pending* finding an operator must knowingly accept, never an automatic
action, since the agent module holds no path to the database or the broker
regardless of what a poisoned page tells it to do.

**Two response states need explicit handling before the response can be
trusted.** `stop_reason == "refusal"` (the API's own safety classifier
declined) is checked before `response.content` is touched at all, since a
refusal's content may be empty. `stop_reason == "pause_turn"` (the
server-side web-search tool hit its internal iteration cap) is resumed by
resending the conversation with the assistant's partial turn appended, up to
three times, per the documented resume pattern — not treated as a failure.

**No mock backend.** Unlike the broker (`MockBroker`) or market data
(cached bars), there is no coherent "simulate a paid third-party LLM for
free" substitute. `app.deps.get_research_agent` returns `None` when
`QUANTLAB_ANTHROPIC_API_KEY` is unset, and `POST /projects/{id}/research`
returns 409 in that case — absence-means-unavailable is the honest default,
not a gap to fill later.

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
│   ├── audit.py         Hash-chained append-only log.
│   ├── backtest.py      SMA crossover simulation + acceptance rule.
│   └── risk.py          Strategy- and order-level risk checks.
├── brokers/           BrokerAdapter + AlpacaPaperBroker + MockBroker.
├── marketdata/        MarketDataProvider + YFinanceProvider + the bar cache.
├── agents/            ResearchAgent + ClaudeResearchAgent. No DB import.
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
