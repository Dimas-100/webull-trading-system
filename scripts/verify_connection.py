"""MILESTONE 1: confirm credentials + endpoint by listing accounts on the active env."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root on path

from webull_api.client import get_settings, trade_client


def main() -> int:
    s = get_settings()
    print(f"env={s.env}  region={s.region}  trade_host={s.host('trade')}")
    res = trade_client().account_v2.get_account_list()
    if res.status_code == 200:
        print("SUCCESS - accounts:")
        print(json.dumps(res.json(), indent=2))
        return 0
    print(f"ERROR {res.status_code}: {res.text}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
