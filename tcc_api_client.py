"""
Total Connect Comfort (Honeywell) web-portal API client.

Auth is a plain ASP.NET session cookie; no API keys, no CSRF token on AJAX calls.

All 47 portal endpoints are exposed. Methods mirror the wire format
exactly (JSON bodies vs form fields vs query params per endpoint).

Usage:
    from tcc_api_client import TotalConnect
    tcc = TotalConnect("user@gmail.com", "Password1")
    tcc.login()

    for loc in tcc.locations():
        print(loc["LocationID"], loc["Name"])
        for dev in tcc.device_status(loc["LocationID"]):
            print(dev["DeviceID"], dev["DispTemp"], dev["DispUnits"])

    tcc.set_setpoints(device_id=1234567, heat=68, cool=72)
    print(tcc.device_data(1234567)["latestData"]["uiData"]["DispTemperature"])
    print(tcc.get_schedule(1234568)["Schedule"]["SchedulePeriods"][0]["StartTime"]["Hours"])

CLI (all commands work headless):
    python tcc_api_client.py login
    python tcc_api_client.py locations
    python tcc_api_client.py zones <locationId>
    python tcc_api_client.py data <deviceId>
    python tcc_api_client.py setpoint <deviceId> [--heat N] [--cool N] [--heat-on 0|1] [--cool-on 0|1] [--fan auto|low|high|...]
    python tcc_api_client.py schedule <deviceId>
    python tcc_api_client.py send-schedule <deviceId>
    python tcc_api_client.py alerts
    python tcc_api_client.py settings <deviceId>
    python tcc_api_client.py edit-period <deviceId> <period> [--heat N] [--cool N] [--fan-mode auto|...] [--fan on|off]
    python tcc_api_client.py export                  # write report to exports dir (JSON + CSV)
    python tcc_api_client.py <raw-endpoint-path>        # passthrough GET of any other route

Credentials: positional, or env TCC_USERNAME / TCC_PASSWORD.
Session: persisted to ~/.tcc_session.json (or $TCC_SESSION_FILE) so CLI calls
skip re-login; pass --fresh to force a new login.
Exports: reports land in ~/.tcc_exports (or $TCC_EXPORTS_DIR).
"""

import csv
import json
import os
import re
import sys
import time
from urllib.parse import urlencode

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This client needs `pip install requests`")

DEFAULT_BASE = "https://mytotalconnectcomfort.com/portal"
DEFAULT_SESSION_FILE = os.path.expanduser("~/.tcc_session.json")
DEFAULT_EXPORTS_DIR = os.path.expanduser("~/.tcc_exports")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36")

# FanMode values accepted by the schedule editor (observed: Auto)
FAN_MODES = {"Auto", "Circulate", "Off", "Low", "Medium", "High"}


class TotalConnectError(Exception):
    pass


