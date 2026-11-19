"""Simulated cities for `python live.py demo`. Numbers mean nothing.

Each fake day has a smooth temperature curve peaking mid-afternoon. Forecasts are the curve
with an error; observations are the curve with small noise. Fake markets track the truth
with noise that shrinks through the day. "Lazy" cities react slowly, so the bot can beat them.
"""
import numpy as np
import pandas as pd
from scipy import stats

import config
from cities import builtin_cities
from model import bracket_bounds, implied_result
from store import local_to_utc, save

DEMO = {"KXHIGHNY": 0.9, "KXHIGHCHI": 0.0, "KXHIGHAUS": 0.7, "KXHIGHDEN": 0.0, "KXHIGHLAX": 0.0, "KXHIGHTPHX": 0.2}


def curve(hours_std, low, high):
    h = np.asarray(hours_std, float) % 24
    rising = 0.5 - 0.5 * np.cos(np.pi * np.clip(h - 6, 0, 9) / 9)
    falling = 0.5 + 0.5 * np.cos(np.pi * np.clip(h - 15, 0, 15) / 15)
    shape = np.where(h < 6, falling * 0 + 0.15 * (1 - h / 6), np.where(h <= 15, rising, falling))
    return low + (high - low) * shape


def market_view(rng, truth, obs_max, hour, lazy):
    """The fake market's belief about the final high at a decision hour."""
    s = np.interp(hour, [10, 13, 16], [1.7, 1.1, 0.6]) * (1 + lazy)
    return truth + rng.normal(0, s), s, obs_max


def price_brackets(brackets, mu, s, obs_max):
    lo_cut = obs_max - 1.0
    out = []
    for t, f, c in brackets:
        a, b = bracket_bounds(t, f, c)
        a = max(a, lo_cut)
        if b <= a:
            out.append(0.01)
            continue
        dist = stats.truncnorm((lo_cut - mu) / s, np.inf, loc=mu, scale=s)
        out.append(float(np.clip(dist.cdf(b) - dist.cdf(a), 0.01, 0.99)))
    return out


def make(seed=5):
    rng = np.random.default_rng(seed)
    cities = {s: c for s, c in builtin_cities().items() if s in DEMO}
    now = pd.Timestamp.now(tz="America/New_York").normalize().tz_localize(None) + pd.Timedelta(hours=13, minutes=40)
    now_utc = now.tz_localize("America/New_York").tz_convert("UTC").tz_localize(None)
    fc_by, obs_by, markets_by = {}, {}, {}
    for s, city in cities.items():
        off = city["std_offset"]
        today = now_utc.tz_localize("UTC").tz_convert(city["tz"]).normalize().tz_localize(None)
        days = pd.date_range(config.WEATHER_START, today)
        doy = days.dayofyear.values
        mean = 84 - 1.4 * (city["lat"] - 25.8)
        amp = 7 + 0.7 * (city["lat"] - 25.8)
        anom = np.zeros(len(days))
        for i in range(1, len(days)):
            anom[i] = 0.6 * anom[i - 1] + rng.normal(0, 5)
        high_true = mean + amp * np.sin(2 * np.pi * (doy - 110) / 365.25) + anom
        low = high_true - rng.uniform(12, 20, len(days))
        fc_err = rng.normal(0, 2.3, len(days))

        fc_rows, obs_rows = [], []
        for i, d in enumerate(days):
            ds = d - pd.Timedelta(hours=off)
            hrs = np.arange(24)
            fc_rows.append(pd.DataFrame({"time": ds + pd.to_timedelta(hrs, "h"),
                                         "fc": curve(hrs, low[i] + fc_err[i], high_true[i] + fc_err[i])}))
            obs_t = curve(hrs, low[i], high_true[i]) - np.abs(rng.normal(0, 0.4, 24))  # readings sit at or under the true curve
            obs_rows.append(pd.DataFrame({"time": ds + pd.to_timedelta(hrs, "h") + pd.Timedelta(minutes=51),
                                          "temp_f": np.round(obs_t, 1), "max6_f": np.nan}))
        fc, ob = pd.concat(fc_rows, ignore_index=True), pd.concat(obs_rows, ignore_index=True)
        cli = pd.DataFrame({"date": days[:-1], "high": np.round(high_true[:-1] + 0.3)})
        save("hourly_fc", s, fc, index=False)
        save("obs", s, ob[ob["time"] < now_utc - pd.Timedelta(days=1)], index=False)
        save("cli", s, cli, index=False)

        mk_rows, q_rows, today_m = [], [], {}
        for i, d in list(enumerate(days))[-config.MARKET_HISTORY_DAYS:]:
            c = 2 * round((high_true[i] + fc_err[i] * 0.7) / 2)
            br = [("less", np.nan, c - 3)] + [("between", lo, lo + 1) for lo in (c - 3, c - 1, c + 1, c + 3)] + \
                 [("greater", c + 4, np.nan)]
            event = f"{s}-{d:%y%b%d}".upper()
            tickers = [f"{event}-{('B' + format(f + 0.5, 'g')) if t == 'between' else 'T' + format(cap if t == 'less' else f, 'g')}"
                       for t, f, cap in br]
            truth = round(high_true[i] + 0.3)
            hour_quotes = {}
            for h in config.DECISION_HOURS:
                t = local_to_utc(d, h, city["tz"])
                day_obs = ob[(ob["time"] >= d - pd.Timedelta(hours=off)) & (ob["time"] <= t)]
                omax = day_obs["temp_f"].max() if len(day_obs) else low[i]
                mu, sd, _ = market_view(rng, truth, omax, h, DEMO[s])
                hour_quotes[h] = price_brackets(br, mu, sd, omax)
            for k, ((t, f, cap), tk) in enumerate(zip(br, tickers)):
                row = {"series": s, "ticker": tk, "event_ticker": event, "date": d, "strike_type": t,
                       "floor_strike": f, "cap_strike": cap, "settled_ts": None, "fee_mult": 1.0}
                if d < today:
                    mk_rows.append({**row, "result": implied_result(t, f, cap, truth)})
                    for h in config.DECISION_HOURS:
                        mid, half = hour_quotes[h][k], rng.choice([0.01, 0.01, 0.02])
                        q_rows.append({"ticker": tk, "hour": h, "yes_bid": round(max(mid - half, 0.01), 2),
                                       "yes_ask": round(min(mid + half, 0.99), 2)})
                else:
                    today_m[tk] = {**row, "result": None, "quotes": {h: hour_quotes[h][k] for h in hour_quotes}}
        save("markets", s, pd.DataFrame(mk_rows), index=False)
        save("quotes", s, pd.DataFrame(q_rows), index=False)
        fc_by[s], obs_by[s], markets_by[s] = fc, ob, (city, today_m)
    return cities, now_utc, fc_by, obs_by, markets_by


