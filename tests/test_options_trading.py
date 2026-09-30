import inspect

from webull_api import safety, trading

C295 = "AAPL260717C00295000"


def _combo():
    return safety.build_option_combo(
        strategy="SINGLE",
        legs=[safety.build_option_leg(symbol=C295, side="BUY", quantity="1", limit_price="5.00")],
    )


class _Resp:
    status_code = 200

    def json(self):
        return {"ok": True}


class _OrderV2:
    def __init__(self):
        self.placed = False

    def preview_option(self, acct, orders, combo_id):
        return _Resp()

    def place_option(self, acct, orders, combo_id):
        self.placed = True
        return _Resp()


class _Client:
    def __init__(self):
        self.order_v2 = _OrderV2()


def test_place_option_dry_run_default_does_not_submit():
    c = _Client()
    out = trading.place_option("acct", _combo(), client=c, env="prod")  # confirm defaults False
    assert out["submitted"] is False and c.order_v2.placed is False


def test_place_option_submits_only_with_confirm():
    c = _Client()
    out = trading.place_option("acct", _combo(), confirm=True, client=c, env="prod")
    assert out["submitted"] is True and c.order_v2.placed is True


def test_place_option_uses_should_submit_gate():
    # source-level invariant: the submit decision routes through safety.should_submit
    assert "should_submit" in inspect.getsource(trading.place_option)


def test_preview_option_never_submits():
    c = _Client()
    trading.preview_option("acct", _combo(), client=c)
    assert c.order_v2.placed is False
