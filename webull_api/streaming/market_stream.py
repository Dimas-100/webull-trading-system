"""Live MQTT market-data quotes via DataStreamingClient. Blocking loop."""
from __future__ import annotations

import uuid

from webull.data.common.category import Category
from webull.data.common.subscribe_type import SubscribeType
from webull.data.data_streaming_client import DataStreamingClient

from ..client import get_settings


def stream_quotes(symbols, category: str = Category.US_STOCK.name,
                  sub_types=None, on_quote=None) -> None:
    """Connect, subscribe to `symbols`, and block forever printing/dispatching quotes."""
    s = get_settings()
    sub_types = sub_types or [SubscribeType.QUOTE.name, SubscribeType.SNAPSHOT.name]
    session_id = uuid.uuid4().hex
    client = DataStreamingClient(
        s.app_key, s.app_secret, s.region, session_id,
        http_host=s.host("data"), mqtt_host=s.host("data_mqtt"),
    )

    def _on_connect(c, _api, sid):
        print(f"connected ({sid}); subscribing {symbols}")
        c.subscribe(symbols, category, sub_types)

    def _on_message(c, topic, quotes):
        (on_quote or (lambda t, q: print(f"{t}: {q}")))(topic, quotes)

    client.on_connect_success = _on_connect
    client.on_quotes_message = _on_message
    client.on_subscribe_success = lambda c, _api, sid: print(f"subscribed ({sid})")
    client.connect_and_loop_forever()
