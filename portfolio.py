"""
Portfolio state + trading rules.

State persisted as JSON in state/portfolio.json:
  {
    "cash": 250.0,
    "holdings": {
      "AAPL": {
        "quantity": 0.123456,
        "cost_basis": 50.0,           # USD spent acquiring this position
        "avg_price": 405.32,
        "first_bought": "2026-04-30T...",
        "last_action": "2026-04-30T..."
      },
      ...
    },
    "history": [ ... trade records ... ],
    "last_total_value": 250.0,
    "last_run": "2026-04-30T..."
  }
"""

from __future__ import annotations
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Trading rules — the "standard rules" guardrails
# --------------------------------------------------------------------------

@dataclass
class TradingRules:
    max_position_pct:       float = 0.20    # ≤20% of portfolio per asset
    min_cash_floor:         float = 10.0    # never deplete below $10
    stop_loss_pct:          float = 0.08    # auto-sell at -8%
    take_profit_pct:        float = 0.15    # auto-sell at +15%
    min_expected_return:    float = 0.02    # need +2% forecast to BUY
    require_positive_q10:   bool  = True    # q10 must be above current price
    cooldown_hours:         int   = 24      # no churn on same asset
    max_concurrent_positions: int = 6
    min_buy_dollars:        float = 5.0     # don't waste on dust trades

    # ----- exit logic (stop-loss / take-profit / forecast reversal) -----
    def evaluate_exits(self, portfolio: "Portfolio", forecasts: dict) -> list[tuple[str, str]]:
        sells = []
        for ticker, position in list(portfolio.holdings.items()):
            if ticker not in forecasts:
                continue
            current = forecasts[ticker]["current_price"]
            avg     = position["avg_price"]
            ret     = (current - avg) / avg

            if not portfolio.cooldown_passed(ticker, self.cooldown_hours):
                continue

            if ret <= -self.stop_loss_pct:
                sells.append((ticker, f"stop_loss({ret*100:.1f}%)"))
            elif ret >= self.take_profit_pct:
                sells.append((ticker, f"take_profit({ret*100:.1f}%)"))
            elif forecasts[ticker]["expected_return"] < -self.min_expected_return:
                # forecast turned bearish — exit
                sells.append((ticker, f"forecast_reversal({forecasts[ticker]['expected_return']*100:.1f}%)"))
        return sells

    # ----- entry logic -----
    def evaluate_entries(self, portfolio: "Portfolio", forecasts: dict) -> list[tuple[str, float]]:
        # rank candidates by expected return (only those passing thresholds)
        ranked = []
        for ticker, f in forecasts.items():
            if ticker in portfolio.holdings:
                continue  # don't double up; one position per asset
            if not portfolio.cooldown_passed(ticker, self.cooldown_hours):
                continue
            if f["expected_return"] < self.min_expected_return:
                continue
            if self.require_positive_q10 and f["q10_end"] <= f["current_price"]:
                # 10th-percentile forecast still below entry → too uncertain
                continue
            ranked.append((ticker, f["expected_return"]))

        ranked.sort(key=lambda x: x[1], reverse=True)

        buys = []
        slots_left = self.max_concurrent_positions - len(portfolio.holdings)
        total_value = portfolio.last_total_value or portfolio.cash
        max_alloc_per_position = total_value * self.max_position_pct

        for ticker, _ in ranked:
            if slots_left <= 0:
                break
            # how much cash can we deploy?
            available = portfolio.cash - self.min_cash_floor
            if available < self.min_buy_dollars:
                break
            allocation = min(max_alloc_per_position, available)
            if allocation < self.min_buy_dollars:
                continue
            buys.append((ticker, allocation))
            # decrement projection so we don't over-allocate within this batch
            portfolio._projected_cash_used = getattr(portfolio, "_projected_cash_used", 0.0) + allocation
            slots_left -= 1

        return buys


