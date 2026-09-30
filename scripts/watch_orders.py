"""Watch live order/position events for one account. Pass account_id as arg."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root on path

from webull_api.streaming.order_events import watch_order_events


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python scripts/watch_orders.py <account_id>")
        return 2
    print(f"watching events for {sys.argv[1]} - Ctrl+C to stop")
    watch_order_events(sys.argv[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
