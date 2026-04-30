"""
Notion logger.

Writes one page per trade decision into the per-asset database.
Database IDs are passed in via env var NOTION_DATABASE_MAP (JSON), e.g.

  {
    "AAPL":    "32-char-database-id",
    "MSFT":    "...",
    "BTC-USD": "...",
    ...
  }

Each database is expected to have these properties (created once via the
bootstrap_notion.py helper):

  Title        (title)         — e.g. "BUY 0.123 @ $185.20"
  Date         (date)          — when the decision was made
  Action       (select)        — BUY / SELL / HOLD
  Price        (number)        — price at decision time
  Quantity     (number)        — units traded (or held)
  Reason       (rich_text)     — stop_loss / take_profit / forecast_signal / no_signal / forecast_reversal
  Forecast 5d  (number)        — forecasted price end of horizon
  Expected %   (number)        — expected return over horizon
  P&L          (number)        — realized (SELL) or unrealized (HOLD) USD
  Cash After   (number)        — cash remaining after the trade
  Asset Class  (select)        — stock / crypto
"""

import logging
from datetime import datetime, timezone
import requests

log = logging.getLogger(__name__)

NOTION_VERSION = "2022-06-28"


class NotionLogger:
    def __init__(self, token: str, database_map: dict[str, str]):
        self.headers = {
            "Authorization":  f"Bearer {token}",
            "Content-Type":   "application/json",
            "Notion-Version": NOTION_VERSION,
        }
        self.database_map = database_map

    def log_trade(self, *, ticker, asset_class, action, price, quantity,
                  reason, forecast, pnl, cash_after):
        if ticker not in self.database_map:
            log.warning("No Notion DB mapped for %s — skipping", ticker)
            return

        db_id = self.database_map[ticker]
        title = f"{action} {quantity:.4f} @ ${price:.2f}"

        properties = {
            "Title": {
                "title": [{"text": {"content": title}}]
            },
            "Date": {
                "date": {"start": datetime.now(timezone.utc).isoformat()}
            },
            "Action": {
                "select": {"name": action}
            },
            "Price":       {"number": round(price, 4)},
            "Quantity":    {"number": round(quantity, 8)},
            "Reason":      {"rich_text": [{"text": {"content": reason}}]},
            "Forecast 5d": {"number": round(forecast["forecast_price"], 4)},
            "Expected %":  {"number": round(forecast["expected_return"] * 100, 3)},
            "P&L":         {"number": round(pnl, 4)},
            "Cash After":  {"number": round(cash_after, 2)},
            "Asset Class": {"select": {"name": asset_class}},
        }

        payload = {"parent": {"database_id": db_id}, "properties": properties}
        r = requests.post(
            "https://api.notion.com/v1/pages",
            headers=self.headers, json=payload, timeout=30,
        )
        if r.status_code >= 300:
            log.error("Notion write failed for %s: %s %s",
                      ticker, r.status_code, r.text[:300])
        else:
            log.debug("Logged %s for %s", action, ticker)
