import os

# ---------------------------------------------------------------------------
# Secrets — set these in Railway dashboard (or local .env). Never hardcode.
# ---------------------------------------------------------------------------
MASSIVE_API_KEY  = os.environ["MASSIVE_API_KEY"]
MASSIVE_BASE_URL = os.environ["MASSIVE_BASE_URL"]
TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# ---------------------------------------------------------------------------
# Strategy thresholds — safe to commit, tunable via env overrides
# ---------------------------------------------------------------------------
ATR_TOUCH_MULTIPLIER = float(os.environ.get("ATR_TOUCH_MULTIPLIER", 0.5))
ATR_PERIOD           = int(os.environ.get("ATR_PERIOD", 14))
RSI_PERIOD           = int(os.environ.get("RSI_PERIOD", 14))
RSI_MIN              = float(os.environ.get("RSI_MIN", 40.0))
RSI_MAX              = float(os.environ.get("RSI_MAX", 60.0))
VOLUME_AVG_PERIOD    = int(os.environ.get("VOLUME_AVG_PERIOD", 20))
MIN_BARS_ABOVE_VWAP  = int(os.environ.get("MIN_BARS_ABOVE_VWAP", 3))
MAX_VWAP_TOUCHES     = int(os.environ.get("MAX_VWAP_TOUCHES", 2))
VIX_TICKER           = os.environ.get("VIX_TICKER", "VIX")
VIX_MAX              = float(os.environ.get("VIX_MAX", 25.0))
TP1_R                = float(os.environ.get("TP1_R", 1.5))
TP2_R                = float(os.environ.get("TP2_R", 3.0))
SL_ATR_MULTIPLIER    = float(os.environ.get("SL_ATR_MULTIPLIER", 1.5))

# Grind stop
GRIND_ZONE_PCT  = float(os.environ.get("GRIND_ZONE_PCT", 0.001))
GRIND_MAX_BARS  = int(os.environ.get("GRIND_MAX_BARS", 4))

# Polling intervals (seconds)
POLL_INTERVAL_SEC  = int(os.environ.get("POLL_INTERVAL_SEC", 60))
SPY_REFRESH_SEC    = int(os.environ.get("SPY_REFRESH_SEC", 300))
VIX_REFRESH_SEC    = int(os.environ.get("VIX_REFRESH_SEC", 900))
TIER2_POLL_SEC     = int(os.environ.get("TIER2_POLL_SEC", 15))
REQUEST_DELAY_SEC  = float(os.environ.get("REQUEST_DELAY_SEC", 0.2))

# Watchlist path (relative to repo root)
WATCHLIST_PATH = os.environ.get("WATCHLIST_PATH", "watchlist.txt")
