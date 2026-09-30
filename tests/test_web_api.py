"""Direct tests of the toolkit modules the web routes used to wrap: market_data's entitlement mapping, the Finnhub
news client and the streaming quote manager. (The /api/* route tests, the Copilot SSE route, the web order-ticket
helpers and the instrument wrappers were archived with the web app / webull-analysis connector 2026-09-28.)"""
import pytest
from webull.core.exception.exceptions import ServerException


def test_get_quote_translates_entitlement_401(monkeypatch):
    from webull_api import market_data

    class _MD:
        def get_quotes(self, *a, **k):
            raise ServerException(
                "Unauthorized",
                "Insufficient permission, please subscribe to stock quotes.",
                http_status=401,
            )

    class _Client:
        market_data = _MD()

    monkeypatch.setattr(market_data, "data_client", lambda: _Client())
    with pytest.raises(market_data.MarketDataNotEntitledError):
        market_data.get_quote("AAPL")


def test_get_quote_passes_through_non_entitlement_errors(monkeypatch):
    from webull_api import market_data

    class _MD:
        def get_quotes(self, *a, **k):
            raise ServerException("ServerError", "boom", http_status=500)

    class _Client:
        market_data = _MD()

    monkeypatch.setattr(market_data, "data_client", lambda: _Client())
    with pytest.raises(ServerException):
        market_data.get_quote("AAPL")


