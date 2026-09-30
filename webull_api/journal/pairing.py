"""FIFO long-only pairing: a stream of Fills -> closed round-trip trades + open remainders.
Pure and deterministic. Shorts are out of scope (a SELL with no prior BUY is an orphan)."""
from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timezone

from webull_api.journal.schema import ClosedTrade, Fill, OpenPosition, setup_label

_EPS = 1e-9


def _parse_dt(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        pass
    try:  # epoch milliseconds as a string
        return datetime.fromtimestamp(float(s) / 1000.0, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _epoch(s: str) -> float:
    """UTC epoch seconds for an ISO/epoch-ms timestamp; +inf if unparseable (sorts last)."""
    dt = _parse_dt(s)
    if dt is None:
        return float("inf")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _days_between(a: str, b: str) -> float:
    ea, eb = _epoch(a), _epoch(b)
    if ea == float("inf") or eb == float("inf"):
        return 0.0
    return abs(eb - ea) / 86400.0


def realized_pnl_on_day(fills: list[Fill], day: str, *, source: str = "real",
                        account_id: str | None = None) -> float:
    """Signed realized P&L of round-trips CLOSED on `day` (exit date == `day`, YYYY-MM-DD), for the
    given `source` and (optionally) `account_id`. Pure; reuses the FIFO `pair_fills`. Returns the
    SIGNED total — the caller clamps gains to zero when tightening a loss halt (a realized gain must
    never offset an unrealized loss). No matching close -> 0.0."""
    sel = [f for f in fills
           if f.source == source and (account_id is None or f.account_id == account_id)]
    closed, _ = pair_fills(sel)
    return sum(c.pnl for c in closed if (c.exit_at_iso or "")[:10] == day)


def pair_fills(fills: list[Fill]) -> tuple[list[ClosedTrade], list[OpenPosition]]:
    # Group by account too: real fills from DIFFERENT Webull accounts must never pair against
    # each other (account A's BUY vs account B's SELL is not a round-trip). A falsy account_id
    # buckets together, preserving the old behavior for any legacy data without one.
    groups: dict[tuple[str, str, str], list[Fill]] = defaultdict(list)
    for f in fills:
        groups[(f.symbol, f.source, f.account_id or "")].append(f)

    closed: list[ClosedTrade] = []
    opens: list[OpenPosition] = []

    for (symbol, source, _account_id), gfills in groups.items():
        gfills = sorted(gfills, key=lambda f: _epoch(f.filled_at_iso))
        lots: deque[dict] = deque()  # FIFO of open BUY lots
        for f in gfills:
            if f.side == "BUY":
                lots.append({"qty": f.quantity, "price": f.price,
                             "time": f.filled_at_iso, "context": f.context,
                             "thesis": f.thesis, "strategy_id": f.strategy_id,
                             "trial_id": f.trial_id, "id": f.id})
                continue
            # SELL: consume oldest lots
            remaining = f.quantity
            while remaining > _EPS and lots:
                lot = lots[0]
                matched = min(lot["qty"], remaining)
                cost = lot["price"] * matched
                pnl = (f.price - lot["price"]) * matched
                closed.append(ClosedTrade(
                    symbol=symbol, source=source, quantity=matched,
                    entry_price=lot["price"], exit_price=f.price,
                    entry_at_iso=lot["time"], exit_at_iso=f.filled_at_iso,
                    holding_days=_days_between(lot["time"], f.filled_at_iso),
                    pnl=pnl, return_pct=(pnl / cost * 100.0) if cost else 0.0,
                    win=pnl > 0, entry_context=lot["context"],
                    setup=setup_label(lot["context"]),
                    thesis=lot["thesis"], strategy_id=lot["strategy_id"],
                    trial_id=lot["trial_id"],
                    entry_fill_id=lot["id"], exit_fill_id=f.id))
                lot["qty"] -= matched
                remaining -= matched
                if lot["qty"] <= _EPS:
                    lots.popleft()
            # leftover SELL with no lots -> orphan, ignored
        if lots:
            tot = sum(lot_["qty"] for lot_ in lots)
            avg = sum(lot_["qty"] * lot_["price"] for lot_ in lots) / tot if tot else 0.0
            opens.append(OpenPosition(symbol=symbol, source=source, quantity=tot,
                                      avg_entry_price=avg, opened_at_iso=lots[0]["time"]))
    return closed, opens
