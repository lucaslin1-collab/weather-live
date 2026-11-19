"""Live high-temperature bot: every few minutes, combine the morning forecast with the
station's readings so far, price today's brackets, paper-trade the best ones, and
publish the live site.

  python live.py demo      simulated data, builds demo/site
  python live.py setup     first run: cities, history, intraday prices, backtest
  python live.py update    refresh history and settled markets (daily)
  python live.py backtest  re-score cities and hours, retrain models
  python live.py run       one live cycle (GitHub runs this every 10 minutes)
"""
import argparse
import json
import os
import pickle

import numpy as np
import pandas as pd

import config


def load_models():
    p = os.path.join(config.RESULTS_DIR, "models.pkl")
    saved = pickle.load(open(p, "rb")) if os.path.exists(p) else {"models": {}, "blend": None}
    bp = os.path.join(config.RESULTS_DIR, "backtest.json")
    summary = json.load(open(bp)) if os.path.exists(bp) else None
    return saved, summary


def city_now(city, now_utc, fc, obs, markets, model, blend, verdict):
    """Board for one city plus its priced brackets (DataFrame) for today."""
    from model import bracket_label, bracket_mid
    from nowcast import nearest_hour, raw_features
    from obs import day_window_utc
    from strategy import has_quote

    local = now_utc.tz_localize("UTC").tz_convert(city["tz"])
    today = local.normalize().tz_localize(None)
    ds, de = day_window_utc(today, city["std_offset"])
    board = {"series": city["series"], "city": city["city"], "station": city["station"],
             "verdict": verdict, "local_time": f"{local:%-I:%M %p}", "brackets": [], "note": ""}
    fc_day = fc[(fc["time"] >= ds) & (fc["time"] < de)] if not fc.empty else fc
    obs_day = obs[(obs["time"] >= ds) & (obs["time"] <= now_utc)] if not obs.empty else obs
    feats = raw_features(fc_day, obs_day, today, now_utc, city["std_offset"]) if len(fc_day) else None
    if not feats:
        board["note"] = "Waiting for today's forecast or first observations."
        return board, pd.DataFrame()
    board.update(obs_max=round(feats["obs_max"], 1), obs_now=round(feats["obs_now"], 1),
                 fc_max=round(feats["fc_max"], 1))
    if model is None:
        board["note"] = "Model not trained yet for this city."
        return board, pd.DataFrame()
    hour = nearest_hour(local.hour + local.minute / 60)
    row = pd.DataFrame([{**feats, "hour": hour, "date": today}])
    mu, sigma = model.predict(row)
    board.update(mu=round(float(mu[0]), 1), sigma=round(float(sigma[0]), 1))
    if markets.empty:
        board["note"] = "No open market for today."
        return board, pd.DataFrame()
    m = markets.reset_index(drop=True).copy()
    rows = pd.concat([row] * len(m), ignore_index=True)
    m["p_model"] = model.bracket_probs(m, rows)
    m["mid"] = (m["yes_bid"] + m["yes_ask"]) / 2
    m["p_final"] = m["p_model"]
    ok = has_quote(m)
    if blend is not None and ok.any():
        m.loc[ok, "p_final"] = blend.predict(m.loc[ok, "p_model"], m.loc[ok, "mid"])
    m["label"] = [bracket_label(t, f, c) for t, f, c in zip(m["strike_type"], m["floor_strike"], m["cap_strike"])]
    m["center"] = [bracket_mid(t, f, c) for t, f, c in zip(m["strike_type"], m["floor_strike"], m["cap_strike"])]
    m = m.sort_values("center")
    m["hour"], m["city"], m["verdict"] = hour, city["city"], verdict
    board["brackets"] = [{"label": r.label, "model": round(float(r.p_final), 3),
                          "market": None if np.isnan(r.mid) else round(float(r.mid), 3)} for r in m.itertuples()]
    board["in_window"] = config.DECISION_HOURS[0] <= local.hour <= config.DECISION_HOURS[-1]
    return board, m


