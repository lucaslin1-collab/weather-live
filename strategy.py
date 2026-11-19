"""Turn probabilities into picks. The backtest and the daily run share these rules."""
import numpy as np
import pandas as pd

import config


def taker_fee(price, fee_mult=1.0, contracts=1):
    """Kalshi quadratic taker fee, rounded up to the next cent."""
    raw = config.BASE_FEE_RATE * fee_mult * contracts * price * (1 - price)
    return np.ceil(raw * 100 - 1e-9) / 100


def has_quote(df):
    """Two-sided market with a spread narrow enough to trust the mid as a price."""
    return (df["yes_bid"].notna() & df["yes_ask"].notna() & (df["yes_ask"] > df["yes_bid"])
            & (df["yes_ask"] - df["yes_bid"] <= config.MAX_SPREAD))


def evaluate(rows, prob_col):
    """Best side for each bracket, its price, expected value after fees, and a stake guide."""
    rows = rows.copy()
    p = rows[prob_col]
    fee_mult = rows.get("fee_mult", pd.Series(1.0, index=rows.index)).fillna(1.0)
    yes_price, no_price = rows["yes_ask"], 1 - rows["yes_bid"]
    rows["ev_yes"] = p - yes_price - taker_fee(yes_price, fee_mult)
    rows["ev_no"] = (1 - p) - no_price - taker_fee(no_price, fee_mult)
    yes_better = rows["ev_yes"] >= rows["ev_no"]
    rows["side"] = np.where(yes_better, "YES", "NO")
    rows["price"] = np.where(yes_better, yes_price, no_price)
    rows["prob"] = np.where(yes_better, p, 1 - p)
    rows["ev"] = np.where(yes_better, rows["ev_yes"], rows["ev_no"])
    kelly = (rows["prob"] - rows["price"]) / (1 - rows["price"])
    rows["stake_pct"] = np.clip(config.KELLY_FRACTION * kelly, 0, config.MAX_STAKE_PCT)
    return rows


def select(rows, prob_col):
    """Brackets worth recommending: enough edge, a real quote, not a near-certain price,
    and at most MAX_PICKS_PER_CITY per city per day (brackets are correlated)."""
    rows = rows[has_quote(rows) & rows[prob_col].notna()]
    if rows.empty:
        return rows.assign(ev=[], side=[], price=[], prob=[], stake_pct=[])
    rows = evaluate(rows, prob_col)
    rows = rows[(rows["ev"] >= config.EDGE_THRESHOLD)
                & rows["price"].between(config.MIN_PRICE, config.MAX_PRICE)]
    rows = rows.sort_values("ev", ascending=False)
    return rows.groupby(["series", "date"], group_keys=False).head(config.MAX_PICKS_PER_CITY)


def settle_pnl(side, price, result_yes, fee_mult=1.0):
    """Profit per contract once the market resolves (result_yes is 1 or 0)."""
    won = result_yes if side == "YES" else 1 - result_yes
    return won, won - price - taker_fee(price, fee_mult)
