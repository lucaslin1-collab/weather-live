"""Paper trading (always on) and real orders (off unless deliberately enabled)."""
import base64
import os
import time
import uuid

import numpy as np
import pandas as pd
import requests

import config
from strategy import settle_pnl, taker_fee

POS_COLS = ["date", "series", "city", "ticker", "label", "side", "price", "contracts", "prob", "ev",
            "hour", "opened_at", "fee_mult", "verdict", "won", "pnl"]


def _p(name):
    return os.path.join(config.DATA_DIR, name)


def load_positions():
    p = _p("paper_positions.csv")
    if not os.path.exists(p):
        return pd.DataFrame(columns=POS_COLS)
    return pd.read_csv(p, parse_dates=["date"])


def save_positions(df):
    os.makedirs(config.DATA_DIR, exist_ok=True)
    df.to_csv(_p("paper_positions.csv"), index=False)


def open_positions(signals, positions, now):
    """Paper-buy each new signal at its current price, respecting one position per bracket
    and MAX_PICKS_PER_CITY per city per day. Returns (positions, newly opened)."""
    if signals.empty:
        return positions, signals
    new = []
    for (s, d), g in signals.sort_values("ev", ascending=False).groupby(["series", "date"]):
        held = positions[(positions["series"] == s) & (positions["date"] == d)]
        tickers = set(held["ticker"])
        for r in g.itertuples():
            if r.ticker in tickers or len(tickers) >= config.MAX_PICKS_PER_CITY:
                continue
            tickers.add(r.ticker)
            contracts = max(1, int(r.stake_pct * config.PAPER_BANKROLL // r.price))
            new.append({"date": d, "series": s, "city": r.city, "ticker": r.ticker, "label": r.label,
                        "side": r.side, "price": r.price, "contracts": contracts, "prob": r.prob,
                        "ev": r.ev, "hour": r.hour, "opened_at": now.isoformat(timespec="minutes"),
                        "fee_mult": r.fee_mult, "verdict": r.verdict, "won": np.nan, "pnl": np.nan})
    new = pd.DataFrame(new, columns=POS_COLS)
    return (pd.concat([positions, new], ignore_index=True) if len(new) else positions), new


def settle_positions(client, positions, today):
    for idx, r in positions[positions["won"].isna() & (positions["date"] < today)].iterrows():
        try:
            result = client.market(r["ticker"]).get("result")
        except Exception:  # noqa: BLE001
            continue
        if result in ("yes", "no"):
            won, per = settle_pnl(r["side"], r["price"], int(result == "yes"), r["fee_mult"])
            fee_all = taker_fee(r["price"], r["fee_mult"], int(r["contracts"]))
            pnl = r["contracts"] * (won - r["price"]) - fee_all
            positions.loc[idx, ["won", "pnl"]] = [won, round(float(pnl), 2)]
    return positions


# --- real orders -------------------------------------------------------------------

class KalshiTrader:
    """Signed requests to Kalshi's trading API (RSA-PSS or Ed25519 key)."""

    def __init__(self):
        from cryptography.hazmat.primitives import serialization
        self.key_id = os.environ["KALSHI_API_KEY_ID"]
        self.key = serialization.load_pem_private_key(os.environ["KALSHI_PRIVATE_KEY"].encode(), password=None)
        self.base = config.KALSHI_ORDER_BASES["prod" if config.KALSHI_ENV == "prod" else "demo"]

    def _headers(self, method, path):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        ts = str(int(time.time() * 1000))
        msg = (ts + method + path.split("?")[0]).encode()
        if isinstance(self.key, Ed25519PrivateKey):
            sig = self.key.sign(msg)
        else:
            sig = self.key.sign(msg, padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                                                 salt_length=padding.PSS.DIGEST_LENGTH), hashes.SHA256())
        return {"KALSHI-ACCESS-KEY": self.key_id, "KALSHI-ACCESS-TIMESTAMP": ts,
                "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(), "Content-Type": "application/json"}

    def buy(self, ticker, side, price, count):
        """Immediate-or-cancel limit buy at `price` dollars: fills now at that price or better, or not at all."""
        path = "/trade-api/v2/portfolio/orders"
        cents = int(round(price * 100))
        body = {"ticker": ticker, "action": "buy", "side": side.lower(), "count": int(count), "type": "limit",
                "time_in_force": "immediate_or_cancel", "client_order_id": str(uuid.uuid4()),
                ("yes_price" if side == "YES" else "no_price"): cents}
        resp = requests.post(self.base + path, json=body, headers=self._headers("POST", path), timeout=30)
        resp.raise_for_status()
        return resp.json().get("order", {})


def real_orders_enabled():
    return (config.LIVE_TRADING and os.environ.get("KALSHI_API_KEY_ID")
            and os.environ.get("KALSHI_PRIVATE_KEY"))


def place_real_orders(new_positions, verdicts, today):
    """Mirror new paper positions with real orders, within hard limits. Returns a log."""
    if new_positions.empty or not real_orders_enabled():
        return pd.DataFrame()
    log_path = _p("real_orders.csv")
    log = pd.read_csv(log_path, parse_dates=["date"]) if os.path.exists(log_path) else pd.DataFrame()
    spent = float(log.loc[log["date"] == today, "cost"].sum()) if not log.empty else 0.0
    trader, rows = KalshiTrader(), []
    for r in new_positions.itertuples():
        if config.REAL_ONLY_PROVEN_CITIES and verdicts.get(r.series) != "edge":
            continue
        count = int(min(r.contracts, config.MAX_ORDER_DOLLARS // r.price))
        cost = count * r.price
        if count < 1 or spent + cost > config.MAX_DAILY_DOLLARS:
            continue
        try:
            order = trader.buy(r.ticker, r.side, r.price, count)
            filled = int(float(order.get("fill_count") or order.get("fill_count_fp") or 0))
            status = order.get("status", "")
        except Exception as err:  # noqa: BLE001
            filled, status = 0, f"error: {err}"[:200]
        spent += filled * r.price
        rows.append({"date": today, "ticker": r.ticker, "side": r.side, "price": r.price, "requested": count,
                     "filled": filled, "cost": filled * r.price, "status": status, "env": config.KALSHI_ENV,
                     "time": pd.Timestamp.now(tz="UTC").isoformat(timespec="seconds")})
    if rows:
        out = pd.concat([log, pd.DataFrame(rows)], ignore_index=True) if not log.empty else pd.DataFrame(rows)
        out.to_csv(log_path, index=False)
    return pd.DataFrame(rows)
