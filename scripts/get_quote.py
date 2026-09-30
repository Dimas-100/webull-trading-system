"""Print a quote + recent daily bars for a symbol (default AAPL)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root on path

from webull_api import market_data


def main() -> int:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    print(f"quote {symbol}:")
    print(json.dumps(market_data.get_quote(symbol), indent=2))
    print(f"last 5 daily bars {symbol}:")
    bars = market_data.get_bars(symbol, count="5")
    print(json.dumps(bars, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
