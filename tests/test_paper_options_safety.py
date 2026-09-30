import inspect
from webull_api.paper import options_engine, options_schema


def test_engine_never_imports_real_order_path():
    for mod in (options_engine, options_schema):
        src = inspect.getsource(mod)
        # Use call-shaped tokens (trailing "(") and import-shaped tokens so that docstring
        # *mentions* of the forbidden symbols (which describe what is NOT allowed) are not
        # false positives — only actual calls or imports would trip these.
        for forbidden in ("trading.place(", "place_option(", "should_submit(",
                          "import trading", "from ..trading", "from webull_api.trading"):
            assert forbidden not in src, f"{mod.__name__} must not reference {forbidden!r}"


def test_options_engine_namespace_has_no_gate_bindings():
    import webull_api.paper.options_engine as m
    for attr in ("trading", "place_option", "should_submit"):
        assert not hasattr(m, attr), f"options_engine must not bind {attr!r}"
