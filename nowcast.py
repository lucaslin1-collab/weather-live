"""The live model: given the morning forecast and what the station has recorded so far,
how much higher will today's official high go?

Target: rise = final official high - highest temperature observed so far (>= ~0).
Features: forecast for the rest of the day corrected by how wrong the forecast is right now,
the full-day forecast, and the hour. The final high is then
    observed max so far + rise,  rise ~ Normal(predicted, sigma for that hour) cut off below 0,
which prices every bracket, including the ones already ruled out.
"""
import numpy as np
import pandas as pd
from scipy import stats
from sklearn.linear_model import Ridge

import config
from model import bracket_bounds
from obs import day_window_utc, obs_so_far
from store import local_to_utc

FLOOR = -1.0   # official high can sit up to ~1 °F under METAR-derived values after rounding


def _climate_day(times, std_offset):
    return (times + pd.Timedelta(hours=std_offset)).dt.normalize()


def raw_features(fc_day, obs_day, date, t, std_offset):
    """Features at moment t (UTC) for one climate day; None if data is missing."""
    ds, de = day_window_utc(date, std_offset)
    if len(fc_day) < 20 or obs_day.empty:
        return None
    obs_max, obs_now = obs_so_far(obs_day, ds, t)
    if np.isnan(obs_max):
        return None
    fc_at = float(np.interp(t.value, fc_day["time"].values.astype("int64"), fc_day["fc"].values))
    rest = fc_day.loc[fc_day["time"] > t, "fc"]
    return {"obs_max": obs_max, "obs_now": obs_now, "fc_max": float(fc_day["fc"].max()),
            "fc_at": fc_at, "fc_rest": float(rest.max()) if len(rest) else fc_at,
            "hours_left": (de - t).total_seconds() / 3600}


def build_rows(city, fc, obs, cli=None, hours=None):
    """One row per (day, decision hour) with features, plus the official high when known."""
    hours = hours or config.DECISION_HOURS
    off = city["std_offset"]
    fc = fc.assign(cday=_climate_day(fc["time"], off))
    obs = obs.assign(cday=_climate_day(obs["time"], off))
    fc_by, obs_by = dict(tuple(fc.groupby("cday"))), dict(tuple(obs.groupby("cday")))
    highs = cli.set_index("date")["high"] if cli is not None and not cli.empty else pd.Series(dtype=float)
    rows = []
    for d in sorted(set(fc_by) & set(obs_by)):
        for h in hours:
            t = local_to_utc(d, h, city["tz"])
            f = raw_features(fc_by[d], obs_by[d], d, t, off)
            if f:
                rows.append({"date": d, "hour": h, **f, "high": highs.get(d, np.nan)})
    return pd.DataFrame(rows)


def design(rows):
    X = pd.DataFrame(index=rows.index)
    now_err = rows["obs_now"] - rows["fc_at"]
    X["rest_gap"] = np.maximum(rows["fc_rest"] + now_err - rows["obs_max"], 0)
    X["fc_gap"] = np.maximum(rows["fc_max"] - rows["obs_max"], 0)
    X["now_err"] = now_err
    X["hours_left"] = rows["hours_left"]
    for h in config.DECISION_HOURS[1:]:
        X[f"h{h}"] = (rows["hour"] == h).astype(float)
    return X


class NowcastModel:
    def fit(self, rows):
        rows = rows.dropna(subset=["high"])
        X, rise = design(rows), rows["high"] - rows["obs_max"]
        self.reg_ = Ridge(alpha=1.0).fit(X, rise)
        resid = rise - self.reg_.predict(X)
        self.sigma_ = {h: max(float(resid[rows["hour"] == h].std() or 1.0), 0.6) for h in config.DECISION_HOURS}
        self.n_ = len(rows)
        return self

    def predict(self, rows):
        mu = rows["obs_max"].values + self.reg_.predict(design(rows))
        sigma = np.array([self.sigma_.get(h, max(self.sigma_.values())) for h in rows["hour"]])
        return mu, sigma

    def bracket_probs(self, markets, rows):
        """P(YES) for market rows (strike columns) joined to their feature row by position."""
        mu, sigma = self.predict(rows)
        lo_cut = rows["obs_max"].values + FLOOR
        out = []
        for i, (t, f, c) in enumerate(zip(markets["strike_type"], markets["floor_strike"], markets["cap_strike"])):
            a, b = bracket_bounds(t, f, c)
            a = max(a, lo_cut[i])
            if b <= a:
                out.append(0.0)
                continue
            dist = stats.truncnorm((lo_cut[i] - mu[i]) / sigma[i], np.inf, loc=mu[i], scale=sigma[i])
            out.append(float(dist.cdf(b) - dist.cdf(a)))
        return np.clip(out, 0.0, 1.0)


def nearest_hour(local_hour):
    """Map the current local hour onto the model's hour buckets."""
    return min(config.DECISION_HOURS, key=lambda h: abs(h - local_hour))
