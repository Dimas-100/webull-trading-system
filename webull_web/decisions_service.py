"""Trigger watch for the standing-decisions ledger. Pure evaluators (`evaluate`) answer "did
this decision's condition fire today?" from injected readers; the impure `evening_check` runs
nightly inside the note runner — it appends one check row per open watched decision, pushes a
best-effort ntfy alert per FIRED trigger, and returns the note view. Unknown/missing data NEVER
fires a trigger (the detail says why) — the watch fails quiet, not loud."""
from __future__ import annotations

import logging

from webull_api import market_data

from . import decisions_store, lab_store
from .rsi2_service import _bar_date

_log = logging.getLogger(__name__)


def _check_green_day(watch: dict, today: str, get_bars) -> tuple[bool, str]:
    from webull_api.strategy.bars import to_ohlcv
    sym = str(watch.get("symbol", "")).upper()
    bars = to_ohlcv(get_bars(sym, "D", count="5"))
    if len(bars) < 2 or _bar_date(bars[-1]["time"]) != today:
        return False, f"{sym}: no fresh daily bar"
    prev, last = bars[-2]["close"], bars[-1]["close"]
    if not prev:
        return False, f"{sym}: no prior close"
    pct = (last / prev - 1) * 100
    return (pct > 0, f"{sym} {pct:+.1f}% — {'green' if pct > 0 else 'red'} day")


def _check_dip_reversal(watch: dict, today: str, get_bars) -> tuple[bool, str]:
    """Green fresh bar AND the recent low (last `lookback` bars, default 3) tagged <= `level` —
    the "pullback reached the zone, then printed a reversal day" entry trigger. A green bounce
    that never reached the level is NOT a fire (that's the noise this exists to filter)."""
    from webull_api.strategy.bars import to_ohlcv
    sym = str(watch.get("symbol", "")).upper()
    level = watch.get("level")
    if level is None:
        return False, f"{sym}: dip_reversal has no level"
    lookback = max(1, int(watch.get("lookback", 3)))
    bars = to_ohlcv(get_bars(sym, "D", count="10"))
    if len(bars) < 2 or _bar_date(bars[-1]["time"]) != today:
        return False, f"{sym}: no fresh daily bar"
    prev, last = bars[-2]["close"], bars[-1]["close"]
    if not prev:
        return False, f"{sym}: no prior close"
    lows = [b["low"] for b in bars[-lookback:] if b.get("low") is not None]
    dip = min(lows) if lows else None
    pct = (last / prev - 1) * 100
    if dip is None or dip > float(level):
        return False, f"{sym} {pct:+.1f}% — no dip to {level:g} (recent low {dip:g})" \
            if dip is not None else f"{sym}: no low data"
    if pct <= 0:
        return False, f"{sym} {pct:+.1f}% — red day (dip {dip:g} <= {level:g} tagged)"
    return True, f"{sym} {pct:+.1f}% — green reversal after dip {dip:g} <= {level:g}"


def _check_close_above_sma(watch: dict, today: str, get_bars) -> tuple[bool, str]:
    """Fresh close back above the `window`-day SMA (default 20) — the trend-reclaim leg."""
    from webull_api.strategy.bars import to_ohlcv
    sym = str(watch.get("symbol", "")).upper()
    window = max(2, int(watch.get("window", 20)))
    bars = to_ohlcv(get_bars(sym, "D", count=str(window + 10)))
    if not bars or _bar_date(bars[-1]["time"]) != today:
        return False, f"{sym}: no fresh daily bar"
    closes = [b["close"] for b in bars if b.get("close") is not None]
    if len(closes) < window:
        return False, f"{sym}: history too short for SMA{window}"
    sma = sum(closes[-window:]) / window
    last = closes[-1]
    above = last > sma
    return (above, f"{sym} close {last:g} {'>' if above else '<='} SMA{window} {sma:.2f}")


def _check_rsi2_above(watch: dict, today: str, get_bars) -> tuple[bool, str]:
    """Fresh close with RSI(2) above `threshold` (default 70) — the mean-reversion exit band.
    Mirrors rsi2_service._fresh_rsi (same count=30 + same-day guard) so the watch and the
    runner always agree on the value."""
    from webull_api.strategy.bars import to_ohlcv
    from webull_api.strategy.rsi2 import rsi2_of
    sym = str(watch.get("symbol", "")).upper()
    threshold = float(watch.get("threshold", 70.0))
    bars = to_ohlcv(get_bars(sym, "D", count="30"))
    if not bars or _bar_date(bars[-1]["time"]) != today:
        return False, f"{sym}: no fresh daily bar"
    closes = [b["close"] for b in bars if b.get("close") is not None]
    r = rsi2_of(closes)
    if r is None:
        return False, f"{sym}: history too short for RSI(2)"
    above = r > threshold
    return (above, f"{sym} RSI(2) {r:.1f} {'>' if above else '<='} {threshold:g}"
                   f" (close {closes[-1]:g})")


