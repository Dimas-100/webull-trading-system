"""Shared error/serialization helpers for the webull-based stdio MCP servers.

Each server keeps its own thin ``_safe(fn)`` that delegates here with its typed-exception
*handlers*, so each server's exact error payloads (entitlement message text, OrderValidationError,
NewsNotConfigured, ...) are preserved while the never-crash + JSON-serialize logic is shared.
"""
from __future__ import annotations

import dataclasses
import json
from typing import Any, Callable, Iterable, Tuple

# A handler = (ExceptionType, builder) where builder(exc) -> the error-payload dict.
Handler = Tuple[type, Callable[[BaseException], dict]]


def to_jsonable(obj: Any) -> Any:
    """pydantic v2 model -> dict; dataclass -> dict; else passthrough (dict / list / primitive)."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    return obj


def dump(obj: Any) -> str:
    return json.dumps(to_jsonable(obj), default=str)


def safe(fn: Callable[[], Any], *, handlers: Iterable[Handler] = ()) -> str:
    """Run fn() and return a JSON string; never raise (a stdio MCP must not crash its protocol).

    On an exception the FIRST matching handler's payload is serialized; otherwise a generic
    ``{"error": <type name>, "message": <str(e)[:300]>}``. Handlers are tried in the given order,
    so a server can prioritize a specific exception (e.g. OrderValidationError before a catch-all).
    """
    try:
        return dump(fn())
    except Exception as e:  # noqa: BLE001 — intentional: never crash the stdio server
        for exc_type, builder in handlers:
            if isinstance(e, exc_type):
                return json.dumps(builder(e))
        return json.dumps({"error": type(e).__name__, "message": str(e)[:300]})
