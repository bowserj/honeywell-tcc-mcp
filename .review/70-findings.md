# Consolidated finding register (17 findings)

Severity counts: 1 Critical, 3 High, 6 Medium, 7 Low.
Every Critical/High is [executed] or [observed] with a reproduction.

F-001 | Docker image is unrunnable: entrypoint lacks exec bit
- Severity: Critical | Category: Build-Deploy | Layer: L3/L4 | Tag: [executed]
- Location: Dockerfile:26 (COPY), Dockerfile:31 (ENTRYPOINT); entrypoint.sh:1; README.md:167-206
- Evidence: |
    COPY server.py tcc_api_client.py entrypoint.sh ./
    ENTRYPOINT ["/bin/sh", "-c", "/app/entrypoint.sh \"$@\"", "--"]
  git mode of entrypoint.sh = 100644; in-image mode -rw-rw-r-- root root.
  Probe through container: "FAIL: no initialize result ... --: 1:
  /app/entrypoint.sh: Permission denied".
- Observation: `sh -c "/app/entrypoint.sh ..."` executes the script BY PATH,
  which requires the exec bit. COPY preserved the non-executable source mode;
  no chmod anywhere. The Dockerfile comment claims the entrypoint is "invoked
  through the shell instead of via exec bit" - the code does the opposite.
- Expected: entrypoint must be executable (COPY --chmod=755 or RUN chmod
  before USER, or invoke `sh /app/entrypoint.sh` explicitly).
- Impact: every `docker run` of this image dies at startup. The entire
  documented Docker path (README Docker section, docker-compose service,
  client-config example) never worked. Build succeeds, so CI-style image
  builds hide it.
- Verification: executed. Probe FAIL (11), control run (12), fix validation
  (14): one-line COPY --chmod=755 -> probe PASS with 20 tools.
- Confidence: High
- Recommendation: `COPY --chmod=755 server.py tcc_api_client.py entrypoint.sh ./`
- Effort: S

F-002 | Schedule-period editing crashes 100%: _time_str does not exist
- Severity: High | Category: Correctness | Layer: L3/L4 | Tag: [executed]
- Location: tcc_api_client.py:459; callers 423-456 (edit_schedule_period),
  server.py:279-292 (save_schedule_period tool), tcc_api_client.py:794 (CLI)
- Evidence: |
    start = self._time_str(orig.get("StartTime"))
  AttributeError: 'TotalConnect' object has no attribute '_time_str'
- Observation: `_period_form_fields` calls `self._time_str`, which is defined
  nowhere in the module (single occurrence in repo = the call site). Every
  edit_schedule_period call raises AttributeError before any wire POST.
- Expected: helper exists, or StartTime serialized directly. The path was
  clearly never executed in this revision.
- Impact: save_schedule_period tool, edit-period CLI, and README's
  "Change one schedule period" example are all dead. get_schedule/send_schedule
  still work (read + commit of an un-edited schedule).
- Verification: executed (unit checks T6/T6b/T6c; rg confirms single
  occurrence). No wire write occurs, so no partial state on the portal.
- Confidence: High
- Recommendation: implement _time_str against the portal's expected format
  (inspect the editor form from get_schedule_period_editor, e.g.
  {"Hours": h, "Minutes": m} -> "HH:MM"), then re-test on a live device.
- Effort: S (code) + live verification

F-003 | get_device_alerts serves stale cache forever after first fetch
- Severity: High | Category: Correctness | Layer: L4 | Tag: [executed]
- Location: tcc_api_client.py:303-306 (parse_alerts), 295 (_last_alerts_html),
  server.py:204-213 (get_device_alerts tool)
- Evidence: |
    html = getattr(self, "_last_alerts_html", "")
    if not html:
        self.alerts()
- Observation: parse_alerts refetches only when the cache is EMPTY. Any
  non-empty earlier fetch is reused for the life of the process.
- Expected: alerts listing is a live read; each tool call must fetch.
- Impact: after acknowledge_alert (or any time gap), get_device_alerts returns
  the old list - acknowledged alerts appear still active; an agent can loop
  re-acknowledging or report wrong state. Deterministic from the 2nd call on.
- Verification: executed (T4: stale cache returned with alerts() monkeypatched
  to raise; T4c: fetch count still 1 after 2nd parse).
- Confidence: High
- Recommendation: parse_alerts() should always call self.alerts() (drop the
  cache), or the tool should call alerts() explicitly.
- Effort: S

