"""Process-singleton MQTT quote streamer, normalized to the HTTP-snapshot shape.

One DataStreamingClient runs in the SDK's daemon thread; ticks are normalized and cached
by symbol with a version counter so an SSE generator can emit only what changed. The live
tick field names are unverified until market hours — `_normalize` is defensive and the
caller keeps the HTTP-snapshot polling as a fallback (see the Tier-3 spec).
"""
from __future__ import annotations

import json
import threading
import uuid
from typing import Optional

from webull.data.common.category import Category
from webull.data.common.subscribe_type import SubscribeType
from webull.data.data_streaming_client import DataStreamingClient

from ..client import get_settings

_SYMBOL_KEYS = ("symbol", "ticker", "tickerSymbol")
_FIELD_MAP = (
    ("price", ("price", "close", "deal", "last", "lastPrice")),
    ("pre_close", ("pre_close", "preClose", "prevClose")),
    ("bid", ("bid", "bidPrice")),
    ("ask", ("ask", "askPrice")),
    ("change", ("change",)),
    ("change_ratio", ("change_ratio", "changeRatio")),
)


def _pick(d, keys):
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return None


def _normalize(topic: str, payload: str):
    """Map a streaming tick (topic + JSON string) to the HTTP-snapshot shape, or None."""
    try:
        obj = json.loads(payload)
    except (ValueError, TypeError):
        return None
    if isinstance(obj, list):
        obj = obj[0] if obj else {}
    if not isinstance(obj, dict):
        return None
    symbol = _pick(obj, _SYMBOL_KEYS)
    if not symbol:
        return None
    out = {"symbol": symbol}
    for key, aliases in _FIELD_MAP:
        v = _pick(obj, aliases)
        if v is not None:
            out[key] = str(v)
    return str(symbol).upper(), out


# Snapshot-result attributes worth surfacing (verified against the real market-hours tick: the SDK
# delivers typed SnapshotResult/QuoteResult objects, NOT JSON strings — see _fields_from_sdk).
_SNAPSHOT_FIELDS = ("price", "open", "high", "low", "pre_close", "volume", "change", "change_ratio")


def _top(levels):
    """Top-of-book price string from an SDK asks/bids list, or None."""
    if levels:
        price = getattr(levels[0], "price", None)
        if price is not None:
            return str(price)
    return None


def _fields_from_sdk(message):
    """(SYMBOL, partial snapshot-shape dict) from an SDK SnapshotResult / QuoteResult, or None.

    The live stream delivers typed objects, not JSON: a SnapshotResult carries price/OHLC/pre_close/
    change; a QuoteResult carries bid/ask depth. They are COMPLEMENTARY per symbol, so ingest() MERGES
    successive messages rather than replacing — a QUOTE tick must not wipe the last SNAPSHOT price.
    """
    basic = getattr(message, "basic", None)
    symbol = getattr(basic, "symbol", None)
    if not symbol:
        return None
    out = {"symbol": symbol}
    for key in _SNAPSHOT_FIELDS:
        v = getattr(message, key, None)
        if v is not None:
            out[key] = str(v)
    ask = _top(getattr(message, "asks", None))
    if ask is not None:
        out["ask"] = ask
    bid = _top(getattr(message, "bids", None))
    if bid is not None:
        out["bid"] = bid
    return str(symbol).upper(), out


def extract(topic, message):
    """Normalize ANY streaming message — SDK object, dict, or JSON string — to (SYMBOL, fields) or None."""
    if isinstance(message, (str, bytes)):
        return _normalize(topic, message)
    if isinstance(message, dict):
        return _normalize(topic, json.dumps(message))
    return _fields_from_sdk(message)


