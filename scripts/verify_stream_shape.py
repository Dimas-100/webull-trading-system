"""PENDING MARKET-HOURS CHECK — verify the live MQTT streaming-quote field shape.

Why this exists: the streaming tick's real JSON field names could not be captured on a
weekend (markets closed). `webull_api/streaming/quote_stream._normalize` maps a tick to the
HTTP-snapshot shape using defensive aliases; this script confirms those aliases match a REAL
tick. The app works via the snapshot-polling fallback until this passes.

Run DURING US market hours (ticks only flow when the market is open):

    .venv/Scripts/python.exe scripts/verify_stream_shape.py [SYMBOL ...]

It connects, subscribes, waits ~40s for ticks, and for the first one prints the raw payload,
the normalized output, and WHICH key fields resolved. If `price`/`pre_close`/`bid`/`ask` are
missing, copy the printed raw keys into `_FIELD_MAP` in quote_stream.py and re-run
`pytest -k test_quote_stream_normalize`.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root on path

from webull.data.common.category import Category
from webull.data.common.subscribe_type import SubscribeType
from webull.data.data_streaming_client import DataStreamingClient

from webull_api.client import get_settings
from webull_api.streaming.quote_stream import QuoteStreamManager, extract

KEY_FIELDS = ("price", "pre_close", "bid", "ask")
WAIT_SECONDS = 40


def main() -> int:
    symbols = [s.upper() for s in sys.argv[1:]] or ["AAPL"]
    state = {"ticks": 0, "connected": False}
    mgr = QuoteStreamManager()  # accumulate ticks here to verify the MERGED quote
    s = get_settings()
    client = DataStreamingClient(
        s.app_key, s.app_secret, s.region, uuid.uuid4().hex,
        http_host=s.host("data"), mqtt_host=s.host("data_mqtt"),
    )

    def on_conn(c, _api, _sid):
        state["connected"] = True
        print(f"CONNECTED — subscribing {symbols}", flush=True)
        c.subscribe(symbols, Category.US_STOCK.name,
                    [SubscribeType.QUOTE.name, SubscribeType.SNAPSHOT.name])

    def on_msg(c, topic, quotes):
        state["ticks"] += 1
        mgr.ingest(topic, quotes)  # merge snapshot + quote ticks per symbol
        if state["ticks"] <= 6:
            print(f"\n--- TICK {state['ticks']} (topic={topic}, type={type(quotes).__name__}) ---", flush=True)
            print("repr:", repr(quotes)[:400], flush=True)
            norm = extract(topic, quotes)
            if not norm:
                print("extracted: None (no symbol resolved)", flush=True)
            else:
                sym, out = norm
                print("extracted:", out, flush=True)
                print(f"  this message resolved: {[f for f in KEY_FIELDS if f in out]}", flush=True)

    client.on_connect_success = on_conn
    client.on_quotes_message = on_msg

    threading.Thread(target=client.connect_and_loop_forever, daemon=True).start()
    print(f"waiting up to {WAIT_SECONDS}s for ticks (need market hours)…", flush=True)
    time.sleep(WAIT_SECONDS)
    if state["ticks"] == 0:
        print("\nNO TICKS RECEIVED.", flush=True)
        print("  - If markets are CLOSED, that's expected — re-run during US market hours.", flush=True)
        print(f"  - connected={state['connected']}: if False, the stream failed to connect.", flush=True)
    else:
        print(f"\nReceived {state['ticks']} tick(s). MERGED per-symbol quote (snapshot + quote combined):", flush=True)
        merged = mgr.snapshot_versions(symbols)
        for sym in symbols:
            if sym not in merged:
                print(f"  {sym}: (no data)", flush=True)
                continue
            quote, version = merged[sym]
            resolved = [f for f in KEY_FIELDS if f in quote]
            missing = [f for f in KEY_FIELDS if f not in quote]
            print(f"  {sym} (v{version}): {quote}", flush=True)
            print(f"    resolved key fields: {resolved}", flush=True)
            if missing:
                print(f"    *** STILL MISSING after merge: {missing} ***", flush=True)
            else:
                print("    OK — all key fields resolved after merge.", flush=True)
    os._exit(0)


if __name__ == "__main__":
    raise SystemExit(main())
