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
kill-switch check. There is no path around any of them. Order submission and
risk review are still triggered only by an authenticated operator through
the console. Phase 4 added the first agent in the system, scoped narrowly to
research; Phase 5 adds three more (knowledge test, hypothesis, strategy
code), each still scoped to one proposal type with no path to the database,
the broker, or the state machine. None of the four agents can place an
order, move a project's state, or accept its own output as trustworthy.

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
  the proposed order's notional, buying power vs `allow_leverage`,
  short-selling vs `allow_shorting`, and `max_daily_loss_percentage` against
  the day's tracked equity.

**Day-start equity, for the daily-loss check.** `assess_order` stays pure —
it takes `day_start_equity` as a plain argument rather than touching the
database itself. `submit_project_order` supplies it: on a project's first
order of the UTC calendar day, it creates a `DailyEquityMark` row (account-
wide, like `BrokerAccountSnapshot`) recording the currently-observed
`account.portfolio_value` as that day's baseline; later orders the same day
reuse the same row. There is no scheduled market-open snapshot to read
instead, so "the first equity value observed today" is the only available
definition. The check only ever refuses a `buy`, deliberately — the same
asymmetry as the leverage (buy-only) and shorting (sell-only) checks above:
a circuit breaker should stop opening new risk once the day has already
gone bad, not block an operator from closing a losing position to cut
further loss.

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

### Knowledge test, hypothesis and code generation agents (Phase 5)

Three more single-purpose agents, each following the same shape as the
research agent: a plain Pydantic proposal in, no database session, no
credentials beyond the shared Anthropic API key. Unlike the research agent,
none of the three use the `web_search_20260209` tool — they are closed-book,
reasoning only over context the route layer assembles from already-persisted
rows, never fetching anything new themselves:

- `KnowledgeTestAgent.test(question, findings)` → `KnowledgeTestProposal`
  (`verdict`, `reasoning`, `cited_finding_ids`) — a closed-book check of
  whether a question is supported by a project's *accepted*
  `ResearchFinding` rows (`app/api/routes_pipeline.py` only passes findings
  with `status="accepted"`, never pending or rejected ones).
- `HypothesisAgent.propose(objective, findings, prior_tests)` →
  `HypothesisProposal` (`statement`, `rationale`, `cited_finding_ids`) —
  synthesizes the project's research objective, accepted findings and prior
  knowledge-test verdicts into one testable hypothesis.
- `StrategyCodeAgent.generate(hypothesis_statement, symbol=, timeframe=,
  current_fast_window=, current_slow_window=)` → `StrategySpecProposal`
  (`fast_window`, `slow_window`, `minimum_out_of_sample_trades`,
  `rationale`) — proposes refined SMA-crossover windows for the *existing*
  `Strategy` row's symbol/timeframe. It cannot choose the symbol or
  timeframe; those fields don't exist on the proposal, so there is nothing
  for the model to get wrong there.

Both `KnowledgeTestProposal.cited_finding_ids` and
`HypothesisProposal.cited_finding_ids` share `ClaimProposal`'s structural
pattern: a `field_validator` refuses to construct either object with an
empty citation list, so an unsupported verdict or hypothesis cannot reach a
route.

**Structured output, not prose parsing.** All three call
`client.messages.parse(..., output_format=<the proposal class>)` rather than
`client.messages.create(...)` plus manual JSON parsing — the SDK validates
the response against the Pydantic schema itself
(`app/agents/_common.py:call_claude_structured`), so a malformed response
raises before a route ever sees it, instead of a partially-parsed proposal
reaching persistence.

