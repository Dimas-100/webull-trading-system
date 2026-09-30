"""Pure fill-cost model for the backtest/replay engines. Lives in the DSL layer so backtest and
replay can import it without a lab->strategy import inversion. NO_COST is the explicit cost-free
sentinel (charges nothing and never gaps a stop with slippage)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostModel:
    slippage_pct: float = 0.0           # per-side (%)
    stop_slippage_pct: float = 0.0      # additional on stop fills (%)
    commission_flat: float = 0.0
    commission_per_share: float = 0.0
    min_commission: float = 0.0
    liquidity_adv_frac: float = 0.0     # 0 disables the ADV cap


NO_COST = CostModel()


def entry_fill(open_price: float, cost: CostModel) -> float:
    return open_price * (1 + cost.slippage_pct / 100.0)


def exit_fill(open_price: float, cost: CostModel) -> float:
    return open_price * (1 - cost.slippage_pct / 100.0)


def stop_fill(stop: float, bar_open: float, cost: CostModel) -> float:
    # gap-through: a long stop fills at the worse of the stop and the (gapped) open, then slips.
    base = min(stop, bar_open)
    return base * (1 - (cost.slippage_pct + cost.stop_slippage_pct) / 100.0)


def commission(shares: float, cost: CostModel) -> float:
    return max(cost.commission_flat + cost.commission_per_share * shares, cost.min_commission)


def liquidity_ok(notional: float, bar_volume: float | None, price: float, cost: CostModel) -> bool:
    if cost.liquidity_adv_frac <= 0 or bar_volume is None:
        return True
    return notional <= cost.liquidity_adv_frac * bar_volume * price
