"""get_snapshot pages above the endpoint's 100-symbol cap (HTTP 417 above it) and hands back
one flat row list; at or below the cap the call is passed through untouched."""
from webull_api import market_data


class _Resp:
    status_code = 200

    def __init__(self, body):
        self._b = body

    def json(self):
        return self._b


class _MD:
    def __init__(self):
        self.calls = []

    def get_snapshot(self, symbols, category, **kw):
        self.calls.append((symbols, category, kw))
        return _Resp([{"symbol": s, "price": "1"} for s in symbols.split(",")])


def _wire(monkeypatch):
    md = _MD()
    monkeypatch.setattr(market_data, "data_client",
                        lambda: type("C", (), {"market_data": md})())
    return md


def test_at_or_below_cap_is_a_single_passthrough_call(monkeypatch):
    md = _wire(monkeypatch)
    syms = ",".join(f"S{i}" for i in range(100))
    rows = market_data.get_snapshot(syms)
    assert len(md.calls) == 1 and md.calls[0][0] == syms
    assert len(rows) == 100


def test_above_cap_pages_and_concatenates(monkeypatch):
    md = _wire(monkeypatch)
    syms = [f"S{i}" for i in range(180)]
    rows = market_data.get_snapshot(",".join(syms))
    assert [len(c[0].split(",")) for c in md.calls] == [100, 80]
    assert [r["symbol"] for r in rows] == syms          # order preserved, nothing dropped
    assert md.calls[0][1] == market_data.Category.US_STOCK.name


def test_list_input_and_extend_hours_flag_reach_every_page(monkeypatch):
    md = _wire(monkeypatch)
    market_data.get_snapshot([f"S{i}" for i in range(101)], extend_hour_required=True)
    assert len(md.calls) == 2
    assert all(c[2] == {"extend_hour_required": True} for c in md.calls)


def test_spot_price_is_the_snapshot_price_as_a_float(monkeypatch):
    # Ported 2026-09-28 from the archived GET /api/options/{symbol}/expirations route test, its only coverage
    # (the autopilot, the exec ledger and the options surfaces read spot_price).
    monkeypatch.setattr(market_data, "get_snapshot", lambda s: [{"price": "296"}])
    assert market_data.spot_price("AAPL") == 296.0
