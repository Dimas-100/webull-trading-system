import pytest
from webull_api import trading, safety


class _Resp:
    def __init__(self, code=200, body=None):
        self.status_code = code
        self._body = {"ok": 1} if body is None else body   # an explicit [] is an empty page
        self.text = str(self._body)

    def json(self):
        return self._body


class _Ops:
    def __init__(self):
        self.calls = []

    def preview_order(self, account_id, orders, client_combo_order_id=None):
        self.calls.append(("preview", account_id, orders))
        return _Resp(200, {"preview": True})

    def place_order(self, account_id, orders, client_combo_order_id=None):
        self.calls.append(("place", account_id, orders))
        return _Resp(200, {"order_id": "OID1"})

    def cancel_order(self, account_id, client_order_id):
        self.calls.append(("cancel", account_id, client_order_id))
        return _Resp(200, {"cancelled": client_order_id})

    def replace_order(self, account_id, modify_orders, client_combo_order_id=None):
        self.calls.append(("replace", account_id, modify_orders))
        return _Resp(200, {"replaced": True})

    def get_order_open(self, account_id):
        self.calls.append(("open", account_id))
        return _Resp(200, [{"order_id": "O1", "status": "OPEN"}])

    def get_order_history(self, account_id, **paging):
        # Ignores the cursor on purpose: a broker that hands back the same page for any cursor
        # must terminate the paged read, not loop or duplicate.
        self.calls.append(("history", account_id))
        return _Resp(200, [{"order_id": "O0", "status": "FILLED"}])


class _Client:
    def __init__(self):
        self.order_v2 = _Ops()


def _order():
    return safety.build_order(symbol="AAPL", side="BUY", quantity=1, limit_price=100)


def test_dry_run_previews_but_never_places():
    c = _Client()
    out = trading.place("ACC", _order(), confirm=False, client=c, env="test")
    kinds = [k for (k, *_rest) in c.order_v2.calls]
    assert "preview" in kinds and "place" not in kinds
    assert out["submitted"] is False


def test_confirm_true_places_on_test():
    c = _Client()
    out = trading.place("ACC", _order(), confirm=True, client=c, env="test")
    kinds = [k for (k, *_rest) in c.order_v2.calls]
    assert kinds == ["preview", "place"]
    assert out["submitted"] is True and out["result"]["order_id"] == "OID1"


def test_prod_without_confirm_does_not_place():
    c = _Client()
    out = trading.place("ACC", _order(), confirm=False, client=c, env="prod")
    assert "place" not in [k for (k, *_rest) in c.order_v2.calls]
    assert out["submitted"] is False


def test_invalid_order_raises_before_any_network():
    c = _Client()
    bad = safety.build_order(symbol="AAPL", side="BUY", quantity=0, limit_price=100)
    with pytest.raises(safety.OrderValidationError):
        trading.place("ACC", bad, confirm=True, client=c, env="test")
    assert c.order_v2.calls == []  # nothing was called


def test_cancel_and_modify_route_through_client():
    c = _Client()
    trading.cancel("ACC", "COID", client=c)
    trading.modify("ACC", {"client_order_id": "COID", "quantity": "2"}, client=c)
    kinds = [k for (k, *_rest) in c.order_v2.calls]
    assert kinds == ["cancel", "replace"]


def test_get_open_orders_routes_and_returns():
    c = _Client()
    out = trading.get_open_orders("ACC", client=c)
    assert ("open", "ACC") in c.order_v2.calls
    assert out == [{"order_id": "O1", "status": "OPEN"}]


def test_get_order_history_routes_and_returns():
    c = _Client()
    out = trading.get_order_history("ACC", client=c)
    assert ("history", "ACC") in c.order_v2.calls
    assert out == [{"order_id": "O0", "status": "FILLED"}]