def markets_at(markets_by, t_utc):
    """Today's fake markets priced at the latest decision hour before t."""
    out = {}
    for s, (city, today_m) in markets_by.items():
        local_h = t_utc.tz_localize("UTC").tz_convert(city["tz"]).hour
        hs = [h for h in config.DECISION_HOURS if h <= local_h] or [config.DECISION_HOURS[0]]
        rows = []
        for tk, r in today_m.items():
            mid = r["quotes"][hs[-1]]
            rows.append({k: v for k, v in r.items() if k != "quotes"} |
                        {"yes_bid": round(max(mid - 0.01, 0.01), 2), "yes_ask": round(min(mid + 0.01, 0.99), 2)})
        out[s] = pd.DataFrame(rows)
    return out


def add_history(state):
    """Pretend the last three weeks of backtest trades were paper-traded live."""
    import os
    import trader
    trades = pd.read_csv(os.path.join(config.RESULTS_DIR, "trades.csv"), parse_dates=["date"])
    if trades.empty:
        return
    from cities import load_cities
    from store import load
    from model import bracket_label
    cities = load_cities()
    recent = trades[trades["date"] >= trades["date"].max() - pd.Timedelta(days=20)].copy()
    mk = pd.concat([load("markets", s, parse_dates=["date"]) for s in recent["series"].unique()])
    recent = recent.merge(mk[["ticker", "strike_type", "floor_strike", "cap_strike"]], on="ticker", how="left")
    recent["label"] = [bracket_label(t, f, c) for t, f, c in zip(recent["strike_type"], recent["floor_strike"], recent["cap_strike"])]
    recent["city"] = recent["series"].map(lambda s: cities[s]["city"])
    recent["contracts"] = 10
    recent["pnl"] = recent["pnl"] * 10
    recent["opened_at"] = recent["date"].dt.strftime("%Y-%m-%d") + "T" + recent["hour"].astype(str).str.zfill(2) + ":00"
    recent["fee_mult"], recent["verdict"] = 1.0, "unproven"
    pos = pd.concat([recent[trader.POS_COLS], state["positions"]], ignore_index=True)
    trader.save_positions(pos)
    state["positions"] = pos
