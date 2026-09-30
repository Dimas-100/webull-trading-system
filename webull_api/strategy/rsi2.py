"""Pure RSI2 mean-reversion decision engine. Primitive-in / list-of-decisions-out; no I/O, no market
data, no orders. All strategy thresholds live here so they are unit-tested. Ported from the retired
hand-maintained rsi2_paper_state.json rules. A decision is
{action, symbol, quantity, rsi2, reason}."""
from __future__ import annotations

from dataclasses import dataclass
from math import floor, isfinite

from .indicators import rsi

# Deliberate RSI(2) mean-reversion basket (2026-07-12, widened 13→20 with owner sign-off
# 2026-07-28; specs: docs/superpowers/specs/2026-07-12-claude-sourced-scanner-universes-design.md
# + docs/reviews/2026-07-28-strategy-red-team.md and the 2026-07-28 rsi2-backtest report).
# Criteria: top-tier dollar liquidity, multi-year structural uptrend (dips get systematically
# bought), no binary-gap character, sector spread; broad ETFs are the canonical instruments.
# Dropped from the old hand-ledger port: RIVN/GRAB (no dip-buying base), VZ (dead money),
# MELI (gappy, $1,700+ ~= 30% of one $6k signal); LLY dropped 2026-07-28 (red-team: binary
# trial-readout gap character, the backtest's lone negative name). The 2026-07-28 adds
# (AVGO CAT SMH META ABBV WMT DIA JNJ) were each vetted +EV via backtest_rsi2 --universe;
# HD/NFLX vetted negative and MA/RSP weak — rejected. META's stale manual-hold exclusion was
# resolved with the widening (position closed 2026-07-15; owner-approved). GOOG (not GOOGL)
# kept deliberately — the ledger already holds GOOG lots. Held lots of dropped names still
# exit normally: the runner fetches universe ∪ owned and the engine exits over owned_lots.
# Widened 20→28 on 2026-08-07 with owner sign-off (+ANET +KLAC +LRCX +GS +ETN +RTX +GE +AXP);
# vetting report: docs/reviews/2026-08-07-rsi2-universe-expansion-vetting.md. PURPOSE IS
# THROUGHPUT, NOT EDGE: the proof bar was accumulating ~0.8 decisions/day in August (vs 2.2 in
# July) because RSI(2)<10 starves in an extended tape; ~40% more names means ~40% more decisions.
# NO entry criterion was touched. Bar applied: >= +1.20%/trade over 1,200 daily bars (incl. the
# 2022 bear), POSITIVE IN BOTH SPY regimes (kills one-regime flukes), above its own 200SMA, liquid
# options chain, and sector spread. AMAT/AMD/MU cleared the EV bar but were rejected to avoid
# stacking semis on top of NVDA/AVGO/SMH (MU is also risk-off negative). The healthcare/staples/
# energy block (MRK AMGN ISRG TJX KO PG XOM CVX) all came in under the base rate — rejected.
# HONEST LIMIT: 58 names were screened and the top 8 taken, so the measured +1.2..+2.4%/trade is
# selection-biased on ~50-trade samples; expect regression toward the ~+1.07% universe base rate.
UNIVERSE = ("AAPL", "MSFT", "NVDA", "AMZN", "GOOG", "META", "AVGO", "BRK B", "JPM", "V",
            "COST", "WMT", "CAT", "ABBV", "JNJ", "VOO", "QQQ", "IWM", "DIA", "SMH",
            "ANET", "KLAC", "LRCX", "GS", "ETN", "RTX", "GE", "AXP")
# F and SW were closed by 07-20; only AVUV is still held. Corrected 2026-08-15 (was stale
# ("F", "AVUV", "SW") — see docs/ROADMAP.md #8 journal-integrity follow-ups). Neither F nor SW
# is in UNIVERSE, so this was a no-op on live behavior; it only misdescribed the book.
EXCLUDED_MANUAL_HOLDS = ("AVUV",)


