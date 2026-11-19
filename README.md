# Lucas Lin Weather Live

The intraday companion to the forecast bot. About every 10 minutes during the day, it reads
each Kalshi settlement station's latest observations and combines them with the morning
forecast. From that it estimates how much higher today's temperature will go, prices every
bracket, paper-trades the ones the market looks wrong on, and publishes a live site.

**Real orders are off.** All positions are paper trades at real prices, sized from a
$1,000 paper bankroll.

## How it runs

The `Live bot` workflow (`.github/workflows/live.yml`) runs on GitHub's servers:

| When (UTC) | What |
|---|---|
| First push | Full setup: history download and backtest (~1–2 hours) |
| Every 10 min, 13:00–01:50 | One live cycle: observations, prices, paper trades, site |
| 12:40 daily | Refresh history and settled markets; Mondays also re-run the backtest |

Each run commits its data (positions, observations, models) back to the repository.
GitHub's scheduler can start runs several minutes late.

## Commands

```bash
pip install -r requirements.txt
python live.py demo      # simulated data -> demo/site/index.html
python live.py setup     # first run
python live.py run       # one live cycle
python live.py update    # refresh history and settled markets
python live.py backtest  # re-score cities and hours, retrain
```

## The model

- **Observed so far.** It takes the highest temperature from the start of the NWS climate
  day (midnight to midnight local *standard* time) up to now. It uses METAR temperatures to
  0.1 °C and the stations' 6-hour maximum reports.
- **Predicted rise.** It estimates how much higher the temperature will go, based on:
  - the forecast for the rest of the day, corrected by how far off the forecast is right now,
  - the full-day forecast, and
  - the hour of the day.
- **Brackets.** Each bracket's probability comes from a normal distribution that's cut
  off just below the high already observed. That cut gives brackets already passed a
  probability of zero.
- **Market blend.** The bot blends its probability with the market price, weighting each
  by how well it predicted past outcomes.

**Backtest by hour.** Each decision hour (10:00–16:00 local time) is tested as its own
strategy, so `results/backtest.json` and the site's track record show which part of the
day, if any, has an edge.

## Turning on real orders (later, deliberately)

Only consider this after the paper record and the backtest both show an edge.

1. Create a Kalshi API key, starting on Kalshi's **demo** exchange.
2. In **Settings → Secrets and variables → Actions**, add two secrets:
   `KALSHI_API_KEY_ID`, and `KALSHI_PRIVATE_KEY` (the PEM text).
3. In the same place, under **Variables**, set `LIVE_TRADING` to `true`. Orders go to the
   demo exchange unless you also set `KALSHI_ENV` to `prod`.

Limits are in `config.py`:
- real orders are placed only in cities whose backtest verdict is "edge",
- each order is immediate-or-cancel at the price seen,
- at most $10 per order and $50 per day.

## Data sources

| Data | Source |
|---|---|
| Live observations | aviationweather.gov |
| History and official highs | Iowa Environmental Mesonet |
| Forecasts | Open-Meteo (CC BY 4.0; the free tier is for non-commercial use) |
| Prices | Kalshi's public API |

A research project, not financial advice.
