"""Print balance + positions for every account on the active env."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root on path

from webull_api import portfolio


def _first_account_id(acct) -> str:
    # account_list returns a list of account dicts; pull the id field defensively.
    if isinstance(acct, list) and acct:
        a = acct[0]
        return a.get("account_id") or a.get("accountId") or a.get("id")
    raise RuntimeError(f"unexpected account_list shape: {acct!r}")


def main() -> int:
    accounts = portfolio.list_accounts()
    print("accounts:", json.dumps(accounts, indent=2))
    acct_id = _first_account_id(accounts)
    print(f"\nbalance ({acct_id}):", json.dumps(portfolio.get_balance(acct_id), indent=2))
    print(f"\npositions ({acct_id}):", json.dumps(portfolio.get_positions(acct_id), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