class TotalConnect:
    """Session-based client for the Total Connect Comfort portal."""

    def __init__(self, base_url=DEFAULT_BASE, session_file=None,
                 exports_dir=None, timeout=30, verify=True):
        self.base = base_url.rstrip("/")
        self.session_file = session_file or os.environ.get(
            "TCC_SESSION_FILE", DEFAULT_SESSION_FILE)
        self.exports_dir = exports_dir or os.environ.get(
            "TCC_EXPORTS_DIR", DEFAULT_EXPORTS_DIR)
        self.timeout = timeout
        self.verify = verify
        self._s = requests.Session()
        self._authed_flag = False
        self._s.headers.update({
            "User-Agent": UA,
            "Accept-Language": "en-US,en;q=0.9",
        })

    # ------------------------------------------------------------------ core

    def _url(self, path):
        return path if path.startswith("http") else self.base + path

    def _headers(self, **extra):
        h = {"X-Requested-With": "XMLHttpRequest"}
        h.update(extra)
        return h

    def _check(self, r, context=""):
        if r.status_code == 401 or "login" in r.url.lower() and r.status_code in (200, 302):
            # an authed call that bounced to login = expired session
            if r.status_code == 401 or r.history:
                raise TotalConnectError(
                    f"Session expired (got {r.status_code}) during {context}; re-login needed.")
        if r.status_code >= 400:
            raise TotalConnectError(f"{context}: HTTP {r.status_code}: {r.text[:300]}")
        return r

    # ------------------------------------------------------------------ auth

    def login(self, username=None, password=None, remember=True, time_offset=300):
        """POST /portal form login. Returns True on success.

        time_offset: seconds the local clock is ahead of UTC (300 = UTC-5).
        """
        username = username or os.environ.get("TCC_USERNAME")
        password = password or os.environ.get("TCC_PASSWORD")
        if not username or not password:
            raise TotalConnectError(
                "credentials missing: pass username/password or set "
                "TCC_USERNAME/TCC_PASSWORD env vars")
        form = {
            "timeOffset": str(time_offset),
            "UserName": username,
            "Password": password,
            "RememberMe": str(remember).lower(),
        }
        self._s.headers.pop("Referer", None)
        r = self._s.post(self._url("/"), data=form, allow_redirects=True,
                         headers={"Content-Type": "application/x-www-form-urlencoded"},
                         timeout=self.timeout, verify=self.verify)
        # Good login ends on /portal/ or /portal/Locations with ASPXAUTH set.
        authed = any(c.name.startswith(".ASPXAUTH_TRUEHOME")
                     for c in self._s.cookies)
        if not authed:
            final = r.url
            if "TooManyAttempts" in final:
                raise TotalConnectError(
                    "server rate-limited the login (TooManyAttempts). "
                    "Wait a few minutes and retry.")
            raise TotalConnectError(
                f"login failed (final URL {final}); bad credentials or CAPTCHA")
        self._authed_flag = True
        self._save_session()
        return True

    def _save_session(self):
        try:
            with open(self.session_file, "w") as f:
                json.dump({c.name: c.value for c in self._s.cookies}, f)
        except OSError:
            pass

    def export_report(self):
        """Build a temperature report for every location and write it to
        exports_dir as JSON and CSV.

        Returns {"json": path, "csv": path, "generated_at": iso, "devices": n}.
        """
        os.makedirs(self.exports_dir, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        locations = []
        for loc in self.locations():
            devices = []
            try:
                for dev in self.device_status(loc["LocationID"]):
                    devices.append({
                        "DeviceID": dev.get("DeviceID"),
                        "Temp": dev.get("DispTemp"),
                        "Units": dev.get("DispUnits"),
                        "Humidity": dev.get("IndoorHumi"),
                        "Lost": dev.get("IsLost"),
                        "Alerts": dev.get("Alerts", []),
                    })
            except TotalConnectError:
                pass
            locations.append({
                "LocationID": loc.get("LocationID"),
                "Name": loc.get("Name"),
                "Devices": devices,
            })
        report = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "locations": locations,
        }
        jpath = os.path.join(self.exports_dir, f"report_{stamp}.json")
        with open(jpath, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=1)
        cpath = os.path.join(self.exports_dir, f"report_{stamp}.csv")
        with open(cpath, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["location_id", "location_name", "device_id",
                        "temp", "units", "humidity", "lost", "alerts"])
            n = 0
            for loc in locations:
                for dev in loc["Devices"]:
                    w.writerow([loc["LocationID"], loc["Name"], dev["DeviceID"],
                                dev["Temp"], dev["Units"], dev["Humidity"],
                                dev["Lost"], "; ".join(dev["Alerts"])])
                    n += 1
        return {"json": jpath, "csv": cpath,
                "generated_at": report["generated_at"], "devices": n}

    def load_session(self):
        """Restore cookies from a previous login. Returns True if we look authed."""
        try:
            with open(self.session_file) as f:
                jar = json.load(f)
        except (OSError, ValueError):
            return False
        for name, value in jar.items():
            self._s.cookies.set(name, value, domain="mytotalconnectcomfort.com")
        # Liveness probe: /Locations is the home page for an authed session.
        for attempt in (1, 2):
            try:
                r = self._s.get(self._url("/Locations"), timeout=self.timeout,
                                allow_redirects=True, verify=self.verify)
            except requests.RequestException:
                return False
            if "TooManyAttempts" in r.url:
                time.sleep(5 * attempt)
                continue
            authed = any(c.name.startswith(".ASPXAUTH_TRUEHOME")
                         for c in self._s.cookies) and \
                "/portal/Locations" in r.url
            return authed
        return False

    def ensure_authed(self):
        if any(c.name.startswith(".ASPXAUTH_TRUEHOME") for c in self._s.cookies) \
                and self._authed_flag:
            return
        if self.load_session():
            self._authed_flag = True
            return
        self.login()
        self._authed_flag = True

    # ------------------------------------------------------------------ read

    def locations(self):
        """GET /portal/Locations -> list of location dicts (parsed from the rows).

        Each entry: {LocationID, Name, Url, ClickEnabled, Raw}.
        """
        r = self._check(self._s.get(self._url("/Locations"), timeout=self.timeout,
                                    verify=self.verify), "locations")
        return self._parse_location_list(r.text)

    def _parse_location_list(self, html):
        """Locations page renders each location as <tr data-id=... data-url=...>
        containing a <div class="location-name">Name</div>. Parse that."""
        out = []
        for m in re.finditer(
                r'<tr\b[^>]*data-id="(\d+)"[^>]*>(.*?)</tr>', html, re.S):
            lid, block = m.group(1), m.group(2)
            attrs = re.search(r'data-id="\d+"([^>]*)', m.group(0))
            attrstr = attrs.group(1) if attrs else ""
            url = re.search(r'data-url="([^"]*)"', attrstr)
            click = re.search(r'data-clickenabled="([^"]*)"', attrstr)
            name = re.search(r'class="location-name">\s*(.*?)\s*<', block, re.S)
            out.append({
                "LocationID": int(lid),
                "Name": name.group(1).strip() if name else None,
                "Url": url.group(1) if url else None,
                "ClickEnabled": (click.group(1) == "True") if click else None,
            })
        return out

    def device_status(self, location_id, page=1):
        """POST /portal/Device/GetZoneListData?locationId=&page= -> zone/device list."""
        r = self._check(self._s.post(
            self._url(f"/Device/GetZoneListData?locationId={location_id}&page={page}"),
            headers=self._headers(**{"Content-Type": "application/json; charset=utf-8",
                                     "Origin": "https://mytotalconnectcomfort.com"}),
            data=b"", timeout=self.timeout, verify=self.verify), "device_status")
        return self._json(r)

    def device_data(self, device_id):
        """GET /portal/Device/CheckDataSession/{id} -> live status + uiData."""
        r = self._check(self._s.get(self._url(f"/Device/CheckDataSession/{device_id}"),
                                    headers=self._headers(), timeout=self.timeout,
                                    verify=self.verify), f"device_data({device_id})")
        return self._json(r)

    def alerts(self):
        """GET /portal/Device/Alerts -> HTML fragment of active alerts.

        Also returns parsed alerts via `parse_alerts`.
        """
        r = self._check(self._s.get(self._url("/Device/Alerts"),
                                    headers=self._headers(**{"Accept": "text/html, */*; q=0.01"}),
                                    timeout=self.timeout, verify=self.verify), "alerts")
        self._last_alerts_html = r.text
        return r.text

    def parse_alerts(self):
        """Parse the last fetched alerts into a list of dicts.

        Each: {DeviceID, AlertID, Message}. Requires `alerts()` to have run.
        """
        html = getattr(self, "_last_alerts_html", "")
        if not html:
            self.alerts()
            html = getattr(self, "_last_alerts_html", "")
        out = []
        for m in re.finditer(
                r'<li>\s*<span>(.*?)</span>.*?name="DeviceID"\s+type="hidden"\s+value="(\d+)"'
                r'.*?name="AlertID"\s+type="hidden"\s+value="(\d+)"',
                html, re.S):
            out.append({
                "DeviceID": int(m.group(2)),
                "AlertID": int(m.group(3)),
                "Message": re.sub(r"\s+", " ", m.group(1)).strip(),
            })
        return out

    def acknowledge_alert(self, device_id, alert_id):
        """POST /portal/Device/AcknowledgeAlert (form: DeviceID, AlertID).

        Clears a device alert. Returns the response text.
        """
        r = self._check(self._s.post(
            self._url("/Device/AcknowledgeAlert"),
            headers=self._headers(**{"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                                     "Origin": "https://mytotalconnectcomfort.com"}),
            data=urlencode({"DeviceID": str(device_id), "AlertID": str(alert_id)}),
            timeout=self.timeout, verify=self.verify), f"acknowledge_alert({device_id},{alert_id})")
        return r.text

    def dealer_info(self, location_id):
        """GET /portal/DealerInfo/Index/location/{id} -> dealer info page text."""
        r = self._check(self._s.get(self._url(f"/DealerInfo/Index/location/{location_id}"),
                                    timeout=self.timeout, verify=self.verify),
                        "dealer_info")
        return r.text

    def forecasts(self):
        """GET /portal/Device/Forecasts -> forecasts page."""
        r = self._check(self._s.get(self._url("/Device/Forecasts"),
                                    timeout=self.timeout, verify=self.verify), "forecasts")
        return r.text

    # ------------------------------------------------------------ device write

    def set_setpoints(self, device_id, heat=None, cool=None, heat_on=None,
                      cool_on=None, fan=None, system_switch=None):
        """POST /portal/Device/SubmitControlScreenChanges.

        heat/cool: target setpoints (F). heat_on/cool_on: 1 to enable, 0 to disable,
        None to leave unchanged. fan: fan-mode string (device-dependent).
        """
        if heat is None and cool is None and heat_on is None and cool_on is None \
                and fan is None and system_switch is None:
            raise TotalConnectError("set_setpoints: nothing to change")
        body = {
            "DeviceID": device_id,
            "SystemSwitch": system_switch,
            "HeatSetpoint": heat,
            "CoolSetpoint": cool,
            "HeatNextPeriod": None,
            "CoolNextPeriod": None,
            "StatusHeat": heat_on,
            "StatusCool": cool_on,
            "FanMode": fan,
        }
        r = self._check(self._s.post(
            self._url("/Device/SubmitControlScreenChanges"),
            headers=self._headers(**{"Content-Type": "application/json; charset=UTF-8",
                                     "Origin": "https://mytotalconnectcomfort.com"}),
            data=json.dumps(body), timeout=self.timeout, verify=self.verify),
            f"set_setpoints({device_id})")
        return self._json(r)

    # ---------------------------------------------------------------- schedule

    def get_settings_data(self, device_id):
        """POST /portal/Device/Menu/GetData?deviceID={id} -> settings + alert config."""
        r = self._check(self._s.post(
            self._url(f"/Device/Menu/GetData?deviceID={device_id}"),
            headers=self._headers(), timeout=self.timeout, verify=self.verify),
            f"get_settings_data({device_id})")
        return self._json(r)

    def get_schedule(self, device_id):
        """POST /portal/Device/Menu/GetScheduleData/{id} -> full schedule."""
        r = self._check(self._s.post(self._url(f"/Device/Menu/GetScheduleData/{device_id}"),
                                     headers=self._headers(), timeout=self.timeout,
                                     verify=self.verify), f"get_schedule({device_id})")
        return self._json(r)

    def get_schedule_periods_header(self, device_id):
        """GET /portal/Device/Menu/GetSchedulePeriodsHeader/{id}."""
        r = self._check(self._s.get(self._url(f"/Device/Menu/GetSchedulePeriodsHeader/{device_id}"),
                                    headers=self._headers(), timeout=self.timeout,
                                    verify=self.verify),
                        f"get_schedule_periods_header({device_id})")
        return r.text

    def get_humidifier_data(self, device_id):
        """GET /portal/Device/Menu/GetHumData/{id}."""
        r = self._check(self._s.get(self._url(f"/Device/Menu/GetHumData/{device_id}"),
                                    headers=self._headers(), timeout=self.timeout,
                                    verify=self.verify), f"get_humidifier_data({device_id})")
        return r.text

    def get_dehumidifier_data(self, device_id):
        """GET /portal/Device/Menu/GetDehumData/{id}."""
        r = self._check(self._s.get(self._url(f"/Device/Menu/GetDehumData/{device_id}"),
                                    headers=self._headers(), timeout=self.timeout,
                                    verify=self.verify), f"get_dehumidifier_data({device_id})")
        return r.text

    def get_schedule_period_editor(self, device_id, period):
        """GET /portal/Device/Menu/EditScheduledPeriod/{id}?period={day}_{n} -> HTML form."""
        r = self._check(self._s.get(
            self._url(f"/Device/Menu/EditScheduledPeriod/{device_id}?period={period}"),
            headers=self._headers(), timeout=self.timeout, verify=self.verify),
            f"get_schedule_period_editor({device_id},{period})")
        return r.text

    def edit_schedule_period(self, device_id, period, heat=None, cool=None,
                             fan_mode=None, fan_enabled=None):
        """POST /portal/Device/Menu/EditScheduledPeriod/{id} (form-encoded).

        Saves ONE period. The form expects the original values back as
        PeriodTemplate.Orig* fields. This helper fetches the current schedule first,
        so you can change just one period and re-POST it.
        """
        schedule = self.get_schedule(device_id)
        # SchedulePeriods is a list of {"PeriodID":"0_0","Day":0,"PeriodType":0,...}
        periods = (schedule.get("Schedule") or {}).get("SchedulePeriods", [])
        match = None
        for p in periods:
            pid = str(p.get("PeriodID", ""))
            if pid == str(period) or pid.split("_")[0] + "_" + str(p.get("PeriodType", "")) == str(period):
                match = p
                break
        if match is None:
            raise TotalConnectError(f"schedule period {period!r} not found on device {device_id}")
        orig = match
        new_heat = heat if heat is not None else self._num(orig.get("HeatSetpoint"))
        new_cool = cool if cool is not None else self._num(orig.get("CoolSetpoint"))
        new_fan = fan_mode if fan_mode is not None else self._str(orig.get("FanMode"))
        fan_flag = "True" if (fan_enabled if fan_enabled is not None
                              else self._bool(orig.get("ScheduleFan"))) else "False"
        form = self._period_form_fields(orig, new_heat, new_cool, new_fan, fan_flag)
        r = self._check(self._s.post(
            self._url(f"/Device/Menu/EditScheduledPeriod/{device_id}"),
            headers=self._headers(**{"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                                     "Origin": "https://mytotalconnectcomfort.com"}),
            data=form, timeout=self.timeout, verify=self.verify),
            f"edit_schedule_period({device_id},{period})")
        self.get_schedule(device_id)  # refresh server-side cache (observed behavior)
        return r.text

    def _period_form_fields(self, orig, heat, cool, fan_mode, fan_flag):
        start = self._time_str(orig.get("StartTime"))
        fan_mode = fan_mode if fan_mode in FAN_MODES else "Auto"
        fields = {
            "PeriodTemplate.StartTime": start,
            "PeriodTemplate.OrigStartTime": start,
            "PeriodTemplate.HeatSetpoint": str(heat),
            "PeriodTemplate.OrigHeatSetpoint": str(self._num(orig.get("HeatSetpoint"))),
            "PeriodTemplate.CoolSetpoint": str(cool),
            "PeriodTemplate.OrigCoolSetpoint": str(self._num(orig.get("CoolSetpoint"))),
            "ScheduleFan": fan_flag,
            "PeriodTemplate.FanMode": fan_mode,
            "PeriodTemplate.OrigFanMode": self._str(orig.get("FanMode")),
            "PeriodTemplate.PeriodType": str(orig.get("PeriodType", 0)),
            "PeriodTemplate.Day": str(orig.get("Day", 0)),
        }
        return urlencode(fields)

    @staticmethod
    def _num(v):
        try:
            return float(v) if v is not None else 0
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _str(v):
        return v if isinstance(v, str) else str(v or "Auto")

    @staticmethod
    def _bool(v):
        if isinstance(v, str):
            return v.strip().lower() == "true"
        return bool(v)

    def send_schedule(self, device_id):
        """POST /portal/Device/Menu/SendSchedule?deviceId={id} -> apply committed schedule."""
        r = self._check(self._s.post(
            self._url(f"/Device/Menu/SendSchedule?deviceId={device_id}"),
            headers=self._headers(), timeout=self.timeout, verify=self.verify),
            f"send_schedule({device_id})")
        return r.text

    def discard_schedule_changes(self):
        """POST /portal/Device/Menu/DiscardChangesInSchedule."""
        r = self._check(self._s.post(
            self._url("/Device/Menu/DiscardChangesInSchedule"),
            headers=self._headers(), timeout=self.timeout, verify=self.verify),
            "discard_schedule_changes")
        return r.text

    # ------------------------------------------------------------ location mgmt

    def create_location(self):
        """GET /portal/Location/Create -> form page (add location)."""
        r = self._check(self._s.get(self._url("/Location/Create"),
                                    timeout=self.timeout, verify=self.verify), "create_location")
        return r.text

    def edit_location(self, location_id):
        """GET /portal/Location/Edit/{id}."""
        r = self._check(self._s.get(self._url(f"/Location/Edit/{location_id}"),
                                    timeout=self.timeout, verify=self.verify),
                        f"edit_location({location_id})")
        return r.text

    def get_location_list_data(self):
        """GET /portal/Location/GetLocationListData."""
        r = self._check(self._s.get(self._url("/Location/GetLocationListData"),
                                    headers=self._headers(), timeout=self.timeout,
                                    verify=self.verify), "get_location_list_data")
        return r.text

    def get_timezone_by_zipcode(self, zipcode):
        """GET /portal/Location/GetTimeZoneByZipcode."""
        r = self._check(self._s.get(self._url("/Location/GetTimeZoneByZipcode"),
                                    params={"zipcode": zipcode},
                                    headers=self._headers(), timeout=self.timeout,
                                    verify=self.verify), "get_timezone_by_zipcode")
        return r.text

    # ---------------------------------------------------------------- gateway

    def register_gateway(self, location_id):
        """GET /portal/Gateway/Register/location/{id} -> registration page."""
        r = self._check(self._s.get(self._url(f"/Gateway/Register/location/{location_id}"),
                                    timeout=self.timeout, verify=self.verify),
                        f"register_gateway({location_id})")
        return r.text

    def delete_gateway(self, gateway_id):
        """GET /portal/Gateway/Delete/{id} (returns the post-delete page)."""
        r = self._check(self._s.get(self._url(f"/Gateway/Delete/{gateway_id}"),
                                    timeout=self.timeout, verify=self.verify,
                                    allow_redirects=True), f"delete_gateway({gateway_id})")
        return r.url

    # ---------------------------------------------------------------- account

    def my_account(self):
        """GET /portal/MyAccount."""
        r = self._check(self._s.get(self._url("/MyAccount"), timeout=self.timeout,
                                    verify=self.verify), "my_account")
        return r.text

    def edit_account(self):
        """GET /portal/MyAccount/Edit."""
        r = self._check(self._s.get(self._url("/MyAccount/Edit"), timeout=self.timeout,
                                    verify=self.verify), "edit_account")
        return r.text

    def add_friend(self):
        """GET /portal/MyAccount/AddFriend."""
        r = self._check(self._s.get(self._url("/MyAccount/AddFriend"), timeout=self.timeout,
                                    verify=self.verify), "add_friend")
        return r.text

    def edit_friend(self, friend_id):
        """GET /portal/MyAccount/EditFriend/{id}."""
        r = self._check(self._s.get(self._url(f"/MyAccount/EditFriend/{friend_id}"),
                                    timeout=self.timeout, verify=self.verify),
                        f"edit_friend({friend_id})")
        return r.text

    def allow_customer_support(self):
        """GET /portal/MyAccount/AllowCustomerSupport."""
        r = self._check(self._s.get(self._url("/MyAccount/AllowCustomerSupport"),
                                    timeout=self.timeout, verify=self.verify),
                        "allow_customer_support")
        return r.text

    def change_culture(self, culture):
        """GET /portal/Account/ChangeCulture?culture={en-US|en-GB|fr|...}."""
        r = self._check(self._s.get(self._url("/Account/ChangeCulture"),
                                    params={"culture": culture},
                                    timeout=self.timeout, verify=self.verify,
                                    allow_redirects=True), f"change_culture({culture})")
        return r.url

    def get_state_list(self):
        """GET /portal/Account/GetStateList."""
        r = self._check(self._s.get(self._url("/Account/GetStateList"),
                                    headers=self._headers(), timeout=self.timeout,
                                    verify=self.verify), "get_state_list")
        return r.text

    def location_scripts(self):
        """GET /portal/Account/LocationScripts (xhr, HTML)."""
        r = self._check(self._s.get(self._url("/Account/LocationScripts"),
                                    headers=self._headers(), timeout=self.timeout,
                                    verify=self.verify), "location_scripts")
        return r.text

    def change_password(self):
        """GET /portal/Account/ChangePassword -> form page."""
        r = self._check(self._s.get(self._url("/Account/ChangePassword"),
                                    timeout=self.timeout, verify=self.verify),
                        "change_password")
        return r.text

    def forgot_password(self):
        """GET /portal/Account/ForgotPassword -> form page."""
        r = self._check(self._s.get(self._url("/Account/ForgotPassword"),
                                    timeout=self.timeout, verify=self.verify),
                        "forgot_password")
        return r.text

    def terms_and_conditions(self):
        """GET /portal/Account/TermsAndConditions."""
        r = self._check(self._s.get(self._url("/Account/TermsAndConditions"),
                                    timeout=self.timeout, verify=self.verify),
                        "terms_and_conditions")
        return r.text

    # ------------------------------------------------------------------- misc

    def faqs(self):
        r = self._check(self._s.get(self._url("/Home/FAQs"), timeout=self.timeout,
                                    verify=self.verify), "faqs")
        return r.text

    def feedback(self):
        r = self._check(self._s.get(self._url("/Home/Feedback"), timeout=self.timeout,
                                    verify=self.verify), "feedback")
        return r.text

    def set_mobile_view(self):
        r = self._check(self._s.get(self._url("/Home/SetMobileView"), timeout=self.timeout,
                                    verify=self.verify, allow_redirects=True),
                        "set_mobile_view")
        return r.url

    def home_terms(self):
        r = self._check(self._s.get(self._url("/Home/TermsAndConditions"),
                                    timeout=self.timeout, verify=self.verify),
                        "home_terms")
        return r.text

    def control_screen(self, device_id):
        """GET /portal/Device/Control/{id} -> the HTML shell behind the device APIs."""
        r = self._check(self._s.get(self._url(f"/Device/Control/{device_id}"),
                                    timeout=self.timeout, verify=self.verify),
                        f"control_screen({device_id})")
        return r.text

    def device_menu(self, device_id, tab=1):
        """GET /portal/Device/Menu/{id}[/tabN] -> settings menu shell."""
        suffix = f"/tab{tab}" if tab and tab > 1 else ""
        r = self._check(self._s.get(self._url(f"/Device/Menu/{device_id}{suffix}"),
                                    timeout=self.timeout, verify=self.verify),
                        f"device_menu({device_id},tab={tab})")
        return r.text

    def humidifier(self):
        r = self._check(self._s.get(self._url("/Device/Menu/Humidifier"),
                                    timeout=self.timeout, verify=self.verify), "humidifier")
        return r.text

    def dehumidifier(self):
        r = self._check(self._s.get(self._url("/Device/Menu/Dehumidifier"),
                                    timeout=self.timeout, verify=self.verify), "dehumidifier")
        return r.text

    def raw(self, path, method="GET", **kw):
        """Escape hatch: any other portal route, e.g. tcc.raw('/portal/Locations')."""
        r = self._s.request(method, self._url(path), timeout=self.timeout,
                            verify=self.verify, **kw)
        self._check(r, f"{method} {path}")
        return r

    @staticmethod
    def _json(r):
        try:
            return r.json()
        except ValueError:
            return r.text

    # attribute used by ensure_authed (init'd in __init__)
    _authed_flag = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# -------------------------------------------------------------------------- CLI

def _print_json(obj):
    print(json.dumps(obj, indent=1))


def main(argv):
    import argparse
    p = argparse.ArgumentParser(description="Total Connect Comfort portal API client")
    p.add_argument("cmd", nargs="?", help="command")
    p.add_argument("arg", nargs="?", help="command argument (id, path, ...)")
    p.add_argument("arg2", nargs="?", help="second argument (e.g. period id)")
    p.add_argument("--heat", type=float)
    p.add_argument("--cool", type=float)
    p.add_argument("--heat-on", type=int, choices=[0, 1])
    p.add_argument("--cool-on", type=int, choices=[0, 1])
    p.add_argument("--fan", help="fan mode string")
    p.add_argument("--fan-mode", help="schedule fan mode: auto/circulate/off/low/medium/high")
    p.add_argument("--fresh", action="store_true", help="force re-login")
    p.add_argument("--base", default=DEFAULT_BASE)
    p.add_argument("--session-file", default=None)
    p.add_argument("rest", nargs="*")
    a = p.parse_args(argv)

    if a.cmd is None:
        p.print_help()
        return 1

    tcc = TotalConnect(base_url=a.base, session_file=a.session_file)

    if a.cmd == "login":
        ok = tcc.login()
        print("logged in" if ok else "login failed")
        return 0

    if a.cmd == "locations":
        tcc.ensure_authed()
        _print_json(tcc.locations())
        return 0

    if a.cmd == "zones":
        if not a.arg:
            print("usage: zones <locationId>", file=sys.stderr)
            return 2
        tcc.ensure_authed()
        _print_json(tcc.device_status(a.arg))
        return 0

    if a.cmd == "data":
        if not a.arg:
            print("usage: data <deviceId>", file=sys.stderr)
            return 2
        tcc.ensure_authed()
        _print_json(tcc.device_data(a.arg))
        return 0

    if a.cmd == "setpoint":
        if not a.arg:
            print("usage: setpoint <deviceId> [--heat N] [--cool N] "
                  "[--heat-on 0|1] [--cool-on 0|1] [--fan MODE]", file=sys.stderr)
            return 2
        tcc.ensure_authed()
        res = tcc.set_setpoints(int(a.arg), heat=a.heat, cool=a.cool,
                               heat_on=a.heat_on, cool_on=a.cool_on, fan=a.fan)
        _print_json(res)
        return 0

    if a.cmd == "schedule":
        if not a.arg:
            print("usage: schedule <deviceId>", file=sys.stderr)
            return 2
        tcc.ensure_authed()
        _print_json(tcc.get_schedule(a.arg))
        return 0

    if a.cmd == "send-schedule":
        if not a.arg:
            print("usage: send-schedule <deviceId>", file=sys.stderr)
            return 2
        tcc.ensure_authed()
        print(tcc.send_schedule(a.arg) or "(ok)")
        return 0

    if a.cmd == "edit-period":
        if not a.arg or not a.arg2:
            print("usage: edit-period <deviceId> <period> [--heat N] [--cool N] "
                  "[--fan-mode auto|...] [--fan on|off]", file=sys.stderr)
            return 2
        tcc.ensure_authed()
        print(tcc.edit_schedule_period(
            int(a.arg), a.arg2, heat=a.heat, cool=a.cool, fan_mode=a.fan_mode))
        return 0

    if a.cmd == "alerts":
        tcc.ensure_authed()
        print(tcc.alerts())
        return 0

    if a.cmd == "settings":
        if not a.arg:
            print("usage: settings <deviceId>", file=sys.stderr)
            return 2
        tcc.ensure_authed()
        _print_json(tcc.get_settings_data(a.arg))
        return 0

    if a.cmd == "export":
        tcc.ensure_authed()
        _print_json(tcc.export_report())
        return 0

    if a.cmd.startswith("/") or a.cmd.startswith("http"):
        tcc.ensure_authed()
        print(tcc.raw(a.cmd).text[:2000])
        return 0

    p.print_help()
    return 1


if __name__ == "__main__":
    # guard: session-file attr exists for the flag
    TotalConnect._authed_flag = False
    sys.exit(main(sys.argv[1:]))
