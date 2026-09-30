"""Pure helpers for the options chain: OCC symbol synthesis + invalid-symbol pruning.

No network in the pure section. The chain is synthesized (this SDK has no chain-enumeration
endpoint) and pruned to real contracts via the snapshot API's all-or-nothing invalid-symbol
error. See docs/superpowers/specs/2026-06-18-options-page-chain-design.md.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

_INVALID_RE = re.compile(r"Invalid Symbol:\[([^\]]*)\]")
_OCC_RE = re.compile(r"^([A-Z]+)(\d{6})([CP])(\d{8})$")


def occ_root(symbol: str) -> str:
    """Underlying symbol -> OCC root: strip spaces and dots (real OCC symbology has neither,
    e.g. Berkshire class B is root 'BRKB', not 'BRK B' or 'BRK.B'). Exposed (not private) so any
    caller that later needs to re-associate a parsed OCC root with a universe/position symbol can
    apply the same transform — e.g. occ_root('BRK B') == parse_occ(build_occ('BRK B', ...))['root'].
    """
    return re.sub(r"[ .]", "", symbol.upper())


def fetch_symbol(root_or_symbol: str, universe=None) -> str:
    """OCC-root form -> the market-data-fetchable spelling ('BRKB' -> 'BRK B'); identity when no
    universe entry matches. The inverse of occ_root for FETCHES: quotes/bars endpoints want the
    listing spelling with the space (live-verified 2026-08-15: get_bars('BRKB') is
    INVALID_SYMBOL, get_bars('BRK B') works), while OptionLeg.underlying stores the root."""
    if universe is None:
        from webull_api.strategy.rsi2 import UNIVERSE as universe
    want = occ_root(root_or_symbol)
    for u in universe:
        if occ_root(u) == want:
            return u
    return root_or_symbol


def build_occ(root: str, exp: date, kind: str, strike: float) -> str:
    return f"{occ_root(root)}{exp:%y%m%d}{kind.upper()}{int(round(strike * 1000)):08d}"


def parse_occ(symbol: str) -> dict:
    m = _OCC_RE.match(symbol.upper())
    if not m:
        raise ValueError(f"bad OCC symbol: {symbol!r}")
    root, ymd, kind, strike = m.groups()
    exp = date(2000 + int(ymd[:2]), int(ymd[2:4]), int(ymd[4:6]))
    return {"root": root, "exp": exp, "kind": kind, "strike": int(strike) / 1000}


def default_spacing(spot: float) -> float:
    """Tiered strike-spacing guess. An over-fine guess is safe (invalids get pruned); the only
    cost of a too-coarse guess is missing tighter strikes (a documented completeness limit)."""
    if spot < 25:
        return 1
    if spot < 100:
        return 2.5
    if spot < 500:
        return 5
    return 10


def strike_grid(spot: float, width: int, spacing: float) -> list[float]:
    atm = round(spot / spacing) * spacing
    return [round(atm + i * spacing, 2) for i in range(-width, width + 1) if atm + i * spacing > 0]


def _third_friday(year: int, month: int) -> date:
    d = date(year, month, 1)
    first_friday = d + timedelta(days=(4 - d.weekday()) % 7)
    return first_friday + timedelta(days=14)


def standard_expirations(today: date, weeklies: int = 4, months: int = 6) -> list[date]:
    out: set[date] = set()
    nxt = today + timedelta(days=(4 - today.weekday()) % 7)
    if nxt <= today:
        nxt += timedelta(days=7)
    for i in range(weeklies):
        out.add(nxt + timedelta(days=7 * i))
    y, m = today.year, today.month
    for _ in range(months):
        tf = _third_friday(y, m)
        if tf > today:
            out.add(tf)
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return sorted(out)


def parse_invalid_symbols(msg: str) -> set[str]:
    m = _INVALID_RE.search(msg or "")
    if not m:
        return set()
    return {s.strip() for s in m.group(1).split(",") if s.strip()}


def pair_by_strike(rows: list[dict]) -> list[dict]:
    by_strike: dict[float, dict] = {}
    for r in rows:
        info = parse_occ(r["symbol"])
        slot = by_strike.setdefault(
            info["strike"], {"strike": info["strike"], "call": None, "put": None}
        )
        slot["call" if info["kind"] == "C" else "put"] = r
    return [by_strike[k] for k in sorted(by_strike)]


# ── Orchestrators (impure: call the snapshot API; injectable for tests) ───────────────

def _snapshot_default():
    from . import options  # lazy: options imports this module (avoid circular import)

    return options.get_option_snapshot


def _query(snapshot_fn, symbols: list[str]) -> tuple[list[dict], set[str]]:
    """One batch (≤20). Returns (rows, invalid). On the all-or-nothing invalid-symbol error,
    returns ([], invalid_set) so the caller can prune and retry the survivors."""
    from .options import InvalidOptionSymbolError

    try:
        return list(snapshot_fn(",".join(symbols))), set()
    except InvalidOptionSymbolError as e:
        return [], e.invalid


def fetch_chain(symbol, exp: date, spot: float, width: int = 12, *, snapshot_fn=None) -> dict:
    """Synthesize a strike grid around ATM, snapshot it in ≤20 batches, prune invalids
    (retry the survivors once), and pair calls/puts by strike. Whatever returns is valid."""
    snapshot_fn = snapshot_fn or _snapshot_default()
    spacing = default_spacing(spot)
    strikes = strike_grid(spot, width, spacing)
    syms = [build_occ(symbol, exp, k, s) for s in strikes for k in ("C", "P")]
    rows: list[dict] = []
    for i in range(0, len(syms), 20):
        chunk = syms[i:i + 20]
        got, invalid = _query(snapshot_fn, chunk)
        if invalid:
            survivors = [s for s in chunk if s not in invalid]
            got, _ = _query(snapshot_fn, survivors) if survivors else ([], set())
        rows.extend(got)
    paired = pair_by_strike(rows)
    atm = round(spot / spacing) * spacing
    for r in paired:
        r["atm"] = abs(r["strike"] - atm) < 1e-9
    return {"symbol": symbol.upper(), "expiration": exp.isoformat(), "spot": spot, "rows": paired}


def _strike_ladder(spot: float, spacings: tuple[float, ...] = (1, 2.5, 5, 10)) -> list[float]:
    """A small, deduped ladder of round-number strike candidates near spot at a few common
    listing granularities. A single guessed strike can miss the real listing (e.g. CAT/COST
    verified live: the $1/$5 guess lands on an unlisted strike and INVALID_SYMBOL prunes an
    otherwise-live expiry) -- widening the guess means one miss doesn't sink the expiry.
    Kept small (<=4 probes/expiry) since this runs nightly under a shared rate-limit quota."""
    return sorted({round(spot / sp) * sp for sp in spacings if round(spot / sp) * sp > 0})


def discover_expirations(symbol, spot: float, today: date, *, snapshot_fn=None) -> list[str]:
    """Probe standard expirations against a strike ladder and keep the live ones.
    A candidate is live if ANY of its probe contracts survives the invalid-symbol prune --
    one missed strike guess must not prune an otherwise-valid expiry."""
    snapshot_fn = snapshot_fn or _snapshot_default()
    probes: dict[str, date] = {}
    for d in standard_expirations(today):
        for strike in _strike_ladder(spot):
            probes[build_occ(symbol, d, "C", strike)] = d
    syms = list(probes)
    valid: set[str] = set()
    for i in range(0, len(syms), 20):
        chunk = syms[i:i + 20]
        _, invalid = _query(snapshot_fn, chunk)
        valid |= {s for s in chunk if s not in invalid}
    return [d.isoformat() for d in sorted({probes[s] for s in valid})]
