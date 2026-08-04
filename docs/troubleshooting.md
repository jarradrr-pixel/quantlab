# Troubleshooting

**`ValidationError: Extra inputs are not permitted`**
An unknown `QUANTLAB_*` variable is set — usually a typo. Settings reject
unknown keys deliberately, so a mistyped limit fails loudly instead of
silently falling back to a default. Check the spelling against `.env.example`.

**`Live trading is not implemented`**
`QUANTLAB_TRADING_MODE` is set to something other than `paper`. This is not a
switch to flip; there is no live code path to enable.

**Signed out on every restart**
`QUANTLAB_SECRET_KEY` is unset, so a fresh random key is generated each boot
and invalidates existing session cookies. Generate one and put it in `.env`.

**Login succeeds but the next page bounces to `/login`**
`QUANTLAB_SESSION_COOKIE_SECURE=true` while serving plain HTTP: the browser
accepts the cookie and refuses to send it back. Set it to `false` for local
development, and leave it `true` anywhere with TLS.

**"This form has expired"**
The CSRF token did not match — normally a stale tab after a restart. Reload and
resubmit.

**The kill switch is engaged and I did not engage it**
Expected on first boot. The system starts halted by design. Release it from
`/kill-switch` with a reason, which is recorded.

**`/audit` shows CHAIN BROKEN**
Something modified or deleted an audit row outside the application. The banner
names the first bad sequence number. Treat it as a security event: the log can
no longer be trusted from that point forward. Apply the grants in
`docs/security.md` to prevent recurrence.

**`NameError: name 'Text' is not defined` in a migration**
Alembic renders `astext_type=Text()` without importing it when autogenerating
the JSONB variant. Change it to `sa.Text()`.

**`sqlite3.OperationalError: no such table`**
Migrations have not run. `alembic upgrade head`. On the SQLite fallback the app
also creates tables at start-up, but only for the URL it is configured with —
check `QUANTLAB_DATABASE_URL` points where you think.

**Transition returns 409**
The requested edge is not in the transition table, or a guard refused it. The
response body names the permitted targets. Entering `PAPER_TRADING` needs a
released kill switch and a recorded approval.

**`alpaca_paper_base_url must be https://paper-api.alpaca.markets`**
`QUANTLAB_ALPACA_PAPER_BASE_URL` points somewhere other than Alpaca's paper
endpoint. This is not configurable to a live endpoint — there is no live
trading code path, by design.

**`broker_backend='alpaca' requires alpaca_api_key and alpaca_api_secret to both be set`**
`QUANTLAB_BROKER_BACKEND=alpaca` was set without both Alpaca credentials.
Either set both, or leave `QUANTLAB_BROKER_BACKEND` unset (default `mock`) to
paper-trade against the built-in simulator with no external account needed.

**`No cached market data for SYMBOL; run a backtest for it first`**
Order submission (`POST /projects/{id}/orders`) prices the order off the
latest cached bar in `market_bars`. Run a backtest for that symbol first (it
fetches and caches bars as a side effect), or wait for `MarketDataService` to
be called some other way — nothing populates the cache automatically.

**Transition to `RISK_REVIEW`/`AWAITING_HUMAN_APPROVAL` succeeds but the page shows no verdict**
`POST /projects/{id}/risk-review` and `POST /projects/{id}/backtests` are
separate actions from the plain state transition — moving the FSM forward
does not itself run the backtest or risk engine. Use the "Run backtest" /
"Run risk review" buttons on the project page.

**`Research agent is not configured (no Anthropic API key)` (409)**
`QUANTLAB_ANTHROPIC_API_KEY` is unset. There is no mock substitute for a paid
LLM call — set the key in `.env` and restart to enable
`POST /projects/{id}/research`.

