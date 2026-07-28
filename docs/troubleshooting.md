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

**`Broker is not configured` (409 on `/broker/verify` or `/broker/reconcile`)**
`QUANTLAB_ALPACA_API_KEY`/`QUANTLAB_ALPACA_API_SECRET` are unset. Both must be
set together; see `.env.example`.
