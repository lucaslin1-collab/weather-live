"""Read-only client for Kalshi's public market-data endpoints. It cannot place orders."""
import time

import pandas as pd
import requests

from config import KALSHI_BASES


def to_unix(value):
    """Kalshi returns some times as ISO strings and some as unix seconds."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return int(value)
    return int(pd.Timestamp(value).timestamp())


def dollars(value):
    """Kalshi fixed-point dollar strings ("0.4500") -> float."""
    if value is None or value == "":
        return None
    return float(value)


class KalshiPublic:
    def __init__(self, bases=KALSHI_BASES, min_interval=0.1, max_retries=6):
        self.bases = list(bases)
        self.session = requests.Session()
        self.min_interval = min_interval  # stays under the basic-tier read limit
        self.max_retries = max_retries
        self._last_call = 0.0

    def get(self, path, **params):
        params = {k: v for k, v in params.items() if v is not None}
        resp = None
        for attempt in range(self.max_retries):
            wait = self.min_interval - (time.time() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.time()
            try:
                resp = self.session.get(f"{self.bases[0]}{path}", params=params, timeout=30)
            except requests.ConnectionError:
                if len(self.bases) > 1:          # host unreachable: try the next one
                    self.bases.append(self.bases.pop(0))
                    continue
                raise
            if resp.status_code == 429 or resp.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            resp.raise_for_status()
            return resp.json()
        if resp is None:
            raise ConnectionError("Kalshi API unreachable")
        resp.raise_for_status()

    def paginate(self, path, key, **params):
        cursor = None
        while True:
            data = self.get(path, cursor=cursor, **params)
            yield from data.get(key, []) or []
            cursor = data.get("cursor")
            if not cursor:
                break

    # --- series and markets ----------------------------------------------------
    def weather_series(self):
        return self.get("/series", category="Climate and Weather").get("series", []) or []

    def series(self, series_ticker):
        return self.get(f"/series/{series_ticker}").get("series", {})

    def historical_cutoff_ts(self):
        """Markets settled before this time are served from /historical/..."""
        return to_unix(self.get("/historical/cutoff").get("market_settled_ts"))

    def settled_markets(self, series_ticker, min_close_ts=None, include_historical=True):
        markets = {}
        for m in self.paginate("/markets", "markets", series_ticker=series_ticker,
                               status="settled", min_close_ts=min_close_ts, limit=1000):
            markets[m["ticker"]] = m
        if include_historical:
            for m in self.paginate("/historical/markets", "markets",
                                   series_ticker=series_ticker, limit=1000):
                markets.setdefault(m["ticker"], m)
        return list(markets.values())

    def open_markets(self, series_ticker):
        return list(self.paginate("/markets", "markets", series_ticker=series_ticker,
                                  status="open", limit=1000))

    def market(self, ticker):
        try:
            return self.get(f"/markets/{ticker}").get("market", {})
        except requests.HTTPError as err:
            if err.response is not None and err.response.status_code == 404:
                return self.get(f"/historical/markets/{ticker}").get("market", {})
            raise

    # --- prices ------------------------------------------------------------------
    def batch_candlesticks(self, tickers, start_ts, end_ts, period=60):
        """Up to 100 markets per call. Returns {ticker: [candles]}."""
        data = self.get("/markets/candlesticks", market_tickers=",".join(tickers),
                        start_ts=int(start_ts), end_ts=int(end_ts), period_interval=period,
                        include_latest_before_start="true")
        out = {}
        for entry in data.get("markets", []) or []:
            ticker = entry.get("market_ticker") or entry.get("ticker")
            out[ticker] = entry.get("candlesticks", []) or []
        return out

    def historical_candlesticks(self, ticker, start_ts, end_ts, period=60):
        data = self.get(f"/historical/markets/{ticker}/candlesticks", start_ts=int(start_ts),
                        end_ts=int(end_ts), period_interval=period)
        return data.get("candlesticks", []) or []
