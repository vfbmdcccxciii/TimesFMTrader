"""
One-time setup for the universe-scan edition.

Creates three databases as children of your parent page:

  * Trade Log    — every BUY / SELL / HOLD event, filterable by Ticker
  * Daily Scan   — top-N ranked candidates per day
  * Daily News   — Finnhub headlines for held positions

Then prints the IDs to paste into GitHub Secrets.

Usage:
  export NOTION_TOKEN=secret_xxx
  export NOTION_PARENT_PAGE_ID=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
  python bootstrap_notion.py
"""

import os
import requests

NOTION_VERSION = "2022-06-28"


def _post_db(headers: dict, schema: dict) -> str:
    r = requests.post(
        "https://api.notion.com/v1/databases",
        headers=headers, json=schema, timeout=30,
    )
    r.raise_for_status()
    return r.json()["id"]


def trade_log_schema() -> dict:
    return {
        "parent": {"type": "page_id", "page_id": os.environ["NOTION_PARENT_PAGE_ID"]},
        "icon":   {"type": "emoji", "emoji": "📒"},
        "title":  [{"type": "text", "text": {"content": "Trade Log"}}],
        "properties": {
            "Title":       {"title": {}},
            "Date":        {"date": {}},
            "Ticker":      {"rich_text": {}},
            "Asset Class": {"select": {"options": [
                {"name": "stock",  "color": "blue"},
                {"name": "crypto", "color": "orange"},
            ]}},
            "Action":      {"select": {"options": [
                {"name": "BUY",  "color": "green"},
                {"name": "SELL", "color": "red"},
                {"name": "HOLD", "color": "gray"},
            ]}},
            "Price":       {"number": {"format": "dollar"}},
            "Quantity":    {"number": {"format": "number"}},
            "Forecast 5d": {"number": {"format": "dollar"}},
            "Expected %":  {"number": {"format": "percent"}},
            "P&L":         {"number": {"format": "dollar"}},
            "Cash After":  {"number": {"format": "dollar"}},
            "Reason":      {"rich_text": {}},
        },
    }


def scan_log_schema() -> dict:
    return {
        "parent": {"type": "page_id", "page_id": os.environ["NOTION_PARENT_PAGE_ID"]},
        "icon":   {"type": "emoji", "emoji": "🔭"},
        "title":  [{"type": "text", "text": {"content": "Daily Scan"}}],
        "properties": {
            "Title":   {"title": {}},
            "Date":    {"date": {}},
            "Rank":    {"number": {"format": "number"}},
            "Ticker":  {"rich_text": {}},
            "Asset Class": {"select": {"options": [
                {"name": "stock",  "color": "blue"},
                {"name": "crypto", "color": "orange"},
            ]}},
            "Status":  {"select": {"options": [
                {"name": "BOUGHT",   "color": "green"},
                {"name": "READY",    "color": "blue"},
                {"name": "NO_CASH",  "color": "yellow"},
                {"name": "NO_SLOTS", "color": "orange"},
                {"name": "COOLDOWN", "color": "purple"},
                {"name": "FILTERED", "color": "gray"},
                {"name": "HELD",     "color": "default"},
            ]}},
            "Current Price": {"number": {"format": "dollar"}},
            "Forecast 5d":   {"number": {"format": "dollar"}},
            "Expected %":    {"number": {"format": "percent"}},
            "Q10":     {"number": {"format": "dollar"}},
            "Q90":     {"number": {"format": "dollar"}},
            "Reason":  {"rich_text": {}},
        },
    }


def news_schema() -> dict:
    return {
        "parent": {"type": "page_id", "page_id": os.environ["NOTION_PARENT_PAGE_ID"]},
        "icon":   {"type": "emoji", "emoji": "📰"},
        "title":  [{"type": "text", "text": {"content": "Daily News"}}],
        "properties": {
            "Title":    {"title": {}},
            "Date":     {"date": {}},
            "Ticker":   {"rich_text": {}},
            "Category": {"select": {"options": [
                {"name": "stock",   "color": "blue"},
                {"name": "crypto",  "color": "orange"},
                {"name": "general", "color": "gray"},
            ]}},
            "Source":   {"rich_text": {}},
            "URL":      {"url": {}},
            "Summary":  {"rich_text": {}},
        },
    }


def main():
    token = os.environ["NOTION_TOKEN"]
    headers = {
        "Authorization":  f"Bearer {token}",
        "Content-Type":   "application/json",
        "Notion-Version": NOTION_VERSION,
    }

    print("Creating Trade Log ...")
    trade_id = _post_db(headers, trade_log_schema())
    print(f"  ✓ {trade_id}")

    print("Creating Daily Scan ...")
    scan_id = _post_db(headers, scan_log_schema())
    print(f"  ✓ {scan_id}")

    print("Creating Daily News ...")
    news_id = _post_db(headers, news_schema())
    print(f"  ✓ {news_id}")

    print("\n--- Paste these into GitHub Secrets ---\n")
    print(f"NOTION_TRADE_LOG_ID     = {trade_id}")
    print(f"NOTION_SCAN_LOG_ID      = {scan_id}")
    print(f"NOTION_NEWS_DATABASE_ID = {news_id}")
    print(f"NOTION_PARENT_PAGE_ID   = {os.environ['NOTION_PARENT_PAGE_ID']}")
    print("FINNHUB_API_KEY         = (free key from https://finnhub.io)\n")
    print("Note: NOTION_DATABASE_MAP from the previous edition is no longer used.")


if __name__ == "__main__":
    main()
