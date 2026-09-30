"""Backtest the EXACT production RSI2 config over Webull daily bars, per SPY regime.

  .venv/Scripts/python.exe scripts/backtest_rsi2.py                 # shipped 13-symbol universe
  .venv/Scripts/python.exe scripts/backtest_rsi2.py --universe AAPL,MSFT   # item-(3) vetting

Read-only: fetches bars, runs webull_api.strategy.rsi2_replay, writes a markdown report +
JSON artifact to docs/reviews/. Never places orders, never touches runner state."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from webull_api.market_data import MarketDataNotEntitledError  # noqa: E402
from webull_api.strategy import rsi2  # noqa: E402
from webull_api.strategy.bars import to_ohlcv  # noqa: E402
from webull_api.strategy.rsi2_replay import replay  # noqa: E402


def _bar_date(bar_time) -> str | None:
    """Mirrors rsi2_service._bar_date (kept local: scripts don't import web services)."""
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
    out = []
    for b in to_ohlcv(raw):
        d = _bar_date(b["time"])
        if d:
            out.append({"date": d, "open": b["open"], "close": b["close"]})
    return out


def _fmt_block(name: str, b: dict) -> str:
    return (f"| {name} | {b['trades']} | {b['win_rate']:.0%} | ${b['expectancy']:,.2f} "
            f"| {b['expectancy_pct']:+.2f}% | {b['avg_hold_days']:.1f}d |")


def run(argv=None, get_bars=None, today=None) -> int:
    ap = argparse.ArgumentParser(description="Replay the exact production RSI2 config.")
    ap.add_argument("--universe", default=None, help="comma-separated symbol override")
    ap.add_argument("--cash", type=float, default=100_000.0)
    ap.add_argument("--count", default="1200", help="daily bars requested per symbol")
    ap.add_argument("--out-dir", default="docs/reviews")
    ap.add_argument("--source", choices=("webull", "tiingo"), default="webull",
                     help="bar source: webull (live entitled API) or tiingo (offline "
                     "split-/dividend-adjusted CSV store, no depth cap)")
    args = ap.parse_args(argv)

    if get_bars is None:  # real run: load env, import the entitled client lazily
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

    bars_by, spans, failed = {}, {}, []
    for sym in universe:
        try:
            rows = _rows(get_bars(sym, "D", count=args.count))
        except MarketDataNotEntitledError:
            raise  # entitlement outage is account-wide, not a per-symbol degrade (spec)
        except Exception as e:
            failed.append(f"{sym}: {type(e).__name__}")
            continue
        if rows:
            bars_by[sym] = rows
            spans[sym] = (rows[0]["date"], rows[-1]["date"], len(rows))
    # SPY gets ~250 extra sessions so its SMA200 warms up BEFORE the universe span starts —
    # otherwise the span's first ~10 months are regime-blind. Webull rejects counts above its
    # depth cap (ServerException at 1450, verified 2026-07-27), so fall back to the standard
    # count; the warmup row below stays as the honest fallback for the capped case.
    try:
        spy_count = str(int(args.count) + 250)
    except ValueError:
        spy_count = args.count
    spy = None
    for attempt in (spy_count, args.count):
        try:
            spy = _rows(get_bars("SPY", "D", count=attempt))
            break
        except MarketDataNotEntitledError:
            raise
        except Exception as e:
            spy_err = type(e).__name__
    if spy is None:
        print(f"FATAL: SPY bars unavailable ({spy_err}) - no regime tags, no benchmark.")
        return 1
    if not spy or not bars_by:
        print("FATAL: no usable bars (SPY or universe empty).")
        return 1

    res = replay(bars_by, spy, cfg=cfg, starting_cash=args.cash)
    s = res.stats

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    # tiingo runs get their own file names so a same-day webull run never clobbers (or is
    # clobbered by) a same-day tiingo run; the webull path keeps the original names exactly.
    name_suffix = "-tiingo" if args.source == "tiingo" else ""
    md_path = out_dir / f"{today}-rsi2-backtest{name_suffix}.md"
    js_path = out_dir / f"{today}-rsi2-backtest{name_suffix}.json"

    spy_bh = (f"{s['spy_buy_hold_pct']:+.2f}%" if s["spy_buy_hold_pct"] is not None else "n/a")
    unfilled = s.get("unfilled_decisions", 0)
    unfilled_note = f" · unsettled decisions at end: {unfilled}" if unfilled else ""

    if args.source == "tiingo":
        source_note = " Source: tiingo (offline, split-/dividend-adjusted bars, no depth cap)."
        price_data_bullet = (
            "- **Dividend-adjusted data**: Tiingo bars are split- and dividend-adjusted, so "
            "per-trade returns include dividends and ex-div gaps do not trigger entries."
        )
        span_bullet = (
            "- Span is the requested `--count` bars from the Tiingo store — a chosen window."
        )
    else:
        source_note = ""
        price_data_bullet = (
            "- **Price-only data**: Webull daily bars are split-adjusted but not dividend-adjusted. "
            "Per-trade returns exclude dividends over ex-dates (a small conservative drag for "
            "~5-day holds), ex-div gaps can trigger entries on mechanical drops, and the SPY "
            "buy-and-hold benchmark is price-only (its total-return figure would be higher)."
        )
        span_bullet = (
            "- Span is whatever Webull's history depth returns per symbol (table above) — not a "
            "chosen window."
        )

    lines = [
        f"# RSI2 exact-config backtest — {today}",
        "",
        f"Config: universe {len(universe)} symbols · RSI(2)<{cfg.entry_below:g} entry / "
        f">{cfg.exit_above:g} exit (trailing-30-bar RSI) · ${cfg.dollars_per_signal:,.0f}/lot · "
        f"max {cfg.max_lots} lots · ${cfg.cash_floor:,.0f} cash floor · "
        f"${args.cash:,.0f} starting cash · fills at next open · zero costs "
        "(Webull equities are commission-free; the open fill IS the live model)."
        f"{source_note}",
        "",
        f"Span: {s['span']['start']} → {s['span']['end']} ({s['span']['trading_days']} trading days). "
        f"Total return {s['total_return_pct']:+.2f}% vs SPY buy-and-hold {spy_bh} · "
        f"max drawdown {s['max_drawdown_pct']:.2f}% · dropped-at-open BUYs: {res.dropped_buys}"
        f"{unfilled_note}.",
        "",
        "| bucket | trades | win | expectancy $ | expectancy % | avg hold |",
        "|---|---|---|---|---|---|",
        _fmt_block("overall", s["overall"]),
        _fmt_block("risk_on", s["risk_on"]),
        _fmt_block("risk_off", s["risk_off"]),
        _fmt_block("warmup", s["warmup"]),
        "",
        "## Per symbol",
        "",
        "| symbol | trades | win | expectancy $ | expectancy % | avg hold |",
        "|---|---|---|---|---|---|",
        *[_fmt_block(sym, b) for sym, b in s["per_symbol"].items()],
        "",
        "## Data spans",
        "",
        "| symbol | first bar | last bar | bars |",
        "|---|---|---|---|",
        *[f"| {sym} | {a} | {b} | {n} |" for sym, (a, b, n) in sorted(spans.items())],
        *([f"", f"Fetch failures (excluded): {', '.join(failed)}"] if failed else []),
        "",
        "## Open positions at end of data",
        "",
        *([f"- {p['symbol']}: {p['shares']:g} sh @ {p['entry_price']:.2f} "
           f"(unrealized {p['unrealized_pnl']:+,.2f})" for p in res.open_positions]
          or ["(none)"]),
        "",
        "## Disclosures",
        "",
        "- **Selection bias**: the shipped universe was hand-picked 2026-07-12 for top-tier "
        "liquidity and multi-year structural uptrends — dips got bought BY CONSTRUCTION over "
        "this lookback. This backtest validates the *mechanics* (bands, sizing, slots, cash "
        "floor, next-open execution), not the symbol selection.",
        span_bullet,
        "- **Regime-blind warmup**: days before SPY accrues 200 closes are untagged; their "
        "trades sit in the `warmup` row (and in overall), NOT in either regime bucket. SPY is "
        "fetched with ~250 extra sessions to shrink this window, but if the data cap bites, the "
        "warmup residual can hold a whole market phase — read the warmup row before trusting "
        "the regime split.",
        price_data_bullet,
        "- **Idle cash earns 0%** (faithful to the paper book): the total-return-vs-SPY line "
        "compares a ~1/3-deployed sleeve against 100% buy-and-hold — per-trade expectancy is "
        "the decision metric, not that line.",
        "- **Excluded list not applied** (`excluded=()`): the replay is a fresh-cash book with "
        "no manual holds to protect, and vetting a candidate requires letting it trade. "
        "Promoting a vetted name that sits in `EXCLUDED_MANUAL_HOLDS` to the live universe "
        "also requires resolving that exclusion.",
    ]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    js_path.write_text(json.dumps({
        "generated": today, "source": args.source, "universe": list(universe),
        "starting_cash": args.cash,
        "stats": s, "dropped_buys": res.dropped_buys,
        "open_positions": res.open_positions,
        "trades": [asdict(t) for t in res.trades],
        "equity_curve": res.equity_curve,
        "spans": {k: list(v) for k, v in spans.items()}, "failed": failed,
    }, indent=2), encoding="utf-8")
    print(f"Wrote {md_path} and {js_path}: {s['overall']['trades']} trades, "
          f"expectancy {s['overall']['expectancy_pct']:+.2f}%/trade.")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