**Research request returns 502 with a message about Claude declining**
The model's own safety classifier refused the question (`stop_reason ==
"refusal"`). This is surfaced as a failed research run, audited as
`research_failed` with the refusal message — rephrase the question or check
that it doesn't ask for something outside the tool's research scope
(investment advice, live trading, and similar are refused by design
elsewhere in this system too).

**`Accept at least one research finding first` (400) at `KNOWLEDGE_TESTING` or `HYPOTHESIS_DEVELOPMENT`**
`POST /projects/{id}/knowledge-tests` and `POST /projects/{id}/hypotheses`
both reason only over *accepted* `ResearchFinding` rows — pending or
rejected findings don't count. Go back to the Knowledge base section and
accept at least one finding via `/projects/{id}/findings/{id}/review` first.

**`Develop a hypothesis first` (400) at `CODE_GENERATION`**
`POST /projects/{id}/generate-code` needs both an existing `Strategy` (from
`STRATEGY_SPECIFICATION`) and a `Hypothesis` row (from
`POST /projects/{id}/hypotheses`) to build its prompt context. Run the
hypothesis step before generating code.

**`Generate code first` (400) at `CODE_VALIDATION`**
`POST /projects/{id}/validate-code` validates the *latest*
`GeneratedStrategyCode` row for the project; there is nothing to validate
until `POST /projects/{id}/generate-code` has run at least once.

**Code validation succeeds (303) but the page shows INVALID and no new strategy version**
This is the deterministic gate working as designed, not a bug: the agent's
proposed `fast_window`/`slow_window`/`minimum_out_of_sample_trades` failed
`app.core.codegen.validate_strategy_spec` (e.g. a window over the 500-bar
cap). The `GeneratedStrategyCode` row is still persisted with
`validated=False` and its `validation_reasons`, but no new `Strategy`
version is created — the previous version remains current. Generate code
again; a fresh agent call may propose different windows.

**`Specify a strategy first` (400) on `/performance-review`**
`POST /projects/{id}/performance-review` scopes its review to the project's
latest `Strategy` version — there is nothing to review until one exists.
Specify a strategy first.

**Performance review shows 0 trades or 0 realized P&L**
`app.core.performance.compute_order_performance` only counts *closed*
round-trip trades (a sell fill matched against an earlier buy fill for the
same symbol) from the current strategy version's own `Order` rows. A buy
with no matching sell yet contributes nothing to `trade_count` or
`realized_pnl` — it isn't mark-to-marked. Submit a closing order, or check
`Order.strategy_id` actually points at the strategy version you expect (an
order submitted before a strategy existed has a `null` `strategy_id` and is
excluded from every version's review).

**`/projects/{id}/experiments` shows "No strategy has been specified for this project yet"**
The page groups every `Backtest`/`RiskAssessment`/`Order`/`PerformanceReview`
by `Strategy` version; with zero strategies specified there is nothing to
group. Specify a strategy via `STRATEGY_SPECIFICATION` first.

**`Too many failed attempts. Try again later.` (429) on `/login`**
The operator hit `login_lockout_threshold` (default 5) consecutive wrong
passwords and is locked for `login_lockout_duration_seconds` (default 900).
The correct password is refused too while locked — this is by design, not a
bug. Wait out the lock, or an administrator can clear it directly (set
`Operator.locked_until` to `NULL`). See
[docs/security.md](docs/security.md)'s "Account lockout."

**Order refused: `today's portfolio loss is N%, exceeding max_daily_loss_percentage`**
The account's tracked equity has dropped more than `max_daily_loss_percentage`
(default 1.0%) since the first order of the UTC calendar day — see
`app.core.performance`'s sibling mechanism in `docs/architecture.md`'s "Risk
engine" section for how that baseline (`DailyEquityMark.opening_equity`) is
captured. Only `buy` orders are refused; submit a `sell` to reduce exposure
instead, or wait for the next UTC day, when a fresh `DailyEquityMark` gets
created from whatever the account's equity is at that point.

**Is `docker compose up` safe to expose beyond localhost?**
No, not as shipped. It's a local development/staging convenience: it never
sets `QUANTLAB_ENVIRONMENT=production`, so `Settings`' production-strictness
validator (which would reject `debug=true`, an insecure session cookie, or a
non-Postgres database URL) never runs against this path, and there is no TLS
termination anywhere in `docker-compose.yml`. Put a real reverse proxy with
TLS in front before exposing it beyond `127.0.0.1`.
