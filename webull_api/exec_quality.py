"""Derived execution-quality analytics — PURE (no I/O, never imports trading). Joins the
exec_ledger's decision records with the journal's REAL fills by id (the order's client_order_id) and expresses
signed slippage vs the order-type-appropriate reference. Reference chain stop -> limit -> mark
implements the per-type rule (stop orders carry stop_price; plain limits carry limit_price;
markets carry only the mark). Positive signed_bp = cost (BUY filled above / SELL filled below
the reference). Stored facts live in data/exec + data/journal; nothing here is persisted."""
from __future__ import annotations

import statistics

from webull_api.lab.schema import DEFAULT_COST_CONFIG

# The lab's per-side STRESS slippage in bp (mirrors gate_a.stress_cost: double the assumed
# slippage with a 0.10% floor). Computed locally from the light schema config so this module
# never imports the SDK client stack; a parity test pins it to gate_a.stress_cost. Today: 10.0.
_STRESS_SLIPPAGE_BP = max(2 * DEFAULT_COST_CONFIG.slippage_pct, 0.10) * 100.0


def _ref(decision: dict) -> tuple[str, float] | None:
    """(kind, price) slippage reference: stop -> limit -> mark. None = unmeasurable."""
    for kind, key in (("stop", "stop_price"), ("limit", "limit_price"), ("mark", "mark")):
        v = decision.get(key)
        try:
            if v is not None and float(v) > 0:
                return kind, float(v)
        except (TypeError, ValueError):
            continue
    return None


def slippage_rows(decisions: list[dict], fills: list[dict]) -> list[dict]:
    """One row per decision matched to a fill (by id) with a usable reference; chronological."""
    by_id = {str(f.get("id")): f for f in fills if f.get("id")}
    rows: list[dict] = []
    for d in decisions:
        f = by_id.get(str(d.get("id")))
        if f is None:
            continue
        ref = _ref(d)
        price = f.get("price")
        if ref is None or not isinstance(price, (int, float)) or price <= 0:
            continue
        kind, ref_price = ref
        sign = 1.0 if str(d.get("side", "")).upper() == "BUY" else -1.0
        bp = (float(price) - ref_price) / ref_price * 10_000.0 * sign
        rows.append({"id": d.get("id"), "symbol": d.get("symbol"), "side": d.get("side"),
                     "order_type": d.get("order_type"), "ref_kind": kind, "ref": ref_price,
                     "fill_price": float(price), "signed_bp": round(bp, 2),
                     "filled_at_iso": f.get("filled_at_iso")})
    rows.sort(key=lambda r: str(r.get("filled_at_iso") or ""))
    return rows


def summary(rows: list[dict], *, awaiting: int = 0) -> dict:
    """Owner-facing aggregates. Empty input -> zeroed summary, no crash."""
    bps = [r["signed_bp"] for r in rows]
    by_type: dict[str, dict] = {}
    for r in rows:
        b = by_type.setdefault(str(r.get("order_type") or "?"), {"n": 0, "_sum": 0.0})
        b["n"] += 1
        b["_sum"] += r["signed_bp"]
    for b in by_type.values():
        b["avg_bp"] = round(b.pop("_sum") / b["n"], 2)
    worst = max(rows, key=lambda r: r["signed_bp"], default=None)
    return {
        "fills_matched": len(rows),
        "awaiting_fill": awaiting,
        "avg_bp": round(sum(bps) / len(bps), 2) if bps else 0.0,
        "median_bp": round(statistics.median(bps), 2) if bps else 0.0,
        "worst": ({"symbol": worst["symbol"], "signed_bp": worst["signed_bp"],
                   "filled_at_iso": worst["filled_at_iso"]} if worst else None),
        "by_order_type": by_type,
        "modeled_bp": _STRESS_SLIPPAGE_BP,
    }
