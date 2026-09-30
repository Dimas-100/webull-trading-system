"""Pure income-strategy yields: covered call & cash-secured put. Read-only. No raise/NaN."""
from __future__ import annotations

from datetime import date

from .options_analytics import to_float, year_fraction


def _ann(period_return, dte):
    # A sub-1-day dte would explode the x365/dte annualization (the year_fraction floor
    # makes an expiration-day dte ~3e-4 -> a ~10^6x figure). Same/next-day income isn't
    # an annual yield: return the period return, but annualized honestly degrades to
    # None (sorted last) instead of a 10^8% number.
    return period_return * (365.0 / dte) if dte and dte >= 1 else None


def _r4(v):
    return round(v, 4) if v is not None else None


def covered_call_yield(spot, strike, premium, t):
    spot, strike, premium = to_float(spot), to_float(strike), to_float(premium)
    if not spot or strike is None or premium is None or not t or t <= 0:
        return None
    dte = t * 365.0
    static = premium / spot
    if_called = (premium + (strike - spot)) / spot
    return {"static_return": round(static, 4), "static_annualized": _r4(_ann(static, dte)),
            "if_called_return": round(if_called, 4), "if_called_annualized": _r4(_ann(if_called, dte)),
            "downside_breakeven": round(spot - premium, 4), "cushion_pct": round(premium / spot, 4)}


def csp_yield(strike, premium, t):
    strike, premium = to_float(strike), to_float(premium)
    if not strike or premium is None or not t or t <= 0:
        return None
    dte = t * 365.0
    roc = premium / strike
    return {"return_on_cash": round(roc, 4), "annualized": _r4(_ann(roc, dte)),
            "effective_buy": round(strike - premium, 4), "discount_pct": round(premium / strike, 4),
            "breakeven": round(strike - premium, 4)}


def _mid(q):
    bid, ask = to_float(q.get("bid")), to_float(q.get("ask"))
    if bid is not None and ask is not None and bid > 0 and ask > 0:
        return (bid + ask) / 2
    return to_float(q.get("price"))


def income_yields(rows, spot, t, side):
    spot = to_float(spot)
    out = []
    if not spot:
        return out
    for r in rows or []:
        strike = to_float(r.get("strike"))
        q = r.get("call") if side == "call" else r.get("put")
        if strike is None or not isinstance(q, dict):
            continue
        # Only OTM contracts are real income candidates (ITM premium is mostly intrinsic =
        # a synthetic stock position, not income): covered calls above spot, CSPs below.
        if (side == "call" and strike < spot) or (side == "put" and strike > spot):
            continue
        prem = _mid(q)
        if prem is None:
            continue
        y = covered_call_yield(spot, strike, prem, t) if side == "call" else csp_yield(strike, prem, t)
        if y is None:
            continue
        out.append({"strike": strike, "premium": round(prem, 4), **y})
    key = "static_annualized" if side == "call" else "annualized"
    out.sort(key=lambda d: d.get(key) if d.get(key) is not None else -1e9, reverse=True)
    return out


def income_for(symbol, expiration, side, *, today=None, spot_fn=None, chain_fn=None):
    from . import options_chain
    from .options_analytics import _spot_default
    today = today or date.today()
    spot_fn = spot_fn or _spot_default
    chain_fn = chain_fn or (lambda sym, exp, spot: options_chain.fetch_chain(sym, exp, spot))
    exp = date.fromisoformat(expiration)
    spot = spot_fn(symbol)
    t = year_fraction(today, exp)
    chain = chain_fn(symbol, exp, spot)
    return {"symbol": symbol.upper(), "expiration": expiration, "spot": spot, "side": side,
            "t": round(t, 6), "rows": income_yields(chain.get("rows", []), spot, t, side)}
