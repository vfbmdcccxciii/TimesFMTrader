"""
TimesFM Paper Trader — daily run.

Workflow:
  1. Load portfolio state (cash + holdings).
  2. Pull recent price history for each candidate asset.
  3. Run TimesFM forecast for each.
  4. Apply trading rules → decide BUY / SELL / HOLD.
  5. Execute paper trades, update state.
  6. Log each decision to its Notion database.
"""

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

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

CANDIDATES = {
    # ticker : asset_class
    "AAPL":    "stock",
    "MSFT":    "stock",
    "NVDA":    "stock",
    "GOOGL":   "stock",
    "TSLA":    "stock",
    "BTC-USD": "crypto",
    "ETH-USD": "crypto",
    "SOL-USD": "crypto",
}

CONTEXT_DAYS  = 200    # how much price history to feed TimesFM
HORIZON_DAYS  = 5      # forecast 5 trading days ahead
STATE_FILE    = Path("state/portfolio.json")
INITIAL_CASH  = 250.0  # USD

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("trader")

# --------------------------------------------------------------------------
# Price data
# --------------------------------------------------------------------------

def fetch_history(ticker: str, days: int) -> pd.Series:
    """Return a pandas Series of daily close prices, indexed by date."""
    # buffer for non-trading days
    df = yf.download(
        ticker,
        period=f"{days + 60}d",
        interval="1d",
        progress=False,
        auto_adjust=True,
    )
    if df.empty:
        raise RuntimeError(f"No price data for {ticker}")
    closes = df["Close"].dropna().tail(days)
    # yfinance sometimes returns a DataFrame even for single ticker
    if isinstance(closes, pd.DataFrame):
        closes = closes.iloc[:, 0]
    return closes


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main() -> int:
    log.info("=== TimesFM paper trader: daily run ===")

    # --- state
    portfolio = Portfolio.load(STATE_FILE, initial_cash=INITIAL_CASH)
    rules = TradingRules()
    log.info("Cash: $%.2f | Positions: %d | Total value (last known): $%.2f",
             portfolio.cash, len(portfolio.holdings), portfolio.last_total_value)

    # --- model
    log.info("Loading TimesFM 2.5 (200M)...")
    forecaster = TimesFMForecaster(horizon=HORIZON_DAYS)

    # --- notion
    notion = NotionLogger(
        token=os.environ["NOTION_TOKEN"],
        database_map=json.loads(os.environ["NOTION_DATABASE_MAP"]),
    )

    # --- decisions
    forecasts: dict[str, dict] = {}
    for ticker in CANDIDATES:
        try:
            history = fetch_history(ticker, CONTEXT_DAYS)
            point, q10, q90 = forecaster.forecast(history.values)
            current_price = float(history.iloc[-1])
            forecast_price = float(point[-1])  # end of horizon
            expected_return = (forecast_price - current_price) / current_price
            forecasts[ticker] = {
                "current_price":   current_price,
                "forecast_price":  forecast_price,
                "expected_return": expected_return,
                "q10_end":         float(q10[-1]),
                "q90_end":         float(q90[-1]),
            }
            log.info("%s: $%.2f → $%.2f (%.2f%%)  [day1=$%.2f q10=$%.2f q90=$%.2f n=%d]",
                     ticker, current_price, forecast_price, expected_return * 100,
                     float(point[0]), float(q10[-1]), float(q90[-1]), len(point))
        except Exception as e:
            log.error("Forecast failed for %s: %s", ticker, e)
            continue

    # 1) check exits first (stop-loss / take-profit / forecast reversal)
    sells = rules.evaluate_exits(portfolio, forecasts)
    for ticker, reason in sells:
        price = forecasts[ticker]["current_price"]
        qty   = portfolio.holdings[ticker]["quantity"]
        proceeds = price * qty
        cost_basis = portfolio.holdings[ticker]["cost_basis"]
        pnl = proceeds - cost_basis
        portfolio.sell(ticker, qty, price)
        notion.log_trade(
            ticker=ticker,
            asset_class=CANDIDATES[ticker],
            action="SELL",
            price=price,
            quantity=qty,
            reason=reason,
            forecast=forecasts[ticker],
            pnl=pnl,
            cash_after=portfolio.cash,
        )
        log.info("SOLD %s × %.6f @ $%.2f (%s) | P&L: $%.2f",
                 ticker, qty, price, reason, pnl)

    # 2) then evaluate entries with whatever cash is available
    buys = rules.evaluate_entries(portfolio, forecasts)
    for ticker, allocation in buys:
        price = forecasts[ticker]["current_price"]
        qty = allocation / price
        portfolio.buy(ticker, qty, price)
        notion.log_trade(
            ticker=ticker,
            asset_class=CANDIDATES[ticker],
            action="BUY",
            price=price,
            quantity=qty,
            reason="forecast_signal",
            forecast=forecasts[ticker],
            pnl=0.0,
            cash_after=portfolio.cash,
        )
        log.info("BOUGHT %s × %.6f @ $%.2f (alloc $%.2f)",
                 ticker, qty, price, allocation)

    # 3) HOLD entries — log status for everything we own and didn't trade
    traded = {t for t, _ in sells} | {t for t, _ in buys}
    for ticker in portfolio.holdings:
        if ticker in traded or ticker not in forecasts:
            continue
        notion.log_trade(
            ticker=ticker,
            asset_class=CANDIDATES[ticker],
            action="HOLD",
            price=forecasts[ticker]["current_price"],
            quantity=portfolio.holdings[ticker]["quantity"],
            reason="no_signal",
            forecast=forecasts[ticker],
            pnl=portfolio.unrealized_pnl(ticker, forecasts[ticker]["current_price"]),
            cash_after=portfolio.cash,
        )

    # 4) update mark-to-market and save
    portfolio.mark_to_market({t: f["current_price"] for t, f in forecasts.items()})
    portfolio.save(STATE_FILE)

    log.info("=== Done. Total portfolio value: $%.2f ===", portfolio.last_total_value)
    return 0


if __name__ == "__main__":
    sys.exit(main())
