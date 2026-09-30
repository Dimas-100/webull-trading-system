from datetime import date

from webull_api import options_chain as oc
from webull_api.options import InvalidOptionSymbolError


def test_build_and_parse_occ_roundtrip():
    assert oc.build_occ("AAPL", date(2026, 7, 17), "C", 285) == "AAPL260717C00285000"
    assert oc.build_occ("AAPL", date(2026, 7, 17), "P", 292.5) == "AAPL260717P00292500"
    p = oc.parse_occ("AAPL260717C00285000")
    assert p == {"root": "AAPL", "exp": date(2026, 7, 17), "kind": "C", "strike": 285.0}


def test_build_occ_strips_space_from_share_class_root():
    # BUG 1 (roadmap #9): "BRK B" (a space in the underlying) used to land inside the OCC root,
    # which every expiry probe then failed as INVALID_SYMBOL. Real OCC symbology has no
    # separator: Berkshire class B is root "BRKB".
    occ = oc.build_occ("BRK B", date(2026, 8, 21), "C", 510)
    assert occ == "BRKB260821C00510000"
    assert " " not in occ

    # The round trip must stay valid (previously parse_occ raised ValueError on the space) and
    # the parsed root must still associate back with the "BRK B" universe/position symbol via
    # the same normalization the rest of the codebase uses for share-class forms.
    parsed = oc.parse_occ(occ)
    assert parsed["root"] == "BRKB"
    assert parsed["root"] == oc.occ_root("BRK B")


def test_build_occ_strips_dot_from_root_too():
    occ = oc.build_occ("BRK.B", date(2026, 8, 21), "C", 510)
    assert occ == "BRKB260821C00510000"
    assert oc.parse_occ(occ)["root"] == oc.occ_root("BRK.B") == "BRKB"


def test_default_spacing_tiers():
    assert oc.default_spacing(10) == 1
    assert oc.default_spacing(60) == 2.5
    assert oc.default_spacing(296) == 5
    assert oc.default_spacing(900) == 10


def test_strike_grid_centers_on_atm():
    g = oc.strike_grid(296, width=2, spacing=5)
    assert g == [285.0, 290.0, 295.0, 300.0, 305.0]  # ATM rounds to 295


def test_standard_expirations_are_fridays_sorted_unique():
    exps = oc.standard_expirations(date(2026, 6, 18), weeklies=4, months=6)
    assert exps == sorted(set(exps))
    assert all(d.weekday() == 4 for d in exps)  # all Fridays
    assert all(d > date(2026, 6, 18) for d in exps)


def test_parse_invalid_symbols():
    msg = "Invalid Symbol:[AAPL260717C00297500, AAPL260717C00292500]."
    assert oc.parse_invalid_symbols(msg) == {"AAPL260717C00297500", "AAPL260717C00292500"}
    assert oc.parse_invalid_symbols("nope") == set()


def test_pair_by_strike():
    rows = [
        {"symbol": "AAPL260717C00295000", "strike_price": "295.00", "price": "8.7"},
        {"symbol": "AAPL260717P00295000", "strike_price": "295.00", "price": "6.1"},
        {"symbol": "AAPL260717C00300000", "strike_price": "300.00", "price": "5.5"},
    ]
    out = oc.pair_by_strike(rows)
    assert [r["strike"] for r in out] == [295.0, 300.0]
    assert out[0]["call"]["price"] == "8.7" and out[0]["put"]["price"] == "6.1"
    assert out[1]["put"] is None


def test_fetch_chain_prunes_invalid_and_pairs():
    def snap(csv):
        syms = csv.split(",")
        bad = {s for s in syms if s.endswith("00292500") or s.endswith("00297500")}  # $2.5 invalid
        if bad:
            raise InvalidOptionSymbolError(bad)
        return [{"symbol": s, "strike_price": f"{oc.parse_occ(s)['strike']:.2f}"} for s in syms]

    out = oc.fetch_chain("AAPL", date(2026, 7, 17), spot=296, width=2, snapshot_fn=snap)
    strikes = [r["strike"] for r in out["rows"]]
    assert 292.5 not in strikes and 295.0 in strikes  # invalids pruned
    assert any(r["atm"] for r in out["rows"])  # one ATM flagged
    assert out["expiration"] == "2026-07-17"
    assert out["symbol"] == "AAPL"


def test_discover_expirations_keeps_only_live():
    live = date(2026, 7, 17)

    def snap(csv):
        syms = csv.split(",")
        bad = {s for s in syms if oc.parse_occ(s)["exp"] != live}
        if bad:
            raise InvalidOptionSymbolError(bad)
        return [{"symbol": s} for s in syms]

    got = oc.discover_expirations("AAPL", spot=296, today=date(2026, 6, 18), snapshot_fn=snap)
    assert got == ["2026-07-17"]


def test_strike_ladder_offers_multiple_spacings():
    # spot=974: spacing 1 -> 974, spacing 2.5/5 -> 975, spacing 10 -> 970. Deduped, sorted.
    assert oc._strike_ladder(974) == [970.0, 974.0, 975.0]


def test_discover_expirations_survives_a_bad_single_strike_guess():
    # BUG 2 (roadmap #9): verified live for COST -- discover_expirations used to guess ONE
    # strike per expiry; if that guess wasn't actually listed, the whole (valid) expiry got
    # pruned. Simulate: strike 974 is not listed, but 970 and 975 are -- the expiry must
    # survive because at least one ladder probe is valid.
    live = date(2026, 8, 21)

    def snap(csv):
        syms = csv.split(",")
        bad = {s for s in syms if oc.parse_occ(s)["strike"] == 974.0}
        if bad:
            raise InvalidOptionSymbolError(bad)
        return [{"symbol": s} for s in syms]

    got = oc.discover_expirations("CAT", spot=974, today=date(2026, 7, 20), snapshot_fn=snap)
    assert live.isoformat() in got


def test_discover_expirations_prunes_expiry_when_no_probe_is_valid():
    # Sanity check the other direction: an expiry with NO valid probe strike at all must still
    # be pruned (this isn't "accept everything").
    def snap(csv):
        syms = csv.split(",")
        raise InvalidOptionSymbolError(set(syms))

    got = oc.discover_expirations("CAT", spot=974, today=date(2026, 7, 20), snapshot_fn=snap)
    assert got == []
