"""Station observations: history from the Iowa Environmental Mesonet, live from aviationweather.gov.

Temperatures are kept in °F with tenths. METARs carry a "T-group" with temperature to
0.1 °C, and at 00/06/12/18 UTC a "1-group" with the true maximum over the past 6 hours,
which catches peaks that fall between hourly reports.
"""
import io
import re
import time

import numpy as np
import pandas as pd
import requests

IEM_ASOS = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
AWC_METAR = "https://aviationweather.gov/api/data/metar"
UA = {"User-Agent": "lucaslinweather-live (research bot)"}

_T_GROUP = re.compile(r"\bT([01])(\d{3})([01])(\d{3})\b")
_BODY = re.compile(r"\s(M?\d{2})/(M?\d{2})?(?=\s)")
_MAX6 = re.compile(r"(?<=\s)1([01])(\d{3})(?=\s|$)")


def c_to_f(c):
    return c * 9 / 5 + 32


def parse_metar(raw):
    """Return (temp_f, max6_f) from a raw METAR; either may be None."""
    if not isinstance(raw, str):
        return None, None
    temp = None
    m = _T_GROUP.search(raw)
    if m:
        temp = c_to_f((-1 if m.group(1) == "1" else 1) * int(m.group(2)) / 10)
    else:
        b = _BODY.search(" " + raw + " ")
        if b:
            v = b.group(1)
            temp = c_to_f(-int(v[1:]) if v.startswith("M") else int(v))
    max6 = None
    if "RMK" in raw:
        rmk = " " + raw.split("RMK", 1)[1] + " "
        g = _MAX6.search(rmk)
        if g:
            max6 = c_to_f((-1 if g.group(1) == "1" else 1) * int(g.group(2)) / 10)
    return temp, max6


def _iem_station(station):
    return station[1:] if station.startswith("K") and len(station) == 4 else station


def fetch_history(station, start, end):
    """Hourly + special METARs between two dates (UTC), one request per year."""
    frames = []
    for year in range(pd.Timestamp(start).year, pd.Timestamp(end).year + 1):
        s = max(pd.Timestamp(start), pd.Timestamp(f"{year}-01-01"))
        e = min(pd.Timestamp(end), pd.Timestamp(f"{year}-12-31"))
        e1 = e + pd.Timedelta(days=1)
        df = None
        for sid in (_iem_station(station), station):
            params = {"station": sid, "data": "tmpf,metar", "tz": "UTC", "format": "onlycomma",
                      "latlon": "no", "missing": "M", "report_type": "3,4",
                      "year1": s.year, "month1": s.month, "day1": s.day,   # end date is exclusive
                      "year2": e1.year, "month2": e1.month, "day2": e1.day}
            for attempt in range(4):
                resp = requests.get(IEM_ASOS, params=params, headers=UA, timeout=120)
                if resp.status_code in (429, 503):
                    time.sleep(10 * (attempt + 1))
                    continue
                resp.raise_for_status()
                break
            df = pd.read_csv(io.StringIO(resp.text), na_values=["M"])
            if len(df):
                break
        if df is not None and len(df):
            frames.append(df)
        time.sleep(1)
    if not frames:
        return pd.DataFrame(columns=["time", "temp_f", "max6_f"])
    raw = pd.concat(frames, ignore_index=True)
    return _tidy(raw["valid"], raw.get("tmpf"), raw.get("metar"))


def _tidy(times, tmpf, metars):
    parsed = [parse_metar(m) for m in (metars if metars is not None else [None] * len(times))]
    out = pd.DataFrame({
        "time": pd.to_datetime(times, utc=True).dt.tz_localize(None),
        "temp_f": [p[0] for p in parsed],
        "max6_f": [p[1] for p in parsed],
    })
    if tmpf is not None:
        out["temp_f"] = out["temp_f"].fillna(pd.to_numeric(pd.Series(list(tmpf)), errors="coerce"))
    return out.dropna(subset=["time"]).drop_duplicates("time").sort_values("time").reset_index(drop=True)


def fetch_latest(stations, hours=30):
    """Recent METARs for many stations in one call. Returns {station: DataFrame}."""
    resp = requests.get(AWC_METAR, params={"ids": ",".join(stations), "format": "json", "hours": hours},
                        headers=UA, timeout=60)
    resp.raise_for_status()
    rows = resp.json() if resp.text.strip() else []
    out = {}
    for st in stations:
        recs = [r for r in rows if r.get("icaoId") == st]
        if not recs:
            out[st] = pd.DataFrame(columns=["time", "temp_f", "max6_f"])
            continue
        times = pd.to_datetime([r.get("obsTime") for r in recs], unit="s")
        df = _tidy(pd.Series(times), [c_to_f(r["temp"]) if r.get("temp") is not None else np.nan for r in recs],
                   [r.get("rawOb") for r in recs])
        out[st] = df
    return out


# --- features at a moment in the day -----------------------------------------------

def day_window_utc(date, std_offset):
    """The NWS climate day: midnight to midnight local STANDARD time, in UTC."""
    start = pd.Timestamp(date) - pd.Timedelta(hours=std_offset)
    return start, start + pd.Timedelta(days=1)


def obs_so_far(obs, day_start, t):
    """Highest temperature observed from the start of the climate day up to time t,
    plus the latest reading. 6-hour maxima count only if their whole window is inside."""
    sel = obs[(obs["time"] >= day_start) & (obs["time"] <= t)]
    if sel.empty or sel["temp_f"].notna().sum() == 0:
        return np.nan, np.nan
    hi = sel["temp_f"].max()
    m6 = sel[(sel["time"] - pd.Timedelta(hours=6) >= day_start) & sel["max6_f"].notna()]
    if len(m6):
        hi = max(hi, m6["max6_f"].max())
    now = sel.dropna(subset=["temp_f"]).iloc[-1]["temp_f"]
    return float(hi), float(now)
