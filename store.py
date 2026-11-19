"""Local CSV store for the live bot: hourly forecasts, station observations, official
highs, settled markets and their prices at each decision hour. Updated incrementally."""
import os
import re
import time
from datetime import datetime

import numpy as np
import pandas as pd
import requests

import config
from kalshi_client import dollars, to_unix
from obs import fetch_history

SUPPORTED = {"between", "less", "less_or_equal", "greater", "greater_or_equal"}
PREV_RUNS_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
IEM_CLI_URL = "https://mesonet.agron.iastate.edu/json/cli.py"
_KEY = re.compile(r"^temperature_2m_previous_day(\d+)(?:_(.+))?$")


def path(kind, series):
    return os.path.join(config.DATA_DIR, kind, f"{series}.csv")


def load(kind, series, **kw):
    p = path(kind, series)
    return pd.read_csv(p, **kw) if os.path.exists(p) else pd.DataFrame()


def save(kind, series, df, **kw):
    os.makedirs(os.path.dirname(path(kind, series)), exist_ok=True)
    df.to_csv(path(kind, series), **kw)


def local_today(city):
    return pd.Timestamp.now(tz=city["tz"]).normalize().tz_localize(None)


def event_date(event_ticker):
    return pd.Timestamp(datetime.strptime(event_ticker.rsplit("-", 1)[-1], "%y%b%d"))


def local_to_utc(date, hour, tz):
    return (pd.Timestamp(date) + pd.Timedelta(hours=hour)).tz_localize(tz).tz_convert("UTC").tz_localize(None)


def market_row(m, series, fee_mult):
    if m.get("strike_type") not in SUPPORTED:
        return None
    return {
        "series": series, "ticker": m["ticker"], "event_ticker": m["event_ticker"],
        "date": event_date(m["event_ticker"]), "strike_type": m["strike_type"],
        "floor_strike": m.get("floor_strike"), "cap_strike": m.get("cap_strike"),
        "result": {"yes": 1, "no": 0}.get(m.get("result")),
        "settled_ts": to_unix(m.get("settlement_ts")) or to_unix(m.get("close_time")),
        "yes_bid": dollars(m.get("yes_bid_dollars")), "yes_ask": dollars(m.get("yes_ask_dollars")),
        "fee_mult": fee_mult,
    }


def _get_json(url, params, tries=5):
    for attempt in range(tries):
        resp = requests.get(url, params=params, timeout=90)
        if resp.status_code == 429 or resp.status_code >= 500:
            time.sleep(5 * (attempt + 1))
            continue
        resp.raise_for_status()
        return resp.json()
    resp.raise_for_status()


# --- hourly forecasts (ensemble mean of the models, as issued a day earlier) -------------

def fetch_hourly_forecast(city, start, end, chunk_days=120):
    frames = []
    last = pd.Timestamp(end) + pd.Timedelta(days=1)
    for cs in pd.date_range(start, last, freq=f"{chunk_days}D"):
        ce = min(cs + pd.Timedelta(days=chunk_days - 1), last)
        data = _get_json(PREV_RUNS_URL, {
            "latitude": city["lat"], "longitude": city["lon"],
            "hourly": ",".join(f"temperature_2m_previous_day{n}" for n in config.LEADS),
            "models": ",".join(config.FORECAST_MODELS), "temperature_unit": "fahrenheit",
            "timezone": "GMT", "start_date": f"{cs:%Y-%m-%d}", "end_date": f"{ce:%Y-%m-%d}"})
        hourly = data["hourly"]
        cols = {k: pd.to_numeric(pd.Series(v), errors="coerce") for k, v in hourly.items() if _KEY.match(k)}
        df = pd.DataFrame({"time": pd.to_datetime(hourly["time"])})
        df["fc"] = pd.DataFrame(cols).mean(axis=1) if cols else np.nan
        frames.append(df)
        time.sleep(0.3)
    return pd.concat(frames, ignore_index=True).dropna().drop_duplicates("time")


def fetch_cli(station, years):
    rows = []
    for y in years:
        payload = _get_json(IEM_CLI_URL, {"station": station, "year": int(y)})
        recs = payload.get("results", payload) if isinstance(payload, dict) else payload
        rows += [{"date": r.get("valid"), "high": r.get("high")} for r in recs]
    df = pd.DataFrame(rows, columns=["date", "high"])
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["high"] = pd.to_numeric(df["high"], errors="coerce")
    return df.dropna().drop_duplicates("date", keep="last").sort_values("date")


