"""Dry-run-guarded order entry. Submits only after BOTH confirm=True AND a typed CONFIRM.

Usage:
  python scripts/place_order.py --account ACC --symbol AAPL --side BUY --qty 1 --limit 100
  (add --confirm to arm submission; you will still be asked to type CONFIRM)
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root on path

from webull_api import safety, trading
from webull_api.client import get_settings


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--account", required=True)
    p.add_argument("--symbol", required=True)
    p.add_argument("--side", required=True, choices=["BUY", "SELL"])
    p.add_argument("--qty", required=True)
    p.add_argument("--limit", default=None, help="omit for a MARKET order")
    p.add_argument("--confirm", action="store_true", help="arm real submission")
    args = p.parse_args()

    order = safety.build_order(
        symbol=args.symbol, side=args.side, quantity=args.qty,
        order_type="LIMIT" if args.limit else "MARKET", limit_price=args.limit,
    )
    safety.validate_order(order)

    if not args.confirm:
        trading.place(args.account, order, confirm=False)  # prints preview, dry-runs
        print("\n(no --confirm flag -> dry run only)")
        return 0

    env = get_settings().env
    typed = input(f"Type CONFIRM to submit this {env.upper()} order: ").strip()
    if typed != "CONFIRM":
        print("Not confirmed - aborting.")
        return 1

    out = trading.place(args.account, order, confirm=True)
    print("RESULT:", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
