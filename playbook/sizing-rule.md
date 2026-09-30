# Real-book sizing rule

**Owner:** Dimas
**Governs:** discretionary real-money entries on the live Webull account, while the RSI2 real path
stays gated behind its own arming checklist. Once that path arms, sizing there is its own gate,
not this rule.
**Source:** the sizing & stacking study's PASS (`docs/reviews/2026-09-10-sizing-study-memo.md`,
cell S6) plus the shared-pool shadow design (`docs/superpowers/specs/2026-09-11-pool-shadow-design.md`
§3). This file is that spec section written for me to actually follow, not a new decision.
**Effective date:** 2026-09-11.
**No code changes here.** The autopilot's submit gate, the whole-share floor, the confirm gate —
none of it moves. This is guidance for my own hand at entry, checkable in ten seconds with the
script below.

---

## The rule

- **Target: one sixth of equity per lot, whole shares, at most six lots counting BOTH plans
  together** (swing RSI2 + swing IBS ETF — the S6 rule the study passed on both its develop and
  confirm windows). `slot = equity / 6`.
- **Below the size where a sixth affords the universe** — about $10,000 at today's prices; $5,000
  already affords 26 of the 28 swing names and every ETF — the whole-share floor binds before the
  slot math does. Below that level, size instead by the **S4 cell**: at most **four lots**, each
  **up to a quarter of equity** (`slot ≤ equity / 4`), never one lot above a quarter, and only
  names whose single share actually fits the slot. S4 sits inside the study's drawdown budget and
  within noise of S6 — it isn't a downgrade, it's the same discipline at a size where sixths can't
  buy a share.
- **Today's real book** (three lots on roughly $1,600) already sits inside S4 without changing
  anything.
- **Skip, never stretch.** A name whose one share costs more than the slot gets skipped that day.
  The size never gets raised to fit it — that's the whole rule, in one sentence.

## The autopilot per-order cap

`WEBULL_AUTOPILOT_MAX_NOTIONAL` (owner-set env, currently $525) stays where it is until `equity / 6`
actually exceeds it — roughly **$3,150 of equity**. At that point, set it to `equity / 6` rounded
down to the nearest $25, and re-check it monthly (equity moves; the cap should track it, not lag it
by months). **No code change either way** — the gate itself (`safety.should_submit`, the dry-run
default, the prod gate) stays byte-untouched; this is an env value, not a rail.

**Since 2026-09-23 the RSI2 real sleeve applies this rule itself** (spec
`docs/superpowers/specs/2026-09-23-rsi2-real-equity-sizing-design.md`): with
`WEBULL_RSI2_REAL_SLOT_DIVISOR=6` in `.env` the 17:30 runner sizes each lot as net liq ÷ 6, clipped
under the cap, from the balance it already reads. The evening note's RSI2-real line prints the budget
and what bound it; `cap binds: … — raise WEBULL_AUTOPILOT_MAX_NOTIONAL` is the one manual step left,
and it is the step above. `WEBULL_RSI2_REAL_DOLLARS` is the older fixed figure — delete it when the
divisor is set (left set, it is one more ceiling and the note says so).

## Checking it — `scripts/sizing_guidance.py`

```
.venv\Scripts\python.exe scripts\sizing_guidance.py [--equity N]
```

Prints, at live Tiingo closes: the swing names and ETFs one share fits at `equity/6` and at
`equity/4`, plus the same picture across a fixed grid ($1,600 / $3,200 / $5,000 / $10,000 /
$25,000) so I can see where the whole-share floor stops binding without doing the arithmetic by
hand. `--equity` defaults to the latest real net liq (falls back to $1,600 if that's unreadable).
Read-only — Tiingo store + `netliq_store` only, no broker call, no order path.

## What this is not

This is guidance for MY discretionary entries; the RSI2 real sleeve now sizes from the same rule
automatically (above), and the lot count (`WEBULL_RSI2_REAL_MAX_LOTS`) stays my hand. Coupling the
shared pool to either paper runner (so the runners size FROM this rule instead of me checking it by
hand) is phase 2 of the pool-shadow spec, gated on ≥ 10 clean shadow sessions, the IBS runner's
ten-session review, and my own go — see spec §4. Nothing here arms early.
