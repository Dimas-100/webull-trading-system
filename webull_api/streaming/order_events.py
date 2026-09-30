"""Live gRPC order/position status events via TradeEventsClient. Blocking subscribe.

UAT REQUIRES an explicit host (per the SDK sample); prod resolves the host itself.
"""
from __future__ import annotations

import logging

from webull.trade.events.types import (EVENT_TYPE_ORDER, EVENT_TYPE_POSITION,
                                        ORDER_STATUS_CHANGED, POSITION_STATUS_CHANGED)
from webull.trade.trade_events_client import TradeEventsClient

from ..client import get_settings


def watch_order_events(account_id: str, on_event=None) -> None:
    """Subscribe to order/position status changes for `account_id`. Blocks."""
    s = get_settings()
    host = None if s.env == "prod" else s.host("events")
    client = TradeEventsClient(s.app_key, s.app_secret, s.region, host=host)
    client.on_log = lambda level, msg: print(logging.getLevelName(level), msg)

    def _on_msg(event_type, subscribe_type, payload, raw_message):
        if on_event:
            on_event(event_type, subscribe_type, payload)
            return
        if event_type == EVENT_TYPE_ORDER and subscribe_type == ORDER_STATUS_CHANGED:
            print("ORDER:", payload)
        elif event_type == EVENT_TYPE_POSITION and subscribe_type == POSITION_STATUS_CHANGED:
            print("POSITION:", payload)

    client.on_events_message = _on_msg
    client.do_subscribe([account_id])
