"""Small shared helpers for the toolkit. Pure, no network, no SDK imports."""
from __future__ import annotations

from typing import Any, Iterable


def num(d: Any, keys: Iterable[str], default: float | None = None) -> float | None:
    """First parseable float among ``keys`` in dict ``d``, else ``default``.

    The defensive normalizer for the Webull SDK's loose dict shapes: tolerates a None dict,
    skips None / empty-string values, and skips unparseable values — so a single canonical
    implementation replaces the ~7 near-identical private copies that had drifted slightly.
    """
    for k in keys:
        v = (d or {}).get(k)
        if v is None or v == "":
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return default
