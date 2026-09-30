"""Daily net-liq snapshot — the suite's net-liq step. Values the real book (broker balance,
net-liq AND cash — the cash trail is the flows step's deposit-detection baseline) and both
paper books, then appends ONE line per ET day to data/activity/netliq_history.jsonl for
the Home page's equity curve. Read-only everywhere: broker reads only; paper accounts are
loaded and marked but NEVER saved (the exits runners own settlement). No `trading` import,
no order path. Mirrors the suite's lock + same-day-guard discipline (run-log key: "netliq")."""
from __future__ import annotations

import logging

from webull_api import run_log

from . import netliq_store, paper_service, runner_util

_log = logging.getLogger(__name__)
_KEY = "netliq"


def best_real_account() -> tuple[str | None, float | None, float | None]:
    """(account_id, net_liq, total cash) of the managed real book — the largest finite
    net-liq account. All-None on any failure — a broker outage records null, never a
    guess. Also the flows step's account source, so the two steps always agree on which
    book is "the" book."""
    try:
        from webull_api import portfolio
        best: tuple[str | None, float | None, float | None] = (None, None, None)
        for a in runner_util.retry_throttled(portfolio.list_accounts) or []:
            if not isinstance(a, dict):
                continue
            aid = a.get("account_id") or a.get("id")
            if not aid:
                continue
            bal = runner_util.retry_throttled(lambda: portfolio.get_balance(str(aid))) or {}
            raw = bal.get("total_net_liquidation_value") or bal.get("net_liquidation")
            try:
                v = float(raw)
            except (TypeError, ValueError):
                continue
            if best[1] is None or v > best[1]:
                try:
                    cash = float(bal.get("total_cash_balance"))
                except (TypeError, ValueError):
                    cash = None
                best = (str(aid), v, cash)
        return best
    except Exception:
        _log.exception("netliq: real book unavailable")
        return (None, None, None)


def _paper_equity_net_liq() -> float | None:
    try:
        from webull_api.paper import engine as paper_engine
        acct = paper_service.load_account()
        prices = paper_service.last_prices(paper_service.account_symbols(acct))
        v = paper_engine.account_view(acct, prices).get("net_liquidation")
        return float(v) if v is not None else None
    except Exception:
        _log.exception("netliq: paper equity book unavailable")
        return None


def _paper_options_net_liq() -> float | None:
    try:
        from webull_api.paper import options_engine as eng
        from webull_web import paper_options_service as svc
        acct = svc.load_account()
        marks = svc.marks_for(sorted(set(svc.account_occs(acct))))
        v = eng.account_view(acct, marks).get("net_liquidation")
        return float(v) if v is not None else None
    except Exception:
        _log.exception("netliq: paper options book unavailable")
        return None


def _fmt(v: float | None) -> str:
    return "null" if v is None else f"${v:,.2f}"


def run(force: bool = False) -> dict:
    iso, today = paper_service.now_et()
    with runner_util.filelock(_KEY, iso) as got:
        if not got:
            return {"result": "no_op", "errors": [],
                    "summary": "Net-liq: another run in progress", "ran_at": iso}
        if runner_util.already_ran_today(_KEY, today, force):
            return {"result": "no_op", "errors": [],
                    "summary": "Net-liq: already ran today", "ran_at": iso}

        _aid, real, cash = best_real_account()
        pe = _paper_equity_net_liq()
        po = _paper_options_net_liq()
        errors = [f"{name} book unavailable" for name, v in
                  (("real", real), ("paper_equity", pe), ("paper_options", po)) if v is None]
        if real is None and pe is None and po is None:
            result = "error"
            summary = "Net-liq: no book could be valued — nothing recorded"
        else:
            netliq_store.append({"date": today, "ts": iso, "real": real, "real_cash": cash,
                                 "paper_equity": pe, "paper_options": po})
            result = "ok"
            summary = f"Net-liq: real {_fmt(real)} · paper-eq {_fmt(pe)} · paper-opt {_fmt(po)}"
        run_log.append([{"key": _KEY, "ts": iso, "result": result, "summary": summary,
                         "placed": 0, "errors": errors}])
        return {"result": result, "errors": errors, "summary": summary, "ran_at": iso}
