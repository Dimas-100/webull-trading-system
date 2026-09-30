"""Pure options decision-math (Black-Scholes) + analytics, with impure orchestrators
at the bottom (injectable for tests). No NaN, no raise: degenerate inputs -> None.
Assumes European options, dividend yield q=0, risk-free DEFAULT_RISK_FREE. Read-only."""
from __future__ import annotations

import math
from datetime import date

DEFAULT_RISK_FREE = 0.04
_MULT = 100  # US equity option contract multiplier
_SQRT2 = math.sqrt(2.0)


# ── helpers ───────────────────────────────────────────────────────────────────

def to_float(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def normalize_iv(raw):
    v = to_float(raw)
    if v is None or v <= 0:
        return None
    # Decimal-vs-percent heuristic: Webull's confirmed unit is decimal, so a true 350% IV
    # arrives as 3.5 and must NOT be crushed to 3.5%. Values <= 5 are read as decimals,
    # > 5 as percents — the tradeoff is that 500%+ decimal IVs are vanishingly rare, as
    # are sub-5% percent-form IVs, so 5 is the least-wrong cutoff.
    return v / 100.0 if v > 5 else v


def year_fraction(today: date, expiry: date) -> float:
    return max((expiry - today).days, 0) / 365.0 or 1e-6  # floor avoids div-by-zero


# ── Black-Scholes primitives ────────────────────────────────────────────────────

def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / _SQRT2))


def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def d1_d2(S, K, r, sigma, t):
    sig_t = sigma * math.sqrt(t)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * t) / sig_t
    return d1, d1 - sig_t


def bs_price(S, K, r, sigma, t, kind: str) -> float:
    d1, d2 = d1_d2(S, K, r, sigma, t)
    disc = K * math.exp(-r * t)
    if kind.upper().startswith("C"):
        return S * norm_cdf(d1) - disc * norm_cdf(d2)
    return disc * norm_cdf(-d2) - S * norm_cdf(-d1)


def bs_delta(S, K, r, sigma, t, kind: str) -> float:
    d1, _ = d1_d2(S, K, r, sigma, t)
    return norm_cdf(d1) if kind.upper().startswith("C") else norm_cdf(d1) - 1.0


# ── expected move ───────────────────────────────────────────────────────────────

def expected_move(spot, atm_iv, t, atm_straddle=None) -> dict:
    spot = to_float(spot)
    iv = normalize_iv(atm_iv)
    out = {"iv": iv, "sigma1": None, "sigma1_pct": None, "upper": None, "lower": None,
           "straddle_move": to_float(atm_straddle), "straddle_implied_sigma1": None}
    if spot and iv and t and t > 0:
        s1 = spot * iv * math.sqrt(t)
        out.update(sigma1=s1, sigma1_pct=s1 / spot, upper=spot + s1, lower=spot - s1)
    if out["straddle_move"]:
        out["straddle_implied_sigma1"] = out["straddle_move"] / math.sqrt(2 / math.pi)
    return out


# ── strategy analytics (single + vertical via a piecewise-linear payoff) ─────────

def intrinsic(S, kind, strike) -> float:
    return max(S - strike, 0.0) if kind.upper().startswith("C") else max(strike - S, 0.0)


def _sign(side: str) -> int:
    return 1 if side.upper() == "BUY" else -1


def leg_pnl_per_share(S, leg) -> float:
    return _sign(leg["side"]) * (intrinsic(S, leg["kind"], leg["strike"]) - leg["premium"])


def payoff_at(S, legs) -> float:
    return sum(leg["qty"] * leg_pnl_per_share(S, leg) for leg in legs)


def payoff_curve(legs, lo, hi, n=61) -> list[dict]:
    if any(leg.get("premium") is None for leg in legs) or n < 2 or lo >= hi:
        return []
    step = (hi - lo) / (n - 1)
    return [{"price": round(lo + i * step, 4), "pnl": round(payoff_at(lo + i * step, legs), 6)}
            for i in range(n)]


