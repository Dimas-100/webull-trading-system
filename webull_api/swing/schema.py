"""Pydantic models for the Swing Planner output (validated, JSON-serializable)."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel


class CheckResult(BaseModel):
    name: str
    ok: bool
    detail: str = ""


class GateResult(BaseModel):
    n: int
    name: str
    ok: bool
    detail: str = ""


class SwingPlan(BaseModel):
    symbol: str
    book: float
    indicators: dict
    disqualifiers: list[CheckResult] = []
    gates: list[GateResult] = []
    entry: Optional[float] = None
    stop: Optional[float] = None
    target: Optional[float] = None
    shares: Optional[int] = None
    actual_risk_dollars: Optional[float] = None
    actual_risk_pct: Optional[float] = None
    notional: Optional[float] = None
    rr: Optional[float] = None
    exit_mode: Optional[Literal["A", "B"]] = None
    spy_risk_off: Optional[bool] = None
    verdict: Literal["PASS", "SKIP"]
    reason: str = ""
    notes: list[str] = []
