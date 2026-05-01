"""
Notion logger.

Three things live here:

1. log_trade(...)        → one page per ticker per run in the per-ticker DBs.
2. log_news(item)        → one page per news item in the daily news DB.
3. update_status_callout → rewrites the "Daily Status" callout block at the
                            top of the parent page each run.

NOTION_DATABASE_MAP (env var, JSON):
  {"AAPL": "32-char-db-id", "MSFT": "...", ...}

NOTION_NEWS_DATABASE_ID (env var): id of the single news DB on the parent.
NOTION_PARENT_PAGE_ID    (env var): id of the parent page (for status callout).

Each per-ticker DB is expected to have these properties (created via
bootstrap_notion.py):

  Title        (title)         e.g. "BUY 0.123 @ $185.20"
  Date         (date)          when the decision was made
  Action       (select)        BUY / SELL / HOLD
  Price        (number)        price at decision time
  Quantity     (number)        units traded (or held)
  Reason       (rich_text)     stop_loss / take_profit / forecast_signal /
                                no_signal / forecast_reversal /
                                below_min_return / uncertain_q10 / no_cash /
                                no_slots / cooldown_active / fetch_error
  Forecast 5d  (number)        forecasted price end of horizon
  Expected %   (number)        expected return over horizon
  P&L          (number)        realized (SELL) or unrealized (HOLD) USD
  Cash After   (number)        cash remaining after the trade
  Asset Class  (select)        stock / crypto

The news DB has these properties:

  Title        (title)         the headline
  Date         (date)          when published
  Ticker       (select)        AAPL / MSFT / ... / ALL
  Category     (select)        stock / crypto / general
  Source       (rich_text)     publisher
  URL          (url)           link to the article
  Summary      (rich_text)     short summary
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import requests

log = logging.getLogger(__name__)

NOTION_VERSION = "2022-06-28"
NOTION_API     = "https://api.notion.com/v1"

# A single banner image hot-linked from Unsplash. Stable URL, public, free.
DEFAULT_BANNER = (
    "https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3"
    "?auto=format&fit=crop&w=1500&q=80"
)


class NotionLogger:
    def __init__(
        self,
        token: str,
        database_map: dict[str, str],
        news_database_id: str | None = None,
        parent_page_id:   str | None = None,
    ):
        self.headers = {
            "Authorization":  f"Bearer {token}",
            "Content-Type":   "application/json",
            "Notion-Version": NOTION_VERSION,
        }
        self.database_map     = database_map
        self.news_database_id = news_database_id
        self.parent_page_id   = parent_page_id

    # ------------------------------------------------------------------
    # Trade log
    # ------------------------------------------------------------------
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
            "Reason":      {"rich_text": [{"text": {"content": reason[:1900]}}]},
            "Forecast 5d": {"number": round(forecast.get("forecast_price", 0.0), 4)},
            # Expected % is stored as a fraction (Notion percent format expects 0.05 = 5%)
            "Expected %":  {"number": round(forecast.get("expected_return", 0.0), 6)},
            "P&L":         {"number": round(pnl, 4)},
            "Cash After":  {"number": round(cash_after, 2)},
            "Asset Class": {"select": {"name": asset_class}},
        }

        payload = {"parent": {"database_id": db_id}, "properties": properties}
        r = requests.post(f"{NOTION_API}/pages",
                          headers=self.headers, json=payload, timeout=30)
        if r.status_code >= 300:
            log.error("Notion write failed for %s: %s %s",
                      ticker, r.status_code, r.text[:300])
        else:
            log.debug("Logged %s for %s", action, ticker)

    # ------------------------------------------------------------------
    # News log
    # ------------------------------------------------------------------
    def log_news(self, item: dict) -> None:
        if not self.news_database_id:
            return

        properties = {
            "Title":    {"title": [{"text": {"content": item["title"][:1900]}}]},
            "Date":     {"date": {"start": item["published_at"]}},
            "Ticker":   {"select": {"name": item["ticker"]}},
            "Category": {"select": {"name": item["category"]}},
            "Source":   {"rich_text": [{"text": {"content": item["source"][:200]}}]},
            "Summary":  {"rich_text": [{"text": {"content": item["summary"][:1900]}}]},
        }
        if item.get("url"):
            properties["URL"] = {"url": item["url"]}

        payload = {
            "parent":     {"database_id": self.news_database_id},
            "properties": properties,
        }
        r = requests.post(f"{NOTION_API}/pages",
                          headers=self.headers, json=payload, timeout=30)
        if r.status_code >= 300:
            log.error("Notion news write failed (%s): %s %s",
                      item["ticker"], r.status_code, r.text[:300])

    # ------------------------------------------------------------------
    # Daily Status callout on parent page
    # ------------------------------------------------------------------
    def update_status_callout(
        self,
        *,
        cash: float,
        total_value: float,
        holdings: dict,
        forecasts: dict,
        initial_cash: float,
    ) -> None:
        """Maintain a single callout block at the top of the parent page.

        Strategy: list direct children of the page, archive any block whose
        plain text starts with the marker '⚖️ Daily Status', then append a
        fresh callout. This is idempotent across runs.
        """
        if not self.parent_page_id:
            return

        # ensure the page has a banner cover (set once; updates are no-op if same)
        try:
            requests.patch(
                f"{NOTION_API}/pages/{self.parent_page_id}",
                headers=self.headers,
                json={"cover": {"type": "external",
                                "external": {"url": DEFAULT_BANNER}}},
                timeout=20,
            )
        except Exception as e:
            log.warning("Could not set page cover: %s", e)

        # archive the previous status callout (if any)
        try:
            self._archive_old_status_blocks(self.parent_page_id)
        except Exception as e:
            log.warning("Could not archive old status block: %s", e)

        # build the new callout
        invested = sum(
            forecasts.get(t, {}).get("current_price", 0.0)
            * holdings[t]["quantity"]
            for t in holdings
        )
        total_pnl     = total_value - initial_cash
        pnl_pct       = (total_pnl / initial_cash * 100.0) if initial_cash else 0.0
        cash_pct      = (cash / total_value * 100.0) if total_value else 0.0
        positions_txt = self._format_positions(holdings, forecasts)

        emoji = "📈" if total_pnl >= 0 else "📉"
        sign  = "+" if total_pnl >= 0 else ""
        ts    = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        rich_text = [
            {"type": "text",
             "text": {"content": "⚖️ Daily Status — "},
             "annotations": {"bold": True}},
            {"type": "text", "text": {"content": f"{ts}\n\n"}},
            {"type": "text",
             "text": {"content": f"💰 Cash: ${cash:,.2f} ({cash_pct:.1f}% of portfolio)\n"}},
            {"type": "text",
             "text": {"content": f"📊 Invested: ${invested:,.2f}\n"}},
            {"type": "text",
             "text": {"content": f"💼 Total Value: ${total_value:,.2f}\n"}},
            {"type": "text",
             "text": {"content": f"{emoji} P&L vs ${initial_cash:.0f} start: "
                                  f"{sign}${total_pnl:,.2f} ({sign}{pnl_pct:.2f}%)\n\n"},
             "annotations": {"bold": True,
                             "color": "green" if total_pnl >= 0 else "red"}},
            {"type": "text",
             "text": {"content": "Positions:\n"},
             "annotations": {"bold": True}},
            {"type": "text", "text": {"content": positions_txt}},
        ]

        callout = {
            "object": "block",
            "type":   "callout",
            "callout": {
                "rich_text": rich_text,
                "icon":      {"type": "emoji", "emoji": "⚖️"},
                "color":     "gray_background",
            },
        }

        # Append the new callout to the parent page.
        try:
            r = requests.patch(
                f"{NOTION_API}/blocks/{self.parent_page_id}/children",
                headers=self.headers,
                json={"children": [callout]},
                timeout=20,
            )
            if r.status_code >= 300:
                log.error("Failed to append status callout: %s %s",
                          r.status_code, r.text[:300])
        except Exception as e:
            log.error("Status callout append failed: %s", e)

    # ---------- helpers ----------
    def _archive_old_status_blocks(self, page_id: str) -> None:
        """Archive any block on the page whose first text starts with '⚖️ Daily Status'."""
        cursor = None
        while True:
            params = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            r = requests.get(
                f"{NOTION_API}/blocks/{page_id}/children",
                headers=self.headers, params=params, timeout=20,
            )
            r.raise_for_status()
            data = r.json()
            for block in data.get("results", []):
                if block.get("type") != "callout":
                    continue
                rich = block["callout"].get("rich_text", [])
                first = rich[0]["plain_text"] if rich else ""
                if first.startswith("⚖️ Daily Status"):
                    requests.patch(
                        f"{NOTION_API}/blocks/{block['id']}",
                        headers=self.headers,
                        json={"archived": True},
                        timeout=15,
                    )
            if data.get("has_more"):
                cursor = data.get("next_cursor")
            else:
                break

    @staticmethod
    def _format_positions(holdings: dict, forecasts: dict) -> str:
        if not holdings:
            return "(none — fully in cash)"
        lines = []
        for t, p in holdings.items():
            cur = forecasts.get(t, {}).get("current_price", p["avg_price"])
            value = cur * p["quantity"]
            ret_pct = ((cur - p["avg_price"]) / p["avg_price"] * 100.0
                       if p["avg_price"] else 0.0)
            sign = "+" if ret_pct >= 0 else ""
            lines.append(
                f"  • {t}: {p['quantity']:.6f} @ avg ${p['avg_price']:.2f} "
                f"→ ${value:,.2f} ({sign}{ret_pct:.2f}%)"
            )
        return "\n".join(lines)
