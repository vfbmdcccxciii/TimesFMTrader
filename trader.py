"""
TimesFM Paper Trader — daily run (universe-scan edition).

Workflow:
  1. Build today's universe (S&P 500 + top-50 crypto).
  2. Bulk-download price + volume history via yfinance.
  3. Apply liquidity / quality filters.
  4. Forecast every survivor with TimesFM (batched).
  5. Apply trading rules → BUY / SELL / HOLD.
  6. Log:
       * every BUY / SELL / HOLD event to the unified Trade Log DB
       * top-N ranked candidates to the Daily Scan DB
       * news for held positions only
       * a fresh Daily Status callout on the parent page
"""

from __future__ import annotations

import os
import sys
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import yfinance as yf
import pandas as pd

from timesfm_engine import TimesFMForecaster
from portfolio import Portfolio, TradingRules
from notion_client import NotionLogger
from news_client import NewsFetcher
from universe import (
    build_universe,
    passes_quality_filter,
    chunked,
    MIN_HISTORY_DAYS,
)

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

CONTEXT_DAYS    = 220   # how much price history to feed TimesFM (>= MIN_HISTORY_DAYS)
HORIZON_DAYS    = 5     # forecast 5 trading days ahead
STATE_FILE      = Path("state/portfolio.json")
INITIAL_CASH    = 250.0
SCAN_LOG_TOP_N  = 25    # how many top candidates to log to Daily Scan
FORECAST_BATCH  = 32    # batch size for TimesFM
DOWNLOAD_CHUNK  = 100   # tickers per yfinance call
CRYPTO_TOP_N    = 50

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("trader")


# --------------------------------------------------------------------------
# Bulk price download
# --------------------------------------------------------------------------

