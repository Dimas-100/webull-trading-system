---
name: trade-planner
description: >-
  Use when about to take a Webull swing trade — sizing a stock position,
  planning an entry/stop/target, checking whether a setup qualifies, or asking
  "should I take this," "how many shares," "where's my stop," "does this pass my
  rules." Runs a candidate through the swing-trading-plan before you place it.
---

# Trade Planner (Webull swing book)

## What this is

A pre-trade **mechanical executor** for the Webull swing book. Bring a
candidate; this runs it through `trading/swing-trading-plan.md` — the gates, the
sizing math, the caps and circuit breakers — and returns a **journal-ready trade
plan** or a clean **SKIP** naming the gate that failed.

The plan is followed **mechanically**: a failed gate is a SKIP, not a maybe.
"It looks strong" is not an override — taking a trade the gates reject is a rule
violation by the plan's own definition (setup drift), and *inventing a target
beyond real resistance to clear the R:R bar* is gaming the rule, not passing it.
The skill applies the rules; it never bends them. It plans — **you** place the
trade in Webull.

## Step 0 — load the rulebook (source of truth)

Read `trading/swing-trading-plan.md` and use ITS numbers — don't hardcode:
`BOOK`, the gate thresholds, the sizing formula, the caps, the breakers. The
plan is revisable at scheduled reviews and `BOOK` is the user's call.
**If `BOOK` is still the `$______` placeholder, ask for it before sizing
anything** — every formula depends on it.

## Step 1 — disqualifier check (any one → SKIP)

Kill it fast if any hard disqualifier is present (plan §3): earnings within 10
trading days · price outside `$10 … 0.20×BOOK` · avg volume < 1M/day or < $20M/day
· ATR(14) > 7% of price · daily downtrend (50<200 SMA, or price<200 SMA) ·
leveraged/inverse ETF · binary-event name · >10% gap in the last 5 sessions ·
weekly chart clearly down. Regime: `SPY < its 200-SMA` → at most **1** open
position, or stand aside.

## Step 2 — gather the daily-chart inputs

Ask for whatever isn't provided (the user reads these off the EOD chart):
price · 50 & 200 SMA · RSI(14) · ATR(14) · 20-day avg volume · pullback swing
low · marked horizontal support · trigger-candle high · nearest resistance. Also:
current open positions + their risk, and recent realized P&L (for Step 5).

## Step 3 — run Gates 1–5 in order (stop at first failure)

1. **Trend up:** `50 SMA > 200 SMA` AND `price > 200 SMA`.
2. **Valid pullback:** pullback low within `1×ATR` of the 50 SMA **or** a marked
   support, AND RSI(14) dipped into **35–45**. (RSI < 30 while below the 50 SMA →
   not a dip → SKIP.)
3. **Reversal trigger:** the latest *completed* daily candle closes above the
   prior day's high, or is a bullish engulfing/hammer closing in its upper half.
   **Entry = buy-stop at trigger high + $0.05.**
4. **Confirmation:** trigger-day volume ≥ 20-day average (if within 10% below,
   MACD histogram turning up *may* substitute — one, not both, required).
5. **Reward:risk ≥ 2.0:** `(nearest real resistance − entry) / (entry − stop) ≥ 2.0`.
   The target is the nearest *meaningful* resistance — you don't get to move it
   higher to make the ratio work.

Any gate fails → **SKIP**, name the gate.

## Step 4 — stop, then size (compute risk AFTER floor())

- **STOP** = `pullback swing low − 0.25×ATR(14)`. **Sanity cap:** if
  `entry − STOP > 2×ATR` → SKIP (entry too extended above support).
- `risk_dollars = BOOK × 1%` · `shares = floor(risk_dollars / (entry − stop))`.
- Then the **actual** risk: `actual_risk_$ = shares × (entry − stop)` ·
  `actual_risk_% = actual_risk_$ / BOOK` · `notional = shares × entry`.
- **Granularity rules:**
  - `shares = 0` → take **1 share only if** its risk ≤ **2% of BOOK**; else SKIP.
  - `actual_risk_% < 0.5%` → size up toward the 2% ceiling **but** keep
    `notional ≤ 40% of BOOK`; if you can't, SKIP (sub-0.5% is noise).
  - Never average down / add to a loser to "fix" size.
- **Minimum viable trade — all three or SKIP:** `0.5% ≤ actual_risk_% ≤ 2%`,
  ≥ 1 whole share, `notional ≤ 40% of BOOK`.

Compute `actual_risk_%` **after** `floor()` every time — silent risk inflation
from rounding + a wide stop is the #1 small-account killer.

## Step 5 — portfolio caps & circuit breakers

SKIP or size down if any is breached (plan §6, §8): open positions already at
**3** (or **1** in a risk-off regime) · adding this pushes **total open risk >
4% of BOOK** · `notional > 40% of BOOK` · a breaker is live (daily ≥ −2R/−4%,
weekly ≥ −6% or 3 straight losses, drawdown halt −15% from peak, or an active
revenge-trade cooldown).

## Step 6 — output

**If it passes — a journal-ready plan:**

| Field | Value |
|---|---|
| setup | pullback-in-trend (+ 50 SMA or which horizontal support) |
| entry | trigger high + $0.05 |
| stop | swing low − 0.25×ATR |
| target | the **lower** of +2R and the nearest resistance (whichever you'd hit first) |
| shares | from `floor()` |
| actual risk | `$X (Y% of BOOK)` |
| R:R | the Gate-5 ratio |
| exit mode | A (full exit; required when ≤ 3 shares) or B (scale + trail; only ≥ 4 shares) |

Then offer to log it once filled — the journal fields map 1:1 (plan Appendix).

**If it fails — `SKIP` + the exact gate/rule that killed it**, one line. A SKIP
is the correct, common outcome; most days the right action is no trade.

## What this does not do

It doesn't place the trade (you do, in Webull), doesn't manage open positions
intraday (this is EOD), and doesn't change a rule mid-trade — the plan is frozen
between scheduled reviews. Rule changes go in `swing-trading-plan.md` at a
review, never here.
