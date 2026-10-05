# Remediation log (2026-10-05, same session as the review)

All fixes verified by execution: 37-check post-fix harness (0 FAIL),
py_compile, ruff, bandit, stdio JSON-RPC probe (bare python), full docker
build from the repo's fixed Dockerfile + stdio probe through the container
(20 tools, PASS), entrypoint fail-fast demonstration, compose config render.

## Fixed (offline-verifiable)

- F-001 Critical: Dockerfile COPY --chmod=755 (was: image unrunnable,
  exec bit missing). Verified: image builds, entrypoint is -rwxr-xr-x
  in-image, stdio probe through container PASS. Fix validated twice
  (scratch copy first, then repo).
- F-002 High: _time_str implemented; edit_schedule_period redesigned to
  round-trip the live editor form's fields verbatim (StartTime exact by
  construction; no time-format guessing). JSON fallback retained with
  documented "HH:MM" inference. Both paths executed in harness: form path
  POSTs StartTime "6:00 AM" unchanged + only the requested overrides;
  fallback POSTs 06:00 with no crash.
- F-003 High: parse_alerts always refetches (stale cache removed).
  Executed: fetch count 2 across two parses.
- F-004 High: threading.RLock; all portal requests funneled through locked
  _get/_post; ensure_authed holds the lock across login (double-login /
  TooManyAttempts race closed). Executed: RLock reentrancy, funnel source.
- F-005 Medium: _check raises on login-URL responses regardless of
  redirect history; _json raises TotalConnectError on non-JSON bodies;
  raw(check=False) added for /Account/LogOff (logout tool fixed to use it).
- F-006 Medium: entrypoint.sh set -e (fail fast on unwritable /data;
  demonstrated: rc=1 with clear mkdir error instead of silent rc=0);
  _save_session warns on stderr when persistence fails.
- F-007 Medium: export_report normalizes Alerts (null/non-list safe;
  executed with Alerts=None), records per-location errors in JSON + new
  CSV "error" column instead of silently dropping failed locations.
- F-008 Medium: time_offset auto-derived from the local clock
  (minutes-west, JS getTimezoneOffset convention; executed: posted value
  matches -(tm_gmtoff//60)); self-contradictory docstring fixed.
- F-009 Medium (partial): every write tool + raw_request + login/logout
  audited to stderr (9 call sites, credentials never logged, stdout clean
  per AST check). The opt-in CONTROL GATE remains a user decision.
- F-010 Medium: session file written 0600 (os.open mode + chmod for
  pre-existing files); unwritable path warns on stderr. All executed.
- F-011 Low: cookie domain derived from base URL (executed with a
  custom base).
- F-012 Low: -it -> -i in README + Dockerfile comment; compose gets
  stdin_open: true, tty: false; warning about `docker compose config`
  leaking resolved TCC_PASSWORD.
- F-013 Low: README/server docstring drift fixed (export_report listed,
  generic install path, python 3.10 floor, DispTemp example no longer
  crashes on null, session_status description matches behavior,
  save_schedule_period caveat updated); client now reads the credentials
  file too, matching what the README already claimed.
- F-014 Low: CLI __main__ catches TotalConnectError -> "error: ..." +
  exit 1 (demonstrated); _int_arg validates numeric ids; dead "login
  failed" else-branch note: kept (login() still returns True on success;
  failure raises, caught by the new handler); --fresh flag actually wired
  (was parsed but never used).
- F-015 Low: dead code removed (dup class attr, __main__ guard, Referer
  pop, .text[:0]); __exit__ closes the session; ruff --fix applied
  (12 auto-fixes). Remaining lint is intentional: 21x BLE001 error
  envelope, 1x S110 audit guard, EXE001 shebang (cosmetic).
- F-016 Low: requirements pinned (requests~=2.34, mcp~=2.3; verified
  against 2.34.2 / 2.3.0).
- F-017 Low: .dockerignore excludes .env*, credentials*.json, .tcc_*,
  .review/.
- Bonus: _parse_location_list attribute-order independence (executed);
  Pyright None-access nit fixed defensively.

## Needs user intervention

1. F-002 live verification: one real edit_schedule_period + send_schedule
   against a live thermostat (form round-trip should be exact; confirm the
   portal accepts it; if the fallback path ever fires, verify its time
   format).
2. F-008 live verification: confirm the timeOffset sign/units on a real
   login (inferred JS convention; container runs UTC and now sends 0).
3. F-009 decision: add a TCC_ENABLE_CONTROL=1-style refusal gate on write
   tools (would change write-by-default behavior; matches the DNAFusion
   pattern you use elsewhere)? Audit logging is already in.
4. Commit: all changes are uncommitted on main, per repo policy.
   `git diff --stat` shows the full set.