def _check_lab_m_reached(watch: dict, read_meta) -> tuple[bool, str]:
    target = int(watch.get("m", 0))
    m = int(read_meta().get("M", 0))
    return (m >= target, f"M {m}/{target}")


def _regime_str(cycle: dict) -> str:
    r = cycle.get("regime") or {}
    return f"{r.get('trend', '?')}/{r.get('vol', '?')}"


def _check_lab_regime_flip(watch: dict, read_cycles) -> tuple[bool, str]:
    baseline = str(watch.get("baseline", ""))
    need = int(watch.get("min_cycles", 1))
    cycles = read_cycles(limit=100)
    if not cycles:
        return False, "no lab cycles yet"
    streak = 0
    for c in reversed(cycles):
        if _regime_str(c) == baseline:
            break
        streak += 1
    current = _regime_str(cycles[-1])
    if streak >= need:
        return True, f"regime {current} ×{streak} (left {baseline})"
    if streak:
        return False, f"regime {current} ×{streak} (need {need} off {baseline})"
    return False, f"regime {baseline} holds"


def _check_autopilot_placed(watch: dict, today: str, read_autopilot_day) -> tuple[bool, str]:
    from datetime import date, timedelta
    lookback = max(1, int(watch.get("lookback_days", 2)))
    need = max(1, int(watch.get("min", 1)))
    y, m, d = (int(x) for x in today[:10].split("-"))
    days = [(date(y, m, d) - timedelta(days=i)).isoformat() for i in range(lookback)]
    placed = {dy: int((read_autopilot_day(dy) or {}).get("placed", 0) or 0) for dy in days}
    total = sum(placed.values())
    if total >= need:
        hits = " · ".join(f"{dy}: {n} placed" for dy, n in placed.items() if n)
        return True, f"{total} real order(s) placed ({hits})"
    return False, f"no real orders placed in the last {lookback} day(s)"


def _check_proof_bar_met(watch: dict, read_north_star) -> tuple[bool, str]:
    # Thresholds are NOT duplicated here (and deliberately not overridable per-decision):
    # the paper stats feed the canonical north_star.proof_bar, the same bar the scorecard
    # and go-live readiness use.
    from webull_api import north_star
    paper = (read_north_star() or {}).get("paper") or {}
    if paper.get("chip") == "unknown":
        return False, "proof-bar stats unavailable"
    pb = north_star.proof_bar(int(paper.get("decisions", 0) or 0),
                              float(paper.get("expectancy", 0.0) or 0.0),
                              float(paper.get("win_rate", 0.0) or 0.0),
                              expectancy_pct=paper.get("expectancy_pct"))
    pct = pb.get("expectancy_pct")
    exp_txt = (f"{float(pct):+.1f}%/trade (${pb['expectancy']:.2f})" if pct is not None
               else f"${pb['expectancy']:.2f}/trade")
    detail = (f"proof bar {pb['sample']}/{pb['sample_target']} · expectancy {exp_txt}"
              f" · edge {'met' if pb['edge_met'] else 'not met'}")
    return bool(pb["machine_met"]), detail


def _default_north_star() -> dict:
    from . import north_star_service
    return north_star_service.build(None)


