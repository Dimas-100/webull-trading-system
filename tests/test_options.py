import pytest
from webull.core.exception.exceptions import ServerException

from webull_api import options
from webull_api.market_data import MarketDataNotEntitledError


class _Resp:
    def __init__(self, code, payload=None, text=""):
        self.status_code = code
        self._p = payload
        self.text = text

    def json(self):
        return self._p


def _patch_snapshot(monkeypatch, fn):
    class _OMD:
        get_option_snapshot = staticmethod(fn)

    class _DC:
        option_market_data = _OMD()

    monkeypatch.setattr(options, "data_client", lambda: _DC())


def test_snapshot_ok(monkeypatch):
    _patch_snapshot(monkeypatch, lambda s, c: _Resp(200, [{"symbol": s}]))
    assert options.get_option_snapshot("AAPL260717C00295000") == [{"symbol": "AAPL260717C00295000"}]


def test_snapshot_entitlement(monkeypatch):
    def boom(s, c):
        raise ServerException(
            "Unauthorized",
            "Insufficient permission, please subscribe to US_OPTION quotes.",
            http_status=401,
        )

    _patch_snapshot(monkeypatch, boom)
    with pytest.raises(MarketDataNotEntitledError):
        options.get_option_snapshot("X")


def test_snapshot_invalid_symbol(monkeypatch):
    def boom(s, c):
        raise ServerException("INVALID_SYMBOL", "Invalid Symbol:[A, B].", http_status=417)

    _patch_snapshot(monkeypatch, boom)
    with pytest.raises(options.InvalidOptionSymbolError) as ei:
        options.get_option_snapshot("A,B")
    assert ei.value.invalid == {"A", "B"}
