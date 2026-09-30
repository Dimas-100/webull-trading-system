"""Pydantic v2 models for a Strategy and its backtest result (validated, deterministic)."""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, model_validator


class SmaCross(BaseModel):
    type: Literal["sma_cross"]
    fast: int = Field(gt=0)
    slow: int = Field(gt=0)
    direction: Literal["above", "below"]

    @model_validator(mode="after")
    def _fast_lt_slow(self):
        if self.fast >= self.slow:
            raise ValueError("fast must be < slow")
        return self


class EmaCross(BaseModel):
    type: Literal["ema_cross"]
    fast: int = Field(gt=0)
    slow: int = Field(gt=0)
    direction: Literal["above", "below"]

    @model_validator(mode="after")
    def _fast_lt_slow(self):
        if self.fast >= self.slow:
            raise ValueError("fast must be < slow")
        return self


class RsiCond(BaseModel):
    type: Literal["rsi"]
    period: int = Field(gt=0)
    threshold: float = Field(ge=0, le=100)
    comparison: Literal["below", "above"]


class Breakout(BaseModel):
    type: Literal["breakout"]
    lookback: int = Field(gt=0)
    direction: Literal["high", "low"]


class PriceVsSma(BaseModel):
    type: Literal["price_vs_sma"]
    period: int = Field(gt=1, le=300)
    side: Literal["above", "below"]


class MacdSignal(BaseModel):
    type: Literal["macd"]
    fast: int = Field(gt=0)
    slow: int = Field(gt=0)
    signal: int = Field(default=9, gt=0)
    ref: Literal["signal", "zero"] = "signal"
    direction: Literal["above", "below"]

    @model_validator(mode="after")
    def _fast_lt_slow(self):
        if self.fast >= self.slow:
            raise ValueError("fast must be < slow")
        return self


class AtrPct(BaseModel):
    type: Literal["atr_pct"]
    period: int = Field(default=14, gt=0, le=100)
    level: float = Field(gt=0, le=100)
    side: Literal["above", "below"]


class IbsCond(BaseModel):
    type: Literal["ibs"]
    level: float = Field(gt=0, lt=1)
    side: Literal["below", "above"]


class ZScoreCond(BaseModel):
    type: Literal["zscore"]
    period: int = Field(gt=1, le=300)
    threshold: float = Field(ge=-5, le=5)
    comparison: Literal["below", "above"]


class DropFromHigh(BaseModel):
    type: Literal["drop_from_high"]
    lookback: int = Field(gt=1, le=300)
    pct: float = Field(gt=0, le=90)


class ConsecDown(BaseModel):
    type: Literal["consec_down"]
    count: int = Field(ge=2, le=10)


# LEAF union (11) — depth-1: this is what a composite's `conditions` accept, so a composite can
# never contain another composite (no nested boolean trees — the primary overfit firewall).
Condition = Annotated[
    Union[SmaCross, EmaCross, RsiCond, Breakout, PriceVsSma, MacdSignal, AtrPct,
          IbsCond, ZScoreCond, DropFromHigh, ConsecDown],
    Field(discriminator="type")]


class _Composite(BaseModel):
    conditions: list[Condition] = Field(min_length=2, max_length=3)

    @model_validator(mode="after")
    def _no_dup_leaves(self):
        seen: list[dict] = []
        for c in self.conditions:
            d = c.model_dump()
            if d in seen:
                raise ValueError("composite has duplicate leaves")
            seen.append(d)
        return self


class AllOf(_Composite):
    type: Literal["all_of"]


class AnyOf(_Composite):
    type: Literal["any_of"]


# ENTRY/EXIT/FILTER node = a leaf OR a depth-1 composite.
EntryNode = Annotated[
    Union[SmaCross, EmaCross, RsiCond, Breakout, PriceVsSma, MacdSignal, AtrPct,
          IbsCond, ZScoreCond, DropFromHigh, ConsecDown, AllOf, AnyOf],
    Field(discriminator="type")]


def leaf_count(node) -> int:
    """Leaves in a DSL node: 0 for None, 1 for a leaf, len(conditions) for a composite."""
    if node is None:
        return 0
    if getattr(node, "type", None) in ("all_of", "any_of"):
        return len(node.conditions)
    return 1


class Sizing(BaseModel):
    type: Literal["fixed_dollar", "pct_equity"] = "pct_equity"
    value: float = Field(default=100, gt=0)


class Strategy(BaseModel):
    name: str
    symbol: str
    timeframe: Literal["1m", "5m", "1H", "1D", "1W"] = "1D"
    lookback_bars: int = Field(default=500, gt=10, le=1200)
    entry: EntryNode
    exit: EntryNode | None = None
    filter: EntryNode | None = None
    stop_loss_pct: float | None = Field(default=None, gt=0)
    take_profit_pct: float | None = Field(default=None, gt=0)
    sizing: Sizing = Field(default_factory=Sizing)
    starting_equity: float = Field(default=10000, gt=0)
    origin: Literal["user", "lab_generated"] | None = None

    @model_validator(mode="after")
    def _leaf_budget(self):
        total = leaf_count(self.entry) + leaf_count(self.exit) + leaf_count(self.filter)
        if total > 6:
            raise ValueError(f"strategy has {total} leaves; max 6")
        return self


class Trade(BaseModel):
    entry_time: str
    entry_price: float
    exit_time: str
    exit_price: float
    shares: float
    pnl: float
    return_pct: float
    exit_reason: Literal["stop", "target", "signal", "end_of_data"]


class EquityPoint(BaseModel):
    time: str
    equity: float


class BacktestResult(BaseModel):
    total_return_pct: float
    buy_hold_return_pct: float
    num_trades: int
    win_rate: float
    avg_win_pct: float
    avg_loss_pct: float
    expectancy: float
    max_drawdown_pct: float
    equity_curve: list[EquityPoint]
    trades: list[Trade]
