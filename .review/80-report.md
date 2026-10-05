# Review report: honeywell-tcc-mcp

System: MCP stdio server (20 tools) wrapping the Honeywell Total Connect
Comfort web portal through a scraping client (tcc_api_client.py, 828 lines,
also a CLI) plus Docker packaging. 9 files, ~1.2k source lines, 1 feature
commit. All 9 files read line-by-line; findings verified by execution where
possible (18-check unit harness, stdio JSON-RPC probe, two docker builds,
ruff, bandit, git history scan).

Overall: solid small codebase with genuinely good MCP ergonomics (accurate
20/20 tool table, write/verify guidance in tool descriptions, session caching,
clean stdio implementation - handshake PASS bare and in a fixed container),
but two never-executed paths shipped broken: the Docker image cannot start at
all (F-001), and schedule editing always crashes (F-002).

Top risks (full register: 70-findings.md):
1. F-001 Critical - Docker image unrunnable (exec bit); fixed and validated
   with one line: COPY --chmod=755.
2. F-002 High - save_schedule_period/edit-period dead: _time_str missing.
3. F-003 High - get_device_alerts returns stale cache after first call.
4. F-004 High - shared Session/state across tool threads; double-login races
   into the portal rate limiter.
5. F-005 Medium - login bounce without history slips through _check and
   surfaces as ok:true with HTML.
6. F-006 Medium - unwritable /data chain: entrypoint no set -e + swallowed
   OSError -> silent re-login loop.
7. F-007 Medium - export_report crash on Alerts=null + silent location drops.
8. F-008 Medium - hardcoded time_offset 300 with self-contradictory doc.
9. F-009 Medium - no write-gate/audit on tools or raw_request.
10. F-010 Medium - bearer cookie cached world-readable.

Scorecard (1-5): Correctness 2, Security 3, Reliability 2, Performance 4,
Maintainability 3, Testability 1 (zero tests), Observability 2 (no logging),
Build-Deploy 1 (F-001), Documentation 3, Dependencies 3, Compliance/Privacy 3,
DevEx 3.

Remediation order: F-001 (one line, then docker run probe), F-003 (one line),
F-002 (implement _time_str + live verify), F-004 (lock), F-006/F-007/F-010
(small), then docs pass F-013/F-012 and pinning F-016.

What is done well: error-envelope consistency across all tools; instructions
field that actually encodes operational gotchas (rate limiter, verify-after-
write); .gitignore artifact hygiene and no secrets in history; non-root
container; minimal dependency surface; CLI parity for cron use.

Coverage: 9/9 files, 1,218/1,218 source lines, all 20 tools + all client
methods dispositioned. No exclusions. Executed: 18 unit checks, 3 stdio
probes, 2 docker builds (+1 fix-validation build), ruff, bandit, compile,
git history. Not verifiable offline: live-portal wire behaviors (bounce
semantics, Alerts wire shape, timeOffset units) - flagged per finding.