def test_get_company_news(monkeypatch):
    from webull_web import news

    captured = {}

    class _Res:
        status_code = 200
        def json(self):
            return [{"headline": "Apple up", "datetime": 1, "source": "Yahoo"}]

    def fake_get(url, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return _Res()

    monkeypatch.setattr(news, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setenv("FINNHUB_API_KEY", "k123")
    monkeypatch.setattr(news.requests, "get", fake_get)
    out = news.get_company_news("AAPL")
    assert out[0]["headline"] == "Apple up"
    assert captured["url"].endswith("/company-news")
    assert captured["params"]["symbol"] == "AAPL"
    assert captured["params"]["token"] == "k123"
    assert "from" in captured["params"] and "to" in captured["params"]


def test_get_company_news_missing_key(monkeypatch):
    import pytest as _pytest
    from webull_web import news

    monkeypatch.setattr(news, "load_dotenv", lambda *a, **k: None)
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    with _pytest.raises(news.NewsNotConfiguredError):
        news.get_company_news("AAPL")


def test_get_company_news_non_200(monkeypatch):
    import pytest as _pytest
    from webull_web import news

    class _Res:
        status_code = 500
        def json(self):
            return {}

    monkeypatch.setattr(news, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setenv("FINNHUB_API_KEY", "k")
    monkeypatch.setattr(news.requests, "get", lambda *a, **k: _Res())
    with _pytest.raises(RuntimeError):
        news.get_company_news("AAPL")


def test_quote_stream_normalize():
    from webull_api.streaming import quote_stream as qs
    sym, out = qs._normalize("topic", '{"symbol":"aapl","price":"100","preClose":"99","bid":"99.9","ask":"100.1","changeRatio":"0.0101"}')
    assert sym == "AAPL"
    assert out["symbol"] == "aapl"
    assert out["price"] == "100"
    assert out["pre_close"] == "99"      # camelCase alias mapped to snapshot key
    assert out["change_ratio"] == "0.0101"
    assert qs._normalize("t", "not json") is None
    assert qs._normalize("t", '{"price":"1"}') is None   # no symbol -> None


def test_quote_stream_manager_ingest():
    from webull_api.streaming import quote_stream as qs
    mgr = qs.QuoteStreamManager()
    mgr.ingest("t", '{"symbol":"AAPL","price":"100","pre_close":"99"}')
    mgr.ingest("t", '{"symbol":"AAPL","price":"101","pre_close":"99"}')
    sv = mgr.snapshot_versions(["AAPL", "MSFT"])
    assert "AAPL" in sv and "MSFT" not in sv
    quote, version = sv["AAPL"]
    assert quote["price"] == "101" and version == 2   # two ingests -> version 2


def test_quote_stream_extracts_and_merges_sdk_objects():
    # The live stream delivers typed SnapshotResult/QuoteResult objects (verified at market hours),
    # NOT JSON strings. SnapshotResult carries price; QuoteResult carries bid/ask — they must MERGE.
    from decimal import Decimal
    from types import SimpleNamespace
    from webull_api.streaming import quote_stream as qs

    snap = SimpleNamespace(
        basic=SimpleNamespace(symbol="AAPL"),
        price=Decimal("295.17"), open=Decimal("294.19"), high=Decimal("297.78"),
        low=Decimal("291.70"), pre_close=Decimal("291.13"), volume=Decimal("23813511"),
        change=Decimal("4.04"), change_ratio=Decimal("0.0139"),
    )
    quote = SimpleNamespace(
        basic=SimpleNamespace(symbol="AAPL"),
        asks=[SimpleNamespace(price=Decimal("295.19"))],
        bids=[SimpleNamespace(price=Decimal("295.18"))],
    )
    # extract pulls the right fields from each typed message
    assert qs.extract("snapshot", snap)[1]["price"] == "295.17"
    assert qs.extract("quote", quote)[1]["ask"] == "295.19"

    mgr = qs.QuoteStreamManager()
    mgr.ingest("snapshot", snap)
    mgr.ingest("quote", quote)            # complementary message must NOT wipe the price
    merged, version = mgr.snapshot_versions(["AAPL"])["AAPL"]
    assert merged["price"] == "295.17"    # survived the quote tick (merge, not replace)
    assert merged["pre_close"] == "291.13"
    assert merged["bid"] == "295.18" and merged["ask"] == "295.19"
    assert version == 2


class _FakeStreamSettings:
    app_key = "k"
    app_secret = "s"
    region = "us"
    def host(self, name):
        return f"{name}-host"


def _install_fake_stream_client(monkeypatch):
    """Patch quote_stream's DataStreamingClient + get_settings; return the created-clients list."""
    from webull_api.streaming import quote_stream as qs
    created = []

    class FakeClient:
        def __init__(self, *a, **k):
            self.subscribed = []
            self.connect_calls = 0
            self.disconnect_calls = 0
            self.on_connect_success = None
            self.on_quotes_message = None
            created.append(self)

        def connect_and_loop_async(self, thread_daemon=False):
            self.connect_calls += 1

        def subscribe(self, syms, cat, types):
            self.subscribed.append((list(syms), cat, list(types)))

        def disconnect(self):
            self.disconnect_calls += 1

    monkeypatch.setattr(qs, "DataStreamingClient", FakeClient)
    monkeypatch.setattr(qs, "get_settings", lambda: _FakeStreamSettings())
    return qs, created


def test_quote_stream_starts_one_client_and_wires_callbacks(monkeypatch):
    qs, created = _install_fake_stream_client(monkeypatch)
    mgr = qs.QuoteStreamManager()
    mgr.subscribe(["AAPL"])
    mgr.subscribe(["MSFT"])  # second subscribe must NOT create a second client/daemon
    assert len(created) == 1
    c = created[0]
    assert c.connect_calls == 1
    assert c.on_connect_success is not None and c.on_quotes_message is not None


def test_quote_stream_on_connect_resubscribes_accumulated(monkeypatch):
    qs, created = _install_fake_stream_client(monkeypatch)
    mgr = qs.QuoteStreamManager()
    mgr.subscribe(["AAPL", "MSFT"])  # not connected yet -> no immediate client.subscribe
    c = created[0]
    assert c.subscribed == []
    mgr._on_connect(c, None, None)  # connect fires -> replay the accumulated set
    assert len(c.subscribed) == 1
    syms, cat, types = c.subscribed[0]
    assert set(syms) == {"AAPL", "MSFT"}
    assert "US_STOCK" in cat and set(types) == {"QUOTE", "SNAPSHOT"}


def test_quote_stream_subscribe_after_connect_only_new(monkeypatch):
    qs, created = _install_fake_stream_client(monkeypatch)
    mgr = qs.QuoteStreamManager()
    mgr.subscribe(["AAPL"])
    c = created[0]
    mgr._on_connect(c, None, None)
    c.subscribed.clear()
    mgr.subscribe(["AAPL", "MSFT"])  # only MSFT is new
    assert [s for (s, *_r) in c.subscribed] == [["MSFT"]]


def test_quote_stream_on_message_survives_malformed(monkeypatch):
    from types import SimpleNamespace
    qs, created = _install_fake_stream_client(monkeypatch)
    mgr = qs.QuoteStreamManager()
    # None / objects with no `.basic` must NOT raise (the SDK swallows callback exceptions, so a
    # raise here would silently drop EVERY tick — the past total-failure bug).
    mgr._on_message(None, "t", None)
    mgr._on_message(None, "t", SimpleNamespace(nope=1))
    assert mgr.snapshot_versions(["AAPL"]) == {}
    # a valid typed snapshot still ingests
    snap = SimpleNamespace(basic=SimpleNamespace(symbol="AAPL"), price="100")
    mgr._on_message(None, "snapshot", snap)
    assert mgr.snapshot_versions(["AAPL"])["AAPL"][0]["price"] == "100"


def test_quote_stream_close_tears_down(monkeypatch):
    qs, created = _install_fake_stream_client(monkeypatch)
    mgr = qs.QuoteStreamManager()
    mgr.subscribe(["AAPL"])
    c = created[0]
    mgr.close()
    assert c.disconnect_calls == 1
    assert mgr._client is None and mgr._started is False and mgr._connected is False
    # close() is idempotent / safe with no client
    mgr.close()


def test_close_manager_does_not_create_one(monkeypatch):
    from webull_api.streaming import quote_stream as qs
    monkeypatch.setattr(qs, "_manager", None)
    qs.close_manager()  # must be a no-op, not construct a manager
    assert qs._manager is None