def evaluate(watch: dict, *, today: str, get_bars=None,
             read_meta=None, read_cycles=None, read_autopilot_day=None,
             read_north_star=None) -> tuple[bool, str]:
    # Late-bound defaults: resolving the readers at CALL time keeps them monkeypatchable and
    # means importing this module can never capture a stale reference.
    get_bars = get_bars if get_bars is not None else market_data.get_bars
    read_meta = read_meta if read_meta is not None else lab_store.read_meta
    read_cycles = read_cycles if read_cycles is not None else lab_store.read_cycles
    if read_autopilot_day is None:
        from webull_api.autopilot import status as autopilot_status
        read_autopilot_day = autopilot_status.today_decisions
    read_north_star = read_north_star if read_north_star is not None else _default_north_star
    kind = str((watch or {}).get("type", ""))
    if kind == "green_day":
        return _check_green_day(watch, today, get_bars)
    if kind == "dip_reversal":
        return _check_dip_reversal(watch, today, get_bars)
    if kind == "close_above_sma":
        return _check_close_above_sma(watch, today, get_bars)
    if kind == "rsi2_above":
        return _check_rsi2_above(watch, today, get_bars)
    if kind == "lab_m_reached":
        return _check_lab_m_reached(watch, read_meta)
    if kind == "lab_regime_flip":
        return _check_lab_regime_flip(watch, read_cycles)
    if kind == "autopilot_placed":
        return _check_autopilot_placed(watch, today, read_autopilot_day)
    if kind == "proof_bar_met":
        return _check_proof_bar_met(watch, read_north_star)
    if kind == "any":
        results = [evaluate(w, today=today, get_bars=get_bars, read_meta=read_meta,
                            read_cycles=read_cycles, read_autopilot_day=read_autopilot_day,
                            read_north_star=read_north_star)
                   for w in watch.get("of", [])]
        if not results:
            return False, "manual — no machine check"
        fired = any(f for f, _ in results)
        return fired, " · ".join(d for _, d in results)
    return False, "manual — no machine check"


def _safe_fills() -> list:
    try:
        from . import journal_store
        return list(journal_store.load_fills())
    except Exception:
        return []


def _real_flat(symbol: str, fills: list) -> bool:
    """True when the REAL book has traded `symbol` and its net quantity is now zero.
    Paper fills never count; an untraded symbol is not "flat" (nothing to resolve)."""
    net, seen = 0.0, False
    for f in fills:
        d = f.model_dump() if hasattr(f, "model_dump") else dict(f)
        if d.get("source") != "real" or str(d.get("symbol") or "").upper() != symbol.upper():
            continue
        seen = True
        try:
            q = float(d.get("quantity") or 0)
        except (TypeError, ValueError):
            q = 0.0
        net += q if str(d.get("side") or "").upper() == "BUY" else -q
    return seen and abs(net) < 1e-9


def view() -> list[dict]:
    """Current ledger state for the API — disk-only, no evaluation, no writes."""
    return decisions_store.fold(decisions_store.load())


def evening_check(today: str, iso: str) -> list[dict]:
    """Evaluate every OPEN decision's watch and append check rows. Returns the note view:
    [{id, title, fired, detail, decision, manual}, ...] for open decisions only. A fired trigger
    is carried to the phone by the manager's note's ATTENTION section (2026-09-21: the separate
    per-trigger push was folded into the note -- one evening push, not three), and it re-appears
    every night the condition holds until the decision is resolved: a deliberate nag."""
    folded = decisions_store.fold(decisions_store.load())
    checks: list[dict] = []
    note_view: list[dict] = []
    fills = _safe_fills()
    resolved: list[dict] = []
    for d in folded:
        if d.get("status") != "open":
            continue
        watch = d.get("watch")
        if not isinstance(watch, dict):
            # Hand-written rows sometimes carry a free-text `watch` ("check RSI(2)>70 in the
            # evening note..."). That is a note to the owner, not a machine check -- treat it as
            # manual instead of crashing the whole evening check (2026-09-21: three such rows
            # took every decision's nightly check and auto-resolve down with them).
            watch = None
        subject = str((watch or {}).get("symbol") or "")
        if subject and _real_flat(subject, fills):
            # The rule's subject is no longer held: the decision is fulfilled by the book, not by
            # a trigger. Resolve it instead of nagging every night (2026-09-04: the NVDA earnings
            # rule fired for nine evenings after the lot had been sold).
            row = {k: v for k, v in d.items() if k != "last_check"}
            row.update({"kind": "decision", "ts": iso, "status": "resolved",
                        "resolution": "Subject flat", "resolved_at": iso, "date": today,
                        "note": (f"{subject} is flat in the real book — rule fulfilled; "
                                 f"auto-resolved by the evening check {today}.")})
            resolved.append(row)
            continue
        if watch:
            try:
                fired, detail = evaluate(watch, today=today)
            except Exception as e:
                fired, detail = False, f"check unavailable ({type(e).__name__})"
            checks.append({"kind": "check", "id": d["id"], "ts": iso, "date": today,
                           "fired": fired, "detail": detail})
        else:
            fired, detail = False, "manual — no machine check"
        note_view.append({"id": d["id"], "title": d.get("title") or d["id"],
                          "fired": fired, "detail": detail,
                          "decision": str(d.get("decision") or ""), "manual": watch is None})
    if resolved:
        decisions_store.append(resolved)
    if checks:
        decisions_store.append(checks)
    return note_view
