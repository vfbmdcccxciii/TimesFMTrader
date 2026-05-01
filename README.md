# TimesFM Paper Trader

A daily-automated paper trading experiment. Google's [TimesFM 2.5](https://huggingface.co/google/timesfm-2.5-200m-pytorch) forecasts daily close prices for a small universe of stocks and crypto. A rule-based engine turns those forecasts into BUY / SELL / HOLD decisions on a $250 paper portfolio, and writes every decision to a per-asset Notion database.

**This is an experiment, not investment advice.** TimesFM is a general-purpose foundation model trained on time-series data — it was not trained for trading and short-horizon market forecasting is close to a random walk. Don't extrapolate paper results to real money.

---

## Architecture

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│ GitHub Actions  │───▶│   trader.py      │───▶│  Notion API     │
│ daily cron      │    │  ┌────────────┐  │    │  (one DB per    │
│                 │    │  │ TimesFM    │  │    │   ticker)       │
└─────────────────┘    │  │ forecast   │  │    └─────────────────┘
                       │  └─────┬──────┘  │
                       │        ▼         │
                       │  ┌────────────┐  │
                       │  │ Trading    │  │
                       │  │ rules      │  │
                       │  └─────┬──────┘  │
                       │        ▼         │
                       │  state/          │
                       │  portfolio.json  │ ← committed back to repo
                       └──────────────────┘
```

## Trading rules

| Rule | Value | Why |
|------|-------|-----|
| Max position size | 20% of portfolio | Forces ≥5-position diversification |
| Stop-loss | -8% from entry | Caps downside per position |
| Take-profit | +15% from entry | Locks in gains, avoids round-trips |
| BUY threshold | Forecast ≥ +2% over 5 days | Filters noise |
| Confidence filter | 10th-percentile forecast must exceed current price | Skips uncertain signals |
| Cash floor | $10 | Buffer for rounding |
| Cooldown | 24h between trades on same asset | Prevents whipsaw |
| Max concurrent positions | 6 | |
| Long-only, no leverage, no shorting | | Sanity |

## Universe

Stocks: AAPL, MSFT, NVDA, GOOGL, TSLA  
Crypto: BTC-USD, ETH-USD, SOL-USD

Edit `CANDIDATES` in `trader.py` to change.

---

## Setup

### 1. Notion side

1. Go to <https://www.notion.so/my-integrations> → **New integration**. Name it "TimesFM Trader". Save the **Internal Integration Token** (starts with `secret_` or `ntn_`) — this is your `NOTION_TOKEN`.
2. In Notion, create a parent page (e.g. "📊 TimesFM Trader"). Open the page → click `…` (top-right) → **Connections** → connect your integration.
3. Copy the parent page's ID. It's the 32-char hex in the URL: `notion.so/Page-Name-`**`abcdef0123456789...`**.

### 2. Bootstrap the databases

```bash
git clone <this-repo>
cd timesfm-trader
pip install requests

export NOTION_TOKEN=secret_xxx
export NOTION_PARENT_PAGE_ID=abcdef0123456789...
python bootstrap_notion.py
```

This creates 8 per-ticker databases plus a single "Daily News" database, then prints the JSON map and the news DB id.

### 3. (Optional but recommended) Free news API

Get a free Finnhub key at <https://finnhub.io/dashboard>. The free tier gives 60 calls/min — well within budget.

### 4. GitHub side

In your repo: **Settings → Secrets and variables → Actions → New repository secret**, add:

| Name | Value |
|---|---|
| `NOTION_TOKEN` | The integration token from step 1 |
| `NOTION_DATABASE_MAP` | The JSON printed by `bootstrap_notion.py` |
| `NOTION_NEWS_DATABASE_ID` | The news DB id printed by `bootstrap_notion.py` |
| `NOTION_PARENT_PAGE_ID` | Same id you exported in step 2 (used to update the Daily Status callout) |
| `FINNHUB_API_KEY` | Your Finnhub API key (optional — news is skipped if absent) |

Push the repo. The workflow runs Mon–Fri at 21:30 UTC (≈30 min after US market close). You can also trigger it manually from the **Actions** tab.

### 5. First run

The first run will:
- See no `state/portfolio.json` → initialize with $250 cash and no holdings.
- Forecast all 8 candidates.
- Buy the top-ranked candidates that pass thresholds (likely 0–6).
- Log a row to each ticker's Notion database.
- Commit the new `state/portfolio.json` to the repo.

Subsequent runs read the committed state, so the portfolio is durable across runs without any external database.

---

## Files

| File | What it does |
|---|---|
| `trader.py` | Orchestrator — pulls prices, calls TimesFM, applies rules, logs to Notion |
| `timesfm_engine.py` | Loads TimesFM 2.5 and runs forecasts |
| `portfolio.py` | Portfolio state class + `TradingRules` (edit thresholds here) |
| `notion_client.py` | Writes one page per decision to Notion |
| `bootstrap_notion.py` | One-time: creates the 8 databases with the right schema |
| `state/portfolio.json` | Persisted portfolio (auto-committed) |
| `.github/workflows/daily.yml` | The cron schedule |

## Things to watch

- **GitHub Actions free tier** is 2,000 minutes/month for private repos (unlimited for public). One run is ~5–10 minutes including model load → well within budget. The HF cache action persists the model between runs after the first download.
- **TimesFM 2.5 is 200M params** (~800MB). Fits comfortably on the 7GB ubuntu-latest runner. If you switch to 2.0-500M (>4GB), expect higher RAM use and longer cold-start.
- **yfinance** occasionally rate-limits or returns gaps. The script catches per-ticker failures and continues.
- **Notion API rate limit** is 3 req/sec average. We make ≤8 writes per run — fine.
- **Survivorship & look-ahead bias**: yfinance's `auto_adjust=True` uses split- and dividend-adjusted prices. That's correct for forecasting *price levels*, but be aware that backtests against history will look better than reality.

## Extending

- **More assets**: edit `CANDIDATES` in `trader.py`, run `bootstrap_notion.py` again for new ones, update the secret.
- **Hourly instead of daily**: change `interval="1d"` to `"1h"` and the cron expression. TimesFM handles any frequency.
- **Compare to baseline**: also run a "buy-and-hold the top-1 by past return" strategy in parallel and log to a separate database — that tells you if TimesFM is actually adding value vs momentum.
- **Confidence-weighted sizing**: instead of equal 20% slots, size positions by `(expected_return / forecast_uncertainty)`.