def bulk_download(tickers: list[str], days: int) -> dict[str, dict]:
    """
    Returns {ticker: {"closes": pd.Series, "volumes": pd.Series}}
    for every ticker that yfinance returned data for.
    Uses chunks to avoid rate limits and silent partial failures.
    """
    out: dict[str, dict] = {}
    period = f"{days + 60}d"  # buffer for non-trading days
    for chunk in chunked(tickers, DOWNLOAD_CHUNK):
        try:
            df = yf.download(
                tickers=chunk,
                period=period,
                interval="1d",
                progress=False,
                auto_adjust=True,
                group_by="ticker",
                threads=True,
            )
        except Exception as e:
            log.warning("Bulk download failed for chunk of %d: %s", len(chunk), e)
            continue
        if df is None or df.empty:
            continue
        is_multi = isinstance(df.columns, pd.MultiIndex)
        if not is_multi:
            # flat columns → only one ticker came back
            t = chunk[0] if len(chunk) == 1 else None
            if t is None:
                continue
            try:
                closes = df["Close"].dropna()
                vols   = df["Volume"].dropna() if "Volume" in df.columns else None
                if len(closes):
                    out[t] = {"closes": closes, "volumes": vols}
            except Exception:
                pass
            continue
        # MultiIndex (ticker, field)
        top_level = df.columns.get_level_values(0).unique()
        for t in chunk:
            if t not in top_level:
                continue
            try:
                sub = df[t]
            except Exception:
                continue
            if "Close" not in sub.columns:
                continue
            closes = sub["Close"].dropna()
            vols   = sub["Volume"].dropna() if "Volume" in sub.columns else None
            if len(closes) == 0:
                continue
            out[t] = {"closes": closes, "volumes": vols}
    return out


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main() -> int:
    log.info("=== TimesFM paper trader: universe-scan run ===")

    # ---- state
    portfolio = Portfolio.load(STATE_FILE, initial_cash=INITIAL_CASH)
    rules = TradingRules()
    log.info("Cash: $%.2f | Positions: %d | Total value: $%.2f",
             portfolio.cash, len(portfolio.holdings), portfolio.last_total_value)

    # ---- universe
    universe = build_universe(crypto_n=CRYPTO_TOP_N)

    # ensure any currently-held tickers are part of the scan, even if they
    # somehow dropped out of the index this week (rare but real)
    for t in portfolio.holdings:
        universe.setdefault(t, "stock" if not t.endswith("-USD") else "crypto")

    log.info("Universe size: %d", len(universe))

    # ---- bulk price fetch
    raw = bulk_download(list(universe.keys()), CONTEXT_DAYS)
    log.info("Downloaded prices for %d/%d tickers", len(raw), len(universe))

    # ---- quality filter
    candidates: dict[str, dict] = {}      # passes filter
    filtered:   dict[str, str]  = {}      # ticker -> filter reason
    for t, asset_class in universe.items():
        if t not in raw:
            filtered[t] = "no_price_data"
            continue
        ok, reason = passes_quality_filter(
            raw[t]["closes"], raw[t].get("volumes"), asset_class
        )
        if ok:
            candidates[t] = {
                "asset_class": asset_class,
                "closes":      raw[t]["closes"],
            }
        else:
            filtered[t] = reason
    log.info("Filtered: %d → %d passing candidates",
             len(filtered), len(candidates))

    # ---- TimesFM
    log.info("Loading TimesFM 2.5 (200M)...")
    forecaster = TimesFMForecaster(horizon=HORIZON_DAYS)

    forecasts: dict[str, dict] = {}
    series_cap = 256  # matches model max_context
    items = list(candidates.items())
    for batch in chunked(items, FORECAST_BATCH):
        tickers_b = [t for t, _ in batch]
        series_b  = [c["closes"].values[-series_cap:] for _, c in batch]
        try:
            results = forecaster.forecast_batch(series_b)
        except Exception as e:
            log.error("Forecast batch failed (%d tickers): %s", len(batch), e)
            continue
        for (t, c), (point, q10, q90) in zip(batch, results):
            current_price = float(c["closes"].iloc[-1])
            forecast_price = float(point[-1])
            expected_return = (forecast_price - current_price) / current_price
            forecasts[t] = {
                "asset_class":     c["asset_class"],
                "current_price":   current_price,
                "forecast_price":  forecast_price,
                "expected_return": expected_return,
                "q10_end":         float(q10[-1]),
                "q90_end":         float(q90[-1]),
            }
    log.info("Forecasted %d candidates", len(forecasts))

    # ---- notion
    notion = NotionLogger(
        token=os.environ["NOTION_TOKEN"],
        trade_log_id=os.environ["NOTION_TRADE_LOG_ID"],
        scan_log_id=os.environ["NOTION_SCAN_LOG_ID"],
        news_database_id=os.environ.get("NOTION_NEWS_DATABASE_ID"),
        parent_page_id=os.environ.get("NOTION_PARENT_PAGE_ID"),
    )

    asset_class = {t: f["asset_class"] for t, f in forecasts.items()}
    # add classes for holdings whose forecast may have failed
    for t in portfolio.holdings:
        asset_class.setdefault(t, "crypto" if t.endswith("-USD") else "stock")

    # 1) Exits
    sells = rules.evaluate_exits(portfolio, forecasts)
    sold = set()
    for t, reason in sells:
        price = forecasts[t]["current_price"]
        qty = portfolio.holdings[t]["quantity"]
        cost_basis = portfolio.holdings[t]["cost_basis"]
        pnl = price * qty - cost_basis
        portfolio.sell(t, qty, price)
        notion.log_trade(
            ticker=t, asset_class=asset_class[t],
            action="SELL", price=price, quantity=qty, reason=reason,
            forecast=forecasts[t], pnl=pnl, cash_after=portfolio.cash,
        )
        sold.add(t)
        log.info("SOLD %s × %.6f @ $%.2f (%s) | P&L: $%.2f",
                 t, qty, price, reason, pnl)

    # 2) Entries
    buys, skip_reasons = rules.evaluate_entries(portfolio, forecasts)
    bought = set()
    for t, allocation in buys:
        price = forecasts[t]["current_price"]
        qty = allocation / price
        portfolio.buy(t, qty, price)
        notion.log_trade(
            ticker=t, asset_class=asset_class[t],
            action="BUY", price=price, quantity=qty, reason="forecast_signal",
            forecast=forecasts[t], pnl=0.0, cash_after=portfolio.cash,
        )
        bought.add(t)
        log.info("BOUGHT %s × %.6f @ $%.2f (alloc $%.2f)",
                 t, qty, price, allocation)

    # 3) HOLD log for currently-held positions that didn't trade
    for t, position in portfolio.holdings.items():
        if t in bought or t in sold:
            continue
        if t in forecasts:
            f = forecasts[t]
            reason = rules.hold_reason(t, position, f)
            price = f["current_price"]
            pnl = portfolio.unrealized_pnl(t, price)
        else:
            f = {"forecast_price": 0.0, "expected_return": 0.0,
                 "q10_end": 0.0, "q90_end": 0.0, "current_price": 0.0}
            reason = filtered.get(t, "no_price_data")
            reason = f"hold_no_forecast({reason})"
            price = position["avg_price"]
            pnl = 0.0
        notion.log_trade(
            ticker=t, asset_class=asset_class[t],
            action="HOLD", price=price,
            quantity=position["quantity"],
            reason=reason, forecast=f, pnl=pnl,
            cash_after=portfolio.cash,
        )

    # 4) Daily Scan: top-N by expected_return
    ranked = sorted(forecasts.items(),
                    key=lambda kv: kv[1]["expected_return"],
                    reverse=True)[:SCAN_LOG_TOP_N]
    for rank, (t, f) in enumerate(ranked, start=1):
        if t in bought:
            status, reason = "BOUGHT", "executed BUY this run"
        elif t in portfolio.holdings:
            status, reason = "HELD", "already in portfolio"
        elif t in skip_reasons:
            r = skip_reasons[t]
            if r.startswith("no_cash"):
                status = "NO_CASH"
            elif r.startswith("no_slots"):
                status = "NO_SLOTS"
            elif r.startswith("cooldown"):
                status = "COOLDOWN"
            elif r.startswith("below_min_return") or r.startswith("uncertain_q10"):
                status = "FILTERED"
            else:
                status = "READY"
            reason = r
        elif f["expected_return"] >= rules.min_expected_return:
            status, reason = "READY", "passed thresholds, ranked too low for slots"
        else:
            status, reason = "FILTERED", (
                f"below_min_return({f['expected_return']*100:.2f}% "
                f"< {rules.min_expected_return*100:.1f}%)"
            )
        notion.log_scan(
            rank=rank, ticker=t, asset_class=asset_class[t],
            current_price=f["current_price"],
            forecast_price=f["forecast_price"],
            expected_return=f["expected_return"],
            q10=f["q10_end"], q90=f["q90_end"],
            status=status, reason=reason,
        )
    log.info("Logged %d rows to Daily Scan", len(ranked))

    # 5) Save state
    portfolio.mark_to_market({t: f["current_price"] for t, f in forecasts.items()})
    portfolio.save(STATE_FILE)

    # 6) News — only for held positions
    try:
        news_token = os.environ.get("FINNHUB_API_KEY")
        if news_token and notion.news_database_id and portfolio.holdings:
            held = list(portfolio.holdings.keys())
            stocks  = [t for t in held if asset_class.get(t) == "stock"]
            cryptos = [t for t in held if asset_class.get(t) == "crypto"]
            fetcher = NewsFetcher(api_key=news_token)
            items = fetcher.fetch_daily(stocks, cryptos, max_per_ticker=3)
            log.info("Fetched %d news items for %d held positions",
                     len(items), len(held))
            for item in items:
                notion.log_news(item)
        else:
            log.info("Skipping news fetch (no key, no news DB, or no positions)")
    except Exception as e:
        log.error("News fetch/log failed: %s", e)

    # 7) Status callout
    try:
        if notion.parent_page_id:
            notion.update_status_callout(
                cash=portfolio.cash,
                total_value=portfolio.last_total_value,
                holdings=portfolio.holdings,
                forecasts=forecasts,
                initial_cash=INITIAL_CASH,
                universe_size=len(universe),
                forecasted=len(forecasts),
                bought=len(bought),
                sold=len(sold),
            )
    except Exception as e:
        log.error("Status callout update failed: %s", e)

    log.info("=== Done. Total portfolio value: $%.2f ===", portfolio.last_total_value)
    return 0


if __name__ == "__main__":
    sys.exit(main())
