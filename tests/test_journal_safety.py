"""The journal engine + ingest must never touch the real-money order gate. They read order
history via the read-only trading.get_order_history only — never place/preview/submit."""
import inspect

from webull_api.journal import analytics, normalize, pairing, schema
from webull_web import journal_ingest, journal_store

_FORBIDDEN = ("should_submit(", "trading.place(", "trading.cancel(", "trading.modify(",
              ".place_order(", "preview_order(")


def test_pure_engine_never_calls_or_imports_the_gate():
    for mod in (schema, pairing, analytics, normalize):
        src = inspect.getsource(mod)
        for bad in _FORBIDDEN:
            assert bad not in src, f"{mod.__name__} must not contain {bad!r}"
        assert not hasattr(mod, "trading"), f"{mod.__name__} must not import trading"
        assert not hasattr(mod, "should_submit")


def test_ingest_uses_only_readonly_order_history():
    src = inspect.getsource(journal_ingest)
    for bad in _FORBIDDEN:
        assert bad not in src, f"journal_ingest must not contain {bad!r}"
    # it DOES legitimately read order history (read-only):
    assert "get_order_history" in src


def test_store_has_no_gate_bindings():
    assert not hasattr(journal_store, "trading")
    assert "should_submit(" not in inspect.getsource(journal_store)
