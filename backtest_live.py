"""Walk-forward backtest of the live bot, scored by city and by hour of the day.

For each month, each city's nowcast model is trained only on earlier days, the market blend
only on earlier out-of-sample predictions, and trades follow the live rules: at each hour,
buy brackets whose edge clears the bar, at most one position per bracket and
MAX_PICKS_PER_CITY per city per day.
"""
import json
import os
import pickle

import numpy as np
import pandas as pd

import config
from model import EPS, MarketBlend
from nowcast import NowcastModel, build_rows
from store import load
from strategy import evaluate, has_quote, settle_pnl


def brier(p, y):
    return float(np.mean((np.asarray(p, float) - np.asarray(y, float)) ** 2))


def ci90(trades, n=2000, seed=0):
    if trades.empty:
        return [0.0, 0.0]
    by_day = trades.groupby(["series", "date"])["pnl"].sum().values
    totals = np.random.default_rng(seed).choice(by_day, size=(n, len(by_day))).sum(axis=1)
    return [float(np.percentile(totals, 5)), float(np.percentile(totals, 95))]


def city_rows(city):
    s = city["series"]
    fc = load("hourly_fc", s, parse_dates=["time"])
    ob = load("obs", s, parse_dates=["time"])
    cli = load("cli", s, parse_dates=["date"])
    if fc.empty or ob.empty or cli.empty:
        return pd.DataFrame()
    return build_rows(city, fc, ob, cli)


def market_hours(series):
    mk = load("markets", series, parse_dates=["date"])
    q = load("quotes", series)
    if mk.empty or q.empty:
        return pd.DataFrame()
    return mk.merge(q, on="ticker")


def walk_forward(cities):
    feats = {s: city_rows(c) for s, c in cities.items()}
    mh = {s: market_hours(s) for s in cities}
    allm = pd.concat([m for m in mh.values() if not m.empty], ignore_index=True)
    if allm.empty:
        raise SystemExit("No market data yet. Run `python live.py setup` first.")
    preds = []
    for month in sorted(allm["date"].dt.to_period("M").unique()):
        month_rows = []
        for s, city in cities.items():
            f, m = feats[s], mh[s]
            if f.empty or m.empty:
                continue
            m = m[m["date"].dt.to_period("M") == month]
            train = f[(f["date"] < month.start_time) & f["high"].notna()]
            if m.empty or train["date"].nunique() < config.MIN_TRAIN_DAYS:
                continue
            model = NowcastModel().fit(train)
            j = m.merge(f.drop(columns="high"), on=["date", "hour"], how="inner")
            if j.empty:
                continue
            j["p_model"] = model.bracket_probs(j, j)
            j["mu"], j["sigma"] = model.predict(j)
            month_rows.append(j)
        if not month_rows:
            continue
        rows = pd.concat(month_rows, ignore_index=True)
        rows["mid"] = (rows["yes_bid"] + rows["yes_ask"]) / 2
        rows["p_blend"] = np.nan
        if preds:
            prior = pd.concat(preds)
            prior = prior[has_quote(prior)]
            if prior["date"].nunique() >= config.MIN_BLEND_DAYS:
                blend = MarketBlend().fit(prior["p_model"], prior["mid"], prior["result"])
                ok = has_quote(rows)
                rows.loc[ok, "p_blend"] = blend.predict(rows.loc[ok, "p_model"], rows.loc[ok, "mid"])
        preds.append(rows)
    if not preds:
        raise SystemExit(f"Not enough history: each city needs {config.MIN_TRAIN_DAYS}+ days.")
    return pd.concat(preds, ignore_index=True), feats


def simulate(preds, prob_col="p_blend"):
    """Hour by hour within each city-day, like the live bot."""
    q = preds[has_quote(preds) & preds[prob_col].notna()]
    if q.empty:
        return pd.DataFrame(columns=["series", "date", "hour", "ticker", "side", "price", "won", "pnl"])
    q = evaluate(q, prob_col)
    q = q[(q["ev"] >= config.EDGE_THRESHOLD) & q["price"].between(config.MIN_PRICE, config.MAX_PRICE)]
    trades = []
    for (s, d), g in q.sort_values(["hour", "ev"], ascending=[True, False]).groupby(["series", "date"]):
        held = set()
        for r in g.itertuples():
            if r.ticker in held or len(held) >= config.MAX_PICKS_PER_CITY:
                continue
            held.add(r.ticker)
            won, pnl = settle_pnl(r.side, r.price, r.result, r.fee_mult)
            trades.append(dict(series=s, date=d, hour=r.hour, ticker=r.ticker, side=r.side,
                               price=r.price, prob=r.prob, ev=r.ev, won=won, pnl=pnl))
    return pd.DataFrame(trades)


