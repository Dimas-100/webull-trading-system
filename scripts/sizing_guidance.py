"""Real-book sizing guidance (spec 2026-09-11 pool-shadow design §3): prints the S6 affordability
picture at live Tiingo closes so the owner's written rule (`playbook/sizing-rule.md`) is checkable
in ten seconds. Read-only -- no order path, no broker call, no network beyond what the Tiingo
store already has on disk; the only reads are `webull_api.tiingo.store` and `webull_web.netliq_store`.

    .venv/Scripts/python.exe scripts/sizing_guidance.py [--equity N]

`--equity` defaults to the latest real net liq from `netliq_store` (the most recent dated row with
a non-null "real" field), falling back to $1,600.00 when the store is empty or unreadable.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from webull_api.hold_session.candidates import IBS_ETF_UNIVERSE          # noqa: E402
from webull_api.pool import affordability                                # noqa: E402
from webull_api.strategy.rsi2 import UNIVERSE                            # noqa: E402
from webull_api.tiingo import store                                      # noqa: E402

GRID_EQUITIES = (1_600.0, 3_200.0, 5_000.0, 10_000.0, 25_000.0)
DIVISORS = (6, 4)
FALLBACK_EQUITY = 1_600.0


# ---------------------------------------------------------------- I/O (not exercised by tests)

def default_equity() -> tuple[float, str]:
    """The latest real net liq from `netliq_store`, walking back from the newest row for the first
    non-null "real" -- a broker outage records null on the newest day without erasing an earlier
    real value. Falls back to $1,600.00, undated, on any failure or an empty/all-null store."""
    try:
        from webull_web import netliq_store
        for row in reversed(netliq_store.load()):
            real = row.get("real")
            if real is not None:
                return float(real), f"latest real net liq ({row.get('date', '?')})"
    except Exception:
        pass
    return FALLBACK_EQUITY, "fallback (no real net liq on record)"


def _last_closes(symbols) -> dict[str, float]:
    out: dict[str, float] = {}
    for sym in symbols:
        rows = store.read(sym)
        if not rows:
            continue
        close = rows[-1].get("close")
        if close is not None:
            out[sym] = float(close)
    return out


def live_prices() -> tuple[dict[str, float], dict[str, float]]:
    """(swing prices, ETF prices) -- last Tiingo close per symbol, disjoint universes (asserted at
    `hold_session.candidates` import time), missing/unpriced names simply absent."""
    return _last_closes(UNIVERSE), _last_closes(IBS_ETF_UNIVERSE)


# ---------------------------------------------------------------- pure formatting (tested directly)

def _money(v: float) -> str:
    return f"${v:,.2f}"


def format_affordable_line(prices: dict[str, float], equity: float, divisor: int, label: str) -> str:
    """One line: which of `prices` a share fits at equity/divisor, and how many shares. Pure --
    the guidance-list half of the report, and the seam the test exercises on stubbed prices."""
    afford = affordability.affordable(prices, equity, divisor)
    slot = equity / divisor
    names = ", ".join(f"{sym} {n}sh" for sym, n in sorted(afford.items()))
    return (f"  {label} — slot {_money(slot)}, {len(afford)}/{len(prices)} afford a share: "
            f"{names or 'none'}")


def format_grid(prices: dict[str, float], label: str, equities=GRID_EQUITIES, divisors=DIVISORS) -> list[str]:
    """The fixed-equity grid for one universe, one line per equity: "1,600 -> /6 8/28 · /4 17/28"."""
    rows = affordability.table(prices, list(equities), divisors)
    lines = [f"  {label} (of {rows[0]['total'] if rows else len(prices)}):"]
    for row in rows:
        cells = " · ".join(f"/{d} {row['by_divisor'][d]['affordable']}/{row['total']}"
                            for d in divisors)
        lines.append(f"    {_money(row['equity']):>12}  {cells}")
    return lines


def format_report(swing_prices: dict[str, float], etf_prices: dict[str, float], equity: float,
                   equity_source: str) -> str:
    """The whole printed report -- pure, given already-fetched price dicts and a resolved equity.
    Never touches the network or the Tiingo store; this is what `tests/pool/test_guidance_script.py`
    calls directly on a stubbed price dict."""
    lines = [f"Real-book sizing guidance — equity {_money(equity)} ({equity_source})", ""]
    lines.append(f"Affordable today at equity/6 and equity/4 ({_money(equity)}):")
    for divisor in DIVISORS:
        lines.append(format_affordable_line(swing_prices, equity, divisor, "swing"))
        lines.append(format_affordable_line(etf_prices, equity, divisor, "ETF"))
    lines.append("")
    lines.append(f"Affordability grid — {', '.join(_money(e) for e in GRID_EQUITIES)}:")
    lines.extend(format_grid(swing_prices, "swing"))
    lines.extend(format_grid(etf_prices, "ETF"))
    lines.append("")
    lines.append("Rule: playbook/sizing-rule.md. Autopilot cap WEBULL_AUTOPILOT_MAX_NOTIONAL stays "
                  "put until equity/6 exceeds it; no code change either way.")
    return "\n".join(lines)


# ---------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    # Force UTF-8 so the em dashes / middle dots never crash a redirected/cp1252 stdout (see lab_status.py).
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--equity", type=float, default=None,
                        help="override the equity used to size (default: latest real net liq, else $1,600)")
    args = parser.parse_args(argv)

    if args.equity is not None:
        equity, equity_source = args.equity, "given via --equity"
    else:
        equity, equity_source = default_equity()

    swing_prices, etf_prices = live_prices()
    print(format_report(swing_prices, etf_prices, equity, equity_source))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
