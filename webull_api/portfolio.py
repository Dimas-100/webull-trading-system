"""Read-only account data: accounts, balances, positions."""
from __future__ import annotations

from .client import trade_client


def _check(res):
    if res.status_code != 200:
        raise RuntimeError(f"portfolio error {res.status_code}: {res.text}")
    return res.json()


def list_accounts():
    return _check(trade_client().account_v2.get_account_list())


def get_balance(account_id: str):
    return _check(trade_client().account_v2.get_account_balance(account_id))


def get_positions(account_id: str):
    return _check(trade_client().account_v2.get_account_position(account_id))