@dataclass(frozen=True)
class Rsi2Config:
    universe: tuple = UNIVERSE
    excluded: tuple = EXCLUDED_MANUAL_HOLDS
    entry_below: float = 10.0
    exit_above: float = 70.0
    dollars_per_signal: float = 6000.0
    max_lots: int = 6
    cash_floor: float = 20000.0


DEFAULT_CONFIG = Rsi2Config()


def rsi2_of(closes) -> float | None:
    """Latest RSI(2) over a close series (oldest-first), or None during warmup / on short input."""
    series = rsi(list(closes), 2)
    return series[-1] if series and series[-1] is not None else None


def decide(*, owned_lots, cash, rsi_by_symbol, price_by_symbol, cfg=DEFAULT_CONFIG) -> list[dict]:
    decisions: list[dict] = []
    owned_syms = {l["symbol"] for l in owned_lots}

    # Exits first: an owned lot whose RSI(2) crossed the exit band.
    exit_syms = set()
    for lot in owned_lots:
        sym = lot["symbol"]
        r = rsi_by_symbol.get(sym)
        if r is not None and r > cfg.exit_above:
            decisions.append({"action": "SELL", "symbol": sym, "quantity": float(lot["shares"]),
                              "rsi2": r, "reason": f"RSI(2)={r:.2f} > {cfg.exit_above:g} exit"})
            exit_syms.add(sym)

    # Cash + slots available AFTER the exits fill (exits happen before entries in the same pass).
    avail_cash = float(cash)
    for d in decisions:
        px = price_by_symbol.get(d["symbol"])
        if px is not None:
            avail_cash += d["quantity"] * px
    lots_after_exits = len([l for l in owned_lots if l["symbol"] not in exit_syms])
    slots = cfg.max_lots - lots_after_exits

    # Entry candidates: universe minus owned minus excluded, RSI(2) below the entry band.
    candidates = []
    for sym in cfg.universe:
        if sym in owned_syms or sym in cfg.excluded:
            continue
        r = rsi_by_symbol.get(sym)
        px = price_by_symbol.get(sym)
        if r is None or px is None:
            continue
        if r < cfg.entry_below:
            candidates.append((r, sym, px))
    candidates.sort(key=lambda t: t[0])  # most oversold first

    for r, sym, px in candidates:
        if slots <= 0:
            break
        shares = floor(cfg.dollars_per_signal / px) if px > 0 else 0
        if shares <= 0:
            continue
        cost = shares * px
        if avail_cash - cost < cfg.cash_floor:
            continue  # would breach the cash floor
        decisions.append({"action": "BUY", "symbol": sym, "quantity": float(shares),
                          "rsi2": r, "reason": f"RSI(2)={r:.2f} < {cfg.entry_below:g} entry"})
        avail_cash -= cost
        slots -= 1

    return decisions


def real_config(*, dollars: float | None, max_lots: int) -> Rsi2Config:
    """Sizing config for the REAL book. Entry/exit thresholds and the universe are shared with
    paper deliberately — only the money changes. `dollars=None` means 'spend whatever settled
    cash allows', encoded as infinity so the per-name budget is settled cash itself."""
    return Rsi2Config(dollars_per_signal=float("inf") if dollars is None else float(dollars),
                      max_lots=int(max_lots), cash_floor=0.0)


