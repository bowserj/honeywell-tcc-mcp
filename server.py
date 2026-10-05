#!/usr/bin/env python3
"""
MCP server for the Honeywell Total Connect Comfort web portal.

Wraps tcc_api_client.TotalConnect and exposes it as MCP tools over stdio.

Credentials (one of):
  1. Env vars:        TCC_USERNAME / TCC_PASSWORD
  2. Credentials file: TCC_CREDENTIALS_FILE (default ~/.tcc_credentials.json)
     with {"username": "...", "password": "..."}

The session cookie is cached in TCC_SESSION_FILE (default ~/.tcc_mcp_session.json)
so the server stays logged in across restarts; use the `login` tool to force
a fresh login (mind the server-side rate limiter).

Run (stdio):
    python server.py

Client config example (Claude Desktop / any MCP client):
    {
      "mcpServers": {
        "honeywell": {
          "command": "python",
          "args": ["/path/to/honeywell-tcc-mcp/server.py"],
          "env": {"TCC_USERNAME": "you@example.com", "TCC_PASSWORD": "..."}
        }
      }
    }

Tools:
  login, logout, session_status,
  list_locations, get_location_zones,
  get_device_data, get_device_alerts, acknowledge_alert,
  set_setpoints,
  get_device_settings, get_schedule, save_schedule_period,
  send_schedule, discard_schedule_changes,
  get_humidifier_data, get_dehumidifier_data, get_forecasts, get_dealer_info,
  export_report (temperature report as JSON + CSV in the exports dir),
  raw_request (escape hatch for any other /portal route)
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server import MCPServer

from tcc_api_client import TotalConnect, TotalConnectError

DEFAULT_CREDENTIALS_FILE = os.path.expanduser("~/.tcc_credentials.json")
DEFAULT_SESSION_FILE = os.path.expanduser("~/.tcc_mcp_session.json")
DEFAULT_EXPORTS_DIR = os.environ.get("TCC_EXPORTS_DIR",
                                     os.path.expanduser("~/.tcc_exports"))

server = MCPServer(
    name="honeywell-totalconnect",
    title="Honeywell Total Connect Comfort",
    description=(
        "Control and read your Total Connect Comfort (Resideo) thermostats "
        "through the mytotalconnectcomfort.com web portal: setpoints, "
        "schedules, alerts, locations, devices."
    ),
    instructions=(
        "All device IDs and location IDs are numeric; discover them with "
        "list_locations and get_location_zones. Setpoint writes return "
        "{'success': 1} on the wire; verify with get_device_data before "
        "telling the user a change stuck (thermostats occasionally fail to "
        "acknowledge, which then shows up as an alert). The portal rate-limits "
        "login; prefer the cached session and only call login when "
        "session_status reports it expired."
    ),
)

_tcc: TotalConnect | None = None


def get_tcc() -> TotalConnect:
    """Lazily create the shared client."""
    global _tcc
    if _tcc is None:
        _tcc = TotalConnect(
            timeout=45,
            session_file=os.environ.get("TCC_SESSION_FILE", DEFAULT_SESSION_FILE),
            exports_dir=DEFAULT_EXPORTS_DIR,
        )
    return _tcc


def _credentials():
    path = os.environ.get("TCC_CREDENTIALS_FILE", DEFAULT_CREDENTIALS_FILE)
    if os.environ.get("TCC_USERNAME") and os.environ.get("TCC_PASSWORD"):
        return os.environ["TCC_USERNAME"], os.environ["TCC_PASSWORD"]
    try:
        with open(path) as f:
            d = json.load(f)
        return d["username"], d["password"]
    except (OSError, ValueError, KeyError):
        return None, None


def _err(e: Exception) -> dict:
    return {"ok": False, "error": str(e),
            "type": type(e).__name__}


def _ok(data):
    return {"ok": True, "data": data}


def _audit(tool: str, **args):
    """Log tool invocations to stderr (stdout is the MCP protocol stream).

    Write tools and the raw escape hatch are audited so there is a record of
    what changed the account; credentials are never logged, and an audit
    failure must never break the tool call.
    """
    try:
        print(f"[tcc-audit {time.strftime('%Y-%m-%dT%H:%M:%S')}] {tool} "
              f"{json.dumps(args, default=str)}", file=sys.stderr, flush=True)
    except Exception:
        pass


# ------------------------------------------------------------------ session

@server.tool(name="login", description=(
    "Log in to Total Connect Comfort and cache the session. Returns ok=true "
    "on success. Avoid calling repeatedly - the server rate-limits login "
    "(TooManyAttempts); the session persists across server restarts."))
def login_tool(username: str | None = None,
               password: str | None = None) -> dict:
    """username/password fall back to env/credentials file."""
    u, p = (username or _credentials()[0]), (password or _credentials()[1])
    if not u or not p:
        return _err(TotalConnectError(
            "no credentials: pass username+password or set TCC_USERNAME/"
            "TCC_PASSWORD or ~/.tcc_credentials.json"))
    try:
        _audit("login", username=u)  # password never logged
        get_tcc().login(u, p)
        return _ok({"message": "logged in; session cached"})
    except Exception as e:
        return _err(e)


@server.tool(name="logout", description="Log out of the portal (GET /Account/LogOff).")
def logout_tool() -> dict:
    _audit("logout")
    try:
        # check=False: after logging off, the portal's success response IS
        # the login page, which _check would otherwise flag as expired.
        r = get_tcc().raw("/Account/LogOff", check=False)
        return _ok({"message": "logged out", "final_url": r.url})
    except Exception as e:
        return _err(e)


@server.tool(name="session_status", description=(
    "Check whether the cached login session is still valid. Returns "
    "ok + authenticated true when the session is live; ok=false with the "
    "error when it has expired (then call login)."))
def session_status_tool() -> dict:
    tcc = get_tcc()
    try:
        tcc.ensure_authed()
    except Exception as e:
        return _err(e)
    return _ok({"authenticated": True,
                "message": "session valid"})


# ---------------------------------------------------------------- locations

@server.tool(name="list_locations", description=(
    "List the account's locations. Returns LocationID, Name, Url per location."))
def list_locations_tool() -> dict:
    try:
        tcc = get_tcc()
        tcc.ensure_authed()
        return _ok(tcc.locations())
    except Exception as e:
        return _err(e)


@server.tool(name="get_location_zones", description=(
    "Get live zone/device data for a location (temperature, humidity, "
    "alert flags per device). location_id from list_locations."))
def get_location_zones_tool(location_id: int, page: int = 1) -> dict:
    try:
        tcc = get_tcc()
        tcc.ensure_authed()
        return _ok(tcc.device_status(location_id, page))
    except Exception as e:
        return _err(e)


@server.tool(name="get_dealer_info", description=(
    "Dealer/contact info for a location (HTML page text)."))
def get_dealer_info_tool(location_id: int) -> dict:
    try:
        tcc = get_tcc()
        tcc.ensure_authed()
        return _ok({"html": tcc.dealer_info(location_id)[:8000]})
    except Exception as e:
        return _err(e)


# ------------------------------------------------------------------ devices

@server.tool(name="get_device_data", description=(
    "Live device status: current temperature, setpoints, indoor/outdoor "
    "humidity, fan state, deadband, active alert HTML. device_id from "
    "get_location_zones."))
def get_device_data_tool(device_id: int) -> dict:
    try:
        tcc = get_tcc()
        tcc.ensure_authed()
        return _ok(tcc.device_data(device_id))
    except Exception as e:
        return _err(e)


@server.tool(name="get_device_alerts", description=(
    "List active device alerts across the account, parsed into "
    "{DeviceID, AlertID, Message}."))
def get_device_alerts_tool() -> dict:
    try:
        tcc = get_tcc()
        tcc.ensure_authed()
        return _ok(tcc.parse_alerts())
    except Exception as e:
        return _err(e)


@server.tool(name="acknowledge_alert", description=(
    "Acknowledge (clear) a device alert. IDs come from get_device_alerts. "
    "This is a WRITE operation on the account."))
def acknowledge_alert_tool(device_id: int, alert_id: int) -> dict:
    _audit("acknowledge_alert", device_id=device_id, alert_id=alert_id)
    try:
        tcc = get_tcc()
        tcc.ensure_authed()
        tcc.acknowledge_alert(device_id, alert_id)
        return _ok({"message": f"acknowledged alert {alert_id} on device {device_id}"})
    except Exception as e:
        return _err(e)


@server.tool(name="set_setpoints", description=(
    "Change thermostat setpoints/switches (WRITE). Pass at least one of "
    "heat/cool; null values leave fields unchanged. heat_on/cool_on: 1 to "
    "turn the stage on, 0 off. Returns the server's {success:1} ack. Verify "
    "afterwards with get_device_data (thermostats occasionally don't "
    "acknowledge and raise an alert)."))
def set_setpoints_tool(device_id: int, heat: float | None = None,
                       cool: float | None = None,
                       heat_on: int | None = None,
                       cool_on: int | None = None,
                       fan: str | None = None) -> dict:
    _audit("set_setpoints", device_id=device_id, heat=heat, cool=cool,
           heat_on=heat_on, cool_on=cool_on, fan=fan)
    try:
        tcc = get_tcc()
        tcc.ensure_authed()
        res = tcc.set_setpoints(device_id, heat=heat, cool=cool,
                               heat_on=heat_on, cool_on=cool_on, fan=fan)
        return _ok(res)
    except Exception as e:
        return _err(e)


@server.tool(name="get_device_settings", description=(
    "Device settings + alert configuration (GET Menu/GetData)."))
def get_device_settings_tool(device_id: int) -> dict:
    try:
        tcc = get_tcc()
        tcc.ensure_authed()
        return _ok(tcc.get_settings_data(device_id))
    except Exception as e:
        return _err(e)


@server.tool(name="get_schedule", description=(
    "Full weekly schedule for a device (list of SchedulePeriods with "
    "day/period/start-time/setpoints/fan)."))
def get_schedule_tool(device_id: int) -> dict:
    try:
        tcc = get_tcc()
        tcc.ensure_authed()
        return _ok(tcc.get_schedule(device_id))
    except Exception as e:
        return _err(e)


@server.tool(name="save_schedule_period", description=(
    "Edit ONE schedule period and save it (WRITE). period is the PeriodID "
    "from get_schedule (e.g. '0_1' = day 0 period 1). Pass only the fields "
    "to change; the rest are re-sent unchanged from the current schedule. "
    "Use send_schedule afterwards to commit the whole schedule to the "
    "thermostat."))
def save_schedule_period_tool(device_id: int, period: str,
                              heat: float | None = None,
                              cool: float | None = None,
                              fan_mode: str | None = None,
                              fan_enabled: bool | None = None) -> dict:
    _audit("save_schedule_period", device_id=device_id, period=period,
           heat=heat, cool=cool, fan_mode=fan_mode, fan_enabled=fan_enabled)
    try:
        tcc = get_tcc()
        tcc.ensure_authed()
        tcc.edit_schedule_period(device_id, period, heat=heat, cool=cool,
                                fan_mode=fan_mode, fan_enabled=fan_enabled)
        return _ok({"message": f"saved period {period} on device {device_id}; "
                               "call send_schedule to commit to the thermostat"})
    except Exception as e:
        return _err(e)


@server.tool(name="send_schedule", description=(
    "Commit the (possibly edited) schedule to the thermostat (WRITE)."))
def send_schedule_tool(device_id: int) -> dict:
    _audit("send_schedule", device_id=device_id)
    try:
        tcc = get_tcc()
        tcc.ensure_authed()
        tcc.send_schedule(device_id)
        return _ok({"message": f"schedule sent to device {device_id}"})
    except Exception as e:
        return _err(e)


@server.tool(name="discard_schedule_changes", description=(
    "Discard pending schedule edits on the server side (WRITE)."))
def discard_schedule_changes_tool() -> dict:
    _audit("discard_schedule_changes")
    try:
        tcc = get_tcc()
        tcc.ensure_authed()
        tcc.discard_schedule_changes()
        return _ok({"message": "schedule changes discarded"})
    except Exception as e:
        return _err(e)


@server.tool(name="get_humidifier_data", description=(
    "Humidifier data for a device (only for humidification-capable devices)."))
def get_humidifier_data_tool(device_id: int) -> dict:
    try:
        tcc = get_tcc()
        tcc.ensure_authed()
        return _ok({"html": tcc.get_humidifier_data(device_id)[:6000]})
    except Exception as e:
        return _err(e)


@server.tool(name="get_dehumidifier_data", description=(
    "Dehumidifier data for a device (only for dehumidification-capable devices)."))
def get_dehumidifier_data_tool(device_id: int) -> dict:
    try:
        tcc = get_tcc()
        tcc.ensure_authed()
        return _ok({"html": tcc.get_dehumidifier_data(device_id)[:6000]})
    except Exception as e:
        return _err(e)


@server.tool(name="get_forecasts", description=(
    "Energy/weather forecasts page (HTML text)."))
def get_forecasts_tool() -> dict:
    try:
        tcc = get_tcc()
        tcc.ensure_authed()
        return _ok({"html": tcc.forecasts()[:6000]})
    except Exception as e:
        return _err(e)


@server.tool(name="export_report", description=(
    "Generate a temperature report for every location and write it to the "
    "exports directory (JSON + CSV). In Docker the exports dir lives on the "
    "storage volume at /data/exports, so files persist. Returns the file "
    "paths and device count."))
def export_report_tool() -> dict:
    _audit("export_report")
    try:
        tcc = get_tcc()
        tcc.ensure_authed()
        return _ok(tcc.export_report())
    except Exception as e:
        return _err(e)


@server.tool(name="raw_request", description=(
    "Escape hatch: issue any other GET request against the portal (path "
    "relative to /portal, e.g. '/MyAccount' or '/Device/Menu/1234/tab2'). "
    "Returns the response body text (truncated to 8000 chars)."))
def raw_request_tool(path: str) -> dict:
    _audit("raw_request", path=path)
    try:
        tcc = get_tcc()
        tcc.ensure_authed()
        # tcc.raw() joins onto base (which ends in /portal); accept both forms.
        p = path if path.startswith("/") else "/" + path
        if p.startswith("/portal/"):
            p = p[len("/portal"):]
        r = tcc.raw(p)
        return _ok({"status": r.status_code, "url": r.url,
                    "text": r.text[:8000]})
    except Exception as e:
        return _err(e)


def main():
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