def _slope(S, legs) -> float:
    """d(payoff)/dS just above S: a call adds +1 once S>=strike, a put -1 while S<strike."""
    s = 0.0
    for leg in legs:
        sgn = _sign(leg["side"]) * leg["qty"]
        if leg["kind"].upper().startswith("C"):
            s += sgn * (1.0 if S >= leg["strike"] else 0.0)
        else:
            s += sgn * (-1.0 if S < leg["strike"] else 0.0)
    return s


def prob_of_touch(spot, barrier, sigma, t):
    """Probability the underlying touches `barrier` before `t` — driftless reflection
    approximation 2*N(-|ln(barrier/spot)|/(sigma*sqrt(t))), the standard ~2x-prob-OTM
    touch estimate. Clamped to [0,1]. None on bad input."""
    spot = to_float(spot)
    barrier = to_float(barrier)
    sigma = normalize_iv(sigma)
    if not spot or not barrier or not sigma or not t or t <= 0:
        return None
    m = abs(math.log(barrier / spot)) / (sigma * math.sqrt(t))
    return round(min(1.0, max(0.0, 2 * norm_cdf(-m))), 4)


def expected_value(spot, legs, r, t, sigma, n=201, n_sd=6.0):
    """Per-share risk-neutral expected P&L: trapezoidal integral of payoff x lognormal
    density over spot*exp((r - sigma^2/2)t +/- n_sd*sigma*sqrt(t)) — bounds that scale
    with vol (fixed [0.4, 2.5]x-spot bounds truncated real mass at high sigma, flipping
    the EV sign on a fair ATM put at sigma=0.6/t=1). Integrated in log-space via
    s = spot*exp(mu + sd*z) so the uniform z-grid puts resolution where the probability
    mass is at any vol. ~0 for a BS-fair-priced position (no-arbitrage). None on bad input."""
    spot = to_float(spot)
    sigma = normalize_iv(sigma)
    if not spot or not sigma or not t or t <= 0 or any(to_float(l.get("premium")) is None for l in legs):
        return None
    mu = (r - 0.5 * sigma * sigma) * t
    sd = sigma * math.sqrt(t)
    step = 2 * n_sd / (n - 1)
    ev = 0.0
    for i in range(n):
        z = -n_sd + i * step
        s = max(spot * math.exp(mu + sd * z), 1e-12)  # clamp: exp underflow -> tiny positive
        w = 0.5 if i in (0, n - 1) else 1.0          # trapezoid weights
        ev += w * payoff_at(s, legs) * norm_pdf(z) * step
    return round(ev, 4)


def scenario_grid(spot, legs, r, t, price_moves=(-0.05, -0.02, 0.0, 0.02, 0.05),
                  iv_shifts=(-0.05, 0.0, 0.05), days_forward=0) -> dict:
    """Position P&L re-priced (Black-Scholes) under each (price move, IV shift) + time decay."""
    spot = to_float(spot)
    out = {"iv_shifts": list(iv_shifts), "rows": []}
    if not spot or any(l.get("iv") is None or to_float(l.get("premium")) is None for l in legs):
        return out
    t2 = max(t - days_forward / 365.0, 1e-6)
    # Baseline = each leg's current BS value, so P&L is measured "from here" (0% move = $0),
    # isolating the scenario impact and cancelling any BS-vs-market-mid basis.
    base = {id(l): bs_price(spot, l["strike"], r, max(l["iv"], 1e-4), max(t, 1e-6), l["kind"]) for l in legs}
    for mv in price_moves:
        s2 = spot * (1 + mv)
        pnls = []
        for shift in iv_shifts:
            pnl = 0.0
            for l in legs:
                sig2 = max(l["iv"] + shift, 1e-4)
                val = bs_price(s2, l["strike"], r, sig2, t2, l["kind"])
                pnl += l["qty"] * _sign(l["side"]) * (val - base[id(l)]) * _MULT
            pnls.append(round(pnl, 2))
        out["rows"].append({"price_move": mv, "price": round(s2, 2), "pnl": pnls})
    return out


