"""Stream live quotes for symbols passed on the CLI (default AAPL). Ctrl+C to stop."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root on path

from webull_api.streaming.market_stream import stream_quotes


def main() -> int:
    symbols = sys.argv[1:] or ["AAPL"]
    print(f"streaming {symbols} - Ctrl+C to stop")
    stream_quotes(symbols)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
