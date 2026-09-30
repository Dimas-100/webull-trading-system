from webull_web import fundamentals_data as fd


def test_map_industry_keywords():
    assert fd.map_industry_to_etf("Semiconductors") == "XLK"
    assert fd.map_industry_to_etf("Banking") == "XLF"
    assert fd.map_industry_to_etf("Pharmaceuticals") == "XLV"
    assert fd.map_industry_to_etf("Oil & Gas") == "XLE"
    assert fd.map_industry_to_etf("REIT") == "XLRE"
    assert fd.map_industry_to_etf("Zzqqx nonsense") is None
    assert fd.map_industry_to_etf(None) is None


def test_finnhub_metrics_none_without_key(monkeypatch):
    monkeypatch.setattr(fd, "_key", lambda: "")
    assert fd.finnhub_metrics("AAPL") is None


def test_finnhub_metrics_parses_response(monkeypatch):
    monkeypatch.setattr(fd, "_key", lambda: "k")

    class R:
        status_code = 200

        @staticmethod
        def json():
            return {"metric": {"peTTM": 30.0}}

    monkeypatch.setattr(fd.requests, "get", lambda *a, **k: R())
    assert fd.finnhub_metrics("AAPL") == {"peTTM": 30.0}


def test_sector_etf_from_profile(monkeypatch):
    monkeypatch.setattr(fd, "_key", lambda: "k")

    class R:
        status_code = 200

        @staticmethod
        def json():
            return {"finnhubIndustry": "Technology"}

    monkeypatch.setattr(fd.requests, "get", lambda *a, **k: R())
    assert fd.sector_etf("AAPL") == "XLK"


def test_finnhub_symbol_normalizes():
    assert fd.finnhub_symbol("BRKB") == "BRK.B"     # alias (no separator)
    assert fd.finnhub_symbol("BRK B") == "BRK.B"    # space form
    assert fd.finnhub_symbol("brk b") == "BRK.B"    # case + space
    assert fd.finnhub_symbol("BRK.B") == "BRK.B"    # already correct
    assert fd.finnhub_symbol("AAPL") == "AAPL"      # normal passthrough


def test_finnhub_request_uses_normalized_symbol(monkeypatch):
    monkeypatch.setattr(fd, "_key", lambda: "k")
    seen = {}

    class R:
        status_code = 200

        @staticmethod
        def json():
            return {"finnhubIndustry": "Financial Services"}

    def capture(url, params=None, timeout=None):
        seen["symbol"] = params["symbol"]
        return R()

    monkeypatch.setattr(fd.requests, "get", capture)
    assert fd.company_industry("BRKB") == "Financial Services"
    assert seen["symbol"] == "BRK.B"   # the dotted form was sent to Finnhub, not "BRKB"


def test_finnhub_metrics_swallows_errors(monkeypatch):
    monkeypatch.setattr(fd, "_key", lambda: "k")

    def boom(*a, **k):
        raise RuntimeError("network")

    monkeypatch.setattr(fd.requests, "get", boom)
    assert fd.finnhub_metrics("AAPL") is None
    assert fd.sector_etf("AAPL") is None
