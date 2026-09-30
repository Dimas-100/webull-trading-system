"""Builds and caches the per-transport Webull clients from Settings.

Each service gets its own ApiClient because their hosts differ; add_endpoint keys
on region, so one ApiClient cannot hold two service hosts at once.
"""
from __future__ import annotations

from functools import lru_cache

from dotenv import load_dotenv

from webull.core.client import ApiClient
from webull.core.http.initializer.client_initializer import ClientInitializer as _ClientInitializer
from webull.data.data_client import DataClient
from webull.trade.trade_client import TradeClient

from .config import Settings, load_settings

# ── SDK compat shim (webull-openapi-python-sdk 2.0.10) ───────────────────────
# On TradeClient/DataClient construction the SDK probes `GET /openapi/config` to learn
# whether token-checking is enabled. The UAT host does not serve that route, so the SDK
# raises on the 404 *before* the documented token-create + SMS-verify flow can run
# (region "hk" skips this probe; "us" is hardcoded to make the network call). We wrap the
# probe so any failure falls back to "enabled" -> proceed with the token flow, which is
# the correct behavior on both UAT and production. See CLAUDE.md "Gotchas".
_orig_check_token_enable = _ClientInitializer._check_token_enable


def _safe_check_token_enable(api_client):
    try:
        return _orig_check_token_enable(api_client)
    except Exception:
        return True


_ClientInitializer._check_token_enable = staticmethod(_safe_check_token_enable)
# ─────────────────────────────────────────────────────────────────────────────

# ── Route SDK file logs into <repo>/logs/ (keeps the repo root uncluttered) ──
# Mechanism lives in sdk_logging — standalone (no prod-config import) so the sandbox
# client can apply the same reroute. Calling it here keeps every prod-path import of
# this module rerouted exactly as before.
from .sdk_logging import reroute_sdk_file_logs

reroute_sdk_file_logs()
# ─────────────────────────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_dotenv()  # populate os.environ from .env (no-op if absent; never overrides real env)
    return load_settings()


def _api_client(service: str) -> ApiClient:
    s = get_settings()
    client = ApiClient(s.app_key, s.app_secret, s.region)
    client.add_endpoint(s.region, s.host(service))
    return client


@lru_cache(maxsize=1)
def trade_client() -> TradeClient:
    return TradeClient(_api_client("trade"))


@lru_cache(maxsize=1)
def data_client() -> DataClient:
    return DataClient(_api_client("data"))