def cycle(cities, now_utc, fc_by, obs_by, markets_by, client=None, settle=True):
    """One live cycle. Data is passed in so the demo and tests can supply their own."""
    import trader
    from strategy import evaluate, has_quote

    saved, summary = load_models()
    verdicts = {s: c["verdict"] for s, c in (summary or {}).get("cities", {}).items()}
    boards, cands = [], []
    for s, city in cities.items():
        b, priced = city_now(city, now_utc, fc_by.get(s, pd.DataFrame()), obs_by.get(s, pd.DataFrame()),
                             markets_by.get(s, pd.DataFrame()), saved["models"].get(s), saved["blend"],
                             verdicts.get(s, "unproven"))
        boards.append(b)
        if not priced.empty and b.get("in_window"):
            cands.append(priced)

    signals = pd.DataFrame()
    if cands:
        c = pd.concat(cands, ignore_index=True)
        c = evaluate(c[has_quote(c)], "p_final") if has_quote(c).any() else c.iloc[:0]
        if len(c):
            signals = c[(c["ev"] >= config.EDGE_THRESHOLD) & c["price"].between(config.MIN_PRICE, config.MAX_PRICE)]

    positions = trader.load_positions()
    positions, new = trader.open_positions(signals, positions, now_utc)
    real = pd.DataFrame()
    if len(new):
        today_et = now_utc.tz_localize("UTC").tz_convert("America/New_York").normalize().tz_localize(None)
        real = trader.place_real_orders(new, verdicts, today_et)
    if settle and client is not None:
        positions = trader.settle_positions(client, positions, now_utc.normalize())
    trader.save_positions(positions)
    quotes = [r for m in markets_by.values() if len(m) for r in m[["ticker", "yes_bid", "yes_ask"]].to_dict("records")]
    return {"boards": boards, "signals": signals, "new": new, "positions": positions,
            "summary": summary, "real": real, "now": now_utc, "quotes": quotes}


def fetch_inputs(client, cities, now_utc):
    """Today's forecasts (fetched once a day), latest METARs, open markets."""
    from obs import fetch_latest
    from store import fetch_hourly_forecast, load, market_row, save
    fc_by, markets_by = {}, {}
    latest = {}
    try:
        latest = fetch_latest([c["station"] for c in cities.values()])
    except Exception as err:  # noqa: BLE001
        print(f"  observations unavailable: {err}")
    for s, city in cities.items():
        fc = load("hourly_fc", s, parse_dates=["time"])
        today = now_utc.tz_localize("UTC").tz_convert(city["tz"]).normalize().tz_localize(None)
        ds = today - pd.Timedelta(hours=city["std_offset"])
        have = fc[(fc["time"] >= ds) & (fc["time"] < ds + pd.Timedelta(days=1))] if not fc.empty else fc
        if len(have) < 20:
            try:
                fresh = fetch_hourly_forecast(city, today - pd.Timedelta(days=1), today)
                fc = pd.concat([fc, fresh]).drop_duplicates("time", keep="last").sort_values("time") if not fc.empty else fresh
                save("hourly_fc", s, fc, index=False)
            except Exception as err:  # noqa: BLE001
                print(f"  {s}: forecast unavailable ({err})")
        fc_by[s] = fc
        try:
            rows = [r for r in (market_row(m, s, city["fee_multiplier"]) for m in client.open_markets(s)) if r]
            mk = pd.DataFrame(rows)
            markets_by[s] = mk[mk["date"] == today] if len(mk) else mk
        except Exception as err:  # noqa: BLE001
            print(f"  {s}: open markets unavailable ({err})")
            markets_by[s] = pd.DataFrame()
    obs_by = {s: latest.get(c["station"], pd.DataFrame()) for s, c in cities.items()}
    return fc_by, obs_by, markets_by


def publish(state, demo=False):
    import live_site
    live_site.build(state, demo=demo)
    status = {"generated": state["now"].isoformat(timespec="minutes") + "Z",
              "open_signals": int(len(state["signals"])),
              "positions_today": int((state["positions"]["date"] == state["positions"]["date"].max()).sum())
              if len(state["positions"]) else 0}
    with open(os.path.join(config.SITE_DIR, "status.json"), "w") as fh:
        json.dump(status, fh)