F-004 | Shared client across threads: races on Session, cache, and login
- Severity: High | Category: Reliability | Layer: L4 | Tag: [observed+inferred]
- Location: server.py:74-86 (_tcc singleton); tcc_api_client.py:83 (Session),
  205-238 (load_session/ensure_authed), 295 (_last_alerts_html)
- Evidence: mcp 2.3.0 runs sync tool functions on worker threads:
  mcp/server/mcpserver/resolve.py:556:
    result = await anyio.to_thread.run_sync(lambda: fn(**kwargs))
- Observation: one requests.Session (not thread-safe per its own docs) plus
  process-lifetime mutable state (_last_alerts_html, _authed_flag) are shared
  by all 20 tools with no locking. ensure_authed is check-then-act.
- Expected: serialize portal access behind a lock (the client is one
  rate-limited session anyway), or per-call clients.
- Impact: realistic trigger - MCP clients issue concurrent tool calls (e.g.
  list_locations + get_device_data on a cold cache): both threads see
  _authed_flag False, both POST login -> portal rate limiter
  (TooManyAttempts) locks the account out for minutes; cookie-jar mutation
  during concurrent requests can also corrupt the shared session.
- Verification: dispatch model observed in installed SDK source; interleaving
  consequences inferred (confidence Medium for the lockout actually firing).
- Confidence: Medium (mechanism certain; trigger frequency client-dependent)
- Recommendation: wrap ensure_authed + login in a threading.Lock; simplest is
  one global lock around every tcc.* call.
- Effort: S/M

F-005 | _check misses login bounce without history; _json masks it as ok data
- Severity: Medium | Category: Correctness | Layer: L4 | Tag: [executed]
- Location: tcc_api_client.py:100-108 (_check), 688-693 (_json)
- Evidence: |
    if r.status_code == 401 or "login" in r.url.lower() and r.status_code in (200, 302):
        if r.status_code == 401 or r.history:
- Observation: a 200 login page served with NO redirect history is not raised
  (T2 executed); _json then silently returns the HTML body as the tool's
  "data" (T3 executed), so the tool answers ok:true with a login page.
- Expected: any login-page response must fail loudly; JSON endpoints must
  raise on non-JSON bodies.
- Impact: if any AJAX endpoint serves the login view directly on expiry
  instead of redirecting, every read tool silently returns HTML garbage while
  claiming success.
