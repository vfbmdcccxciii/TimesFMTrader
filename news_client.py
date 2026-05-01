"""
Daily news fetcher.

Pulls company news from Finnhub for stocks and general crypto news for the
crypto universe. Free Finnhub plan: 60 calls/min — well within budget.

Set the FINNHUB_API_KEY env var (also exposed as a GitHub Actions secret).
Get a free key at https://finnhub.io/dashboard.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

import requests

log = logging.getLogger(__name__)

FINNHUB_BASE = "https://finnhub.io/api/v1"


class NewsFetcher:
    def __init__(self, api_key: str, lookback_hours: int = 24):
        self.api_key = api_key
        self.lookback_hours = lookback_hours

    # ---------- public ----------
    def fetch_daily(
        self,
        stock_tickers: list[str],
        crypto_tickers: list[str],
        max_per_ticker: int = 3,
    ) -> list[dict]:
        """Return a flat list of normalized news items.

        Each item dict has:
            ticker, category (stock/crypto/general), title, source, url,
            summary, published_at (ISO-8601 UTC string).
        """
        items: list[dict] = []

        # ----- stocks: company-news endpoint -----
        today = datetime.now(timezone.utc).date()
        since = today - timedelta(days=2)  # company-news endpoint takes a date range
        for t in stock_tickers:
            try:
                rows = self._company_news(t, since.isoformat(), today.isoformat())
                for row in rows[:max_per_ticker]:
                    items.append(self._normalize(row, ticker=t, category="stock"))
            except Exception as e:
                log.warning("Finnhub company news failed for %s: %s", t, e)
            time.sleep(0.2)  # be gentle on rate limit

        # ----- crypto: general crypto category news, then attribute by symbol -----
        try:
            crypto_rows = self._market_news(category="crypto")
            symbol_to_ticker = self._crypto_symbol_map(crypto_tickers)
            counts: dict[str, int] = {t: 0 for t in crypto_tickers}
            for row in crypto_rows:
                hit = self._match_crypto(row, symbol_to_ticker)
                if not hit:
                    continue
                if counts[hit] >= max_per_ticker:
                    continue
                items.append(self._normalize(row, ticker=hit, category="crypto"))
                counts[hit] += 1
                if all(v >= max_per_ticker for v in counts.values()):
                    break
        except Exception as e:
            log.warning("Finnhub crypto news failed: %s", e)

        # ----- one general market headline so the day always has something -----
        try:
            general = self._market_news(category="general")
            if general:
                items.append(self._normalize(general[0], ticker="ALL", category="general"))
        except Exception as e:
            log.warning("Finnhub general news failed: %s", e)

        # filter to last `lookback_hours`
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.lookback_hours)
        items = [
            i for i in items
            if datetime.fromisoformat(i["published_at"]) >= cutoff
        ]
        return items

    # ---------- internals ----------
    def _company_news(self, ticker: str, since: str, until: str) -> list[dict]:
        r = requests.get(
            f"{FINNHUB_BASE}/company-news",
            params={"symbol": ticker, "from": since, "to": until,
                    "token": self.api_key},
            timeout=15,
        )
        r.raise_for_status()
        return r.json() or []

    def _market_news(self, category: str) -> list[dict]:
        r = requests.get(
            f"{FINNHUB_BASE}/news",
            params={"category": category, "token": self.api_key},
            timeout=15,
        )
        r.raise_for_status()
        return r.json() or []

    @staticmethod
    def _crypto_symbol_map(crypto_tickers: list[str]) -> dict[str, str]:
        # BTC-USD -> {"BTC": "BTC-USD", "BITCOIN": "BTC-USD"}
        names = {
            "BTC": ["BTC", "BITCOIN"],
            "ETH": ["ETH", "ETHEREUM", "ETHER"],
            "SOL": ["SOL", "SOLANA"],
        }
        out: dict[str, str] = {}
        for t in crypto_tickers:
            base = t.split("-")[0]
            for kw in names.get(base, [base]):
                out[kw.upper()] = t
        return out

    @staticmethod
    def _match_crypto(row: dict, symbol_map: dict[str, str]) -> str | None:
        text = f"{row.get('headline', '')} {row.get('summary', '')}".upper()
        for kw, ticker in symbol_map.items():
            # word-ish boundary match
            if f" {kw} " in f" {text} " or f" {kw}." in text or f" {kw}," in text:
                return ticker
        return None

    @staticmethod
    def _normalize(row: dict, *, ticker: str, category: str) -> dict:
        ts = row.get("datetime", 0)
        if ts:
            published = datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
        else:
            published = datetime.now(timezone.utc).isoformat()
        title = (row.get("headline") or "").strip()[:1900]
        summary = (row.get("summary") or "").strip()[:1900]
        return {
            "ticker":       ticker,
            "category":     category,
            "title":        title or "(no title)",
            "source":       (row.get("source") or "Finnhub")[:200],
            "url":          (row.get("url") or "").strip(),
            "summary":      summary,
            "published_at": published,
        }