def real_budget(*, net_liq: float | None, divisor: float | None, dollars: float | None,
                cap: float | None) -> tuple[float | None, str]:
    """Resolve the real sleeve's per-signal dollars (spec 2026-09-23 §2). Pure.

    divisor None -> (dollars, "unset"): the pre-divisor path, byte for byte (None still means
    'spend whatever settled cash allows').
    divisor set  -> min(net_liq / divisor, cap, dollars) — the binding term is named "cap" /
    "dollars" / "divisor"; ties resolve toward the term that needs an owner action, in that order
    (a cap tie says 'raise the cap', a dollars tie says 'delete the leftover DOLLARS line').
    Fail closed: divisor <= 0 or non-finite -> (None, "divisor"); net_liq missing, non-finite or
    <= 0 -> (None, "net_liq"). Callers must queue NOTHING on those — never widen to settled cash.
    """
    if divisor is None:
        return dollars, "unset"
    try:
        d = float(divisor)
    except (TypeError, ValueError):
        return None, "divisor"
    if not isfinite(d) or d <= 0:
        return None, "divisor"
    try:
        nl = None if net_liq is None else float(net_liq)
    except (TypeError, ValueError):
        return None, "net_liq"
    if nl is None or not isfinite(nl) or nl <= 0:
        return None, "net_liq"
    candidates: list[tuple[str, float]] = []
    if cap is not None:
        candidates.append(("cap", float(cap)))
    if dollars is not None:
        candidates.append(("dollars", float(dollars)))
    candidates.append(("divisor", nl / d))
    basis, value = min(candidates, key=lambda t: t[1])     # min() keeps the FIRST minimum -> tie order
    return value, basis


def _real_candidates(owned_lots, rsi_by_symbol, price_by_symbol, cfg):
    """(rsi, symbol, price) for entry-eligible names, most oversold first. Pure."""
    owned = {l["symbol"] for l in owned_lots}
    out = []
    for sym in cfg.universe:
        if sym in owned or sym in cfg.excluded:
            continue
        r, px = rsi_by_symbol.get(sym), price_by_symbol.get(sym)
        if r is None or px is None or px <= 0:
            continue
        if r < cfg.entry_below:
            out.append((r, sym, px))
    out.sort(key=lambda t: t[0])
    return out


def _budget(settled_cash: float, cfg: Rsi2Config) -> float:
    return min(float(settled_cash), cfg.dollars_per_signal)


def decide_entries_cash(*, owned_lots, settled_cash, rsi_by_symbol, price_by_symbol,
                        cfg=DEFAULT_CONFIG) -> list[dict]:
    """BUY decisions for the real book, spending ONLY cash on hand.

    Unlike `decide`, an owned lot above the exit band frees neither cash nor a slot: its exit is
    a standing queued row that may not have fired, and spending its proceeds early would place an
    order the account cannot fund. Whole shares only — a fractional lot cannot carry a resting
    protective stop."""
    slots = cfg.max_lots - len(owned_lots)
    if slots <= 0:
        return []
    cash = float(settled_cash)
    decisions: list[dict] = []
    for r, sym, px in _real_candidates(owned_lots, rsi_by_symbol, price_by_symbol, cfg):
        if slots <= 0:
            break
        shares = floor(_budget(cash, cfg) / px)
        if shares <= 0:
            continue
        decisions.append({"action": "BUY", "symbol": sym, "quantity": float(shares),
                          "rsi2": r, "reason": f"RSI(2)={r:.2f} < {cfg.entry_below:g} entry"})
        cash -= shares * px
        slots -= 1
    return decisions


def unaffordable(*, owned_lots, settled_cash, rsi_by_symbol, price_by_symbol,
                 cfg=DEFAULT_CONFIG) -> list[dict]:
    """Entry signals skipped purely because one whole share costs more than the budget.

    Logged so the proof-bar read never mistakes 'could not afford it' for 'no signal' — the
    false-scarcity trap that made options capacity look signal-starved in July 2026.

    A slot-full skip is a DIFFERENT thing and must not be reported here: with no open slot the
    engine would have declined every candidate regardless of price, so calling those 'unaffordable'
    poisons the very distinction this function exists to protect."""
    if cfg.max_lots - len(owned_lots) <= 0:
        return []
    out = []
    for r, sym, px in _real_candidates(owned_lots, rsi_by_symbol, price_by_symbol, cfg):
        if floor(_budget(float(settled_cash), cfg) / px) <= 0:
            out.append({"symbol": sym, "price": px, "rsi2": r})
    return out