def _pop_from_payoff(spot, legs, breakevens, r, t, sigma):
    """Risk-neutral probability of profit: sum the lognormal mass of every price interval
    (delimited by the breakevens) where the payoff is positive. Handles 0/1/2+ breakevens."""
    spot = to_float(spot)
    sigma = normalize_iv(sigma)
    if not spot or not sigma or not t or t <= 0:
        return None

    def p_above(x):
        if x <= 0:
            return 1.0
        d2 = (math.log(spot / x) + (r - 0.5 * sigma * sigma) * t) / (sigma * math.sqrt(t))
        return norm_cdf(d2)

    bounds = [0.0] + sorted(breakevens) + [math.inf]
    pop = 0.0
    for lo, hi in zip(bounds, bounds[1:]):
        mid = lo * 1.5 + 1.0 if hi == math.inf else (lo + hi) / 2
        if payoff_at(mid, legs) > 0:
            pop += p_above(lo) - (0.0 if hi == math.inf else p_above(hi))
    return round(min(1.0, max(0.0, pop)), 4)


def analyze_strategy(spot, legs, r, t, sigma) -> dict:
    spot = to_float(spot)
    strategy = {1: "single", 2: "vertical"}.get(len(legs), "custom")
    out = {"strategy": strategy, "net_debit": None, "net_debit_dollar": None,
           "breakevens": [], "max_profit": None, "max_loss": None,
           "max_profit_dollar": None, "max_loss_dollar": None,
           "unbounded_profit": False, "unbounded_loss": False, "risk_reward": None,
           "pop": None, "prob_itm": None,
           "expected_value": None, "expected_value_dollar": None, "prob_of_touch": None,
           "net_greeks": {"delta": None, "gamma": None, "theta": None, "vega": None},
           "notes": "POP is risk-neutral (lognormal); q=0; r=%.3f" % (r,)}

    # net greeks (best-effort; a missing greek for a leg is skipped)
    for g in out["net_greeks"]:
        vals = [_sign(l["side"]) * l["qty"] * l[g] for l in legs if l.get(g) is not None]
        out["net_greeks"][g] = round(sum(vals), 4) if vals else None

    if not legs or any(to_float(l.get("premium")) is None for l in legs) or spot is None:
        return out  # can't price -> structural/greek fields only

    net = sum(_sign(l["side"]) * l["premium"] * l["qty"] for l in legs)  # + = debit
    out["net_debit"] = round(net, 4)
    out["net_debit_dollar"] = round(net * _MULT, 2)

    strikes = sorted({l["strike"] for l in legs})
    crit = sorted(set([0.0] + strikes))
    vals = [payoff_at(S, legs) for S in crit]
    # The underlying can't go below 0 (S=0 is already a critical point), so only the
    # high side (S->inf) can be unbounded; its slope above the top strike decides.
    hi_slope = _slope(strikes[-1] + 1.0, legs)
    out["unbounded_profit"] = hi_slope > 0
    out["unbounded_loss"] = hi_slope < 0

    finite_max, finite_min = max(vals), min(vals)
    if not out["unbounded_profit"]:
        out["max_profit"] = round(finite_max, 4)
        out["max_profit_dollar"] = round(finite_max * _MULT, 2)
    if not out["unbounded_loss"]:
        out["max_loss"] = round(-finite_min, 4)          # positive magnitude of the worst loss
        out["max_loss_dollar"] = round(-finite_min * _MULT, 2)
    if out["max_profit"] is not None and out["max_loss"] not in (None, 0):
        out["risk_reward"] = round(abs(out["max_profit"] / out["max_loss"]), 4)

    # breakevens: zero-crossings between sorted critical points, plus the upper outer ray
    bes = []
    for a_, b_ in zip(crit, crit[1:]):
        pa, pb = payoff_at(a_, legs), payoff_at(b_, legs)
        if pa == 0:
            bes.append(a_)
        if (pa < 0 < pb) or (pa > 0 > pb):
            bes.append(a_ + (b_ - a_) * (0 - pa) / (pb - pa))
    top = crit[-1]
    if hi_slope != 0:
        be = top - payoff_at(top, legs) / hi_slope
        if be > top:
            bes.append(be)
    out["breakevens"] = sorted({round(b, 4) for b in bes})

    # POP: sum the lognormal mass of every breakeven-delimited interval where payoff > 0
    # (handles 0/1/2+ breakevens — profit-between for condors, profit-outside for straddles).
    sigma = normalize_iv(sigma)
    out["pop"] = _pop_from_payoff(spot, legs, out["breakevens"], r, t, sigma)

    if strategy == "single" and sigma and t and t > 0:
        l = legs[0]
        d2 = d1_d2(spot, l["strike"], r, sigma, t)[1]
        out["prob_itm"] = round(norm_cdf(d2) if l["kind"].upper().startswith("C") else norm_cdf(-d2), 4)

    ev = expected_value(spot, legs, r, t, sigma) if (sigma and spot) else None
    if ev is not None:
        out["expected_value"] = ev
        out["expected_value_dollar"] = round(ev * _MULT, 2)
    out["prob_of_touch"] = (prob_of_touch(spot, out["breakevens"][0], sigma, t)
                            if strategy == "single" and out["breakevens"] else None)
    return out