# ---- order history is PAGED (2026-09-18): the SDK returns 10 rows by default, so a busy
# week silently dropped fills from the journal sync and let the flows runner book a phantom
# owner withdrawal (the 09-16 LRCX fill fell outside the page). Every consumer goes through
# trading.get_order_history, so the paging lives here.

class _PagedOps:
    """SDK-shaped history: `page_size` bounds a page, the cursor is the previous page's last
    (client_order_id, order_id) and an exhausted cursor yields an empty page."""

    def __init__(self, envelopes):
        self.envelopes = envelopes
        self.calls = []

    def get_order_history(self, account_id, page_size=None, start_date=None, end_date=None,
                          last_client_order_id=None, last_order_id=None):
        self.calls.append({"page_size": page_size, "last_client_order_id": last_client_order_id,
                           "last_order_id": last_order_id})
        start = 0
        if last_client_order_id is not None:
            ids = [e["client_order_id"] for e in self.envelopes]
            start = ids.index(last_client_order_id) + 1
        return _Resp(200, self.envelopes[start:start + (page_size or 10)])


def _envelope(i):
    return {"client_order_id": f"c{i}", "order_id": f"o{i}",
            "orders": [{"client_order_id": f"c{i}", "order_id": f"o{i}", "symbol": "AAPL",
                        "status": "FILLED"}]}


def _paged_client(n):
    c = _Client()
    c.order_v2 = _PagedOps([_envelope(i) for i in range(n)])
    return c


def test_get_order_history_pages_until_a_short_page():
    c = _paged_client(23)
    out = trading.get_order_history("ACC", client=c, page_size=10, sleep_fn=lambda s: None)
    assert [e["client_order_id"] for e in out] == [f"c{i}" for i in range(23)]
    # the first page has no cursor; each later page resumes from the previous page's last ids;
    # a page shorter than the requested size is the last one (the broker's cap is 100 and the
    # read never asks for more), so the 3-row third page ends the read without a fourth call.
    assert [k["last_client_order_id"] for k in c.order_v2.calls] == [None, "c9", "c19"]
    assert [k["last_order_id"] for k in c.order_v2.calls] == [None, "o9", "o19"]
    assert all(k["page_size"] == 10 for k in c.order_v2.calls)


def test_get_order_history_reads_one_more_page_after_an_exactly_full_one():
    c = _paged_client(20)
    out = trading.get_order_history("ACC", client=c, page_size=10, sleep_fn=lambda s: None)
    assert len(out) == 20
    assert [k["last_client_order_id"] for k in c.order_v2.calls] == [None, "c9", "c19"]


def test_get_order_history_asks_for_a_large_page_by_default():
    c = _paged_client(3)
    trading.get_order_history("ACC", client=c)
    assert c.order_v2.calls[0]["page_size"] == trading.HISTORY_PAGE_SIZE >= 50


def test_get_order_history_never_asks_for_a_page_size_the_broker_rejects():
    """Live 2026-09-18: the endpoint 417s on page_size outside 10..100 (OPENAPI_PARAM_ERR)."""
    c = _paged_client(3)
    trading.get_order_history("ACC", client=c, page_size=3)
    assert c.order_v2.calls[0]["page_size"] == 10
    c = _paged_client(3)
    trading.get_order_history("ACC", client=c, page_size=500)
    assert c.order_v2.calls[0]["page_size"] == 100


class _StuckOps(_PagedOps):
    """A broker that ignores the cursor: every call returns the same full first page."""

    def get_order_history(self, account_id, page_size=None, **cursor):
        self.calls.append({"page_size": page_size, **cursor})
        return _Resp(200, self.envelopes[:page_size or 10])


def test_get_order_history_stops_when_the_cursor_is_ignored():
    c = _Client()
    c.order_v2 = _StuckOps([_envelope(i) for i in range(30)])
    out = trading.get_order_history("ACC", client=c, page_size=10, sleep_fn=lambda s: None)
    assert [e["client_order_id"] for e in out] == [f"c{i}" for i in range(10)]   # no duplicates
    assert len(c.order_v2.calls) == 2                                            # no loop


