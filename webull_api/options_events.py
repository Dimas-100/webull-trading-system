"""Pure earnings-move analytics + an injectable orchestrator. Read-only. No raise/NaN."""
from __future__ import annotations

from datetime import date
from statistics import mean, median

from .options_analytics import to_float


def historical_earnings_moves(bars, earnings_dates):
    """For each past earnings date, the |% move| of the reaction bar vs the prior close.
    Entries may be ISO date strings or {"date", "hour"} dicts (Finnhub hour: bmo/amc/dmh).
    An amc reporter announces AFTER that day's close, so its reaction is the first bar
    STRICTLY AFTER the date (the on-date bar closes pre-announcement and would
    systematically understate the move); bmo/dmh/unknown use the first bar on/after.
    `bars` = daily OHLCV dicts with a date-ish key + close."""
    rows = []
    for b in bars or []:
        d = b.get("date") or b.get("timestamp") or b.get("t")
        c = to_float(b.get("close"))
        if d is None or c is None:
            continue
        rows.append((str(d)[:10], c))
    rows.sort(key=lambda x: x[0])
    dates = [d for d, _ in rows]
    closes = [c for _, c in rows]
    moves = []
    for e in earnings_dates or []:
        if isinstance(e, dict):
            ed, hour = e.get("date"), (e.get("hour") or "").lower()
        else:
            ed, hour = e, ""
        if not ed:
            continue
        ed = str(ed)[:10]
        after = next((i for i, d in enumerate(dates) if (d > ed if hour == "amc" else d >= ed)), None)
        if after is None or after == 0 or not closes[after - 1]:
            continue
        moves.append(abs(closes[after] / closes[after - 1] - 1) * 100)
    return moves


def earnings_move(spot, straddle, past_moves):
    spot, straddle = to_float(spot), to_float(straddle)
    out = {"implied_pct": None, "hist_avg_pct": None, "hist_median_pct": None,
           "verdict": "unknown", "samples": len(past_moves or [])}
    if spot and straddle:
        out["implied_pct"] = round(straddle / spot * 100, 4)
    if past_moves:
        out["hist_avg_pct"] = round(mean(past_moves), 4)
        out["hist_median_pct"] = round(median(past_moves), 4)
    if out["implied_pct"] is not None and out["hist_avg_pct"]:
        a = out["hist_avg_pct"]
        out["verdict"] = ("expensive" if out["implied_pct"] > a * 1.15
                          else "cheap" if out["implied_pct"] < a * 0.85 else "fair")
    return out


def _atm_straddle(symbol, exp, spot, snapshot_fn):
    """Best-effort ATM straddle mid for one expiration (grid-snapped, tolerant of invalids)."""
    from .options import InvalidOptionSymbolError
    from .options_analytics import _mid
    from .options_chain import build_occ, default_spacing
    spacing = default_spacing(spot)
    base = round(spot / spacing) * spacing
    for k in (base, base + spacing, base - spacing):
        if k <= 0:
            continue
        csv = ",".join([build_occ(symbol, exp, "C", k), build_occ(symbol, exp, "P", k)])
        try:
            rows = list(snapshot_fn(csv))
        except InvalidOptionSymbolError:
            continue
        by = {("C" if r["symbol"][-9] == "C" else "P"): r for r in rows}
        if by.get("C") and by.get("P") and _mid(by["C"]) is not None and _mid(by["P"]) is not None:
            return _mid(by["C"]) + _mid(by["P"])
    return None


def earnings_move_for(symbol, *, today=None, spot_fn=None, snapshot_fn=None, exp_fn=None,
                      bars_fn=None, next_earn_fn=None, past_earn_fn=None):
    from . import market_data, options, options_chain
    from .options_analytics import _spot_default
    today = today or date.today()
    spot_fn = spot_fn or _spot_default
    snapshot_fn = snapshot_fn or options.get_option_snapshot
    exp_fn = exp_fn or (lambda sym, spot, d: options_chain.discover_expirations(sym, spot, d))
    bars_fn = bars_fn or (lambda s, **k: market_data.get_bars(s, count="400"))
    if next_earn_fn is None or past_earn_fn is None:
        from webull_web import news
        next_earn_fn = next_earn_fn or news.get_next_earnings_date
        past_earn_fn = past_earn_fn or news.get_past_earnings_dates

    spot = spot_fn(symbol)
    nxt = next_earn_fn(symbol)
    out = {"symbol": symbol.upper(), "spot": spot, "next_earnings": nxt, "expiration": None,
           "implied_reason": None}
    straddle = None
    if nxt:
        exps = exp_fn(symbol, spot, today)
        brak = next((e for e in exps if e >= nxt), None)
        if brak:
            out["expiration"] = brak
            straddle = _atm_straddle(symbol, date.fromisoformat(brak), spot, snapshot_fn)
        else:
            # No listed expiration on/after the earnings date: a pre-event straddle
            # excludes the move entirely, so implied is honestly unavailable instead
            # of a mislabeled number.
            out["implied_reason"] = "no expiration on/after earnings"
    try:
        past_moves = historical_earnings_moves(bars_fn(symbol), past_earn_fn(symbol) or [])
    except Exception:
        past_moves = []
    out.update(earnings_move(spot, straddle, past_moves))
    return out
