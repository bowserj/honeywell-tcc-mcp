# honeywell-tcc-mcp

An MCP (Model Context Protocol) server that drives the
[mytotalconnectcomfort.com](https://mytotalconnectcomfort.com/portal) web portal:
read locations, devices, alerts, and schedules, and write setpoints / schedule
periods.

The wire protocol is a plain ASP.NET session (cookie auth, no API keys).
The underlying client is `tcc_api_client.py` (also usable standalone, with
a CLI).

## Layout

```
server.py         MCP server (stdio), 20 tools
tcc_api_client.py TotalConnect client + CLI (login/locations/zones/data/setpoint/export/...)
requirements.txt  requests + mcp
```

## Setup

```bash
pip install -r requirements.txt
```

Credentials, one of:

1. `TCC_USERNAME` / `TCC_PASSWORD` env vars (recommended for MCP clients), or
2. `~/.tcc_credentials.json`: `{"username": "you@example.com", "password": "..."}`

The session cookie caches to `~/.tcc_mcp_session.json` (override with
`TCC_SESSION_FILE`) so the server stays logged in across restarts. The portal
rate-limits login (`/portal/Error/TooManyAttempts`); the `login` tool is for
refreshing a dead session, not for use on every start.

## Run

```bash
python server.py          # stdio, as any MCP client expects
```

## Client config (Claude Desktop / generic MCP)

```json
{
  "mcpServers": {
    "honeywell": {
      "command": "python",
      "args": ["~/projects/honeywell/mcp/server.py"],
      "env": {
        "TCC_USERNAME": "you@example.com",
        "TCC_PASSWORD": "..."
      }
    }
  }
}
```

## Tools

| Tool | Wire call | Notes |
|---|---|---|
| `login` | POST /portal | refresh cached session |
| `logout` | GET /Account/LogOff | |
| `session_status` | GET /Locations probe | |
| `list_locations` | GET /Locations | LocationID + Name per location |
| `get_location_zones` | POST /Device/GetZoneListData | live per-device temp/humidity/alerts |
| `get_device_data` | GET /Device/CheckDataSession/{id} | full uiData + fan + alerts |
| `get_device_alerts` | GET /Device/Alerts | parsed {DeviceID, AlertID, Message} |
| `acknowledge_alert` | POST /Device/AcknowledgeAlert | write |
| `set_setpoints` | POST /Device/SubmitControlScreenChanges | write; verify after |
| `get_device_settings` | POST /Device/Menu/GetData | |
| `get_schedule` | POST /Device/Menu/GetScheduleData/{id} | 28 periods/week |
| `save_schedule_period` | POST /Device/Menu/EditScheduledPeriod/{id} | write; one period |
| `send_schedule` | POST /Device/Menu/SendSchedule | write; commit to thermostat |
| `discard_schedule_changes` | POST /Device/Menu/DiscardChangesInSchedule | write |
| `get_humidifier_data` / `get_dehumidifier_data` | GET /Device/Menu/Get{De}HumData/{id} | humidification-capable devices only |
| `get_forecasts` | GET /Device/Forecasts | |
| `get_dealer_info` | GET /DealerInfo/Index/location/{id} | |
| `raw_request` | any GET under /portal | escape hatch |
| `export_report` | all locations + zones | writes JSON + CSV to the exports dir (volume) |

All tools return `{"ok": true, "data": ...}` or `{"ok": false, "error": ...}`.

## Examples

Import the client directly (the MCP server is just a thin wrapper around it).
Credentials come from `TCC_USERNAME` / `TCC_PASSWORD` env vars or
`~/.tcc_credentials.json`:

```python
from tcc_api_client import TotalConnect

tcc = TotalConnect()
tcc.login()          # or skip if a cached session in ~/.tcc_mcp_session.json is valid
```

Report temperatures at every location:

```python
for loc in tcc.locations():
    print(loc["LocationID"], loc["Name"])
    for dev in tcc.device_status(loc["LocationID"]):
        print(f"  {dev['DeviceID']}  {dev['DispTemp']:.0f}{dev['DispUnits']}  "
              f"humidity {dev.get('IndoorHumi')}%  alerts={dev['Alerts']}")
```

Set a temperature:

```python
res = tcc.set_setpoints(1234567, cool=72)          # {"success": 1}
ui = tcc.device_data(1234567)["latestData"]["uiData"]
print(ui["CoolSetpoint"], ui["StatusCool"])       # verify the change stuck
```

Turn cooling on/off, or change both setpoints at once:

```python
tcc.set_setpoints(1234567, cool_on=0)              # disable cooling stage
tcc.set_setpoints(1234567, heat=66, cool=70)
```

Change one schedule period and commit it to the thermostat:

```python
sched = tcc.get_schedule(1234568)
pid = sched["Schedule"]["SchedulePeriods"][3]["PeriodID"]  # e.g. "1_1"
tcc.edit_schedule_period(1234568, pid, cool=74)
tcc.send_schedule(1234568)
```

Check (and clear) alerts:

```python
for a in tcc.parse_alerts():
    print(a)
    tcc.acknowledge_alert(a["DeviceID"], a["AlertID"])
```

The same tasks via the CLI (useful for cron jobs):

```bash
python tcc_api_client.py locations
python tcc_api_client.py zones 1000000
python tcc_api_client.py data 1234567
python tcc_api_client.py setpoint 1234567 --cool 72
python tcc_api_client.py export          # writes report_<timestamp>.json + .csv
```

Export a report (JSON + CSV, all locations) to the exports directory:

```python
paths = tcc.export_report()
print(paths)  # {"json": "/data/exports/report_20261005_143022.json", "csv": "...", "devices": 4}
```

## Caveats

- Thermostats occasionally fail to acknowledge setpoint changes; the portal
  then raises an alert ("did not acknowledge the changes submitted at ...").
  Verify writes with `get_device_data` / `get_device_alerts`.
- `save_schedule_period` re-sends the other current field values of the period
  (the web app requires `Orig*` fields), so it is safe to change one value.
- Page-route endpoints (Location/Edit, Gateway/Register, MyAccount/... etc.)
  are reachable via `raw_request`; they are form pages, not JSON APIs.

## Docker

```bash
docker build -t honeywell-tcc-mcp .
docker run --rm -it \
  --name honeywell-tcc-mcp \
  -e TCC_USERNAME -e TCC_PASSWORD \
  -v tcc-data:/data \
  honeywell-tcc-mcp
```

- The container runs the MCP server on stdio (attach with `-i`, or launch it
  from your MCP client, see below). It launches with the fixed name
  `honeywell-tcc-mcp`, so only one instance can run at a time; `--rm` frees
  the name on exit, so the MCP client can relaunch it.
- `/data` is a volume: session cache (`/data/tcc_session.json`), optional
  credentials file (`/data/credentials.json`), and all report exports
  (`/data/exports/`, via `export_report` or the CLI `export` command) live
  there, not in the image. The image itself contains no secrets.
- Runs as non-root user `mcp` (uid 10001).

MCP client config that launches the container (Claude Desktop / any client):

```json
{
  "mcpServers": {
    "honeywell": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "--name", "honeywell-tcc-mcp",
        "-e", "TCC_USERNAME=you@example.com",
        "-e", "TCC_PASSWORD=...",
        "-v", "tcc-data:/data",
        "honeywell-tcc-mcp"
      ]
    }
  }
}
```

