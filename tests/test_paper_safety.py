"""Defense-in-depth for the hard invariant: the paper engine must never reach the real
order-submit path. It may use only the pure safety helpers."""
import inspect

from webull_api.paper import engine, schema


def test_engine_does_not_call_real_submit_path():
    # Call-shaped tokens (trailing "(") so a docstring/comment MENTION of the gate is not a
    # false positive — only an actual call to the real order path trips these.
    src = inspect.getsource(engine)
    for forbidden in ("should_submit(", "trading.place(", "trading.cancel(",
                      "trading.modify(", "order_v2", ".place_order(", "preview_order("):
        assert forbidden not in src, f"paper engine must not call {forbidden!r}"


def test_engine_namespace_has_no_gate_bindings():
    # the real-gate module/function were never imported into the engine namespace
    assert not hasattr(engine, "trading")
    assert not hasattr(engine, "should_submit")


def test_engine_uses_only_pure_safety_helpers():
    src = inspect.getsource(engine)
    assert "safety.build_order" in src and "safety.validate_order" in src


def test_schema_has_no_gate_bindings():
    assert not hasattr(schema, "trading")
    assert not hasattr(schema, "should_submit")
    assert "should_submit(" not in inspect.getsource(schema)