def _upsert(kind, series, new, key, parse):
    old = load(kind, series, parse_dates=parse)
    out = pd.concat([old, new], ignore_index=True) if not old.empty else new
    out = out.drop_duplicates(key, keep="last").sort_values(key)
    save(kind, series, out, index=False)
    return out


def update_weather(city):
    """Hourly forecasts, observations and official highs, from the last stored day to today."""
    series, today = city["series"], local_today(city)
    fc_old = load("hourly_fc", series, parse_dates=["time"])
    start = pd.Timestamp(config.WEATHER_START) if fc_old.empty else fc_old["time"].max().normalize() - pd.Timedelta(days=3)
    _upsert("hourly_fc", series, fetch_hourly_forecast(city, start, today), "time", ["time"])

    ob_old = load("obs", series, parse_dates=["time"])
    ostart = pd.Timestamp(config.WEATHER_START) if ob_old.empty else ob_old["time"].max().normalize() - pd.Timedelta(days=2)
    _upsert("obs", series, fetch_history(city["station"], ostart, today), "time", ["time"])

    cli_old = load("cli", series, parse_dates=["date"])
    cstart = pd.Timestamp(config.WEATHER_START) if cli_old.empty else cli_old["date"].max() - pd.Timedelta(days=5)
    _upsert("cli", series, fetch_cli(city["station"], range(cstart.year, today.year + 1)), "date", ["date"])


# --- settled markets and their prices at each decision hour --------------------------

def update_markets(client, cities, history_days=None):
    history_days = history_days or config.MARKET_HISTORY_DAYS
    cutoff = client.historical_cutoff_ts()
    for series, city in cities.items():
        old = load("markets", series, parse_dates=["date"])
        if old.empty:
            since = local_today(city) - pd.Timedelta(days=history_days)
            raw = client.settled_markets(series, include_historical=True)
        else:
            since = old["date"].max() - pd.Timedelta(days=2)
            raw = client.settled_markets(series, min_close_ts=int(since.timestamp()), include_historical=False)
        rows = [r for r in (market_row(m, series, city["fee_multiplier"]) for m in raw) if r]
        known = set(old["ticker"]) if not old.empty else set()
        rows = [r for r in rows if r["date"] >= since and r["result"] is not None and r["ticker"] not in known]
        print(f"  {series}: {len(rows)} new settled markets")
        if not rows:
            continue
        new = pd.DataFrame(rows).drop(columns=["yes_bid", "yes_ask"])
        quotes = hourly_quotes(client, new, city, cutoff)
        _upsert("quotes", series, quotes, ["ticker", "hour"], None) if not quotes.empty else None
        _upsert("markets", series, new, "ticker", ["date"])


def hourly_quotes(client, mk, city, cutoff_ts):
    """YES bid/ask at each decision hour, from hourly candles."""
    out = []

    def pick(candles, ts):
        usable = [c for c in candles if c.get("end_period_ts", 0) <= ts]
        if not usable:
            return None, None
        last = max(usable, key=lambda c: c["end_period_ts"])
        return (dollars((last.get("yes_bid") or {}).get("close_dollars")),
                dollars((last.get("yes_ask") or {}).get("close_dollars")))

    for date, day in mk.groupby("date"):
        hours = {h: int(local_to_utc(date, h, city["tz"]).timestamp()) for h in config.DECISION_HOURS}
        start, end = min(hours.values()) - 2 * 3600, max(hours.values())
        archived = cutoff_ts is not None and day["settled_ts"].fillna(0).max() < cutoff_ts
        try:
            if archived:
                candles = {t: client.historical_candlesticks(t, start, end) for t in day["ticker"]}
            else:
                candles = client.batch_candlesticks(list(day["ticker"]), start, end)
        except Exception as err:  # noqa: BLE001
            print(f"    quotes for {date:%Y-%m-%d} failed: {err}")
            continue
        for t in day["ticker"]:
            for h, ts in hours.items():
                bid, ask = pick(candles.get(t, []), ts)
                out.append({"ticker": t, "hour": h, "yes_bid": bid, "yes_ask": ask})
    return pd.DataFrame(out)
