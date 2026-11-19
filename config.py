"""Settings for the live (intraday) high-temperature bot."""
import os

# --- Kalshi -------------------------------------------------------------------
KALSHI_BASES = [
    "https://api.elections.kalshi.com/trade-api/v2",
    "https://external-api.kalshi.com/trade-api/v2",
]

# Same city table as the forecast bot (series -> city, station, lat, lon, time zone).
BUILTIN_CITIES = {
    "KXHIGHNY":    ("New York (Central Park)", "KNYC", 40.7789, -73.9692, "America/New_York"),
    "KXHIGHCHI":   ("Chicago (Midway)",        "KMDW", 41.7860, -87.7524, "America/Chicago"),
    "KXHIGHMIA":   ("Miami",                   "KMIA", 25.7881, -80.3169, "America/New_York"),
    "KXHIGHAUS":   ("Austin",                  "KAUS", 30.1831, -97.6799, "America/Chicago"),
    "KXHIGHLAX":   ("Los Angeles (LAX)",       "KLAX", 33.9382, -118.3866, "America/Los_Angeles"),
    "KXHIGHDEN":   ("Denver",                  "KDEN", 39.8466, -104.6562, "America/Denver"),
    "KXHIGHPHIL":  ("Philadelphia",            "KPHL", 39.8733, -75.2268, "America/New_York"),
    "KXHIGHTPHX":  ("Phoenix",                 "KPHX", 33.4277, -112.0038, "America/Phoenix"),
    "KXHIGHTHOU":  ("Houston (Hobby)",         "KHOU", 29.6375, -95.2825, "America/Chicago"),
    "KXHIGHTMIN":  ("Minneapolis",             "KMSP", 44.8831, -93.2289, "America/Chicago"),
    "KXHIGHTOKC":  ("Oklahoma City",           "KOKC", 35.3889, -97.6006, "America/Chicago"),
    "KXHIGHTSFO":  ("San Francisco (SFO)",     "KSFO", 37.6197, -122.3647, "America/Los_Angeles"),
    "KXHIGHTDC":   ("Washington DC (DCA)",     "KDCA", 38.8483, -77.0342, "America/New_York"),
    "KXHIGHTBOS":  ("Boston",                  "KBOS", 42.3606, -71.0097, "America/New_York"),
    "KXHIGHTDAL":  ("Dallas (DFW)",            "KDFW", 32.8975, -97.0219, "America/Chicago"),
    "KXHIGHTSEA":  ("Seattle",                 "KSEA", 47.4447, -122.3144, "America/Los_Angeles"),
    "KXHIGHTLV":   ("Las Vegas",               "KLAS", 36.0719, -115.1634, "America/Los_Angeles"),
    "KXHIGHTATL":  ("Atlanta",                 "KATL", 33.6301, -84.4418, "America/New_York"),
    "KXHIGHTSATX": ("San Antonio",             "KSAT", 29.5443, -98.4839, "America/Chicago"),
    "KXHIGHTNOLA": ("New Orleans",             "KMSY", 29.9934, -90.2580, "America/Chicago"),
    "KXHIGHTSAN":  ("San Diego",               "KSAN", 32.7336, -117.1831, "America/Los_Angeles"),
    "KXHIGHTSDF":  ("Louisville",              "KSDF", 38.1811, -85.7392, "America/Kentucky/Louisville"),
    "KXHIGHTEWR":  ("Newark",                  "KEWR", 40.6825, -74.1694, "America/New_York"),
    "KXHIGHTTTN":  ("Trenton",                 "KTTN", 40.2767, -74.8135, "America/New_York"),
}

# --- Weather -----------------------------------------------------------------------
FORECAST_MODELS = ["gfs_seamless", "ecmwf_ifs025", "icon_seamless", "gem_seamless"]
LEADS = [1]                    # forecasts as issued the day before (same as the forecast bot)
WEATHER_START = "2024-01-15"

# --- When the live bot trades ------------------------------------------------------
# Local clock hours (in each city's own time zone) when the bot may act. The backtest
# scores each of these hours separately, so you can see which part of the day has an edge.
DECISION_HOURS = [10, 11, 12, 13, 14, 15, 16]
MARKET_HISTORY_DAYS = 150      # days of intraday Kalshi prices `setup` downloads for the backtest

# --- Signals (same rules as the forecast bot) -------------------------------------------
EDGE_THRESHOLD = 0.04
MAX_SPREAD = 0.10
MIN_PRICE, MAX_PRICE = 0.04, 0.96
MAX_PICKS_PER_CITY = 2          # positions per city per day
BASE_FEE_RATE = 0.07
KELLY_FRACTION = 0.25
MAX_STAKE_PCT = 0.02

# --- Paper trading ----------------------------------------------------------------------
PAPER_BANKROLL = 1000.0         # pretend dollars the paper trader sizes bets from

# --- Real orders: OFF unless you deliberately turn them on ---------------------------------
# Requires ALL of: repository variable LIVE_TRADING=true, KALSHI_ENV=prod (anything else uses
# Kalshi's demo exchange), and secrets KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY.
LIVE_TRADING = os.environ.get("LIVE_TRADING", "").lower() == "true"
KALSHI_ENV = os.environ.get("KALSHI_ENV", "demo").lower()
KALSHI_ORDER_BASES = {
    "prod": "https://api.elections.kalshi.com",
    "demo": "https://external-api.demo.kalshi.co",
}
REAL_ONLY_PROVEN_CITIES = True  # real orders only in cities whose backtest shows an edge
MAX_ORDER_DOLLARS = 10.0        # cap per order
MAX_DAILY_DOLLARS = 50.0        # cap on total cost of real orders per day

# --- Backtest --------------------------------------------------------------------------
MIN_TRAIN_DAYS = 120
MIN_BLEND_DAYS = 30

# --- Files and website -----------------------------------------------------------------------
DATA_DIR = "data"
RESULTS_DIR = "results"
SITE_DIR = "site"
SITE_NAME = "Lucas Lin Weather Live"
SITE_URL = os.environ.get("SITE_URL", "").rstrip("/")
SITE_DOMAIN = os.environ.get("SITE_DOMAIN", "")
HUB_URL = "/"                    # the hub's address on the same github.io site
