import pytest
from webull_api.config import load_settings, Settings

BASE = {"WEBULL_APP_KEY": "k", "WEBULL_APP_SECRET": "s"}


def test_defaults_to_test_env():
    s = load_settings(environ=dict(BASE))
    assert s.env == "test"
    assert s.region == "us"
    assert s.host("trade") == "us-openapi-alb.uat.webullbroker.com"


def test_prod_selects_prod_hosts_and_warns(capsys):
    s = load_settings(environ={**BASE, "WEBULL_ENV": "prod"})
    assert s.env == "prod"
    assert s.host("trade") == "api.webull.com"
    assert "REAL MONEY" in capsys.readouterr().err  # banner goes to stderr (stdout is the MCP JSON-RPC channel)


def test_missing_keys_raises():
    with pytest.raises(RuntimeError):
        load_settings(environ={"WEBULL_ENV": "test"})


def test_invalid_env_raises():
    with pytest.raises(ValueError):
        load_settings(environ={**BASE, "WEBULL_ENV": "staging"})


def test_host_for_each_service():
    s = load_settings(environ=dict(BASE))
    assert s.host("data") == "us-openapi-alb.uat.webullbroker.com"
    assert s.host("data_mqtt") == "us-data-api.uat.webullbroker.com"
    assert s.host("events") == "us-openapi-events.uat.webullbroker.com"
