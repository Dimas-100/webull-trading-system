r"""Route the SDK's auto-created file logs (webull_*_sdk.log) into <repo>/logs/.

The SDK's DataClient/TradeClient/streaming client auto-create webull_*_sdk.log files via
set_file_logger(<relative basename>), which lands in the CWD — the repo root for the scheduled
runners and scripts. We rewrite any RELATIVE log path to live under <repo>/logs/ (gitignored). Making
the path absolute also means MCP processes (launched from C:\Windows\System32) write there
instead of failing.

Standalone by design: no prod-config import (webull_api/config.py), so the sandbox client
(webull_api/sandbox/ — must stay out of prod config paths) can apply the same reroute.
Prod paths get it unchanged via webull_api.client, which calls reroute_sdk_file_logs()
at import time.
"""
from __future__ import annotations

import os
from pathlib import Path

_LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"


def _route_to_logs(path) -> str:
    p = Path(path)
    if p.is_absolute():
        return str(p)
    _LOGS_DIR.mkdir(exist_ok=True)
    # Per-process filename: several processes run this SDK concurrently (MCP servers, scheduled
    # runners, scripts), and a SHARED file makes TimedRotatingFileHandler's day-boundary rename fail with
    # a held-open PermissionError storm (observed 2026-08-17). One file per PID keeps rollover
    # collision-free; prune_logs' age pass reaps the files of dead processes.
    return str(_LOGS_DIR / f"{p.stem}-{os.getpid()}{p.suffix}")


def _reroute_file_logger(cls) -> None:
    orig = cls.set_file_logger
    if getattr(orig, "_rerouted_to_logs", False):
        return

    def patched(self, path, *args, **kwargs):
        return orig(self, _route_to_logs(path), *args, **kwargs)

    patched._rerouted_to_logs = True
    cls.set_file_logger = patched


def reroute_sdk_file_logs() -> None:
    """Idempotent: patch the SDK client classes so their file logs land under <repo>/logs/."""
    from webull.core.client import ApiClient

    _reroute_file_logger(ApiClient)  # DataClient + TradeClient file loggers
    try:  # streaming client (webull_data_streaming_sdk.log) — best-effort
        from webull.data.internal.quotes_client import QuotesClient

        _reroute_file_logger(QuotesClient)
    except Exception:  # pragma: no cover - streaming deps optional
        pass
