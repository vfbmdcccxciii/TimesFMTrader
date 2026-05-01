"""
One-time setup: creates a Notion database per ticker and a single
"Daily News" database, all as children of your chosen parent page.
Prints out the JSON map(s) you'll paste into GitHub secrets.

Usage:
  export NOTION_TOKEN=secret_xxx
  export NOTION_PARENT_PAGE_ID=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
  python bootstrap_notion.py

Re-running: this script is destructive in the sense that it creates new
databases each time. Only run it on a fresh parent page, or trash the
old ones first.
"""

import os
import json
import requests

NOTION_VERSION = "2022-06-28"

CANDIDATES = [
    ("AAPL",    "stock"),
    ("MSFT",    "stock"),
    ("NVDA",    "stock"),
    ("GOOGL",   "stock"),
    ("TSLA",    "stock"),
    ("BTC-USD", "crypto"),
    ("ETH-USD", "crypto"),
    ("SOL-USD", "crypto"),
]

EMOJI = {"stock": "📈", "crypto": "🪙"}


# --------------------------------------------------------------------------
# Per-ticker trade-log schema
# --------------------------------------------------------------------------
def trade_log_schema(ticker: str, asset_class: str) -> dict:
    return {
        "parent": {"type": "page_id", "page_id": os.environ["NOTION_PARENT_PAGE_ID"]},
        "icon":   {"type": "emoji", "emoji": EMOJI[asset_class]},
        "title": [{"type": "text", "text": {"content": f"{ticker} — Trade Log"}}],
        "properties": {
            "Title":       {"title": {}},
            "Date":        {"date": {}},
            "Action":      {"select": {"options": [
                {"name": "BUY",  "color": "green"},
                {"name": "SELL", "color": "red"},
                {"name": "HOLD", "color": "gray"},
            ]}},
            "Price":       {"number": {"format": "dollar"}},
            "Quantity":    {"number": {"format": "number"}},
            "Reason":      {"rich_text": {}},
            "Forecast 5d": {"number": {"format": "dollar"}},
            "Expected %":  {"number": {"format": "percent"}},
            "P&L":         {"number": {"format": "dollar"}},
            "Cash After":  {"number": {"format": "dollar"}},
            "Asset Class": {"select": {"options": [
                {"name": "stock",  "color": "blue"},
                {"name": "crypto", "color": "orange"},
            ]}},
        },
    }


# --------------------------------------------------------------------------
# Daily news schema (one DB for everything)
# --------------------------------------------------------------------------
def news_schema() -> dict:
    return {
        "parent": {"type": "page_id", "page_id": os.environ["NOTION_PARENT_PAGE_ID"]},
        "icon":   {"type": "emoji", "emoji": "📰"},
        "title": [{"type": "text", "text": {"content": "Daily News"}}],
        "properties": {
            "Title":    {"title": {}},
            "Date":     {"date": {}},
            "Ticker":   {"select": {"options": [
                {"name": "AAPL",    "color": "blue"},
                {"name": "MSFT",    "color": "blue"},
                {"name": "NVDA",    "color": "blue"},
                {"name": "GOOGL",   "color": "blue"},
                {"name": "TSLA",    "color": "blue"},
                {"name": "BTC-USD", "color": "orange"},
                {"name": "ETH-USD", "color": "orange"},
                {"name": "SOL-USD", "color": "orange"},
                {"name": "ALL",     "color": "gray"},
            ]}},
            "Category": {"select": {"options": [
                {"name": "stock",   "color": "blue"},
                {"name": "crypto",  "color": "orange"},
                {"name": "general", "color": "gray"},
            ]}},
            "Source":  {"rich_text": {}},
            "URL":     {"url": {}},
            "Summary": {"rich_text": {}},
        },
    }


def main():
    token = os.environ["NOTION_TOKEN"]
    headers = {
        "Authorization":  f"Bearer {token}",
        "Content-Type":   "application/json",
        "Notion-Version": NOTION_VERSION,
    }

    db_map = {}
    for ticker, asset_class in CANDIDATES:
        r = requests.post(
            "https://api.notion.com/v1/databases",
            headers=headers,
            json=trade_log_schema(ticker, asset_class),
            timeout=30,
        )
        r.raise_for_status()
        db_id = r.json()["id"]
        db_map[ticker] = db_id
        print(f"  ✓ {ticker:8s} -> {db_id}")

    print("\nDaily News DB ...")
    r = requests.post(
        "https://api.notion.com/v1/databases",
        headers=headers, json=news_schema(), timeout=30,
    )
    r.raise_for_status()
    news_id = r.json()["id"]
    print(f"  ✓ Daily News -> {news_id}")

    print("\n--- Paste these into GitHub Secrets ---\n")
    print("NOTION_DATABASE_MAP =")
    print(json.dumps(db_map))
    print("\nNOTION_NEWS_DATABASE_ID =")
    print(news_id)
    print("\nAlso (re)set:")
    print("  NOTION_PARENT_PAGE_ID  = (your parent page id, same as you exported)")
    print("  FINNHUB_API_KEY        = (free key from https://finnhub.io)")


if __name__ == "__main__":
    main()
