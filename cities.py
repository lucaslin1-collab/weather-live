"""Find every Kalshi daily-high series and the station each one actually settles on."""
import json
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

import config

HIGH_SERIES = re.compile(r"^KXHIGHT?[A-Z]{2,5}$")
# NWS climate report links look like ...product=CLI&issuedby=NYC; rules may say "CLINYC".
STATION_PATTERNS = [r"issuedby=([A-Z]{3})", r"\bCLI([A-Z]{3})\b"]


def std_offset_hours(tz):
    """NWS climate days run midnight to midnight local STANDARD time, all year."""
    return ZoneInfo(tz).utcoffset(datetime(2026, 1, 15)).total_seconds() / 3600


def parse_station(texts):
    for text in texts:
        for pattern in STATION_PATTERNS:
            match = re.search(pattern, text or "")
            if match:
                return "K" + match.group(1)
    return None


def nws_station_info(station):
    resp = requests.get(f"https://api.weather.gov/stations/{station}", timeout=20,
                        headers={"User-Agent": "kalshi-hightemp-ai (research script)"})
    resp.raise_for_status()
    data = resp.json()
    lon, lat = data["geometry"]["coordinates"][:2]
    props = data["properties"]
    return props.get("name") or station, lat, lon, props.get("timeZone")


def _entry(series, city, station, lat, lon, tz, fee_multiplier=1.0, title=""):
    return dict(series=series, city=city, station=station, lat=lat, lon=lon, tz=tz,
                std_offset=std_offset_hours(tz), fee_multiplier=fee_multiplier, title=title)


def builtin_cities():
    return {s: _entry(s, *v) for s, v in config.BUILTIN_CITIES.items()}


def discover(client):
    """Ask Kalshi which daily-high series exist and read each one's settlement station."""
    try:
        listed = [s for s in client.weather_series() if HIGH_SERIES.match(s.get("ticker", ""))]
    except Exception as err:  # noqa: BLE001
        print(f"  Couldn't list series ({err}); using the built-in city table.")
        return builtin_cities()

    by_station = {v[1]: v for v in config.BUILTIN_CITIES.values()}
    cities = {}
    for s in listed:
        ticker = s["ticker"]
        texts = [json.dumps(s.get("settlement_sources", []))]
        try:
            sample = client.get("/markets", series_ticker=ticker, limit=1).get("markets", [])
            texts += [m.get(k, "") for m in sample for k in ("rules_primary", "rules_secondary")]
        except Exception:  # noqa: BLE001
            pass
        if "temperature" not in (s.get("title", "") + " ".join(texts)).lower():
            continue
        builtin = config.BUILTIN_CITIES.get(ticker)
        station = parse_station(texts) or (builtin[1] if builtin else None)
        if station is None:
            print(f"  {ticker}: couldn't find a settlement station; skipped")
            continue
        if builtin and builtin[1] != station:
            print(f"  {ticker}: rules say it settles at {station}, not {builtin[1]}; using {station}")
        fee = float(s.get("fee_multiplier") or 1.0)
        if station in by_station:
            city, _, lat, lon, tz = by_station[station]
        else:
            try:
                city, lat, lon, tz = nws_station_info(station)
            except Exception as err:  # noqa: BLE001
                print(f"  {ticker}: no coordinates for {station} ({err}); skipped")
                continue
        cities[ticker] = _entry(ticker, city, station, lat, lon, tz, fee, s.get("title", ""))
    if not cities:
        print("  Discovery found nothing; using the built-in city table.")
        return builtin_cities()
    return cities


def cities_path():
    return os.path.join(config.DATA_DIR, "cities.json")


def save_cities(cities):
    os.makedirs(config.DATA_DIR, exist_ok=True)
    with open(cities_path(), "w") as fh:
        json.dump(cities, fh, indent=2)


def load_cities():
    if os.path.exists(cities_path()):
        with open(cities_path()) as fh:
            return json.load(fh)
    return builtin_cities()
