from webull_api.lab.dedup import canon_bucket, fingerprint
from webull_api.strategy.schema import AllOf, PriceVsSma, RsiCond, SmaCross, Strategy


def _strat(entry, **kw):
    return Strategy(name=kw.pop("name", "s"), symbol=kw.pop("symbol", "AAPL"),
                    entry=entry, **kw)


def test_fingerprint_identity_ignores_name_and_symbol():
    a = _strat(SmaCross(type="sma_cross", fast=10, slow=20, direction="above"), name="alpha", symbol="AAPL")
    b = _strat(SmaCross(type="sma_cross", fast=10, slow=20, direction="above"), name="beta", symbol="MSFT")
    assert fingerprint(a) == fingerprint(b)


def test_fingerprint_differs_on_rule_change():
    a = _strat(SmaCross(type="sma_cross", fast=10, slow=20, direction="above"))
    b = _strat(SmaCross(type="sma_cross", fast=10, slow=50, direction="above"))
    assert fingerprint(a) != fingerprint(b)


def test_commutative_leaves_cannot_split_a_fingerprint():
    leaf_x = SmaCross(type="sma_cross", fast=10, slow=20, direction="above")
    leaf_y = RsiCond(type="rsi", period=14, threshold=30, comparison="below")
    ab = _strat(AllOf(type="all_of", conditions=[leaf_x, leaf_y]))
    ba = _strat(AllOf(type="all_of", conditions=[leaf_y, leaf_x]))
    assert fingerprint(ab) == fingerprint(ba)


def test_canon_bucket_collapses_29_30_31():
    s29 = _strat(PriceVsSma(type="price_vs_sma", period=29, side="above"))
    s30 = _strat(PriceVsSma(type="price_vs_sma", period=30, side="above"))
    s31 = _strat(PriceVsSma(type="price_vs_sma", period=31, side="above"))
    assert canon_bucket(s29) == canon_bucket(s30) == canon_bucket(s31)


def test_canon_bucket_collapses_sma_50_52():
    a = _strat(SmaCross(type="sma_cross", fast=50, slow=200, direction="above"))
    b = _strat(SmaCross(type="sma_cross", fast=52, slow=200, direction="above"))
    assert canon_bucket(a) == canon_bucket(b)


def test_canon_bucket_still_separates_distinct_rules():
    a = _strat(PriceVsSma(type="price_vs_sma", period=30, side="above"))
    b = _strat(PriceVsSma(type="price_vs_sma", period=100, side="above"))
    assert canon_bucket(a) != canon_bucket(b)


from webull_api.lab.dedup import behavioral_cohort, equity_correlation, is_duplicate
from webull_api.strategy.schema import EquityPoint


def _curve(values):
    return [EquityPoint(time=f"d{i}", equity=v) for i, v in enumerate(values)]


def test_equity_correlation_identical_returns_is_one():
    a = _curve([100, 101, 103, 102, 105, 107])
    b = _curve([50, 50.5, 51.5, 51.0, 52.5, 53.5])  # same per-step returns, scaled
    assert equity_correlation(a, b) > 0.99


def test_equity_correlation_mirror_is_negative():
    a = _curve([100, 101, 103, 102, 105, 107])
    b = _curve([100, 99, 97, 98, 95, 93])
    assert equity_correlation(a, b) < 0


def test_is_duplicate_structural_by_fingerprint():
    s = _strat(SmaCross(type="sma_cross", fast=10, slow=20, direction="above"))
    dup, matched = is_duplicate(s, {fingerprint(s)})
    assert dup is True and matched == fingerprint(s)


def test_is_duplicate_structural_by_canon_bucket():
    s = _strat(PriceVsSma(type="price_vs_sma", period=31, side="above"))
    near = _strat(PriceVsSma(type="price_vs_sma", period=29, side="above"))
    dup, _ = is_duplicate(s, set(), canon_buckets={canon_bucket(near)})
    assert dup is True


def test_is_duplicate_behavioral_merges_high_corr_curve():
    s = _strat(SmaCross(type="sma_cross", fast=8, slow=21, direction="above"))
    mine = _curve([100, 101, 103, 102, 105, 107])
    theirs = _curve([50, 50.5, 51.5, 51.0, 52.5, 53.5])  # >0.9 correlated
    dup, matched = is_duplicate(s, set(), equity_curve=mine,
                                library_curves=[("fp_other", theirs)], corr_threshold=0.90)
    assert dup is True and matched == "fp_other"


def test_is_duplicate_false_when_unique_and_uncorrelated():
    s = _strat(SmaCross(type="sma_cross", fast=8, slow=21, direction="above"))
    mine = _curve([100, 101, 103, 102, 105, 107])
    other = _curve([100, 99, 97, 98, 95, 93])  # negative corr
    dup, matched = is_duplicate(s, {"some_other_fp"}, canon_buckets={"some_bucket"},
                                equity_curve=mine, library_curves=[("fp_other", other)])
    assert dup is False and matched is None


def test_behavioral_cohort_groups_similar_shapes():
    a = behavioral_cohort(_curve([100, 101, 103, 102, 105, 107]))
    b = behavioral_cohort(_curve([50, 50.5, 51.5, 51.0, 52.5, 53.5]))
    assert a == b


def test_fingerprint_stability_pins_for_legacy_strategies():
    """Adding new Condition union members must not perturb canonicalization of EXISTING
    strategies (else the library/avoid-lists silently reset). Pins computed at f207bac,
    before the union was widened."""
    s1 = Strategy.model_validate({
        "name": "pin_a", "symbol": "SPY", "timeframe": "1D",
        "entry": {"type": "all_of", "conditions": [
            {"type": "rsi", "period": 14, "threshold": 30.0, "comparison": "below"},
            {"type": "breakout", "lookback": 20, "direction": "high"}]},
        "filter": {"type": "price_vs_sma", "period": 200, "side": "above"},
        "stop_loss_pct": 5.0, "take_profit_pct": 10.0, "origin": "lab_generated"})
    s2 = Strategy.model_validate({
        "name": "pin_b", "symbol": "SPY", "timeframe": "1D",
        "entry": {"type": "sma_cross", "fast": 10, "slow": 50, "direction": "above"},
        "exit": {"type": "macd", "fast": 12, "slow": 26, "signal": 9,
                 "ref": "signal", "direction": "below"}})
    assert fingerprint(s1) == "0f05a4ce109dbcaa"
    assert canon_bucket(s1) == "2480542063eeac29"
    assert fingerprint(s2) == "fd758b91969bfd12"
    assert canon_bucket(s2) == "83a00d86abd647cc"
