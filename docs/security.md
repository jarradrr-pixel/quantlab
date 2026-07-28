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

## Sandbox for generated code (Phase 5)

Generated strategy code is the largest attack surface in the design. When it
lands it must run with `--network none`, a read-only data mount, a tmpfs
scratch directory, `--pids-limit`, CPU and memory caps, a wall-clock timeout, a
non-root user, `--cap-drop ALL` and `--security-opt no-new-privileges`, and
with no environment inherited from the host.

Worth considering before building it: a parameterised strategy DSL that the
model fills in achieves the same demonstration with far less exposure than
free-form Python execution.

## Known limitations

- Login throttling is per-worker and in-process. Behind multiple workers, put
  real rate limiting at the reverse proxy.
- No account lockout or second factor. Both are reasonable additions.
- No log shipping. Audit records live in the same database as the data they
  describe; a compromise of that database is a compromise of both.
- `app.core.risk.assess_order` does not enforce `max_daily_loss_percentage`.
  Every other configured limit (position size, total exposure, order count,
  open positions, shorting, leverage) is checked; this one would need
  day-start equity tracking that doesn't exist yet.
