"""
Daily-refreshed trading universe.

Sources
-------
* Stocks  : S&P 500 constituents (Wikipedia table — well-maintained, free).
* Crypto  : Top ~50 by market cap from CoinGecko's free public API.

Filters
-------
After bulk-downloading prices (in trader.py), each candidate must pass:

  * ≥ 200 days of clean (non-NaN) close history
  * Most-recent close ≥ MIN_PRICE
  * 30-day average dollar volume ≥ MIN_DOLLAR_VOL
  * (crypto only) base symbol not on the stablecoin blocklist

Returned tickers use yfinance's expected format: dotted symbols like
`BRK.B` are translated to `BRK-B`, and crypto is `BTC-USD` style.
"""

from __future__ import annotations

import logging
import time
from typing import Iterable

import requests
import pandas as pd

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Tunables
# --------------------------------------------------------------------------

MIN_HISTORY_DAYS  = 200
MIN_PRICE_STOCK   = 5.0
MIN_PRICE_CRYPTO  = 0.05
MIN_DOLLAR_VOL_STOCK  = 5_000_000.0   # $5M ADV
MIN_DOLLAR_VOL_CRYPTO = 50_000_000.0  # $50M daily volume

STABLECOIN_BLOCKLIST = {
    "USDT", "USDC", "DAI", "BUSD", "TUSD", "USDP", "GUSD", "FRAX",
    "USDD", "EURT", "EUROC", "PYUSD", "FDUSD", "USDE", "PAX",
    "LUSD", "MIM", "USTC", "SUSD", "RSR",  # close-enough stables
    "WBTC", "WETH", "STETH", "WSTETH", "RETH", "CBETH",  # wrapped tokens
}

WIKI_SP500 = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
COINGECKO_MARKETS = "https://api.coingecko.com/api/v3/coins/markets"

# Browsers-look-alike header so Wikipedia doesn't block us.
_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; TimesFM-Trader/1.0; "
        "+https://github.com/google-research/timesfm)"
    )
}


# --------------------------------------------------------------------------
# Stocks: S&P 500 from Wikipedia
# --------------------------------------------------------------------------

def fetch_sp500_tickers() -> list[str]:
    """Return current S&P 500 tickers in yfinance format (e.g. 'BRK-B')."""
    try:
        # pandas.read_html does the heavy lifting. The first table on the page
        # is the constituents list with a 'Symbol' column.
        tables = pd.read_html(WIKI_SP500, storage_options=_HTTP_HEADERS)
    except Exception as e:
        log.error("Failed to read S&P 500 from Wikipedia: %s", e)
        return []
    constituents = tables[0]
    if "Symbol" not in constituents.columns:
        log.error("Unexpected S&P 500 table schema: %s", list(constituents.columns))
        return []
    raw = constituents["Symbol"].astype(str).tolist()
    # yfinance uses '-' for class shares, Wikipedia uses '.' (BRK.B vs BRK-B).
    fixed = [t.replace(".", "-").upper().strip() for t in raw if t and t != "nan"]
    log.info("S&P 500: %d tickers", len(fixed))
    return fixed


# --------------------------------------------------------------------------
# Crypto: top-N by market cap from CoinGecko
# --------------------------------------------------------------------------

def fetch_top_crypto_tickers(n: int = 50, retries: int = 3) -> list[str]:
    """Return top crypto tickers in yfinance format (e.g. 'BTC-USD').

    Skips stablecoins and wrapped tokens. Falls back to a small fixed
    list if CoinGecko is unreachable.
    """
    fallback = ["BTC-USD", "ETH-USD", "SOL-USD", "BNB-USD",
                "XRP-USD", "ADA-USD", "DOGE-USD", "AVAX-USD"]
    params = {
        "vs_currency":   "usd",
        "order":         "market_cap_desc",
        "per_page":      max(n * 2, 100),  # over-fetch to survive stablecoin filter
        "page":          1,
        "sparkline":     "false",
        "price_change_percentage": "24h",
    }
    last_err = None
    for attempt in range(retries):
        try:
            r = requests.get(COINGECKO_MARKETS, params=params,
                             headers=_HTTP_HEADERS, timeout=20)
            if r.status_code == 429:
                # rate-limited → exponential back-off
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            rows = r.json() or []
            symbols: list[str] = []
            for row in rows:
                sym = (row.get("symbol") or "").upper().strip()
                if not sym or sym in STABLECOIN_BLOCKLIST:
                    continue
                symbols.append(f"{sym}-USD")
                if len(symbols) >= n:
                    break
            if symbols:
                log.info("CoinGecko top crypto: %d tickers (after filters)", len(symbols))
                return symbols
        except Exception as e:
            last_err = e
            time.sleep(1.5)
    log.warning("CoinGecko unreachable (%s) — falling back to %d hardcoded names",
                last_err, len(fallback))
    return fallback


# --------------------------------------------------------------------------
# Build the full daily universe
# --------------------------------------------------------------------------

def build_universe(crypto_n: int = 50) -> dict[str, str]:
    """Return {ticker: asset_class} for every candidate before price filters."""
    universe: dict[str, str] = {}
    for t in fetch_sp500_tickers():
        universe[t] = "stock"
    for t in fetch_top_crypto_tickers(n=crypto_n):
        universe[t] = "crypto"
    log.info("Pre-filter universe: %d tickers", len(universe))
    return universe


# --------------------------------------------------------------------------
# Post-download price quality filter
# --------------------------------------------------------------------------

def passes_quality_filter(
    closes: pd.Series,
    volumes: pd.Series | None,
    asset_class: str,
) -> tuple[bool, str]:
    """Return (ok, reason). Reason is empty when ok."""
    closes = closes.dropna()
    if len(closes) < MIN_HISTORY_DAYS:
        return False, f"history({len(closes)}d<{MIN_HISTORY_DAYS}d)"

    last_price = float(closes.iloc[-1])
    min_price = MIN_PRICE_STOCK if asset_class == "stock" else MIN_PRICE_CRYPTO
    if last_price < min_price:
        return False, f"price(${last_price:.2f}<${min_price:.2f})"

    if volumes is not None:
        vols = volumes.dropna().tail(30)
        if len(vols) >= 10:
            adv = float((vols * closes.tail(30)).mean())
            min_adv = MIN_DOLLAR_VOL_STOCK if asset_class == "stock" else MIN_DOLLAR_VOL_CRYPTO
            if adv < min_adv:
                return False, f"adv(${adv/1e6:.1f}M<${min_adv/1e6:.0f}M)"
    return True, ""


# --------------------------------------------------------------------------
# Convenience: chunk a list into batches (used by trader.py for forecasting)
# --------------------------------------------------------------------------

def chunked(seq: Iterable, size: int):
    buf: list = []
    for x in seq:
        buf.append(x)
        if len(buf) >= size:
            yield buf
            buf = []
    if buf:
        yield buf
