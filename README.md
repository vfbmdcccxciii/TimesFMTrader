# TimesFM Paper Trader

A daily-automated paper trading experiment. Each run, Google's [TimesFM 2.5](https://huggingface.co/google/timesfm-2.5-200m-pytorch) scans the **S&P 500 + top 50 crypto** by market cap, ranks every name by 5-day expected return, and a rule-based engine turns those forecasts into BUY / SELL / HOLD decisions on a $250 paper portfolio. Every decision is logged to Notion.

**This is an experiment, not investment advice.** TimesFM is a general-purpose foundation model — it was not trained for trading, and short-horizon market forecasting is close to a random walk. Don't extrapolate paper results to real money.

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

| Rule | Value | Why |
|------|-------|-----|
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

---

## Setup

### 1. Notion side

1. Go to <https://www.notion.so/my-integrations> → **New integration**. Name it "TimesFM Trader". Save the **Internal Integration Token**.
2. Create a parent page in Notion. Connect your integration to it.
3. Copy the parent page's 32-char hex ID from its URL.

### 2. Bootstrap the databases

```bash
git clone <this-repo>
cd timesfm-trader
pip install requests

export NOTION_TOKEN=secret_xxx
export NOTION_PARENT_PAGE_ID=abcdef0123456789...
python bootstrap_notion.py
```

Creates three databases (Trade Log, Daily Scan, Daily News) and prints the IDs.

### 3. (Optional but recommended) Free news API

Get a free Finnhub key at <https://finnhub.io/dashboard>. Free tier: 60 calls/min.

### 4. GitHub side

In your repo: **Settings → Secrets and variables → Actions → New repository secret**, add:

| Name | Value |
|---|---|
| `NOTION_TOKEN` | Integration token from step 1 |
| `NOTION_TRADE_LOG_ID` | Trade Log DB id from bootstrap |
| `NOTION_SCAN_LOG_ID` | Daily Scan DB id from bootstrap |
| `NOTION_NEWS_DATABASE_ID` | Daily News DB id from bootstrap |
| `NOTION_PARENT_PAGE_ID` | Parent page id (for the Daily Status callout) |
| `FINNHUB_API_KEY` | Free Finnhub key (optional) |

The previous `NOTION_DATABASE_MAP` secret is no longer used — you can delete it.

Push the repo. The workflow runs Mon–Fri at 21:30 UTC. Trigger manually from the **Actions** tab to test.

### 5. First run

The first run will:
- Pull the S&P 500 list from Wikipedia and top 50 crypto from CoinGecko (~550 tickers).
- Bulk-download price history via yfinance, apply quality filters (~480 stocks, ~30–40 crypto remain).
- Forecast each survivor with TimesFM in batches of 32 (~1.5 minutes).
- See no `state/portfolio.json` → initialize with $250 cash.
- Buy the top-ranked candidates that pass thresholds (likely 0–6).
- Log every BUY/SELL/HOLD to **Trade Log**, top 25 candidates to **Daily Scan**, news for held positions to **Daily News**, and refresh the **Daily Status** callout.
- Commit the new `state/portfolio.json` to the repo.

Subsequent runs read the committed state, so the portfolio is durable across runs.

---

## Files

| File | What it does |
|---|---|
| `trader.py` | Orchestrator — universe, prices, forecasts, rules, Notion |
| `universe.py` | S&P 500 (Wikipedia) + crypto top-N (CoinGecko) + quality filters |
| `timesfm_engine.py` | Loads TimesFM 2.5 and runs batched forecasts |
| `portfolio.py` | Portfolio state class + `TradingRules` (edit thresholds here) |
| `notion_client.py` | Trade Log + Daily Scan + News + Status callout |
| `news_client.py` | Finnhub headline fetcher |
| `bootstrap_notion.py` | One-time: creates the three Notion databases |
| `state/portfolio.json` | Persisted portfolio (auto-committed) |
| `.github/workflows/daily.yml` | The cron schedule |

## Things to watch

- **GitHub Actions free tier** is 2,000 minutes/month for private repos (unlimited for public). With S&P 500 + crypto a run is ~5–10 minutes — within budget.
- **TimesFM 2.5 is 200M params** (~800MB). Fits comfortably on the 7GB ubuntu-latest runner. The HF cache action persists the model between runs.
- **yfinance** can rate-limit on bulk requests. We chunk to 100 tickers per call and skip missing data.
- **CoinGecko free tier** rate-limits at ~30 req/min; we make 1 call per run with retries.
- **Notion API rate limit** is 3 req/sec average. With ~10 trade rows + 25 scan rows + a few news rows per run, we're well inside the limit.
- **Multiple-comparisons risk**: Scanning 500+ names and picking top 6 introduces selection bias. Even with zero real edge, the chosen names will look great relative to a random pick. Treat the experiment accordingly.
- **Survivorship bias**: Wikipedia's S&P 500 list is alive-today only. Backtests against history will look better than reality.

## Extending

- **Bigger universe**: edit `fetch_sp500_tickers()` in `universe.py` to pull Russell 1000 or NASDAQ-100 instead.
- **Hourly instead of daily**: change `interval="1d"` to `"1h"` and the cron expression.
- **Compare to baseline**: also run a "buy-and-hold the top-1 by past return" strategy in parallel and log to a separate database.
- **Confidence-weighted sizing**: instead of equal 20% slots, size positions by `(expected_return / forecast_uncertainty)`.
