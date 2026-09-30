import inspect


def test_rsi2_surface_has_no_real_submit_path():
    from webull_web import rsi2_service
    from webull_api.strategy import rsi2
    from pathlib import Path
    cli_path = Path(__file__).resolve().parent.parent / "scripts" / "rsi2_paper.py"
    cli_src = cli_path.read_text(encoding="utf-8")
    for mod in (rsi2_service, rsi2):
        src = inspect.getsource(mod)
        for bad in ("trading.place", "import trading", "confirm=True", "should_submit"):
            assert bad not in src, f"{mod.__name__} must not contain {bad!r}"
    for bad in ("trading.place", "import trading", "confirm=True", "should_submit"):
        assert bad not in cli_src, f"scripts/rsi2_paper.py must not contain {bad!r}"