def test_get_order_history_refuses_a_truncated_window():
    """Hitting the page cap means the window is NOT fully read — the very defect this fixes —
    so it raises instead of returning a silently short list."""
    c = _paged_client(50)
    with pytest.raises(RuntimeError, match="page"):
        trading.get_order_history("ACC", client=c, page_size=10, max_pages=3,
                                  sleep_fn=lambda s: None)


class _ThrottledOps(_PagedOps):
    """429s the first attempt at the second page — the live endpoint's shape (2026-09-18 probe:
    back-to-back pages hit TOO_MANY_REQUESTS; the order-query budget is ~2 reads per 2-3 s)."""

    def __init__(self, envelopes):
        super().__init__(envelopes)
        self.throttled_once = False

    def get_order_history(self, account_id, page_size=None, last_client_order_id=None,
                          last_order_id=None, **kw):
        if last_client_order_id is not None and not self.throttled_once:
            self.throttled_once = True
            self.calls.append({"page_size": page_size, "last_client_order_id": last_client_order_id,
                               "last_order_id": last_order_id, "throttled": True})
            raise RuntimeError("HTTP Status: 429, Code: TOO_MANY_REQUESTS, Msg: Too many requests")
        return super().get_order_history(account_id, page_size=page_size,
                                         last_client_order_id=last_client_order_id,
                                         last_order_id=last_order_id)


def test_get_order_history_spaces_pages_and_retries_a_throttled_page():
    c = _Client()
    c.order_v2 = _ThrottledOps([_envelope(i) for i in range(23)])
    waits = []
    out = trading.get_order_history("ACC", client=c, page_size=10, sleep_fn=waits.append)
    assert [e["client_order_id"] for e in out] == [f"c{i}" for i in range(23)]
    # a gap before page 2, the retry wait after its 429, a gap before page 3 — never before page 1
    assert waits == [trading.HISTORY_PAGE_GAP_S, trading.HISTORY_RETRY_WAIT_S,
                     trading.HISTORY_PAGE_GAP_S]
    assert [k.get("throttled", False) for k in c.order_v2.calls] == [False, True, False, False]


def test_get_order_history_gives_up_after_bounded_throttle_retries():
    class _Always429(_PagedOps):
        def get_order_history(self, account_id, **kw):
            self.calls.append(kw)
            raise RuntimeError("HTTP Status: 429, Code: TOO_MANY_REQUESTS")

    c = _Client()
    c.order_v2 = _Always429([])
    with pytest.raises(RuntimeError, match="TOO_MANY_REQUESTS"):
        trading.get_order_history("ACC", client=c, sleep_fn=lambda s: None)
    assert len(c.order_v2.calls) == trading.HISTORY_RETRY_TRIES


def test_get_order_history_propagates_other_errors_at_once():
    class _Broken(_PagedOps):
        def get_order_history(self, account_id, **kw):
            self.calls.append(kw)
            raise RuntimeError("HTTP Status: 500, Code: SERVER_ERROR")

    c = _Client()
    c.order_v2 = _Broken([])
    with pytest.raises(RuntimeError, match="SERVER_ERROR"):
        trading.get_order_history("ACC", client=c, sleep_fn=lambda s: None)
    assert len(c.order_v2.calls) == 1


class _BodyOps:
    """Returns the given body for every call (page-shape tests)."""

    def __init__(self, body):
        self.body = body
        self.calls = []

    def get_order_history(self, account_id, **kw):
        self.calls.append(kw)
        return _Resp(200, self.body)


def test_get_order_history_raises_on_an_unrecognised_page_body():
    """A body that is neither a list nor {data: [...]} (e.g. _safe_json's fallback for a
    non-JSON 200) must not read as 'no orders' — the flows runner would book a flow from it."""
    c = _Client()
    c.order_v2 = _BodyOps({"status_code": 200, "text": "<html>gateway</html>"})
    with pytest.raises(RuntimeError, match="unrecognised"):
        trading.get_order_history("ACC", client=c, sleep_fn=lambda s: None)