# ── IV rank (pure, best-effort) ─────────────────────────────────────────────────

def historical_volatility(closes, window=20):
    xs = [to_float(c) for c in (closes or [])]
    xs = [c for c in xs if c and c > 0]
    if len(xs) < window + 1:
        window = len(xs) - 1
    if window < 2:
        return None
    rets = [math.log(xs[i] / xs[i - 1]) for i in range(len(xs) - window, len(xs))]
    mean = sum(rets) / len(rets)
    var = sum((x - mean) ** 2 for x in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(252)


def iv_rank(current, series):
    cur = to_float(current)
    xs = [to_float(s) for s in (series or []) if to_float(s) is not None]
    if cur is None or len(xs) < 2:
        return None
    lo, hi = min(xs), max(xs)
    return None if hi == lo else round((cur - lo) / (hi - lo) * 100, 2)


def iv_percentile(current, series):
    cur = to_float(current)
    xs = [to_float(s) for s in (series or []) if to_float(s) is not None]
    if cur is None or not xs:
        return None
    return round(sum(1 for s in xs if s <= cur) / len(xs) * 100, 2)


def iv_vs_hv(iv, hv):
    iv = normalize_iv(iv)
    hv = to_float(hv)
    if iv is None or not hv:
        return {"iv": iv, "hv": hv, "ratio": None, "label": "unknown"}
    ratio = iv / hv
    label = "elevated" if ratio >= 1.15 else "subdued" if ratio <= 0.85 else "normal"
    return {"iv": round(iv, 4), "hv": round(hv, 4), "ratio": round(ratio, 3), "label": label}


# ── impure orchestrators (injectable; the only network surface) ──────────────────

def _mid(row):
    bid, ask = to_float(row.get("bid")), to_float(row.get("ask"))
    if bid is not None and ask is not None and bid > 0 and ask > 0:
        return (bid + ask) / 2
    return to_float(row.get("price"))


def _spot_default(symbol):
    from . import market_data
    snap = market_data.get_snapshot(symbol)
    row = snap[0] if isinstance(snap, list) and snap else snap
    return to_float(row["price"])


def _snapshot_default():
    from . import options
    return options.get_option_snapshot


def _iv_rank_best_effort(symbol, atm_iv, bars_fn):
    """No clean historical-IV feed -> HV proxy (documented). True IV-history is a future probe."""
    from . import market_data
    bars_fn = bars_fn or (lambda s, **k: market_data.get_bars(s, count="60"))
    try:
        bars = bars_fn(symbol)
        closes = [to_float(b.get("close")) for b in bars] if bars else []
        hv = historical_volatility([c for c in closes if c], window=20)
    except Exception:
        hv = None
    if hv is None:
        return {"iv_rank": None, "iv_rank_method": "unavailable", "iv_vs_hv": None}
    return {"iv_rank": None, "iv_rank_method": "hv_proxy", "iv_vs_hv": iv_vs_hv(atm_iv, hv)}


def expected_move_for(symbol, expiration, *, today=None, spot_fn=None, snapshot_fn=None,
                      bars_fn=None, opt_bars_fn=None, r=DEFAULT_RISK_FREE):
    from .options import InvalidOptionSymbolError
    from .options_chain import build_occ, default_spacing
    today = today or date.today()
    spot_fn = spot_fn or _spot_default
    snapshot_fn = snapshot_fn or _snapshot_default()
    exp = date.fromisoformat(expiration)
    spot = spot_fn(symbol)
    t = year_fraction(today, exp)
    # Snap the ATM strike to the listed-strike grid; a synthesized strike off the grid 417s,
    # so try the nearest grid strikes and tolerate invalids until a real call+put pair is found.
    spacing = default_spacing(spot)
    base = round(spot / spacing) * spacing
    call = put = None
    for k in (base, base + spacing, base - spacing, base + 2 * spacing, base - 2 * spacing):
        if k <= 0:
            continue
        csv = ",".join([build_occ(symbol, exp, "C", k), build_occ(symbol, exp, "P", k)])
        try:
            rows = list(snapshot_fn(csv))
        except InvalidOptionSymbolError:
            continue
        by_kind = {("C" if r_["symbol"][-9] == "C" else "P"): r_ for r_ in rows}
        if by_kind.get("C") and by_kind.get("P"):
            call, put = by_kind["C"], by_kind["P"]
            break
    atm_iv = normalize_iv(call.get("imp_vol")) if call else None
    straddle = None
    if call and put and _mid(call) is not None and _mid(put) is not None:
        straddle = _mid(call) + _mid(put)
    em = expected_move(spot, atm_iv, t, atm_straddle=straddle)
    em.update(symbol=symbol.upper(), expiration=expiration, spot=spot, t=round(t, 6))
    em.update(_iv_rank_best_effort(symbol, atm_iv, bars_fn))
    return em


def analyze_for(symbol, expiration, legs, *, today=None, spot_fn=None, snapshot_fn=None,
                r=DEFAULT_RISK_FREE):
    from .options_chain import parse_occ
    today = today or date.today()
    spot_fn = spot_fn or _spot_default
    snapshot_fn = snapshot_fn or _snapshot_default()
    exp = date.fromisoformat(expiration)
    spot = spot_fn(symbol)
    t = year_fraction(today, exp)
    occs = [l["symbol"] for l in legs]
    rows = {r_["symbol"]: r_ for r_ in snapshot_fn(",".join(occs))}
    pure_legs, ivs = [], []
    for l in legs:
        row = rows.get(l["symbol"], {})
        info = parse_occ(l["symbol"])
        iv = normalize_iv(row.get("imp_vol"))
        if iv:
            ivs.append(iv)
        pure_legs.append({"kind": info["kind"], "side": l["side"], "strike": info["strike"],
                          "premium": _mid(row), "qty": int(to_float(l.get("quantity")) or 1),
                          "iv": iv, "delta": to_float(row.get("delta")),
                          "gamma": to_float(row.get("gamma")), "theta": to_float(row.get("theta")),
                          "vega": to_float(row.get("vega"))})
    sigma = sum(ivs) / len(ivs) if ivs else None
    analytics = analyze_strategy(spot, pure_legs, r, t, sigma)
    lo, hi = (spot * 0.7, spot * 1.3) if spot else (0.0, 0.0)
    # a spread is only as liquid as its least-liquid leg
    from .options_surface import liquidity as _liquidity
    liqs = [_liquidity(rows.get(l["symbol"], {})) for l in legs]
    scored = [q for q in liqs if q.get("score") is not None]
    worst = min(scored, key=lambda q: q["score"]) if scored else {"score": None, "label": "unknown"}
    return {"symbol": symbol.upper(), "expiration": expiration, "spot": spot, "t": round(t, 6),
            "analytics": analytics, "payoff_curve": payoff_curve(pure_legs, lo, hi, 61),
            "scenario": scenario_grid(spot, pure_legs, r, t),
            "liquidity": {"score": worst.get("score"), "label": worst.get("label"), "legs": liqs}}
