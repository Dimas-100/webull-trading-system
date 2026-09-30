# Roadmap — RSI2-only (rewritten 2026-09-29)

> **Open items only.** The project runs one real strategy: RSI2 on the real book at S6 sizing (the 17:30 suite
> queues, the autopilot places, protects and exits). Shipped work lives in `docs/BUILD-LOG.md`; the parked
> systems are archived at git tag `archive/pre-rsi2-only-2026-09-29` (`docs/ARCHIVE.md`). The pre-rewrite
> roadmap is in that tag's `docs/ROADMAP.md`.

0. **The public onboarding kit lives in its own repo:** https://github.com/Dimas-100/ai-trading-kit (since
   2026-09-30). This repo stays the owner's RSI2 system.

1. **~20-trade real-book review, around 2026-10-02.** The trade-review skill over the real RSI2 round trips:
   live expectancy, win rate and hold vs the 20-year base rate (+0.84%/trade), slippage vs the next-open fill,
   stop hits. The last review (2026-09-20) had 13 closed trades.

2. **RSI2-only S6 backtest.** The S6 result (CAGR 28.2%, max drawdown 11.7%,
   `docs/reviews/2026-09-10-sizing-study-confirm.json`) pooled RSI2 + IBS in six shared slots. RSI2 alone at
   equity ÷ 6 is untested. Pre-register the cells, run `scripts/sizing_study.py` (or a variant) on RSI2 alone,
   and record the result before any sizing change leans on it.

3. **Raise the per-lot size — gated.** Raise `WEBULL_RSI2_REAL_DOLLARS` and `WEBULL_AUTOPILOT_MAX_NOTIONAL`
   (owner env) only once the account is at least ~$4–5k AND the live edge is at least half the backtest's
   per-trade expectancy over the review sample. Until then the $500 / $525 caps stay.

4. **Margin 1.25–1.5× — later, pre-registered.** Only after $10k in the account, 30–50 live trades, and
   drawdown circuit breakers built and tested (halt new entries at a set drawdown). Write the rule before any
   margin is used.

5. **Known open items that still matter to RSI2.**
   - **Holiday guard gap:** the autopilot has no NYSE-holiday guard of its own (the stop-restore fix is still
     needed too). `webull_api/market_calendar.NYSE_HOLIDAYS` is now the shared list; extend it every year.
   - **Evening-run catch-up race:** on a wake catch-up, the 17:45 autopilot evening run can finish before the
     17:30 suite's `rsi2_real` step has queued the day's decisions, so a signal waits a day (or an exit row is
     missed until the next run). Order the two, or make the autopilot wait for the day's rsi2_real run-log row.
