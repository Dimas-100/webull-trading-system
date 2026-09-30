# tests/test_readonly_service_guards.py
"""Read-only source guards over the report/ledger services.

These guards used to ride along in the route tests of `/api/north-star`, `/api/trade-anatomy`,
`/api/performance` and `/api/contributions`. Those routes were pruned with the old owner pages
(2026-09-10), so the guard moved here, to the modules it was actually protecting. A read-only surface
must never grow a submit path. (performance_service and trade_anatomy_service were archived with the web
app 2026-09-28; the services below remain.)
"""
import inspect

import pytest

from webull_web import contributions_store, feed_service, north_star_service

_FORBIDDEN = ("trading.place", "import trading", "place_order", "confirm=True")


@pytest.mark.parametrize("mod, forbidden", [
    (north_star_service, _FORBIDDEN),
    (contributions_store, _FORBIDDEN),
    # the kestrel feed gathers every book for another app: the stricter list too (spec 2026-09-26 kestrel feed §6)
    (feed_service, _FORBIDDEN + ("safety.",)),
], ids=["north_star_service", "contributions_store", "feed_service"])
def test_service_surface_has_no_submit_path(mod, forbidden):
    src = inspect.getsource(mod)
    for word in forbidden:
        assert word not in src, f"{mod.__name__} must not contain {word!r}"