# --------------------------------------------------------------------------
# Portfolio state
# --------------------------------------------------------------------------

@dataclass
class Portfolio:
    cash: float
    holdings: dict = field(default_factory=dict)
    history: list = field(default_factory=list)
    last_total_value: float = 0.0
    last_run: str = ""

    @classmethod
    def load(cls, path: Path, initial_cash: float) -> "Portfolio":
        if path.exists():
            data = json.loads(path.read_text())
            return cls(**data)
        log.info("No state file at %s — initializing with $%.2f", path, initial_cash)
        return cls(cash=initial_cash, last_total_value=initial_cash)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.last_run = datetime.now(timezone.utc).isoformat()
        path.write_text(json.dumps({
            "cash": round(self.cash, 4),
            "holdings": self.holdings,
            "history": self.history[-500:],  # cap history
            "last_total_value": round(self.last_total_value, 2),
            "last_run": self.last_run,
        }, indent=2))

    # ----- trades -----
    def buy(self, ticker: str, qty: float, price: float) -> None:
        cost = qty * price
        if cost > self.cash:
            raise ValueError(f"Insufficient cash: ${cost:.2f} > ${self.cash:.2f}")
        self.cash -= cost
        now = datetime.now(timezone.utc).isoformat()
        if ticker in self.holdings:
            # average up (shouldn't normally happen with our rules, but safe)
            existing = self.holdings[ticker]
            new_qty = existing["quantity"] + qty
            new_cost = existing["cost_basis"] + cost
            self.holdings[ticker] = {
                "quantity":     new_qty,
                "cost_basis":   new_cost,
                "avg_price":    new_cost / new_qty,
                "first_bought": existing["first_bought"],
                "last_action":  now,
            }
        else:
            self.holdings[ticker] = {
                "quantity":     qty,
                "cost_basis":   cost,
                "avg_price":    price,
                "first_bought": now,
                "last_action":  now,
            }
        self.history.append({"ts": now, "action": "BUY", "ticker": ticker,
                             "qty": qty, "price": price})

    def sell(self, ticker: str, qty: float, price: float) -> None:
        if ticker not in self.holdings:
            raise ValueError(f"No position in {ticker}")
        position = self.holdings[ticker]
        if qty > position["quantity"] + 1e-9:
            raise ValueError(f"Sell qty {qty} exceeds holding {position['quantity']}")
        self.cash += qty * price
        now = datetime.now(timezone.utc).isoformat()
        self.history.append({"ts": now, "action": "SELL", "ticker": ticker,
                             "qty": qty, "price": price})
        if abs(qty - position["quantity"]) < 1e-9:
            del self.holdings[ticker]
        else:
            # partial sell — reduce cost basis proportionally
            frac_sold = qty / position["quantity"]
            position["quantity"]   -= qty
            position["cost_basis"] *= (1 - frac_sold)
            position["last_action"] = now

    # ----- helpers -----
    def cooldown_passed(self, ticker: str, hours: int) -> bool:
        if ticker not in self.holdings:
            # check history for last action on this ticker
            for h in reversed(self.history):
                if h["ticker"] == ticker:
                    last = datetime.fromisoformat(h["ts"])
                    return datetime.now(timezone.utc) - last >= timedelta(hours=hours)
            return True
        last = datetime.fromisoformat(self.holdings[ticker]["last_action"])
        return datetime.now(timezone.utc) - last >= timedelta(hours=hours)

    def unrealized_pnl(self, ticker: str, current_price: float) -> float:
        if ticker not in self.holdings:
            return 0.0
        p = self.holdings[ticker]
        return (current_price - p["avg_price"]) * p["quantity"]

    def mark_to_market(self, prices: dict) -> None:
        total = self.cash
        for ticker, position in self.holdings.items():
            if ticker in prices:
                total += position["quantity"] * prices[ticker]
            else:
                # fall back to cost basis if no price
                total += position["cost_basis"]
        self.last_total_value = total