**No code executes, so there is no sandbox.** `StrategySpecProposal` is a
parameterized DSL — four numbers and a rationale — not a code string. This
was a deliberate scope decision over the sandboxed-Docker-execution
alternative `docs/security.md` originally floated (see the README's roadmap
section for the trade-off). `app.core.codegen.validate_strategy_spec` is the
deterministic gate: `StrategySpecProposal`'s own validator already makes
`fast_window <= 0`, `slow_window <= 0` and `slow_window <= fast_window`
impossible to construct, so `validate_strategy_spec` only checks what
construction does *not* already guarantee — an upper bound on window size
and a non-negative trade-count threshold. `/projects/{id}/validate-code`
creates a new `Strategy` version (`created_by="agent:claude-opus-5"`) only
when this deterministic check passes; an invalid proposal is recorded
(`GeneratedStrategyCode.validated=False`) but produces no new `Strategy` row
at all. Symbol/timeframe allowlist checks are not this validator's job
either — `app.core.risk.assess_strategy` still checks those later, at
`RISK_REVIEW`, exactly as it does for a manually-specified strategy.

**Engine-run records, not a second review workflow.** `KnowledgeTest`,
`Hypothesis` and `GeneratedStrategyCode` do not have their own accept/reject
route the way `ResearchFinding` does — they behave like `Backtest`/
`RiskAssessment` from Phase 2: a persisted verdict an operator reads before
deciding whether to advance the state machine. Adding a second review layer
here would duplicate the trust gate that already exists further downstream
(risk review, human approval, kill switch) without changing what it
protects against.

### Performance engine & experiment tracking (Phase 6)

`app.core.performance` is pure Python, same shape as `app.core.risk`: no
network, no database, no agent. `compute_order_performance(orders)` realizes
profit and loss from a strategy version's own filled `Order` rows — never
from the broker's account snapshot — by FIFO-matching each symbol's buy
fills against later sell fills (oldest lot first). Each sell that closes at
least part of an open lot is one "trade"; its realized P&L is
`(sell_price - matched_buy_price) * matched_qty`, summed across every lot it
consumes. `evaluate_performance(result)` then mirrors `risk.py`'s
accumulator-verdict pattern (`reasons: list[str]`, `accepted = not reasons`)
rather than `backtest.py`'s single-`reason` style, since multiple
conditions can hold at once: accepted iff `trade_count > 0` **and**
`realized_pnl > 0` — hardcoded, no new `Settings` field, the same
proportionality as `backtest.py`'s hardcoded benchmark-beat rule.

**Two things this engine deliberately does not model.** A sell fill with no
matching open lot (e.g. a short — which `risk.assess_order` already
disallows by default) is excluded from realized P&L rather than modeled;
there is no short-position accounting here. And `max_drawdown` is a dollar
figure, not a percentage — there is no fixed capital base in the order
ledger to divide by, unlike `backtest.py`'s base-100-indexed equity curve
which does have one.

`POST /projects/{id}/performance-review` (`app/api/routes_performance.py`)
mirrors `run_risk_review` exactly: pull the current strategy version's
orders, run the engine, persist a `PerformanceReview` row, audit
`AuditCategory.PERFORMANCE`. No FSM-state check in the route itself — same
precedent as every other engine-run route in this codebase (`run_backtest`,
`run_risk_review`, `run_research`, none of which 409 on the wrong state);
only `submit_project_order` does, because unlike a review, it is
money-adjacent and cannot rely on the UI alone to stay safe.

**Experiment tracking and strategy versioning reuse existing foreign keys —
no new join table.** `Backtest.strategy_id`, `Order.strategy_id` and
`PerformanceReview.strategy_id` already scope those rows to one strategy
version; `RiskAssessment` has no `strategy_id` of its own, only
`backtest_id`, so its rows are reached by joining through
`Backtest.strategy_id` instead.
`GET /projects/{id}/experiments` groups all of it by strategy version,
newest first, giving "strategy versioning" a real history view instead of
`project.html`'s "latest version only" display, with no new model needed.

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
│   ├── risk.py          Strategy- and order-level risk checks.
│   ├── codegen.py       Deterministic StrategySpecProposal validation.
│   └── performance.py   FIFO P&L realization + acceptance verdict.
├── brokers/           BrokerAdapter + AlpacaPaperBroker + MockBroker.
├── marketdata/        MarketDataProvider + YFinanceProvider + the bar cache.
├── agents/            ResearchAgent, KnowledgeTestAgent, HypothesisAgent,
│                       StrategyCodeAgent + Claude implementations. No DB import.
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