class QuoteStreamManager:
    def __init__(self):
        self._lock = threading.Lock()        # guards _latest/_version/_subscribed (the ingest hot path)
        self._life_lock = threading.Lock()   # guards _client/_started/_connected lifecycle
        self._latest: dict[str, dict] = {}
        self._version: dict[str, int] = {}
        self._subscribed: set[str] = set()
        self._client = None
        self._connected = False
        self._started = False

    def ingest(self, topic, payload) -> None:
        # payload may be an SDK SnapshotResult/QuoteResult, a dict, or a JSON string.
        norm = extract(topic, payload)
        if not norm:
            return
        sym, fields = norm
        with self._lock:
            cur = dict(self._latest.get(sym, {}))
            cur.update(fields)  # merge so snapshot price + quote bid/ask accumulate across ticks
            self._latest[sym] = cur
            self._version[sym] = self._version.get(sym, 0) + 1

    def snapshot_versions(self, symbols) -> dict:
        with self._lock:
            out = {}
            for s in symbols:
                u = s.upper()
                if u in self._latest:
                    out[u] = (self._latest[u], self._version[u])
            return out

    def subscribe(self, symbols) -> None:
        # Subscriptions accumulate for the process lifetime (no unsubscribe) — intentional and
        # immaterial for a single-user local tool over a bounded watchlist.
        syms = [s.upper() for s in symbols if s]
        with self._lock:
            new = [s for s in syms if s not in self._subscribed]
            self._subscribed.update(syms)
        self._ensure_started()
        # Snapshot lifecycle state under the lifecycle lock, then call the network OUTSIDE any lock
        # so a slow MQTT subscribe never serializes against the ingest() hot path.
        with self._life_lock:
            client = self._client if self._connected else None
        if new and client is not None:
            client.subscribe(new, Category.US_STOCK.name,
                             [SubscribeType.QUOTE.name, SubscribeType.SNAPSHOT.name])

    # ---- live MQTT (not exercised by unit tests) ----
    def _ensure_started(self) -> None:
        # Atomic check-and-set: without the lock, two concurrent first-time subscribers could each
        # create a DataStreamingClient + daemon thread (a leaked second connection).
        with self._life_lock:
            if self._started:
                return
            self._started = True
            s = get_settings()
            client = DataStreamingClient(
                s.app_key, s.app_secret, s.region, uuid.uuid4().hex,
                http_host=s.host("data"), mqtt_host=s.host("data_mqtt"),
            )
            client.on_connect_success = self._on_connect
            client.on_quotes_message = self._on_message
            self._client = client
            client.connect_and_loop_async(thread_daemon=True)  # non-blocking: launches the daemon

    def _on_connect(self, client, _api, _sid):
        with self._life_lock:
            self._connected = True
        with self._lock:
            syms = list(self._subscribed)
        if syms:
            client.subscribe(syms, Category.US_STOCK.name,
                             [SubscribeType.QUOTE.name, SubscribeType.SNAPSHOT.name])

    def close(self) -> None:
        """Best-effort teardown for clean server shutdown / dev --reload. Defensive about the SDK's
        disconnect surface (method name unverified) and never raises."""
        with self._life_lock:
            client = self._client
            self._client = None
            self._started = False
            self._connected = False
        if client is not None:
            for name in ("disconnect", "loop_stop", "stop", "close"):
                fn = getattr(client, name, None)
                if callable(fn):
                    try:
                        fn()
                    except Exception:
                        pass
                    break

    def _on_message(self, client, topic, quotes):
        # The SDK hands us a typed SnapshotResult/QuoteResult (or, defensively, a str/dict) — ingest()
        # extracts fields from any of them. (The old json.dumps(object) here raised and dropped every tick.)
        self.ingest(topic, quotes)


_manager: Optional[QuoteStreamManager] = None
_manager_lock = threading.Lock()


def get_manager() -> QuoteStreamManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = QuoteStreamManager()
        return _manager


def close_manager() -> None:
    """Close the singleton if one exists (does NOT create one). For app-shutdown teardown."""
    with _manager_lock:
        m = _manager
    if m is not None:
        m.close()
