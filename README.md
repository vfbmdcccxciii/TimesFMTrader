# TimesFM Paper Trader

A daily-automated paper trading experiment. Each run, Google's [TimesFM 2.5](https://huggingface.co/google/timesfm-2.5-200m-pytorch) scans the **S&P 500 + top 50 crypto** by market cap, ranks every name by 5-day expected return, and a rule-based engine turns those forecasts into BUY / SELL / HOLD decisions on a $250 paper portfolio. Every decision is logged to Notion.

**This is an experiment, not investment advice.** TimesFM is a general-purpose foundation model — it was not trained for trading, and short-horizon market forecasting is close to a random walk. Don't extrapolate paper results to real money.

📊 Live results: [TimesFM Trader on Notion](https://www.notion.so/TimesFM-Trader-352b938a169a80afbaf1e310994b9974)

---

## Prerequisites

- **Python 3.11** (used by the workflow; 3.10+ should work locally for the bootstrap step)
- **git**
- A **GitHub account** (you'll fork this repo so the daily cron runs against your fork)
- A **Notion account** with permission to create integrations
- *(Optional)* A free **Finnhub** account for news headlines: <https://finnhub.io>

---

## TL;DR — fork-and-go

```bash
# 1. Fork this repo on GitHub, then clone YOUR fork:
git clone https://github.com/<your-username>/timesfm-trader.git
cd timesfm-trader

# 2. Create a Notion integration → save the token
#    https://www.notion.so/my-integrations  →  New integration  →  copy "Internal Integration Token"

# 3. Create a parent page in Notion → connect the integration
#    Top-right "..." menu  →  Connections  →  Add connections  →  pick your integration

# 4. Bootstrap the three Notion databases (one-time):
pip install requests
export NOTION_TOKEN=secret_xxx
export NOTION_PARENT_PAGE_ID=<32-char hex from your page URL, with or without dashes>
python bootstrap_notion.py
# This prints six values you'll paste into GitHub as repository secrets.

# 5. In your GitHub fork:
#    Settings → Secrets and variables → Actions → "New repository secret"
#    Add the six secrets the script printed.

# 6. Settings → Actions → General → Workflow permissions:
#    Select "Read and write permissions" (so the bot can commit portfolio.json back).

# 7. Trigger the workflow manually to verify:
#    Actions tab → "Daily TimesFM Trader" → Run workflow.
```

If the manual run succeeds you'll see new rows appear in the Notion databases and a refreshed Daily Status callout. After that the cron takes over (Mon–Fri at 21:30 UTC).

---

## Architecture

```
┌─────────────────┐    ┌────────────────────────┐    ┌──────────────────┐
│ GitHub Actions  │───▶│  trader.py             │───▶│  Notion          │
│ daily cron      │    │  ┌──────────────────┐  │    │  ├ Trade Log     │
│                 │    │  │ universe.py      │  │    │  ├ Daily Scan    │
└─────────────────┘    │  │ (S&P 500 + top   │  │    │  ├ Daily News    │
                       │  │  crypto + filt.) │  │    │  └ Status callout│
                       │  └────────┬─────────┘  │    └──────────────────┘
                       │  ┌────────▼─────────┐  │
                       │  │ TimesFM forecast │  │
                       │  │ (batched)        │  │
                       │  └────────┬─────────┘  │
                       │  ┌────────▼─────────┐  │
                       │  │ Trading rules    │  │
                       │  └────────┬─────────┘  │
                       │  state/              │
                       │  portfolio.json      │ ← committed back to repo
                       └──────────────────────┘
```

## Trading rules

| Rule | Default | Why |
|------|---------|-----|
| Universe | S&P 500 + top 50 crypto | Wider net for the model to find signal |
| Quality filter | ≥200 days history, ≥$5 stock price, ≥$5M ADV | Drop illiquid / new IPOs |
| Max position size | 20% of portfolio | Forces ≥5-position diversification |
| Stop-loss | -8% from entry | Caps downside per position |
| Take-profit | +15% from entry | Locks in gains, avoids round-trips |
| BUY threshold | Forecast ≥ +2% over 5 days | Filters noise |
| Confidence filter | 10th-percentile forecast must exceed current price | Skips uncertain signals |
| Cash floor | $10 | Buffer for rounding |
| Cooldown | 24h between trades on same asset | Prevents whipsaw |
| Max concurrent positions | 6 | |
| Long-only, no leverage, no shorting | | Sanity |

(Where to actually change these → see the **Configuration** section below.)

---

## Setup (full walkthrough)

### 1. Notion integration

1. Go to <https://www.notion.so/my-integrations> → **New integration**. Name it "TimesFM Trader". Click **Save**, then copy the **Internal Integration Token** (starts with `secret_` or `ntn_`). This is your `NOTION_TOKEN`.
2. In Notion, create a parent page (any name — e.g. "📊 TimesFM Trader"). Open it.
3. Connect your integration to that page: top-right `...` menu → **Connections** → **Add connections** → search for "TimesFM Trader" → confirm. Without this step the script can't write to your page.
4. Copy the parent page's ID. The Notion URL looks like `https://www.notion.so/My-Page-352b938a169a80afbaf1e310994b9974` — the trailing 32-char hex is the ID. With or without dashes both work.

### 2. Bootstrap the databases (one-time)

```bash
git clone https://github.com/<your-username>/timesfm-trader.git
cd timesfm-trader
pip install requests

export NOTION_TOKEN=secret_xxx
export NOTION_PARENT_PAGE_ID=352b938a169a80afbaf1e310994b9974
python bootstrap_notion.py
```

Creates three databases (Trade Log, Daily Scan, Daily News) on your parent page and prints the six values you'll need in step 4.

### 3. (Optional) Free news API

Get a free Finnhub key at <https://finnhub.io/dashboard>. Free tier: 60 calls/min — well within budget. Skip if you don't want headlines; news fetching is silently disabled when the key isn't set.

### 4. GitHub repository secrets

In your fork: **Settings → Secrets and variables → Actions → New repository secret** for each of the following:

| Name | Value |
|---|---|
| `NOTION_TOKEN` | Integration token from step 1 |
| `NOTION_TRADE_LOG_ID` | Trade Log DB id printed by `bootstrap_notion.py` |
| `NOTION_SCAN_LOG_ID` | Daily Scan DB id printed by `bootstrap_notion.py` |
| `NOTION_NEWS_DATABASE_ID` | Daily News DB id printed by `bootstrap_notion.py` |
| `NOTION_PARENT_PAGE_ID` | Parent page ID from step 1.4 |
| `FINNHUB_API_KEY` | Finnhub key (optional) |

Then enable workflow write permissions: **Settings → Actions → General → Workflow permissions** → "Read and write permissions" → Save. Without this, the bot can't commit the updated `state/portfolio.json` back.

### 5. First run

Push the repo (or trigger the workflow manually from the **Actions** tab). The first run will:

- Pull S&P 500 from Wikipedia and top 50 crypto from CoinGecko (~550 tickers).
- Bulk-download price history via yfinance, apply quality filters (~480 stocks, ~30–40 crypto remain).
- Forecast each survivor with TimesFM in batches of 32 (~1.5 minutes after model load).
- See no `state/portfolio.json` → initialize with $250 cash.
- Buy the top-ranked candidates that pass thresholds (likely 0–6).
- Log every BUY/SELL/HOLD to **Trade Log**, top 25 candidates to **Daily Scan**, news for held positions to **Daily News**, and refresh the **Daily Status** callout.
- Commit the new `state/portfolio.json` to the repo.

Subsequent runs read the committed state, so the portfolio is durable across runs.

The cron is `30 21 * * 1-5` (Mon–Fri 21:30 UTC, ~30 min after US market close). Edit `.github/workflows/daily.yml` line 7 to change.

---

## Configuration

Every tunable lives in one of four files. Edit, commit, push — the next run picks it up.

### Portfolio & forecasting (`trader.py` lines 44–51)

| Constant | Default | Effect |
|---|---|---|
| `CONTEXT_DAYS` | 220 | History window fed to TimesFM. Must be ≥ `MIN_HISTORY_DAYS`. |
| `HORIZON_DAYS` | 5 | Forecast horizon in trading days (1..128). |
| `INITIAL_CASH` | 250.0 | Starting paper cash. Only matters before `state/portfolio.json` exists. |
| `SCAN_LOG_TOP_N` | 25 | How many top-ranked candidates get a row in Daily Scan each run. |
| `FORECAST_BATCH` | 32 | TimesFM batch size. Larger = faster, more memory. |
| `DOWNLOAD_CHUNK` | 100 | Tickers per yfinance bulk call. |
| `CRYPTO_TOP_N` | 50 | How many crypto names to pull from CoinGecko. |

### Trading rules (`portfolio.py` lines 39–47, in the `TradingRules` dataclass)

| Field | Default | Effect |
|---|---|---|
| `max_position_pct` | 0.20 | Max % of portfolio in any one asset. Drop to 0.10 for ≥10 positions. |
| `min_cash_floor` | 10.0 | Never deplete cash below this $ amount. |
| `stop_loss_pct` | 0.08 | Auto-SELL when a position is down this fraction from cost. |
| `take_profit_pct` | 0.15 | Auto-SELL when a position is up this fraction from cost. |
| `min_expected_return` | 0.02 | Don't BUY unless 5-day forecast ≥ this fraction. Tighten to 0.05 for fewer/higher-conviction trades. |
| `require_positive_q10` | True | If True, the 10th-percentile forecast must exceed current price (stricter). |
| `cooldown_hours` | 24 | Don't trade the same asset more than once per this many hours. |
| `max_concurrent_positions` | 6 | Max simultaneous open positions. |
| `min_buy_dollars` | 5.0 | Skip buys smaller than this — avoids dust trades. |

### Universe filters (`universe.py` lines 37–48)

| Constant | Default | Effect |
|---|---|---|
| `MIN_HISTORY_DAYS` | 200 | Drop tickers with fewer days of clean price history. |
| `MIN_PRICE_STOCK` | 5.0 | Drop sub-$5 stocks (penny-stock spreads eat the +2% threshold). |
| `MIN_PRICE_CRYPTO` | 0.05 | Drop micro-priced coins. |
| `MIN_DOLLAR_VOL_STOCK` | 5_000_000 | $5M average daily dollar volume floor. |
| `MIN_DOLLAR_VOL_CRYPTO` | 50_000_000 | $50M daily volume floor. |
| `STABLECOIN_BLOCKLIST` | set of 25 names | Stablecoins + wrapped tokens excluded from the crypto universe. |

### Schedule (`.github/workflows/daily.yml` line 7)

The cron expression `30 21 * * 1-5` means 21:30 UTC, Monday–Friday. Edit to change time or run weekends. Use <https://crontab.guru/> if cron syntax isn't your favorite thing.

---

## Files

| File | What it does |
|---|---|
| `trader.py` | Orchestrator — universe, prices, forecasts, rules, Notion |
| `universe.py` | S&P 500 (Wikipedia) + crypto top-N (CoinGecko) + quality filters |
| `timesfm_engine.py` | Loads TimesFM 2.5 and runs batched forecasts |
| `portfolio.py` | Portfolio state class + `TradingRules` |
| `notion_client.py` | Trade Log + Daily Scan + News + Status callout |
| `news_client.py` | Finnhub headline fetcher |
| `bootstrap_notion.py` | One-time: creates the three Notion databases |
| `state/portfolio.json` | Persisted portfolio (auto-committed by the bot) |
| `.github/workflows/daily.yml` | The cron schedule |

## Things to watch

- **GitHub Actions free tier** is 2,000 minutes/month for private repos (unlimited for public). With S&P 500 + crypto a run is ~5–10 minutes — within budget, but make the repo public if you want unlimited headroom. Secrets stay encrypted regardless.
- **TimesFM 2.5 is 200M params** (~800MB). Fits comfortably on the 7GB ubuntu-latest runner. The HF cache action persists the model between runs.
- **yfinance** can rate-limit on bulk requests and pushes breaking changes a few times a year. We chunk to 100 tickers per call. If you start seeing failures after a yfinance auto-update, pin the version in `requirements.txt` to your last known-good release.
- **CoinGecko free tier** rate-limits at ~30 req/min; we make 1 call per run with retries.
- **Notion API rate limit** is 3 req/sec average. With ~10 trade rows + 25 scan rows + a few news rows per run, we're well inside.
- **Multiple-comparisons risk**: Scanning 500+ names and picking top 6 introduces selection bias. Even with zero real edge, the chosen names will look great relative to a random pick. Treat the experiment accordingly.
- **Survivorship bias**: Wikipedia's S&P 500 list is alive-today only. Backtests against history will look better than reality.
- **Scheduled runs aren't exact-time.** GitHub delays scheduled workflows during high-load periods; expect occasional skipped or delayed runs. Failed runs email the repo owner by default.

## Extending

A few common tweaks, in copy-paste form.

**Bigger budget.** In `trader.py`:

```python
INITIAL_CASH = 1000.0     # was 250.0
```

(Only takes effect before `state/portfolio.json` exists. To reset, delete the file and let the next run re-initialize.)

**Tighter buy threshold (fewer / higher-conviction trades).** In `portfolio.py`, `TradingRules`:

```python
min_expected_return: float = 0.05   # was 0.02 — require ≥+5% forecast
```

**Wider diversification (forces ≥10 positions).** In `portfolio.py`:

```python
max_position_pct:         float = 0.10   # was 0.20
max_concurrent_positions: int   = 10     # was 6
```

**Hourly instead of daily.** In `trader.py`, swap `interval="1d"` for `"1h"` in `bulk_download` and bump `CONTEXT_DAYS`. Then in `.github/workflows/daily.yml`:

```yaml
- cron: "0 * * * 1-5"   # every hour on weekdays
```

**Different universe (e.g. NASDAQ-100 instead of S&P 500).** Replace `fetch_sp500_tickers()` in `universe.py` with a NASDAQ-100 fetch; the rest of the pipeline is universe-agnostic.

**Confidence-weighted sizing.** In `portfolio.evaluate_entries`, replace the equal-weight allocation with `(expected_return / forecast_uncertainty) * total_value`, capped by `max_position_pct`. Forecasts already carry `q10_end` and `q90_end` you can use as the uncertainty band.

**Compare to baseline.** Add a second logger that runs a "buy-and-hold the top-1 by past return" strategy in parallel and writes to a separate Notion DB — that tells you whether TimesFM is adding value vs. simple momentum.