# --- commands -------------------------------------------------------------------------

def cmd_setup(args):
    from cities import discover, save_cities
    from kalshi_client import KalshiPublic
    import backtest_live
    import store
    client = KalshiPublic()
    cities = discover(client)
    save_cities(cities)
    print(f"{len(cities)} cities")
    cmd_update(args, client=client, cities=cities)
    backtest_live.run(cities)
    cmd_run(args, client=client, cities=cities)


def cmd_update(args, client=None, cities=None):
    from cities import load_cities
    from kalshi_client import KalshiPublic
    import store
    client = client or KalshiPublic()
    cities = cities or load_cities()
    from budget import out_of_time
    print("Updating forecasts, observations and official highs ...")
    for n, (s, c) in enumerate(cities.items(), 1):
        if out_of_time(reserve_min=70):
            print("  time budget reached; remaining cities continue next run")
            break
        try:
            store.update_weather(c)
            print(f"  [{n}/{len(cities)}] {c['city']}")
        except Exception as err:  # noqa: BLE001
            print(f"  [{n}/{len(cities)}] {c['city']}: failed ({err})")
    print("Updating settled markets and intraday prices ...")
    store.update_markets(client, cities)


def backtest_is_stale(cities):
    """Re-run when the backtest is a week old or misses cities that now have data."""
    from store import load
    path = os.path.join(config.RESULTS_DIR, "backtest.json")
    if not os.path.exists(path):
        return True
    s = json.load(open(path))
    if pd.Timestamp.now() - pd.Timestamp(s.get("generated", "2000-01-01")) > pd.Timedelta(days=6):
        return True
    ready = {c for c in cities if not load("hourly_fc", c).empty and not load("quotes", c).empty}
    return bool(ready - set(s.get("cities", {})))


def cmd_backtest(args):
    from cities import load_cities
    import backtest_live
    cities = load_cities()
    if getattr(args, "if_stale", False) and not backtest_is_stale(cities):
        print("Backtest is up to date.")
        raise SystemExit(10)
    backtest_live.run(cities)


def cmd_run(args, client=None, cities=None):
    from cities import load_cities
    from kalshi_client import KalshiPublic
    client = client or KalshiPublic()
    cities = cities or load_cities()
    now = pd.Timestamp.now(tz="UTC").tz_localize(None)
    fc_by, obs_by, markets_by = fetch_inputs(client, cities, now)
    state = cycle(cities, now, fc_by, obs_by, markets_by, client=client)
    print(f"{len(state['signals'])} signals, {len(state['new'])} new paper positions"
          + (f", {len(state['real'])} real orders" if len(state["real"]) else ""))
    publish(state)


def cmd_demo(args):
    config.DATA_DIR, config.RESULTS_DIR = os.path.join("demo", "data"), os.path.join("demo", "results")
    config.SITE_DIR = os.path.join("demo", "site")
    import backtest_live
    import synthetic_live
    from cities import save_cities
    cities, now, fc_by, obs_by, markets_by = synthetic_live.make()
    save_cities(cities)
    backtest_live.run(cities)
    state = None
    for minutes in range(-180, 1, 30):          # replay the morning so positions build up
        t = now + pd.Timedelta(minutes=minutes)
        state = cycle(cities, t, fc_by, {s: o[o["time"] <= t] for s, o in obs_by.items()},
                      synthetic_live.markets_at(markets_by, t), settle=False)
    synthetic_live.add_history(state)
    publish(state, demo=True)
    print(f"Demo site: {os.path.abspath(config.SITE_DIR)}/index.html")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in [("setup", cmd_setup), ("update", cmd_update), ("backtest", cmd_backtest),
                     ("run", cmd_run), ("demo", cmd_demo)]:
        p = sub.add_parser(name)
        p.set_defaults(fn=fn)
        if name == "backtest":
            p.add_argument("--if-stale", action="store_true", help="only run if a week old or cities were added")
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
