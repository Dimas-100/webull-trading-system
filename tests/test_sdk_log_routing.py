from pathlib import Path

from webull_api import sdk_logging


def test_relative_log_path_rerouted_under_logs_per_process():
    import os
    out = sdk_logging._route_to_logs("webull_data_sdk.log")
    p = Path(out)
    assert p.is_absolute() and p.parent.name == "logs"
    # PID suffix: concurrent processes must never share a rotating log file (rename collision).
    assert p.name == f"webull_data_sdk-{os.getpid()}.log"


def test_absolute_log_path_unchanged():
    abs_in = str(Path(sdk_logging.__file__).resolve())  # any absolute path
    assert sdk_logging._route_to_logs(abs_in) == abs_in


def test_apiclient_file_logger_is_rerouted():
    from webull_api import client  # noqa: F401 - importing the prod client applies the patch

    from webull.core.client import ApiClient
    assert getattr(ApiClient.set_file_logger, "_rerouted_to_logs", False)


def test_sdk_logging_is_config_free():
    """The sandbox client imports sdk_logging; it must never grow a prod-config dependency
    (webull_api.config / .env loading), or the sandbox's prod-isolation invariants break."""
    src = Path(sdk_logging.__file__).read_text(encoding="utf-8")
    for forbidden in ("from .config", "webull_api.config", "load_dotenv", "load_settings"):
        assert forbidden not in src, f"sdk_logging must stay config-free, found: {forbidden}"