def test_get_order_history_accepts_the_data_envelope_shape():
    c = _Client()
    c.order_v2 = _BodyOps({"data": [_envelope(0), _envelope(1)]})
    out = trading.get_order_history("ACC", client=c, sleep_fn=lambda s: None)
    assert [e["client_order_id"] for e in out] == ["c0", "c1"]


def test_get_order_history_reads_a_null_data_envelope_as_an_empty_page():
    c = _Client()
    c.order_v2 = _BodyOps({"data": None})
    assert trading.get_order_history("ACC", client=c, sleep_fn=lambda s: None) == []
    assert len(c.order_v2.calls) == 1


def test_get_order_history_raises_when_a_full_page_cannot_be_resumed():
    """A full page whose last envelope carries no ids cannot be paged past; returning what was
    read would be a silent truncation."""
    c = _Client()
    c.order_v2 = _BodyOps([{"status": "FILLED", "symbol": "AAPL"} for _ in range(10)])
    with pytest.raises(RuntimeError, match="resume"):
        trading.get_order_history("ACC", client=c, page_size=10, sleep_fn=lambda s: None)


def test_get_order_history_takes_the_cursor_from_the_last_leg_when_the_envelope_has_none():
    legs_only = [{"orders": [{"client_order_id": f"c{i}", "order_id": f"o{i}", "status": "FILLED"}]}
                 for i in range(12)]

    class _LegOps(_PagedOps):
        def get_order_history(self, account_id, page_size=None, last_client_order_id=None,
                              last_order_id=None, **kw):
            self.calls.append({"page_size": page_size, "last_client_order_id": last_client_order_id,
                               "last_order_id": last_order_id})
            start = 0
            if last_client_order_id is not None:
                ids = [e["orders"][0]["client_order_id"] for e in self.envelopes]
                start = ids.index(last_client_order_id) + 1
            return _Resp(200, self.envelopes[start:start + (page_size or 10)])

    c = _Client()
    c.order_v2 = _LegOps(legs_only)
    out = trading.get_order_history("ACC", client=c, page_size=10, sleep_fn=lambda s: None)
    assert len(out) == 12
    assert [k["last_client_order_id"] for k in c.order_v2.calls] == [None, "c9"]
    assert [k["last_order_id"] for k in c.order_v2.calls] == [None, "o9"]


def test_submit_captures_decision_record(monkeypatch):
    from webull_api import exec_ledger
    monkeypatch.setattr(exec_ledger, "_mark", lambda symbol: 101.0)
    c = _Client()
    o = _order()
    out = trading.place("ACC", o, confirm=True, client=c, env="test")
    assert out["submitted"] is True
    recs = exec_ledger.load()
    assert len(recs) == 1
    r = recs[0]
    assert r["id"] == o["client_order_id"] and r["symbol"] == "AAPL"
    assert r["side"] == "BUY" and r["mark"] == 101.0 and r["env"] == "test"


def test_dry_run_captures_nothing():
    from webull_api import exec_ledger
    c = _Client()
    trading.place("ACC", _order(), confirm=False, client=c, env="test")
    assert exec_ledger.load() == []


def test_raising_ledger_never_affects_the_order_result(monkeypatch):
    from webull_api import exec_ledger

    def boom(*a, **k):
        raise RuntimeError("ledger down")

    monkeypatch.setattr(exec_ledger, "record_submit", boom)
    c = _Client()
    out = trading.place("ACC", _order(), confirm=True, client=c, env="test")
    assert out == {"submitted": True, "result": {"order_id": "OID1"}}
    assert [k for (k, *_rest) in c.order_v2.calls] == ["preview", "place"]