- Verification: executed T2/T3.
- Confidence: Medium (portal's exact bounce behavior unverified offline)
- Recommendation: in _json, raise TotalConnectError when the body is not JSON
  on endpoints documented as JSON; broaden _check to treat "login" in r.url as
  expired regardless of history.
- Effort: S

F-006 | Unwritable /data degrades silently: no set -e + swallowed OSError
- Severity: Medium | Category: Reliability | Layer: L4 | Tag: [executed]
- Location: entrypoint.sh:5-11; tcc_api_client.py:148-153 (_save_session)
- Evidence: |
    elif [ "$(id -u)" = "0" ]; then chown ... fi
    mkdir -p /data/exports        <- fails, script continues (no set -e)
    ...
    except OSError: pass          <- session write failure is invisible
- Observation: bind-mounted /data owned by another uid (container runs as
  10001): [ -w /data ] false, not root, mkdir fails rc=1 and the script
  continues (demonstrated); the server then starts, and _save_session's
  except OSError: pass hides the session write failure.
- Expected: entrypoint should fail fast (set -e) or warn explicitly; session
  persistence failure must surface (log + non-zero or tool-visible).
- Impact: with a root-owned bind mount (README suggests bind dirs), every
  restart re-logins -> TooManyAttempts rate limiting; the only symptom is
  mysterious logins. The skill's container rules call out exactly this
  uid/gid-mismatch failure class.
- Verification: executed (docker bind-mount demo, mkdir rc=1 continues).
- Confidence: High
- Recommendation: add `set -e` after the chown block (keep the chown guarded),
  and log a warning from _save_session when the write fails.
- Effort: S

F-007 | export_report: crash on Alerts=null + silent location drops
- Severity: Medium | Category: Correctness | Layer: L3 | Tag: [executed]
- Location: tcc_api_client.py:164-203 (join at 200; swallow at 176-177)
- Evidence: |
    except TotalConnectError:
        pass
    ...
    "; ".join(dev["Alerts"])      <- TypeError when Alerts is None
- Observation: (a) a location whose device_status fails is silently omitted
  from the report (no marker, so the report rewards failure); (b) if any
  device carries "Alerts": null, the CSV stage raises TypeError AFTER the JSON
  half is already written (T8 executed) - orphan artifact, tool error.
- Expected: degraded locations should be recorded with an error field; Alerts
  should be normalized (`dev.get("Alerts") or []`, and joined only if a list
  of strings).
- Impact: export_report can half-write then fail; undercounts are invisible.
- Verification: executed T8.
- Confidence: High (crash path proven; null-Alerts wire shape unverified
  offline - Medium for whether it occurs in practice)
- Recommendation: `alerts = dev.get("Alerts") or []; "; ".join(map(str, alerts))`
  and record per-location errors in the report.
- Effort: S

F-008 | login time_offset: self-contradictory doc, wrong for UTC containers
- Severity: Medium | Category: Correctness/Documentation | Layer: L3 | Tag: [executed]
- Location: tcc_api_client.py:112-128 (docstring 114-115; form field 124)
- Evidence: |
    time_offset: seconds the local clock is ahead of UTC (300 = UTC-5).
- Observation: "seconds" and "300 = UTC-5" contradict each other (300 s is 5
  minutes; UTC-5 is 300 minutes BEHIND, not ahead - T10 executed: both strings
  present). The value is hardcoded 300 regardless of host/container tz; the
  image runs UTC.
- Expected: minutes with JS getTimezoneOffset semantics (UTC-5 -> 300),
  derived from the actual clock (time.timezone/-tm_gmtoff), not hardcoded.
- Impact: non-Eastern deployments (and the UTC container) report the wrong
  client offset to the portal; any portal behavior keyed on client local time
  (schedule display windows) is shifted by up to hours.
- Verification: docstring contradiction executed (T10); wire semantics
  inferred from JS convention (not verifiable offline).
- Confidence: Medium
- Recommendation: compute the offset at login: `time.timezone // -60` style,
  and fix the docstring units/sign.
- Effort: S

F-009 | No safety gate/audit on write tools; raw_request reaches destructive routes
- Severity: Medium | Category: Security/Design | Layer: L1 | Tag: [observed]
- Location: server.py:366-382 (raw_request), 216-247 (ack/set tools);
  tcc_api_client.py:548-553 (delete_gateway = GET /Gateway/Delete/{id})
- Observation: the server exposes write tools (set_setpoints,
  save_schedule_period, send_schedule, discard_schedule_changes,
  acknowledge_alert) and an unrestricted raw_request GET hatch with no
  read-only mode, no confirmation gate, and no audit log. The client's route
  surface includes destructive GETs (delete_gateway).
- Expected: MCP-server safety model for physical-effect backends: gate
  state-changing calls behind an opt-in env var, classify tools read/write at
  registration, log every invocation (allowed/blocked) to an audit file, and
  apply the same gate to the escape hatch.
- Impact: an agent (or prompt-injected content reaching the tool layer) can
  unbind a gateway registration or rewrite HVAC schedules with one raw_request
  call; nothing records who did what.
- Verification: observed in source (tool registrations + raw path); no write
  was executed against the portal during this review.
- Confidence: High (gap); impact severity depends on deployment exposure.
- Recommendation: add TCC_ENABLE_CONTROL=1-style gate + audit log mirroring the
  DNAFusion Flex pattern; block known-destructive routes in raw_request.
- Effort: M

F-010 | Session cookie file written world-readable, no expiry
- Severity: Medium | Category: Security | Layer: L3 | Tag: [executed]
- Location: tcc_api_client.py:148-153
- Evidence: open(self.session_file, "w") -> mode 0o664 on this host (umask 002),
  typical 0o644. Jar stores name/value only -> cookies never client-expire.
- Observation: the .ASPXAUTH_TRUEHOME cookie is a bearer credential; the cache
  file lands group/world-readable on multi-user hosts and inside the /data
  volume (any process with volume access reads it).
- Expected: credential files written 0600 (os.open with mode, or chmod after).
- Impact: session hijack from any same-host reader or volume consumer.
- Verification: executed (mode probe); exposure scenario is deployment-dependent.
- Confidence: High (mode), Medium (exploitability - single-user hosts are low risk)
- Recommendation: os.open(self.session_file, os.O_WRONLY|os.O_CREAT|os.O_TRUNC,
  0o600) and document 0600 for /data/credentials.json.
- Effort: S

F-011 | load_session hardcodes cookie domain; --base deployments break
- Severity: Low | Category: Correctness | Layer: L3 | Tag: [observed]
- Location: tcc_api_client.py:212-213 (domain="mytotalconnectcomfort.com"), 77
- Observation: cookies are restored against a hardcoded domain while the
  client accepts --base / base_url; on a custom base the cookies never match,
  load_session always fails, every run re-logins (rate limiter).
- Confidence: High | Recommendation: derive the domain from self.base's host.
- Effort: S

F-012 | TTY-flagged docker instructions corrupt stdio MCP framing
- Severity: Low | Category: Build-Deploy/Documentation | Layer: L1 | Tag: [observed]
- Location: README.md:171-176 (`docker run --rm -it ...`), Dockerfile:4 (same);
  docker-compose.yml:1-20 (no stdin_open: true / tty: false)
- Observation: MCP stdio over docker must use `-i` and never `-t` (TTY echo +
  CRLF translation corrupts JSON-RPC). The README ad-hoc run and Dockerfile
  header comment both say `-it`; the client-config example correctly uses `-i`.
  compose lacks stdin_open/tty directives.
- Impact: anyone wiring the documented `-it` invocation to a real MCP client
  gets framing corruption; symptom is a "broken server" that is actually
  a docs bug.
- Recommendation: drop -t from both docs; add stdin_open: true, tty: false to
  compose if used for probing.
- Effort: S

F-013 | Documentation drift cluster
- Severity: Low | Category: Documentation | Layer: L3 | Tag: [observed]
- Location: README.md:24 (no python>=3.10 note though annotations require it),
  README.md:49 + server.py:24 (stale ~/projects/honeywell/mcp/server.py path),
  README.md:88-96 (claims client login reads ~/.tcc_credentials.json - only
  server.py does; tcc_api_client.py:117-122 reads env only, and its own error
  message confirms),
  README.md:104-105 (DispTemp `:.0f` crashes on None/offline devices; dev
  ['Alerts'] KeyError-prone),
  README.md:162-163 (save_schedule_period "safe to change" - false per F-002),
  server.py:30-38 (docstring tool list omits export_report; 20 tools exist),
  server.py:140-142 (session_status description promises "authenticated
  false plus a fresh device read"; code only ever returns true or an error)
- Recommendation: one docs pass against verified behavior.
- Effort: S

F-014 | CLI rough edges
- Severity: Low | Category: Correctness | Layer: L3 | Tag: [observed]
- Location: tcc_api_client.py:735-738 (login(): raises on failure, so
  "logged in" if ok else "login failed" else-branch is dead; user gets a
  traceback instead of the message), 767 (int(a.arg) unvalidated ->
  traceback on non-numeric), 750/758/777 (string ids interpolated into URLs
  without validation/encoding)
- Recommendation: wrap main() in try/except TotalConnectError -> friendly exit.
- Effort: S

F-015 | Dead code and lint debt
- Severity: Low | Category: Maintainability | Layer: L3 | Tag: [observed]
- Location: server.py:134 (`.text[:0]` no-op), server.py:47-48 (RUF100: both
  `# noqa: E402` unused), tcc_api_client.py:696 (duplicate _authed_flag class
  attr), 826-827 (pointless __main__ guard assignment), 129 (Referer pop from
  headers never set), 698-702 (__exit__ does not close the session - harmless
  for a stdio server but misleading), ruff: 20x BLE001 (intentional envelope -
  acceptable, add targeted noqa or config), SIM102, FURB167 x3, PIE804, I001,
  EXE001 (shebang without +x)
- Effort: S

F-016 | Unpinned dependencies, no lockfile, undocumented python floor
- Severity: Low | Category: Dependency | Layer: L1 | Tag: [observed]
- Location: requirements.txt:1-2 (requests>=2.31, mcp>=2.0.0)
- Observation: no lockfile; mcp 2.x is an actively moving major (verified
  against 2.3.0 today); `str | None` annotations require python>=3.10 but the
  README host instructions never say so (image correctly uses 3.13).
- Recommendation: pin `mcp~=2.3`, `requests~=2.34` (+ lockfile), note the
  3.10 floor in README.
- Effort: S

F-017 | Build-context and compose secret hygiene
- Severity: Low | Category: Build-Deploy | Layer: L3 | Tag: [observed]
- Location: .dockerignore:1-7 (excludes *.md, data/ but not .env /
  credentials files - a stray credentials.json in the build dir ships to the
  daemon's context), docker-compose.yml:11-15 (TCC_PASSWORD interpolated from
  host env; `docker compose config` prints it resolved - never run where
  output is captured)
- Effort: S
