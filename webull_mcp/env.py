"""MCP startup hardening: load creds + neutralize the SDK's auto-logger for a stdio server.

Claude Desktop launches the MCP from C:\\Windows\\System32 with stdout as the JSON-RPC channel.
The Webull SDK, on client construction, (1) writes `webull_data_sdk.log` to a RELATIVE path (cwd =
System32 → PermissionError) and (2) attaches a stdout StreamHandler (→ corrupts the MCP protocol).
We therefore, before any SDK client is built:
  - load `<repo>/.env` (cwd-independent, via __file__),
  - resolve a relative WEBULL_OPENAPI_TOKEN_DIR to an absolute path under the repo,
  - patch ApiClient so it never writes a log file and routes stream logs to STDERR.
None of this touches the web app (it runs from the repo cwd with stdout as a harmless console).
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _harden_sdk_logging() -> None:
    """Route the SDK's STREAM logger to stderr (never stdout — that's the MCP JSON-RPC channel).
    The SDK FILE logger is handled centrally by webull_api.client (rerouted under <repo>/logs/),
    so it isn't touched here. Idempotent."""
    from webull.core.client import ApiClient

    def _stderr_stream_logger(self, log_level=logging.WARNING, logger_name="webull.core",
                              stream=None, format_string=None):  # noqa: ANN001
        log = logging.getLogger(logger_name)
        log.setLevel(log_level)
        # never stdout — that is the MCP JSON-RPC channel
        if not any(getattr(h, "stream", None) is sys.stderr for h in log.handlers):
            handler = logging.StreamHandler(sys.stderr)
            if format_string:
                handler.setFormatter(logging.Formatter(format_string))
            log.addHandler(handler)
        self._stream_logger_set = True

    ApiClient.set_stream_logger = _stderr_stream_logger


def load_repo_env() -> bool:
    """Load <repo>/.env, chdir to the repo, make the token dir absolute, and harden SDK logging
    for stdio. Returns True if a .env file was found."""
    root = repo_root()
    loaded = load_dotenv(root / ".env")
    # chdir to the repo so RELATIVE non-store paths resolve there (conf/, logs/, .webull-tokens,
    # and any legacy relative *_DIR override) — Claude Desktop launches the MCP from
    # C:\Windows\System32, where those writes would fail with PermissionError. The 8 runtime data
    # stores are repo-anchored regardless of cwd via webull_api/paths.py::data_dir. No-op when
    # already in the repo (tests).
    os.chdir(root)
    token_dir = os.environ.get("WEBULL_OPENAPI_TOKEN_DIR")
    if token_dir and not os.path.isabs(token_dir):
        os.environ["WEBULL_OPENAPI_TOKEN_DIR"] = str((root / token_dir).resolve())
    _harden_sdk_logging()
    return loaded
