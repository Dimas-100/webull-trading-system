"""Exit-band sensitivity for the production RSI2 config: exit RSI(2)>70 vs >80 vs >90.

  .venv/Scripts/python.exe scripts/backtest_exit_bands.py

Agenda item (f) of combined-red-team-lab-review-aug2026 (owner-directed 2026-08-19, motivated
by the 08-18 AAPL exit at $310.01 that ran to ~$317 next day). Fetches bars ONCE and replays
the identical data under each exit band — everything else (entry<10, sizing, slots, cash
floor, next-open fills) stays the shipped config. Read-only: writes a report to docs/reviews/,
never touches runner state. The live band stays 70 until the review rules with owner sign-off.
"""
from __future__ import annotations

import json
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from webull_api.strategy import rsi2  # noqa: E402
from webull_api.strategy.rsi2_replay import replay  # noqa: E402

from backtest_rsi2 import _rows  # noqa: E402  (same bar normalization as the canonical script)

BANDS = (70.0, 80.0, 90.0)
COUNT = "1200"


def run() -> int:
    from webull_mcp.env import load_repo_env
    load_repo_env()
    from webull_api import market_data

    universe = rsi2.DEFAULT_CONFIG.universe
    bars_by, failed = {}, []
    for sym in universe:
        try:
            rows = _rows(market_data.get_bars(sym, "D", count=COUNT))
        except Exception as e:
            failed.append(f"{sym}: {type(e).__name__}")
            continue
        if rows:
            bars_by[sym] = rows
    spy = None
    for attempt in (str(int(COUNT) + 250), COUNT):
        try:
            spy = _rows(market_data.get_bars("SPY", "D", count=attempt))
            break
        except Exception:
            continue
    if not spy or not bars_by:
        print("FATAL: no usable bars.")
        return 1

    results = {}
    for band in BANDS:
        cfg = replace(rsi2.Rsi2Config(universe=universe, excluded=()), exit_above=band)
        res = replay(bars_by, spy, cfg=cfg, starting_cash=100_000.0)
        o = res.stats["overall"]
        worst = min((t.return_pct for t in res.trades), default=0.0)
        best = max((t.return_pct for t in res.trades), default=0.0)
        results[band] = {
            "trades": o["trades"], "win_rate": o["win_rate"],
            "expectancy_usd": o["expectancy"], "expectancy_pct": o["expectancy_pct"],
            "avg_hold_days": o["avg_hold_days"],
            "total_return_pct": res.stats["total_return_pct"],
            "max_drawdown_pct": res.stats["max_drawdown_pct"],
            "worst_trade_pct": worst, "best_trade_pct": best,
        }

    today = datetime.now().date().isoformat()
    out = Path("docs/reviews") / f"{today}-rsi2-exit-band-sensitivity.md"
    hdr = ("| exit band | trades | win | expectancy $ | expectancy % | avg hold | total ret | "
           "max DD | worst | best |")
    sep = "|---|---|---|---|---|---|---|---|---|---|"
    rows_md = [
        f"| >{b:g} | {r['trades']} | {r['win_rate']:.0%} | ${r['expectancy_usd']:,.2f} | "
        f"{r['expectancy_pct']:+.2f}% | {r['avg_hold_days']:.1f}d | {r['total_return_pct']:+.2f}% | "
        f"{r['max_drawdown_pct']:.2f}% | {r['worst_trade_pct']:+.2f}% | {r['best_trade_pct']:+.2f}% |"
        for b, r in results.items()
    ]
    lines = [
        f"# RSI2 exit-band sensitivity (70 vs 80 vs 90) — {today}",
        "",
        "Input for combined-red-team-lab-review-aug2026 agenda item (f) (owner-directed "
        "2026-08-19). Identical bars, identical entry band (<10), shipped sizing — ONLY "
        "`exit_above` varies. Same data caveats as the canonical backtest (selection-biased "
        "universe, price-only bars, zero costs, next-open fills).",
        "",
        f"Universe: {len(bars_by)} symbols with bars"
        + (f" (failed: {', '.join(failed)})" if failed else "") + f" · {COUNT} bars requested.",
        "",
        hdr, sep, *rows_md,
        "",
        "Ruling discipline: the live band stays 70 until the 08-29/30 review rules, with owner "
        "sign-off. This artifact is evidence, not a change.",
    ]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (Path("docs/reviews") / f"{today}-rsi2-exit-band-sensitivity.json").write_text(
        json.dumps({"generated": today, "bands": {str(k): v for k, v in results.items()},
                    "universe": list(bars_by), "failed": failed}, indent=2), encoding="utf-8")
    print(f"Wrote {out}")
    for b, r in results.items():
        print(f"exit>{b:g}: {r['trades']} trades, win {r['win_rate']:.0%}, "
              f"{r['expectancy_pct']:+.2f}%/trade (${r['expectancy_usd']:,.2f}), "
              f"hold {r['avg_hold_days']:.1f}d, ret {r['total_return_pct']:+.2f}%, "
              f"DD {r['max_drawdown_pct']:.2f}%, worst {r['worst_trade_pct']:+.2f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
