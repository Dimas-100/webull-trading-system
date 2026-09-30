"""Shared CLI wrapper for the paper-runner entry scripts (webull_web/runner_cli.py). Tests the
exit-code mapping + exception handling that the four scripts/*.py duplicated byte-for-byte, with the
service run + env-load injected so no real env/market data is touched."""
from webull_api.market_data import MarketDataNotEntitledError
from webull_web import runner_cli


def _noenv():
    pass


def test_exit_code_clean_is_zero():
    assert runner_cli._exit_code({"result": "ok", "errors": []}) == 0


def test_exit_code_soft_errors_is_two():
    assert runner_cli._exit_code({"result": "ok", "errors": ["x"]}) == 2


def test_exit_code_error_result_is_one():
    assert runner_cli._exit_code({"result": "error", "errors": ["x"]}) == 1


def test_run_cli_clean_run_returns_zero_and_prints_summary(capsys):
    code = runner_cli.run_cli("svc", "RSI2", "desc", argv=["run"], env_fn=_noenv,
                              run_fn=lambda force: {"result": "ok", "errors": [], "summary": "done"})
    assert code == 0
    assert "done" in capsys.readouterr().out


def test_run_cli_forwards_force_flag():
    seen = {}
    runner_cli.run_cli("svc", "RSI2", "desc", argv=["run", "--force"], env_fn=_noenv,
                       run_fn=lambda force: seen.update(force=force) or {"result": "ok", "errors": []})
    assert seen["force"] is True


def test_run_cli_soft_errors_return_two():
    code = runner_cli.run_cli("svc", "RSI2", "desc", argv=["run"], env_fn=_noenv,
                              run_fn=lambda force: {"result": "ok", "errors": ["a symbol failed"]})
    assert code == 2


def test_run_cli_not_entitled_returns_one_and_says_skipped(capsys):
    def boom(force):
        raise MarketDataNotEntitledError("nope")
    code = runner_cli.run_cli("svc", "Options entry", "desc", argv=["run"], env_fn=_noenv, run_fn=boom)
    assert code == 1
    assert "SKIPPED" in capsys.readouterr().err


def test_run_cli_unexpected_error_returns_one_and_says_failed(capsys):
    def boom(force):
        raise RuntimeError("token lapsed")
    code = runner_cli.run_cli("svc", "Paper-EOD", "desc", argv=["run"], env_fn=_noenv, run_fn=boom)
    assert code == 1
    assert "FAILED" in capsys.readouterr().err


def test_rsi2_real_runs_after_netliq_and_before_flows():
    from webull_web import runner_cli
    keys = [m for _label, m in runner_cli.PAPER_SUITE]
    assert "rsi2_real_service" in keys
    assert keys.index("netliq_snapshot_service") < keys.index("rsi2_real_service")
    assert keys.index("rsi2_real_service") < keys.index("flows_service")
