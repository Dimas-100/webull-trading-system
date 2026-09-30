import webull_api.watchlist as wl


class _Resp:
    def __init__(self, status, payload, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload


class _WL:
    def __init__(self, lists, instruments):
        self._lists = lists
        self._instruments = instruments

    def get_watchlist(self):
        return _Resp(200, self._lists)

    def get_instruments(self, watchlist_id):
        return _Resp(200, self._instruments)


def _patch(monkeypatch, lists, instruments):
    class _DC:
        watchlist = _WL(lists, instruments)

    monkeypatch.setattr(wl, "data_client", lambda: _DC())


def test_get_watchlists_normalizes(monkeypatch):
    _patch(monkeypatch, [
        {"name": "My Watchlist", "sort": 0, "watchlist_id": "abc"},
        {"name": "US", "sort": 4, "watchlist_id": "def"},
    ], [])
    out = wl.get_watchlists()
    assert out == [
        {"id": "abc", "name": "My Watchlist", "sort": 0},
        {"id": "def", "name": "US", "sort": 4},
    ]


def test_get_symbols_handles_bare_list(monkeypatch):
    _patch(monkeypatch, [], [
        {"symbol": "AAPL", "name": "Apple", "exchange_code": "NASDAQ"},
        {"symbol": "BRK B", "name": "Berkshire", "exchange_code": "NYSE"},
    ])
    out = wl.get_watchlist_symbols("abc")
    assert out == [
        {"symbol": "AAPL", "name": "Apple", "exchange": "NASDAQ"},
        {"symbol": "BRK B", "name": "Berkshire", "exchange": "NYSE"},
    ]


def test_get_symbols_handles_envelope(monkeypatch):
    _patch(monkeypatch, [], {"instruments": [{"symbol": "F"}]})
    assert wl.get_watchlist_symbols("abc") == [{"symbol": "F", "name": None, "exchange": None}]
