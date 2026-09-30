"""Pure chain-wide options vol intelligence: IV skew, term structure, liquidity scoring,
plus an injectable orchestrator. No NaN, no raise. Read-only."""
from __future__ import annotations

from datetime import date

from .options_analytics import normalize_iv, to_float


def _iv(side_quote):
    return normalize_iv(side_quote.get("imp_vol")) if isinstance(side_quote, dict) else None


def skew(rows, spot) -> dict:
    spot = to_float(spot)
    out = {"spot": spot, "points": [], "atm_iv": None, "put_call_iv_spread": None, "slope": None}
    if not rows or not spot:
        return out
    pts = []
    for r in rows:
        strike = to_float(r.get("strike"))
        if strike is None:
            continue
        civ, piv = _iv(r.get("call")), _iv(r.get("put"))
        pts.append({"strike": strike, "moneyness_pct": (strike / spot - 1) * 100,
                    "call_iv": civ, "put_iv": piv})
    out["points"] = pts
    # ATM IV: mid IV of the strike nearest spot
    near = min((p for p in pts if p["call_iv"] or p["put_iv"]),
               key=lambda p: abs(p["strike"] - spot), default=None)
    if near:
        ivs = [v for v in (near["call_iv"], near["put_iv"]) if v]
        out["atm_iv"] = sum(ivs) / len(ivs) if ivs else None
    # put_call_iv_spread: OTM put IV minus OTM call IV at ~symmetric distance (~5% OTM)
    otm_put = min((p for p in pts if p["put_iv"] and p["strike"] < spot),
                  key=lambda p: abs(p["moneyness_pct"] + 5), default=None)
    otm_call = min((p for p in pts if p["call_iv"] and p["strike"] > spot),
                   key=lambda p: abs(p["moneyness_pct"] - 5), default=None)
    if otm_put and otm_call:
        out["put_call_iv_spread"] = round(otm_put["put_iv"] - otm_call["call_iv"], 4)
    # slope: least-squares of mid-IV vs moneyness_pct
    xy = [(p["moneyness_pct"], (p["call_iv"] + p["put_iv"]) / 2)
          for p in pts if p["call_iv"] and p["put_iv"]]
    if len(xy) >= 2:
        n = len(xy)
        sx = sum(x for x, _ in xy)
        sy = sum(y for _, y in xy)
        sxx = sum(x * x for x, _ in xy)
        sxy = sum(x * y for x, y in xy)
        denom = n * sxx - sx * sx
        if denom:
            out["slope"] = round((n * sxy - sx * sy) / denom, 6)
    return out


def term_structure(atm_by_exp) -> dict:
    pts = sorted((p for p in (atm_by_exp or []) if to_float(p.get("atm_iv"))),
                 key=lambda p: p.get("dte", 0))
    # < 2 points is missing data, not a measurement — "unknown", never a fake "flat".
    out = {"points": pts, "front_iv": None, "back_iv": None, "shape": "unknown"}
    if len(pts) < 2:
        return out
    front, back = to_float(pts[0]["atm_iv"]), to_float(pts[-1]["atm_iv"])
    out["front_iv"], out["back_iv"] = front, back
    if back > front + 0.01:
        out["shape"] = "contango"
    elif front > back + 0.01:
        out["shape"] = "backwardation"
    else:
        out["shape"] = "flat"
    return out


def liquidity(quote) -> dict:
    bid, ask = to_float(quote.get("bid")), to_float(quote.get("ask"))
    oi = to_float(quote.get("open_interest"))
    vol = to_float(quote.get("volume"))
    out = {"spread": None, "spread_pct": None, "oi": oi, "volume": vol, "score": None, "label": "unknown"}
    if bid is None or ask is None or ask <= 0 or bid < 0 or ask < bid:
        return out
    mid = (bid + ask) / 2
    if mid <= 0:
        return out
    spread = ask - bid
    spread_pct = spread / mid
    out["spread"], out["spread_pct"] = round(spread, 4), round(spread_pct, 4)
    score = 100.0
    score -= min(spread_pct, 0.5) / 0.5 * 60      # up to -60 for a 50%+ spread
    if (oi or 0) < 100:
        score -= 20
    elif (oi or 0) < 1000:
        score -= 8
    if (vol or 0) < 10:
        score -= 20
    elif (vol or 0) < 100:
        score -= 8
    score = max(0, min(100, round(score)))
    out["score"] = score
    out["label"] = "good" if score >= 80 else "fair" if score >= 50 else "poor"
    return out


# ── impure orchestrator (injectable; the only network surface) ──────────────────

def _spot_default(symbol, market_data):
    snap = market_data.get_snapshot(symbol)
    row = snap[0] if isinstance(snap, list) and snap else snap
    return to_float(row["price"])


def vol_surface_for(symbol, *, today=None, spot_fn=None, snapshot_fn=None, exp_fn=None, chain_fn=None):
    from . import market_data, options, options_chain
    today = today or date.today()
    spot_fn = spot_fn or (lambda s: _spot_default(s, market_data))
    snapshot_fn = snapshot_fn or options.get_option_snapshot
    exp_fn = exp_fn or (lambda sym, spot, d: options_chain.discover_expirations(sym, spot, d))
    chain_fn = chain_fn or (lambda sym, exp, spot: options_chain.fetch_chain(sym, exp, spot))

    spot = spot_fn(symbol)
    exps = exp_fn(symbol, spot, today)
    sk = {"spot": spot, "points": [], "atm_iv": None, "put_call_iv_spread": None, "slope": None}
    if exps:
        chain = chain_fn(symbol, date.fromisoformat(exps[0]), spot)
        sk = skew(chain.get("rows", []), spot)
    # term structure: one ATM call per expiration, grid-snapped, batched (<=20)
    spacing = options_chain.default_spacing(spot)
    atm = round(spot / spacing) * spacing
    occ_by_exp = {options_chain.build_occ(symbol, date.fromisoformat(e), "C", atm): e for e in exps}
    rows = {}
    occs = list(occ_by_exp)
    for i in range(0, len(occs), 20):
        chunk = occs[i:i + 20]
        try:
            # The snapshot API is all-or-nothing on invalid symbols: one off-grid ATM
            # probe would kill the whole batch. Reuse options_chain's prune-and-retry
            # so the surviving expirations still get their ATM IV.
            got, invalid = options_chain._query(snapshot_fn, chunk)
            if invalid:
                survivors = [s for s in chunk if s not in invalid]
                got = options_chain._query(snapshot_fn, survivors)[0] if survivors else []
        except Exception:
            got = []  # best-effort: a failed batch degrades that slice, not the surface
        for r in got:
            rows[r["symbol"]] = r
    atm_by_exp = []
    for occ, e in occ_by_exp.items():
        iv = normalize_iv(rows.get(occ, {}).get("imp_vol"))
        if iv:
            atm_by_exp.append({"expiration": e, "dte": (date.fromisoformat(e) - today).days, "atm_iv": iv})
    return {"symbol": symbol.upper(), "spot": spot, "skew": sk, "term_structure": term_structure(atm_by_exp)}
