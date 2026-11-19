"""Forecast the full distribution of the daily high, then price every bracket from it."""
import numpy as np
import pandas as pd
from scipy import stats
from sklearn.linear_model import LinearRegression, LogisticRegression, Ridge

try:
    from weather import add_daily_features  # used by the forecast model only
except ImportError:
    add_daily_features = None

EPS = 0.01


# --- Contract semantics ---------------------------------------------------------
# The CLI reports whole degrees, so each bracket covers a range of integers and
# we add +/-0.5 when pricing it with a continuous distribution.
#   between  floor=58 cap=59 -> YES if 58 <= T <= 59
#   less     cap=73          -> YES if T < 73   ("72 or below")
#   greater  floor=80        -> YES if T > 80   ("81 or above")
# build_dataset.py checks these rules against Kalshi's actual settlements.

def bracket_bounds(strike_type, floor, cap):
    if strike_type == "between":
        return floor - 0.5, cap + 0.5
    if strike_type == "less":
        return -np.inf, cap - 0.5
    if strike_type == "less_or_equal":
        return -np.inf, cap + 0.5
    if strike_type == "greater":
        return floor + 0.5, np.inf
    if strike_type == "greater_or_equal":
        return floor - 0.5, np.inf
    raise ValueError(f"unsupported strike_type {strike_type!r}")


def bracket_label(strike_type, floor, cap):
    lo, hi = bracket_bounds(strike_type, floor, cap)
    if np.isinf(lo):
        return f"≤{hi - 0.5:.0f}°"
    if np.isinf(hi):
        return f"≥{lo + 0.5:.0f}°"
    return f"{lo + 0.5:.0f}–{hi - 0.5:.0f}°"


def bracket_mid(strike_type, floor, cap):
    """A representative temperature for the bracket (used for the market-implied mean)."""
    lo, hi = bracket_bounds(strike_type, floor, cap)
    if np.isinf(lo):
        return hi - 1.5
    if np.isinf(hi):
        return lo + 1.5
    return (lo + hi) / 2


def implied_result(strike_type, floor, cap, observed_high):
    lo, hi = bracket_bounds(strike_type, floor, cap)
    return int(lo < round(observed_high) < hi)


def logit(p):
    p = np.clip(p, EPS, 1 - EPS)
    return np.log(p / (1 - p))


# --- Stage 1: weather model ---------------------------------------------------------

class HighTempModel:
    """mu  = ridge regression on the model forecasts + seasonality (fixes each model's bias)
    sigma = grows with disagreement between models (ensemble spread)."""

    def __init__(self, alpha=1.0, sigma_floor=1.0):
        self.alpha = alpha
        self.sigma_floor = sigma_floor

    def _design(self, daily):
        daily = add_daily_features(daily)
        X = pd.DataFrame(index=daily.index)
        X["ens1_mean"] = daily["ens1_mean"]
        for col in self.model_cols_:
            # a missing model falls back to the ensemble mean so the row stays usable
            X[col] = daily[col].fillna(daily["ens1_mean"]) if col in daily else daily["ens1_mean"]
        if "ens2_mean" in daily:
            X["trend"] = (daily["ens1_mean"] - daily["ens2_mean"]).fillna(0.0)
        X["doy_sin"], X["doy_cos"] = daily["doy_sin"], daily["doy_cos"]
        return X, daily["ens1_spread"].to_frame()

    def fit(self, daily):
        daily = daily.dropna(subset=["observed_high"])
        self.model_cols_ = sorted(c for c in daily.columns if c.startswith("fc1_"))
        X, spread = self._design(daily)
        ok = X["ens1_mean"].notna()
        X, spread, y = X[ok], spread[ok], daily.loc[ok, "observed_high"]
        self.mu_model_ = Ridge(alpha=self.alpha).fit(X, y)
        resid = y - self.mu_model_.predict(X)
        # E|N(0, s)| = s * sqrt(2/pi), so regress scaled absolute residuals on spread
        self.sigma_model_ = LinearRegression().fit(spread, np.abs(resid) * np.sqrt(np.pi / 2))
        self.train_rmse_ = float(np.sqrt(np.mean(resid ** 2)))
        return self

    def predict_dist(self, daily):
        X, spread = self._design(daily)
        ok = X["ens1_mean"].notna()
        mu = pd.Series(np.nan, index=daily.index)
        sigma = pd.Series(np.nan, index=daily.index)
        mu[ok] = self.mu_model_.predict(X[ok])
        sigma[ok] = np.maximum(self.sigma_model_.predict(spread[ok]), self.sigma_floor)
        return mu, sigma

    def bracket_probs(self, markets, daily):
        """Probability of YES for each market row (needs date, strike_type, floor_strike, cap_strike)."""
        mu, sigma = self.predict_dist(daily)
        m = mu.reindex(pd.to_datetime(markets["date"])).values
        s = sigma.reindex(pd.to_datetime(markets["date"])).values
        bounds = [bracket_bounds(t, f, c) for t, f, c in
                  zip(markets["strike_type"], markets["floor_strike"], markets["cap_strike"])]
        lo = np.array([b[0] for b in bounds])
        hi = np.array([b[1] for b in bounds])
        return stats.norm.cdf((hi - m) / s) - stats.norm.cdf((lo - m) / s)


# --- Stage 2: combine with the market ----------------------------------------------

class MarketBlend:
    """Logistic regression on logit(model p) and logit(market mid).
    Fit ONLY on out-of-sample model predictions; its weights tell you how much
    information the model adds beyond the price."""

    def fit(self, p_model, p_market, outcome):
        X = np.column_stack([logit(p_model), logit(p_market)])
        self.lr_ = LogisticRegression(C=1.0).fit(X, outcome)
        return self

    def predict(self, p_model, p_market):
        X = np.column_stack([logit(p_model), logit(p_market)])
        return self.lr_.predict_proba(X)[:, 1]

    @property
    def weights(self):
        return dict(model=float(self.lr_.coef_[0][0]), market=float(self.lr_.coef_[0][1]),
                    intercept=float(self.lr_.intercept_[0]))
