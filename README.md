# VWAP Pullback Scanner

A real-time intraday scanner that detects the institutional VWAP pullback setup
across a watchlist of US equities and fires two-tier Telegram alerts.
Deployed as a persistent worker on Railway — runs 24/7, scans 9:30 AM – 3:55 PM ET.

---

## Setup: Telegram Bot

1. Open Telegram and search for **@BotFather**.
2. Send `/newbot`, follow the prompts, and copy the **token** it gives you
   (`123456789:AAxxxx…`). This is your `TELEGRAM_TOKEN`.
3. Start a chat with your new bot (just send it `/start`).
4. Search for **@userinfobot** and send `/start` — it replies with your numeric
   chat ID. This is your `TELEGRAM_CHAT_ID`.

---

## Setup: Massive Market Data API

Sign up at [massivemarketdata.com](https://massivemarketdata.com) and copy your
API key into `MASSIVE_API_KEY`. Set `MASSIVE_BASE_URL` to the base URL shown in
your dashboard (e.g. `https://api.massivemarketdata.com`).

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `MASSIVE_API_KEY` | ✅ | Your Massive Market Data API key |
| `MASSIVE_BASE_URL` | ✅ | Base URL for the Massive Market Data API |
| `TELEGRAM_TOKEN` | ✅ | Telegram bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | ✅ | Your Telegram numeric chat ID |
| `ATR_TOUCH_MULTIPLIER` | optional | Touch zone width in ATR units (default 0.5) |
| `ATR_PERIOD` | optional | ATR lookback period (default 14) |
| `RSI_PERIOD` | optional | RSI lookback period (default 14) |
| `RSI_MIN` | optional | RSI lower bound for C5 (default 40) |
| `RSI_MAX` | optional | RSI upper bound for C5 (default 60) |
| `VOLUME_AVG_PERIOD` | optional | Rolling volume avg period for C4 (default 20) |
| `MIN_BARS_ABOVE_VWAP` | optional | Min consecutive bars above VWAP for C1 (default 3) |
| `MAX_VWAP_TOUCHES` | optional | Max allowed VWAP touches for C3 (default 2) |
| `VIX_MAX` | optional | VIX ceiling for C8 (default 25.0) |
| `TP1_R` | optional | Take-profit 1 in R (default 1.5) |
| `TP2_R` | optional | Take-profit 2 in R (default 3.0) |
| `SL_ATR_MULTIPLIER` | optional | Stop-loss distance in ATR (default 1.5) |
| `GRIND_ZONE_PCT` | optional | VWAP grind warning band (default 0.001 = 0.1%) |
| `GRIND_MAX_BARS` | optional | Bars in grind zone before warning (default 4) |
| `POLL_INTERVAL_SEC` | optional | Main poll cycle in seconds (default 60) |
| `SPY_REFRESH_SEC` | optional | SPY snapshot refresh in seconds (default 300) |
| `VIX_REFRESH_SEC` | optional | VIX refresh in seconds (default 900) |
| `TIER2_POLL_SEC` | optional | Tier 2 1-min poll in seconds (default 15) |
| `REQUEST_DELAY_SEC` | optional | Delay between API calls (default 0.2) |
| `WATCHLIST_PATH` | optional | Path to watchlist file (default `watchlist.txt`) |

See [.env.example](.env.example) for a complete reference.

---

## Watchlist

Edit `watchlist.txt` — one ticker per line, `#` for comments:

```
AAPL
MSFT
NVDA
# skip this one for now
# TSLA
```

---

## Running Locally

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set environment variables
cp .env.example .env
# Edit .env with your real keys, then:
export $(grep -v '^#' .env | xargs)

# 3. Run
python main.py
```

The scanner will log to stdout. During non-market hours it sleeps quietly
and wakes up at 9:30 AM ET automatically.

---

## Deploying to Railway

### Option A — GitHub repo (recommended)

1. Push this repo to GitHub.
2. In the [Railway dashboard](https://railway.app), click **New Project → Deploy from GitHub repo**.
3. Select your repository. Railway auto-detects `Procfile` and runs `python main.py`.
4. Go to **Variables** and add all required environment variables from the table above.
5. Click **Deploy**. The worker starts immediately.

### Option B — Railway CLI

```bash
npm install -g @railway/cli
railway login
railway init          # creates a new project
railway up            # deploys current directory
railway variables set MASSIVE_API_KEY=... TELEGRAM_TOKEN=... # etc.
```

### Verifying the deployment

On first start the bot sends a Telegram message:

```
🚀 VWAP Scanner online
Watching N tickers: AAPL, MSFT, …
VIX max: 25.0 | Touch max: 2
```

Logs are visible in the Railway dashboard under **Deployments → Logs**.

---

## Alert Reference

### Tier 1 — Setup Forming
Fires when C1 + C2 + C3 + C4 + C5 + C7 + C8 all pass.
Price is in the VWAP touch zone but reclaim is not confirmed yet.

```
⚡ SETUP FORMING
AAPL | $182.45
VWAP: $181.90 | Touch #1
RSI: 47.3 ↑ | ATR: $1.82
Est. SL: $179.17
SPY: +0.34% | VIX: 18.2
```

### Tier 2 — Entry Signal
Fires when a Tier 1 is active AND the 1-minute bar crosses above the prior bar's high.

```
🟢 ENTRY SIGNAL
AAPL | $182.78
SL:  $179.17
TP1: $188.19 (+1.5R)
TP2: $193.59 (+3.0R)
Risk/share: $3.61
```

### Grind Warning
Fires after Tier 2 if price stalls within 0.1% of VWAP for 4+ consecutive bars.

```
⚠️ VWAP GRIND — AAPL
Stuck near VWAP for 4 bars
Consider exiting flat — momentum stalled
```

---

## Running Tests

```bash
pip install pytest
pytest tests/ -v
```

Tests use hardcoded mock data — no API keys or network access required.

---

## Project Structure

```
vwap_scanner/
├── main.py            # Entry point, signal handling
├── scanner.py         # Scan loop, API calls, state management
├── conditions.py      # C1–C8 condition functions
├── indicators.py      # ATR, RSI, VWAP calculations
├── alerts.py          # Telegram message builders and sender
├── state.py           # TickerState dataclass
├── config.py          # All config from environment variables
├── utils.py           # ET time helpers, logging setup, watchlist loader
├── watchlist.txt      # Tickers to scan
├── Procfile           # Railway process definition
├── railway.toml       # Railway build/deploy config
├── requirements.txt   # Pinned dependencies
├── .env.example       # Environment variable reference
└── tests/
    └── test_conditions.py
```
