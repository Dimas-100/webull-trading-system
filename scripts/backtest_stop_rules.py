"""Stop-rule sensitivity for the production RSI2 config: an 8% backstop and a
breakeven-after-X% rule (owner question 2026-09-08: "I like moving the stop to breakeven as
price rises -- is it in the swing plan? If it's a good idea add it, if not dismiss.") layered
on top of the UNCHANGED entry<10/exit>70 RSI(2) engine via
webull_api.strategy.rsi2_replay.StopRule. Six pre-registered cells replay the IDENTICAL bar
set fetched once: base (no stops -- today's replay, the 20-yr base rate), backstop8 (what the
live autopilot actually does -- protect stage, cost x 0.92), and be1/be2/be3/be5 (backstop8 +
a breakeven arm at 1/2/3/5%, the owner's by-hand habit). Read-only: writes docs/reviews/,
never touches runner state, never places an order.

  .venv/Scripts/python.exe scripts/backtest_stop_rules.py                       # tiingo, 5200 bars
  .venv/Scripts/python.exe scripts/backtest_stop_rules.py --source webull --count 1200

DECISION RULE (pre-registered, printed in the report before the numbers): a breakeven cell is
a candidate rule change only if BOTH (a) its expectancy per trade (%) is >= backstop8's, AND
(b) its max drawdown is no deeper than backstop8's, over the full 20-year window. Otherwise the
idea is dismissed. Adoption still goes through the review with owner sign-off -- this script
only prints CANDIDATE/DISMISS per cell, it never picks one.

NOTE on (b): ``ReplayResult.stats["max_drawdown_pct"]`` is a <=0 percentage (more negative =
deeper). "No deeper than backstop8's" is evaluated as ``candidate_mdd >= backstop8_mdd``
(algebraically) -- equivalently, the candidate's drawdown MAGNITUDE is <= backstop8's
magnitude. A literal signed "<=" on two negative numbers would mark a SHALLOWER (better)
candidate drawdown as failing, which is backwards from the rule's evident intent, so this
script resolves the wording to the magnitude comparison.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from webull_api.strategy import rsi2  # noqa: E402
from webull_api.strategy.bars import to_ohlcv  # noqa: E402
from webull_api.strategy.rsi2_replay import StopRule, replay  # noqa: E402

DECISION_RULE = (
    "A breakeven cell is a candidate rule change only if its expectancy per trade (%) is >= "
    "backstop8's AND its max drawdown is no deeper than backstop8's, over the full 20-year "
    "window. Otherwise the idea is dismissed. Adoption still goes through the review with "
    "owner sign-off."
)

CELLS = (
    ("base", StopRule()),
    ("backstop8", StopRule(backstop_pct=8.0)),
    ("be1", StopRule(backstop_pct=8.0, breakeven_after_pct=1.0)),
    ("be2", StopRule(backstop_pct=8.0, breakeven_after_pct=2.0)),
    ("be3", StopRule(backstop_pct=8.0, breakeven_after_pct=3.0)),
    ("be5", StopRule(backstop_pct=8.0, breakeven_after_pct=5.0)),
)
_JUDGED = ("be1", "be2", "be3", "be5")  # cells the decision rule actually judges


def _bar_date(bar_time) -> str | None:
    """Mirrors rsi2_service._bar_date / backtest_rsi2._bar_date (kept local: scripts don't
    import web services). Not reused from backtest_rsi2 because this script's _rows below
    needs the high/low that backtest_rsi2._rows strips out."""
    s = str(bar_time)
    try:
        if s.isdigit():
            v = int(s)
            if v > 10_000_000_000:
                v //= 1000
            return datetime.utcfromtimestamp(v).date().isoformat()
        return datetime.fromisoformat(s).date().isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def _rows(raw) -> list[dict]:
    """Like backtest_rsi2._rows but KEEPS high/low -- StopRule needs a session's low to
    evaluate a stop hit."""
    out = []
    for b in to_ohlcv(raw):
        d = _bar_date(b["time"])
        if d:
            out.append({"date": d, "open": b["open"], "high": b["high"], "low": b["low"],
                       "close": b["close"]})
    return out


def _cell_row(res) -> dict:
    o = res.stats["overall"]
    worst = min((t.return_pct for t in res.trades), default=0.0)
    best = max((t.return_pct for t in res.trades), default=0.0)
    return {
        "trades": o["trades"], "win_rate": o["win_rate"],
        "expectancy_usd": o["expectancy"], "expectancy_pct": o["expectancy_pct"],
        "avg_hold_days": o["avg_hold_days"],
        "total_return_pct": res.stats["total_return_pct"],
        "max_drawdown_pct": res.stats["max_drawdown_pct"],
        "worst_trade_pct": worst, "best_trade_pct": best,
        "exit_reasons": o["exit_reasons"],
    }


def _verdict(row: dict, backstop8: dict) -> str:
    ok_expectancy = row["expectancy_pct"] >= backstop8["expectancy_pct"]
    ok_drawdown = row["max_drawdown_pct"] >= backstop8["max_drawdown_pct"]  # no deeper -- see NOTE
    return "CANDIDATE" if ok_expectancy and ok_drawdown else "DISMISS"


def run(argv=None, get_bars=None, today=None) -> int:
    ap = argparse.ArgumentParser(
        description="RSI2 stop-rule sensitivity: backstop + breakeven, pre-registered cells.")
    ap.add_argument("--universe", default=None, help="comma-separated symbol override")
    ap.add_argument("--cash", type=float, default=100_000.0)
    ap.add_argument("--count", default="5200", help="daily bars requested per symbol")
    ap.add_argument("--out-dir", default="docs/reviews")
    ap.add_argument("--source", choices=("webull", "tiingo"), default="tiingo",
                     help="bar source: tiingo (offline split-/dividend-adjusted store, no "
                     "depth cap -- default for this script's 20-yr window) or webull (live "
                     "entitled API)")
    args = ap.parse_args(argv)

    if get_bars is None:  # real run: load env, import the bar source lazily
        if args.source == "tiingo":
            from webull_api.tiingo.bars import make_get_bars
            get_bars = make_get_bars()
        else:
            from webull_mcp.env import load_repo_env
            load_repo_env()
            from webull_api import market_data
            get_bars = market_data.get_bars
    today = today or datetime.now().date().isoformat()

    universe = (tuple(s.strip().upper() for s in args.universe.split(",") if s.strip())
                if args.universe else rsi2.DEFAULT_CONFIG.universe)
    cfg = rsi2.Rsi2Config(universe=universe, excluded=())  # shipped bands/sizing/floor

    bars_by, failed = {}, []
    for sym in universe:
        try:
            rows = _rows(get_bars(sym, "D", count=args.count))
        except Exception as e:
            failed.append(f"{sym}: {type(e).__name__}")
            continue
        if rows:
            bars_by[sym] = rows
    # SPY gets ~250 extra sessions so its SMA200 warms up before the universe span starts
    # (same fallback shape as backtest_rsi2.py -- the depth cap only bites the webull source).
    try:
        spy_count = str(int(args.count) + 250)
    except ValueError:
        spy_count = args.count
    spy = None
    for attempt in (spy_count, args.count):
        try:
            spy = _rows(get_bars("SPY", "D", count=attempt))
            break
        except Exception:
            continue
    if not spy or not bars_by:
        print("FATAL: no usable bars.")
        return 1

    # Fetch once, replay six times over the identical bars -- only stop_rule varies per cell.
    rows_by_cell = {name: _cell_row(replay(bars_by, spy, cfg=cfg, starting_cash=args.cash,
                                           stop_rule=stop_rule))
                   for name, stop_rule in CELLS}
    backstop8 = rows_by_cell["backstop8"]
    verdicts = {name: (_verdict(rows_by_cell[name], backstop8) if name in _JUDGED else "")
               for name, _ in CELLS}

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"{today}-rsi2-stop-rules.md"
    js_path = out_dir / f"{today}-rsi2-stop-rules.json"

    def _fmt(name: str) -> str:
        r, v = rows_by_cell[name], verdicts[name]
        reasons = ", ".join(f"{k}:{cnt}" for k, cnt in sorted(r["exit_reasons"].items())) or "-"
        return (f"| {name} | {r['trades']} | {r['win_rate']:.0%} | ${r['expectancy_usd']:,.2f} "
               f"| {r['expectancy_pct']:+.2f}% | {r['avg_hold_days']:.1f}d | "
               f"{r['total_return_pct']:+.2f}% | {r['max_drawdown_pct']:.2f}% | "
               f"{r['worst_trade_pct']:+.2f}% | {r['best_trade_pct']:+.2f}% | {reasons} | "
               f"{v or '-'} |")

    hdr = ("| cell | trades | win | expectancy $ | expectancy % | avg hold | total ret | "
          "max DD | worst | best | exit reasons | verdict |")
    sep = "|---|---|---|---|---|---|---|---|---|---|---|---|"
    rows_md = [_fmt(name) for name, _ in CELLS]

    lines = [
        f"# RSI2 stop-rule sensitivity -- {today}",
        "",
        "Owner question (2026-09-08): \"I like moving the stop to breakeven as price rises -- "
        "is it in the swing plan? If it's a good idea add it, if not dismiss.\" Identical bars, "
        "identical entry<10/exit>70 RSI(2) engine, shipped sizing/slots/cash floor -- ONLY the "
        "stop rule layered on top (webull_api.strategy.rsi2_replay.StopRule) varies per cell.",
        "",
        f"Universe: {len(bars_by)} symbols with bars"
        + (f" (failed: {', '.join(failed)})" if failed else "") + f" · {args.count} bars "
        f"requested · source: {args.source} · ${args.cash:,.0f} starting cash.",
        "",
        "## Decision rule (pre-registered before the numbers below)",
        "",
        DECISION_RULE,
        "",
        hdr, sep, *rows_md,
        "",
        "## Cells",
        "",
        "- `base` -- `StopRule()` -- the replay as it stands (no stops), the 20-yr base rate.",
        "- `backstop8` -- `StopRule(backstop_pct=8.0)` -- what the live autopilot actually "
        "does (protect stage, cost x 0.92).",
        "- `be1` / `be2` / `be3` / `be5` -- `StopRule(backstop_pct=8.0, "
        "breakeven_after_pct=1/2/3/5)` -- the owner's by-hand habit, layered on top of the "
        "live backstop.",
        "",
        "Verdict is blank for `base`/`backstop8` -- the decision rule only judges a breakeven "
        "cell against `backstop8`, never against itself or the no-stop base case.",
    ]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    js_path.write_text(json.dumps({
        "generated": today, "source": args.source, "universe": list(universe),
        "count": args.count, "starting_cash": args.cash,
        "decision_rule": DECISION_RULE,
        "cells": {name: {**rows_by_cell[name], "verdict": verdicts[name]} for name, _ in CELLS},
        "failed": failed,
    }, indent=2), encoding="utf-8")

    print(f"Wrote {md_path} and {js_path}.")
    print(DECISION_RULE)
    for name, _ in CELLS:
        r, v = rows_by_cell[name], verdicts[name]
        print(f"{name}: {r['trades']} trades, win {r['win_rate']:.0%}, "
             f"{r['expectancy_pct']:+.2f}%/trade (${r['expectancy_usd']:,.2f}), "
             f"hold {r['avg_hold_days']:.1f}d, ret {r['total_return_pct']:+.2f}%, "
             f"DD {r['max_drawdown_pct']:.2f}%, worst {r['worst_trade_pct']:+.2f}%"
             + (f" -- {v}" if v else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
