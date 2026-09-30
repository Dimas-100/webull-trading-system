"""Pydantic v2 models for the local paper-trading account (validated, serializable)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..journal.schema import ThesisRecord
from ..safety import OrderValidationError


class PaperError(OrderValidationError):
    """A paper-specific rejection (insufficient buying power, oversell). Subclasses
    OrderValidationError so the web layer's existing 400 handler catches it."""


class PaperPosition(BaseModel):
    symbol: str
    quantity: float        # long-only (>= 0)
    avg_cost: float


class PaperOrder(BaseModel):
    paper_order_id: str
    symbol: str
    side: Literal["BUY", "SELL"]
    order_type: Literal["MARKET", "LIMIT"]
    quantity: float
    limit_price: float | None = None
    time_in_force: Literal["DAY", "GTC"] = "DAY"
    status: Literal["pending", "filled", "cancelled", "expired", "rejected"] = "pending"
    created_at: str
    placed_et_date: str
    filled_at: str | None = None
    fill_price: float | None = None
    thesis: ThesisRecord | None = None
    # Provenance (the proven-strategy paper runner stamps these): the Lab-proven strategy / trial that
    # placed the order. None for discretionary/RSI2 orders. Optional so existing account JSON parses.
    strategy_id: str | None = None
    trial_id: str | None = None
    # Execution-fidelity queue (2026-07-27 spec). "immediate" = legacy instant-fill behavior;
    # "next_open" = only settle_next_open may fill it, at the first session open after placement.
    fill_policy: Literal["immediate", "next_open"] = "immediate"
    ref_price: float | None = None      # snapshot price at placement; cash-reservation basis
    fill_session: str | None = None     # session date (YYYY-MM-DD) whose open filled it


class PaperAccount(BaseModel):
    account_id: str = "default"
    starting_cash: float
    cash: float
    positions: dict[str, PaperPosition] = Field(default_factory=dict)
    open_orders: list[PaperOrder] = Field(default_factory=list)
    history: list[PaperOrder] = Field(default_factory=list)
    realized_pnl: float = 0.0
    created_at: str
    updated_at: str
