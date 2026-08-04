# Security notes

## Secrets

Credentials come from the environment. `.env` is gitignored; `.env.example`
carries no real values. Nobody should ever paste a credential into a chat
window, a commit message or an issue.

Redaction runs as a logging filter, so it applies to every record regardless of
which call site produced it — including third-party library logs. Patterns
cover labelled secrets (`api_key=…`, `password: …`, `Authorization: Bearer …`),
Alpaca-style key identifiers and JWT-shaped strings. Structured payloads are
walked recursively by key name.

Account and order identifiers are masked with `mask_identifier`, which keeps
the last four characters so an operator can correlate against the broker UI
without the full value appearing in a log.

`QUANTLAB_ALPACA_API_KEY`/`QUANTLAB_ALPACA_API_SECRET` and
`QUANTLAB_ANTHROPIC_API_KEY` join the same sensitive-key set as every other
credential (`_SENSITIVE_KEYS` in `app/logging_config.py`), so they're
redacted from logs and from any audit payload that happens to include them.
This does **not** extend to
`BrokerAccountSnapshot.raw_response` or `BrokerReconciliationRun.findings` --
those are plain database columns holding Alpaca's account/position/order
responses as-is, not audit payloads, and are never passed through
`redact_mapping`. That's deliberate: Alpaca's account/position/order responses
don't carry secrets, only account-identifying data an operator needs to read
back, but it is an asymmetry with the audit log worth knowing before adding a
new broker field that might carry something sensitive.

Verified by test: audit payloads containing `api_key` store `***REDACTED***`;
a live server run produced zero occurrences of the session key or the operator
password across the log and all four rendered pages.

## Authentication

Argon2id via `argon2-cffi` at library defaults, with a 12-character minimum and
`check_needs_rehash` available for parameter upgrades. Sessions are signed
cookies (`itsdangerous`) carrying only an operator id and a CSRF token —
no authorisation data — so deactivating an operator takes effect on their next
request rather than at cookie expiry. Cookies are `HttpOnly`, `SameSite=Lax`
and `Secure` unless explicitly disabled for local plain-HTTP development.

Failures return one message for unknown accounts, inactive accounts and wrong
passwords alike, and a test asserts the responses are indistinguishable.

### Account lockout

An operator with `login_lockout_threshold` (default 5) consecutive
wrong-password attempts is locked for `login_lockout_duration_seconds`
(default 900) — persisted on the `Operator` row (`failed_login_count`,
`locked_until`), not the in-memory, per-process `RateLimiter` used for
IP-based throttling above, which would not survive a restart or a second
worker. A locked account skips the password check entirely while locked
(no Argon2id hashing spent on an attempt that cannot succeed), and the
counter resets on the next attempt after the lock expires, or immediately
on a successful login.

**This is the one place a response is deliberately distinguishable.** A
lockout response (429, "Too many failed attempts") differs from the generic
wrong-password response (401, "Email or password is incorrect"), which
marginally reveals that an account exists after enough failed guesses. This
is the standard, accepted trade-off of any lockout mechanism — OWASP ASVS
treats it as such — the alternative is no protection against sustained
credential guessing at all. Unknown emails are unaffected either way: there
is no `Operator` row to count failures against.

## CSRF

Every state-changing form carries a per-session token compared with
`hmac.compare_digest`. Missing and forged tokens both yield 403, each covered
by a test.

## Headers

`Content-Security-Policy` with `default-src 'self'` and `frame-ancestors 'none'`,
`X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`,
`Referrer-Policy: same-origin`. CORS is off unless origins are configured
explicitly; the empty default is correct for a same-origin console.

## Database hardening

Grant the application role `INSERT` and `SELECT` on `audit_events` — no
`UPDATE`, no `DELETE`:

```sql
REVOKE UPDATE, DELETE ON audit_events FROM quantlab_app;
GRANT INSERT, SELECT ON audit_events TO quantlab_app;
```

The hash chain makes tampering detectable; the grant makes it hard. Use them
together.

## Generated strategy code has no sandbox, because it has no code (Phase 5)

This section originally specced a Docker sandbox (`--network none`, a
read-only data mount, a tmpfs scratch directory, `--pids-limit`, CPU/memory
caps, a wall-clock timeout, a non-root user, `--cap-drop ALL`,
`--security-opt no-new-privileges`, no inherited environment) for executing
LLM-generated Python. That path was not taken.

Instead, `app.agents.strategy_code.ClaudeStrategyCodeAgent` returns a
`StrategySpecProposal` — four numbers (`fast_window`, `slow_window`,
`minimum_out_of_sample_trades`) and a rationale string, validated by
`app.core.codegen.validate_strategy_spec` before it can produce a new
`Strategy` version. There is no code string anywhere in this path, so there
is nothing to execute and nothing to sandbox. The parameterized-DSL
alternative this section used to flag as "worth considering" is what got
built; see `docs/architecture.md`'s "Knowledge test, hypothesis and code
generation agents" section for the full design.

## Known limitations

- `app.rate_limit.RateLimiter` (used for the anonymous, IP-keyed login
  throttle ahead of account lockout) is still in-memory and per-process by
  design — correct for one worker, but each additional app worker or
  replica would keep its own independent counter if reached directly. The
  Docker Compose path closes this: `nginx.conf`'s `limit_req` zone lives in
  nginx's own shared memory and is the only entry point (the app's port is
  no longer published to the host), so it is the real, authoritative limit
  regardless of app worker count. Deploying without that compose file — a
  bare `docker run`, a different orchestrator — means putting equivalent
  protection at whatever sits in front instead.
- No second factor. Account lockout (above) covers sustained password
  guessing; a second factor is still a reasonable addition on top of it.
- No log shipping. Audit records live in the same database as the data they
  describe; a compromise of that database is a compromise of both.
- `app.core.performance.compute_order_performance` realizes P&L only on
  closed round-trip trades from a strategy version's own filled orders. It
  does not mark-to-market a position still open, and a sell fill with no
  matching open lot (e.g. an unmodeled short) is excluded from the result
  rather than estimated.
