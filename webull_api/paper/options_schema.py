"""Pydantic v2 models for the options paper-trading account (separate from the equity sim)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..safety import OrderValidationError


class OptionsPaperError(OrderValidationError):
    """An options-paper rejection (insufficient buying power, naked short, oversell).
    Subclasses OrderValidationError so the web layer's existing 400 handler catches it."""


class OptionLeg(BaseModel):
    occ: str                                  # OCC contract symbol, e.g. AAPL260717C00300000
    underlying: str
    option_type: Literal["CALL", "PUT"]
    strike: float
    expiration: str                           # ISO date
    side: Literal["BUY", "SELL"]              # leg direction at entry (long / short)


class OptionPositionUnit(BaseModel):
    unit_id: str
    strategy: Literal["SINGLE", "VERTICAL"]
    legs: list[OptionLeg]
    quantity: int                             # contracts (> 0)
    avg_net_price: float                      # per-contract net entry; + debit / − credit
    collateral: float                         # cash reserved for this unit (0 for long/debit)
    opened_at: str


class OptionPaperOrder(BaseModel):
    paper_order_id: str
    strategy: Literal["SINGLE", "VERTICAL"]
    legs: list[OptionLeg]
    quantity: int
    intent: Literal["OPEN", "CLOSE"]
    close_unit_id: str | None = None
    order_type: Literal["MARKET", "LIMIT"]
    limit_net_price: float | None = None      # net debit (+) / credit (−) for LIMIT
    time_in_force: Literal["DAY", "GTC"] = "DAY"
    status: Literal["pending", "filled", "cancelled", "expired", "rejected"] = "pending"
    created_at: str
    placed_et_date: str
    filled_at: str | None = None
    fill_net_price: float | None = None
    reject_reason: str | None = None          # why evaluate() rejected a resting order
    # Which price basis an expiration settlement used (status="expired" records only):
    # "exp_close" = the expiration-day close was available; "current_spot" = the spot at
    # whatever later refresh settled it (an approximation — disclosed to the Journal).
    settle_basis: Literal["exp_close", "current_spot"] | None = None
    # ── realized-P&L stamp (set on a filled CLOSE / expired settlement; lets the Journal build a
    #    closed options trade without OPEN<->CLOSE pairing). All None on OPEN / pending / cancelled. ──
    realized_pnl: float | None = None        # $ P&L of this close/expiration event
    open_net_price: float | None = None      # closed unit's per-contract net entry (+debit / −credit)
    close_value: float | None = None         # per-contract close value (mid on close, intrinsic on expiry)
    closed_qty: int | None = None            # contracts closed in this event
    unit_opened_at: str | None = None        # when the closed unit was opened (holding period)
    risk_basis: float | None = None          # $ capital at risk for the closed portion (return% basis)


class OptionsPaperAccount(BaseModel):
    account_id: str = "options"
    starting_cash: float
    cash: float
    reserved_collateral: float = 0.0
    positions: list[OptionPositionUnit] = Field(default_factory=list)
    open_orders: list[OptionPaperOrder] = Field(default_factory=list)
    history: list[OptionPaperOrder] = Field(default_factory=list)
    realized_pnl: float = 0.0
    created_at: str
    updated_at: str
