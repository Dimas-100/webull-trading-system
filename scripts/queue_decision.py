"""Queue an executable decision for the armed autopilot executor. Sessions QUEUE; the executor
PLACES (through the gate). This script writes a JSONL row and pings ntfy — it can never place.

  python scripts/queue_decision.py equity-sell FBTC --qty ALL --trigger green_day
  python scripts/queue_decision.py equity-sell AAPL --qty ALL --trigger rsi2_above [--threshold 70]
  python scripts/queue_decision.py option-buy F --expiry 2026-09-18 --strike 14 --right C \
      --qty 1 --limit 0.50 [--trigger immediate] [--expires 2026-08-07]
"""
import argparse
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from webull_api import decisions_exec  # noqa: E402
from webull_api.autopilot import audit  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    eq = sub.add_parser("equity-sell", help="queue an equity SELL decision")
    eq.add_argument("symbol")
    eq.add_argument("--qty", required=True, help="'ALL' or a share count")
    eq.add_argument("--trigger", default="green_day",
                    choices=["green_day", "immediate", "price_above", "price_below",
                             "rsi2_above"])
    eq.add_argument("--level", type=float, default=None, help="level for price_above/below")
    eq.add_argument("--threshold", type=float, default=None,
                    help="RSI(2) band for rsi2_above (default 70 = the mean-reversion exit)")
    eq.add_argument("--order-type", default="MARKET", choices=["MARKET", "LIMIT"])
    eq.add_argument("--limit-price", default=None)
    eq.add_argument("--expires", default="2026-12-31")

    ob = sub.add_parser("option-buy",
                        help="queue a long option BUY: single leg, or a debit vertical via --short-strike")
    ob.add_argument("symbol")
    ob.add_argument("--expiry", required=True)
    ob.add_argument("--strike", type=float, required=True, help="the LONG strike")
    ob.add_argument("--short-strike", type=float, default=None,
                    help="add a short leg -> debit vertical (calls: above the long; puts: below)")
    ob.add_argument("--right", required=True, choices=["C", "P"])
    ob.add_argument("--qty", required=True)
    ob.add_argument("--limit", required=True,
                    help="single leg: the premium; vertical: the NET DEBIT ceiling")
    ob.add_argument("--trigger", default="immediate",
                    choices=["immediate", "green_day", "price_above", "price_below",
                             "rsi2_above"])
    ob.add_argument("--level", type=float, default=None)
    ob.add_argument("--expires", default=None, help="default: today (never fire on stale pricing)")

    a = ap.parse_args(argv)
    trigger = {"kind": a.trigger}
    if a.level is not None:
        trigger["level"] = a.level
    if getattr(a, "threshold", None) is not None:
        trigger["threshold"] = a.threshold

    if a.cmd == "equity-sell":
        row = {"asset": "EQUITY", "symbol": a.symbol.upper(), "side": "SELL", "qty": a.qty,
               "order_type": a.order_type, "trigger": trigger, "expires": a.expires}
        if a.limit_price is not None:
            row["limit_price"] = a.limit_price
    else:
        option = {"expiry": a.expiry, "strike": a.strike, "right": a.right,
                  "quantity": a.qty, "limit_price": a.limit}
        if a.short_strike is not None:
            option["short_strike"] = a.short_strike
        row = {"asset": "OPTION", "symbol": a.symbol.upper(), "side": "BUY",
               "trigger": trigger, "expires": a.expires or date.today().isoformat(),
               "option": option}

    stored = decisions_exec.append(row)
    print(f"queued {stored['id']}: {stored['asset']} {stored['side']} {stored['symbol']} "
          f"trigger={a.trigger} expires={stored['expires']}")
    audit.notify(
        f"decision queued: {stored['side']} {stored['symbol']} ({stored['asset']}, {a.trigger}) — "
        f"executes at the next armed core-hours run"
        f"{' after the cooling window' if stored['side'] == 'BUY' else ''}; kill file halts",
        os.environ.get("WEBULL_AUTOPILOT_NOTIFY", ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