def summarize(rows, trades):
    q = rows[has_quote(rows)]
    out = {"markets": int(len(q)), "days": int(rows[["series", "date"]].drop_duplicates().shape[0])}
    if len(q):
        base = brier(q["mid"], q["result"])
        out["bss_model"] = 1 - brier(q["p_model"], q["result"]) / base
        qb = q[q["p_blend"].notna()]
        if len(qb):
            out["bss_blend"] = 1 - brier(qb["p_blend"], qb["result"]) / brier(qb["mid"], qb["result"])
    out["trades"] = int(len(trades))
    if len(trades):
        out.update(win_rate=float(trades["won"].mean()), pnl=float(trades["pnl"].sum()),
                   pnl_per_trade=float(trades["pnl"].mean()), ci90=ci90(trades))
    tr, ci = out["trades"], out.get("ci90", [0, 0])
    out["verdict"] = ("edge" if tr >= 30 and ci[0] > 0 and out.get("bss_blend", -1) > 0 else
                      "no edge" if tr >= 30 and (ci[1] < 0 or out.get("bss_blend", 0) < -0.02) else "unproven")
    return out


def run(cities):
    preds, feats = walk_forward(cities)
    trades = simulate(preds)
    summary = {"generated": pd.Timestamp.now().isoformat(timespec="minutes"),
               "period": [f"{preds['date'].min():%Y-%m-%d}", f"{preds['date'].max():%Y-%m-%d}"],
               "overall": summarize(preds, trades), "cities": {}, "hours": {}}
    for s, rows in preds.groupby("series"):
        summary["cities"][s] = {**summarize(rows, trades[trades["series"] == s] if len(trades) else trades),
                                "city": cities[s]["city"]}
    # Each hour scored as its own strategy: trade only at that hour of the day
    for h, rows in preds.groupby("hour"):
        summary["hours"][str(h)] = summarize(rows, simulate(rows))
    daily = trades.groupby("date")["pnl"].sum().sort_index().cumsum() if len(trades) else pd.Series(dtype=float)
    summary["equity"] = [[f"{d:%Y-%m-%d}", round(float(v), 2)] for d, v in daily.items()]

    # Final models on all data, for the live runner
    models = {}
    for s, f in feats.items():
        f = f[f["high"].notna()] if not f.empty else f
        if not f.empty and f["date"].nunique() >= config.MIN_TRAIN_DAYS:
            models[s] = NowcastModel().fit(f)
    q = preds[has_quote(preds)]
    blend = MarketBlend().fit(q["p_model"], q["mid"], q["result"]) if len(q) else None
    os.makedirs(config.RESULTS_DIR, exist_ok=True)
    preds.to_csv(os.path.join(config.RESULTS_DIR, "predictions.csv"), index=False)
    trades.to_csv(os.path.join(config.RESULTS_DIR, "trades.csv"), index=False)
    json.dump(summary, open(os.path.join(config.RESULTS_DIR, "backtest.json"), "w"), indent=2)
    pickle.dump({"models": models, "blend": blend}, open(os.path.join(config.RESULTS_DIR, "models.pkl"), "wb"))
    print_summary(summary)
    return summary


def print_summary(s):
    def line(name, c):
        ci = c.get("ci90")
        return (f"{name:<26}{c.get('bss_model', np.nan):>+9.3f}{c.get('bss_blend', np.nan):>+9.3f}"
                f"{c['trades']:>8}{c.get('pnl', 0):>9.2f}{(f'{ci[0]:.0f} to {ci[1]:.0f}' if ci else '-'):>16}  {c['verdict']}")
    print(f"\nLive backtest {s['period'][0]} to {s['period'][1]}")
    print(f"{'':<26}{'BSS mdl':>9}{'BSS bld':>9}{'trades':>8}{'P&L $':>9}{'90% CI':>16}  verdict")
    for h, c in sorted(s["hours"].items(), key=lambda kv: int(kv[0])):
        print(line(f"at {int(h):02d}:00 local", c))
    for ser, c in sorted(s["cities"].items()):
        print(line(c["city"][:25], c))
    print(line("ALL", s["overall"]))
