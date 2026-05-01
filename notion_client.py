"""
Notion logger (universe-scan edition).

Endpoints used:
  * log_trade(...)         → unified Trade Log DB (BUY / SELL / HOLD events)
  * log_scan(...)           → Daily Scan DB (top-N ranked candidates)
  * log_news(item)         → Daily News DB
  * update_status_callout  → callout block at the top of the parent page

Required env vars (passed in via the constructor):
  NOTION_TOKEN
  NOTION_TRADE_LOG_ID
  NOTION_SCAN_LOG_ID
  NOTION_NEWS_DATABASE_ID  (optional)
  NOTION_PARENT_PAGE_ID    (optional)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import requests

log = logging.getLogger(__name__)

NOTION_VERSION = "2022-06-28"
NOTION_API     = "https://api.notion.com/v1"

# Stable, public, free banner image.
DEFAULT_BANNER = (
    "https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3"
    "?auto=format&fit=crop&w=1500&q=80"
)


class NotionLogger:
    def __init__(
        self,
        *,
        token: str,
        trade_log_id: str,
        scan_log_id: str,
        news_database_id: str | None = None,
        parent_page_id:   str | None = None,
    ):
        self.headers = {
            "Authorization":  f"Bearer {token}",
            "Content-Type":   "application/json",
            "Notion-Version": NOTION_VERSION,
        }
        self.trade_log_id     = trade_log_id
        self.scan_log_id      = scan_log_id
        self.news_database_id = news_database_id
        self.parent_page_id   = parent_page_id

    # ------------------------------------------------------------------
    # Trade Log
    # ------------------------------------------------------------------
    def log_trade(self, *, ticker, asset_class, action, price, quantity,
                  reason, forecast, pnl, cash_after):
        title = f"{action} {ticker} {quantity:.4f} @ ${price:.2f}"
        properties = {
            "Title":    {"title": [{"text": {"content": title[:1900]}}]},
            "Date":     {"date": {"start": datetime.now(timezone.utc).isoformat()}},
            "Ticker":   {"rich_text": [{"text": {"content": ticker}}]},
            "Asset Class": {"select": {"name": asset_class}},
            "Action":   {"select": {"name": action}},
            "Price":    {"number": round(price, 4)},
            "Quantity": {"number": round(quantity, 8)},
            "Reason":   {"rich_text": [{"text": {"content": str(reason)[:1900]}}]},
            "Forecast 5d": {"number": round(forecast.get("forecast_price", 0.0), 4)},
            "Expected %":  {"number": round(forecast.get("expected_return", 0.0), 6)},
            "P&L":         {"number": round(pnl, 4)},
            "Cash After":  {"number": round(cash_after, 2)},
        }
        self._create_page(self.trade_log_id, properties, what=f"trade {ticker}")

    # ------------------------------------------------------------------
    # Daily Scan
    # ------------------------------------------------------------------
    def log_scan(self, *, rank, ticker, asset_class, current_price,
                 forecast_price, expected_return, q10, q90, status, reason):
        title = f"#{rank:02d} {ticker} {expected_return*100:+.2f}% [{status}]"
        properties = {
            "Title":    {"title": [{"text": {"content": title[:1900]}}]},
            "Date":     {"date": {"start": datetime.now(timezone.utc).isoformat()}},
            "Rank":     {"number": rank},
            "Ticker":   {"rich_text": [{"text": {"content": ticker}}]},
            "Asset Class": {"select": {"name": asset_class}},
            "Status":   {"select": {"name": status}},
            "Current Price": {"number": round(current_price, 4)},
            "Forecast 5d":   {"number": round(forecast_price, 4)},
            "Expected %":    {"number": round(expected_return, 6)},
            "Q10":      {"number": round(q10, 4)},
            "Q90":      {"number": round(q90, 4)},
            "Reason":   {"rich_text": [{"text": {"content": str(reason)[:1900]}}]},
        }
        self._create_page(self.scan_log_id, properties, what=f"scan {ticker}")

    # ------------------------------------------------------------------
    # News Log
    # ------------------------------------------------------------------
    def log_news(self, item: dict) -> None:
        if not self.news_database_id:
            return
        properties = {
            "Title":    {"title": [{"text": {"content": item["title"][:1900]}}]},
            "Date":     {"date": {"start": item["published_at"]}},
            "Ticker":   {"rich_text": [{"text": {"content": item["ticker"]}}]},
            "Category": {"select": {"name": item["category"]}},
            "Source":   {"rich_text": [{"text": {"content": item["source"][:200]}}]},
            "Summary":  {"rich_text": [{"text": {"content": item["summary"][:1900]}}]},
        }
        if item.get("url"):
            properties["URL"] = {"url": item["url"]}
        self._create_page(self.news_database_id, properties,
                          what=f"news {item['ticker']}")

    # ------------------------------------------------------------------
    # Daily Status callout
    # ------------------------------------------------------------------
    def update_status_callout(
        self, *,
        cash: float,
        total_value: float,
        holdings: dict,
        forecasts: dict,
        initial_cash: float,
        universe_size: int,
        forecasted: int,
        bought: int,
        sold: int,
    ) -> None:
        if not self.parent_page_id:
            return

        # Ensure cover; harmless if already set
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

        # Archive previous status callouts
        try:
            self._archive_old_status_blocks(self.parent_page_id)
        except Exception as e:
            log.warning("Could not archive old status block: %s", e)

        invested = sum(
            forecasts.get(t, {}).get("current_price", 0.0)
            * holdings[t]["quantity"]
            for t in holdings
        )
        total_pnl = total_value - initial_cash
        pnl_pct   = (total_pnl / initial_cash * 100.0) if initial_cash else 0.0
        cash_pct  = (cash / total_value * 100.0) if total_value else 0.0
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
             "text": {"content": "Today's scan: "},
             "annotations": {"bold": True}},
            {"type": "text",
             "text": {"content": f"{forecasted} of {universe_size} candidates forecasted, "
                                 f"{bought} bought, {sold} sold.\n\n"}},

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

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _create_page(self, db_id: str, properties: dict, *, what: str = ""):
        payload = {"parent": {"database_id": db_id}, "properties": properties}
        try:
            r = requests.post(f"{NOTION_API}/pages",
                              headers=self.headers, json=payload, timeout=30)
            if r.status_code >= 300:
                log.error("Notion write failed (%s): %s %s",
                          what, r.status_code, r.text[:300])
        except Exception as e:
            log.error("Notion write exception (%s): %s", what, e)

    def _archive_old_status_blocks(self, page_id: str) -> None:
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
