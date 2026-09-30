"""Loads settings from the environment and maps (env, service) -> endpoint host.

Switching test<->prod changes ONLY the host strings returned here.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass

_ENDPOINTS = {
    "test": {
        "trade": "us-openapi-alb.uat.webullbroker.com",
        "data": "us-openapi-alb.uat.webullbroker.com",  # HTTP market data = trade host (data-api.* is MQTT only)
        "data_mqtt": "us-data-api.uat.webullbroker.com",
        "events": "us-openapi-events.uat.webullbroker.com",
    },
    "prod": {
        "trade": "api.webull.com",
        "data": "api.webull.com",  # HTTP market data = trade host (data-api.* is MQTT only)
        "data_mqtt": "data-api.webull.com",
        "events": "events-api.webull.com",
    },
}


@dataclass(frozen=True)
class Settings:
    app_key: str
    app_secret: str
    region: str
    env: str  # "test" | "prod"

    def host(self, service: str) -> str:
        try:
            return _ENDPOINTS[self.env][service]
        except KeyError as exc:
            raise KeyError(f"unknown service {service!r} for env {self.env!r}") from exc


def load_settings(environ=None) -> Settings:
    """Build Settings from a mapping (defaults to os.environ). Pure given its input."""
    environ = os.environ if environ is None else environ
    env = environ.get("WEBULL_ENV", "test").strip().lower()
    if env not in _ENDPOINTS:
        raise ValueError(f"WEBULL_ENV must be 'test' or 'prod', got {env!r}")
    app_key = environ.get("WEBULL_APP_KEY", "").strip()
    app_secret = environ.get("WEBULL_APP_SECRET", "").strip()
    region = environ.get("WEBULL_REGION", "us").strip().lower()
    if not app_key or not app_secret:
        raise RuntimeError("WEBULL_APP_KEY and WEBULL_APP_SECRET must be set (see .env.example)")
    if env == "prod":
        _print_prod_banner()
    return Settings(app_key=app_key, app_secret=app_secret, region=region, env=env)


def _print_prod_banner() -> None:
    # stderr, not stdout: a loud warning belongs there, and an MCP stdio server uses stdout for
    # its JSON-RPC protocol (any stray stdout write corrupts it).
    bar = "!" * 64
    print(f"\n{bar}\n!!  WEBULL_ENV=prod  --  LIVE ACCOUNT / REAL MONEY ENABLED  !!\n{bar}\n", file=sys.stderr)
