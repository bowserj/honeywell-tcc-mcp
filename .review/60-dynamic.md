# Dynamic verification log (all executed 2026-10-05, host = this machine)

Environment: uv 0.10.4, python 3.12.3 host / 3.13 in scratch venv (parity with
image), Docker 29.1.3. Venv: ~/.hermes/cache/scratch/tcc-review/venv (uv, mcp
2.3.0 + requests 2.34.2). Repo files never modified; docker fixes validated in
a scratch copy.

1. `uv pip install -r requirements.txt` -> OK. mcp 2.3.0, requests 2.34.2.
   `mcp>=2.0.0` resolves; server targets mcp 2.x (MCPServer is the renamed
   FastMCP).
2. `python -c "from mcp.server import MCPServer"` -> OK
   (mcp.server re-exports mcp.server.mcpserver.server.MCPServer).
3. `python -c "import server"` -> OK. Module-level MCPServer(...) construction
   and all 20 @server.tool registrations succeed. Clears the suspicion that
   name/title/description/instructions kwargs are invalid in mcp 2.3.0.
4. `python -m py_compile server.py tcc_api_client.py` -> OK.
5. stdio JSON-RPC handshake probe (skill script) against `python server.py`:
   PASS. initialize -> honeywell-totalconnect, proto 2025-06-18;
   tools/list -> 20 tools, all with inputSchema. No network used.
6. ruff check: 29 findings (20 BLE001 intentional error-envelope catches,
   3 FURB167, 2 RUF100, 1 SIM102, 1 EXE001, 1 PIE804, 1 I001).
7. bandit -r: "No issues identified", 1012 LOC analyzed.
8. git history: 2 commits, 10 files ever; no secrets/artifacts in history.
9. unit_checks.py (18 checks, 0 unexpected fail) - see T1-T12 in findings:
   T2 login-page-no-history slips past _check; T3 _json returns raw HTML;
   T4/T4c parse_alerts reuses stale non-empty cache without refetch;
   T5b data-url-before-data-id silently drops Url; T6 _period_form_fields
   raises AttributeError (missing _time_str); T7 _url passes absolute URLs;
   T8 export_report TypeError on Alerts=None; T10 login docstring
   self-contradiction; T12 CLI login dead else-branch.
10. docker build (documented procedure) -> BUILD-OK.
11. docker run via stdio probe -> FAIL: "--: 1: /app/entrypoint.sh: Permission
    denied" on every start. Image ls: -rw-rw-r-- root root; git mode 100644;
    host mode 664. Container never runnable as shipped.
12. chmod+ run-as-root control: entrypoint script content itself executes
    fine once the exec bit exists (printed uid).
13. unwritable /data bind demo: uid 10001; `mkdir -p /data/exports` fails
    rc=1 and the script CONTINUES (no set -e) -> silent degradation chain.
14. fix validation in scratch copy, single change `COPY --chmod=755 ...`:
    rebuild OK, in-image mode -rwxr-xr-x, stdio probe through container:
    PASS, 20 tools. Confirms root cause and one-line remedy.
15. cookie/session file default write mode on this host: 0o664 (umask 002);
    typical single-user umask 022 -> 0o644. Bearer cookie world/group readable.
16. docker compose config -> renders OK.
17. All review images/tags removed after verification; no repo state changed.
