# Webull Day-Trading Plan

**Owner:** Dimas
**Account governed:** Webull day-trading book ONLY — a **separate cash account**, kept apart from the swing book ([swing-trading-plan.md](swing-trading-plan.md)) and all long-term accounts.
**Status:** v1.0 — HYPOTHESIS to be validated by my own forward journal. Nothing here is proven. Day trading has the highest beginner loss rate of anything in this folder; this plan exists to make the *learning* cheap and measurable, not to make money.
**Effective date:** 2026-06-16
**PAUSED 2026-09-08 (owner decision):** real trades under v1.0 are suspended. A five-year replay of v1.0 as coded on the
point-in-time universe found no edge (4,358 trades, 39% win, −0.11%/trade, negative in every year and slice; zero gross of
slippage) — `docs/reviews/2026-09-08-orb-backtest-memo.md`. The paper day session ran 2026-09-08 → 09-11 (four sessions, zero fills) and was CANCELLED 2026-09-14 (owner) — nothing collects day-trading evidence now. A revision
program (pre-registered candidates, holdout confirmation; price band widened to $5–$180) is in progress; v1.1 replaces this
document only through the scheduled review with owner sign-off.
**Next scheduled rule review:** after 20 closed trades, or 1 month, whichever comes first.

---

## 0. How to read this document

Same contract as the swing plan: **follow it mechanically.** Where a rule could be read two ways, it is written to force one action. If you catch yourself "interpreting" a rule mid-session, that is the tell that the rule needs sharpening — write it in the journal and fix it **at the next scheduled review, never mid-trade.**

**The goal at this size is execution reps, not profit.** At a $150 book, your per-trade P&L will be a few dollars and the bid/ask spread is a proportionally large tax. You are not here to grow $150 into $1,000. You are here to learn — without bleeding real money — whether you can (a) wait for one defined setup, (b) size it correctly, (c) honor a stop, and (d) walk away when the day's rules say stop. P&L is the *last* thing you judge.

**Two numbers to set before you trade:**

- **`DAYBOOK` = the hard dollar cap on this account.** The most that will *ever* sit in this account — money you can lose entirely without it touching your life. Worked examples below use **`DAYBOOK = $150`** (the middle of your $100–200 range). Substitute your actual number; the rules don't change. Lock it at the first review: `DAYBOOK = $150` (set 2026-06-16).
- **Whole shares only**, same as the swing book. This creates the same granularity problem, made worse by how small `DAYBOOK` is — Section 6 handles it head-on.

### The cash-account reality (read this twice — it shapes everything)

This is a **cash account**, not margin (margin needs $2,000 minimum, which the June 2026 PDT changes did **not** remove). Two consequences:

1. **No shorting, no leverage.** You can only buy, and only with money you have. This plan is **long-only** by necessity — and that's good, it removes a whole category of ways to get hurt.
2. **Cash settlement (T+1).** When you sell, the proceeds are *unsettled* until the next business day. Buying again with unsettled cash is a **good-faith violation** (three of those and the broker freezes you to settled-cash-only for 90 days). Practical effect: **you get ONE full-size day trade per day.** Do not fight this — it is the single best overtrading guardrail you have. The old $25,000 Pattern Day Trader rule never applied to cash accounts anyway; settled cash is your real limit.

---

## 1. Strategy selection

**The one setup you master: the OPENING RANGE BREAKOUT (ORB), long side only.**

After the open, the market sets a high and a low in its first few minutes. When price decisively breaks *above* that early high on real volume, you buy the breakout, put your stop just under the breakout candle, and target a defined multiple of that risk — exiting the same day, always.

**Why this one, specifically, for a beginner on a tiny cash account:**

1. **Bright-line, mechanical levels.** The opening range is an objective high/low you can draw on the chart. "Broke the level" or "didn't" is unambiguous — which is exactly what produces clean journal data.
2. **The open is where the edge and the volatility live.** The first 30–60 minutes have the day's heaviest volume and clearest directional moves. A breakout there has the best chance of *following through* fast — and intraday, you need it to work fast.
3. **Tight, definable risk.** Your stop sits just under the breakout candle, so each share risks little — the only way a $150 whole-share account can take a real position.
4. **It forces patience.** You do nothing until the range is set and a clean break fires. No range, no break → no trade. That trains the instinct day trading punishes most: the urge to be *in*.
5. **Long-only matches the cash account.** No borrow, no short squeeze risk, no margin.

**Why not the alternatives (so you can defend the choice):** Scalping — needs near-perfect execution and pays in pennies that the spread eats at your size. Momentum/low-float runners — where beginners get destroyed; fake-outs and halts. News fading — requires reading order flow you can't yet. VWAP mean-reversion — you'd be buying *into* weakness, hardest skill, worst beginner outcomes. **One setup. Master it across 30–50 trades before even considering a second — and only at a scheduled review. Taking a different setup because it "looks good" is a rule violation, not improvisation.**

---

## 2. Market, instrument & timeframe

| Item | Choice | Why |
|---|---|---|
| **Instrument** | Liquid US **stocks / ETFs** only, long-only | No leverage surprises; loss bounded by position size. No options, futures, or crypto in this book. |
| **Price band** | **$5 ≤ price ≤ $30** | Cheap enough to buy a workable share count with $150, liquid enough to fill cleanly. Below $5 = junk/halts; above ~$30 you can afford too few shares. |
| **Trend/timing chart** | **5-minute** candles | 1-minute is too noisy for a beginner; 5-minute smooths the chop while still being intraday. |
| **Bias filter** | **VWAP** (volume-weighted average price) | One filter: only take longs while price is *above* VWAP. Keeps you on the buyers' side of the day. |
| **The clock** | Trade the **open: 9:30–11:00 ET**. (Your fallback midday window: 12:30–2:00 ET, same break-of-range logic on the most recent 30-min consolidation.) | The open is primary. Avoid the lunch lull (11:30–12:30) — thin, choppy, fake breakouts. |

**Hard "not today" filters:** skip trading entirely on days with a major scheduled release *at the open* (CPI, jobs report, FOMC decision days) until you have 20+ trades under your belt — those days whipsaw and are no place to learn a breakout.

---

## 3. Pre-market prep & the "in play" list (do this before 9:30)

Day trading is won before the bell. Each morning, build a tiny **"in play today"** list — aim for **2–4 names**, never more (you can only take one trade anyway):

A name earns a spot only if **all** of these are true:
- **Liquid:** 20-day average volume **≥ 5,000,000 shares/day** and **≥ $50M/day** dollar volume. (Higher than the swing plan — intraday you need tight spreads and instant fills.)
- **In your price band:** $5–$30.
- **In play:** has a *reason* to move today — an earnings reaction, news catalyst, or it's gapping and showing **elevated pre-market volume**. A stock with no catalyst usually just drifts.
- **Clean, not a low-float pump:** avoid sub-10M-share-float momentum names that have already run 50%+ pre-market. That's the casino, not this plan.

Mark each name's **pre-market high**, **prior-day high**, and **prior-day close** on the chart — these are your reference levels and likely targets. Set alerts. You are *not* entering yet.

---

## 4. Entry criteria — the ORB gates (ALL must be true, in order)

Indicators, deliberately minimal — **three tools, each one job.** Do not add a fourth.

| Tool | Its one job |
|---|---|
| **Opening range** (high/low of 9:30–9:45 ET, the first three 5-min candles) | Defines the level to break and the structure. |
| **Volume** (per 5-min candle vs. the prior candles) | Confirms the break is real buying, not drift. |
| **VWAP** | Directional filter — longs only while price is above it. |

1. **Gate 1 — Range is set:** let the first **15 minutes** (9:30–9:45 ET) complete. Mark the **opening-range high (ORH)** and **opening-range low (ORL)**. If that range is **wider than ~1.5%** of price, **skip the name** — the stop will be too wide to size, the move already happened.
2. **Gate 2 — Above VWAP:** at the time of the break, price is **above VWAP**. If it's below VWAP, no long. (No exceptions — this is your trend filter.)
3. **Gate 3 — Breakout trigger:** a **5-minute candle closes above the ORH** (not just wicks above — *closes* above). That candle is your **signal candle**. **Entry = a buy-stop at the signal candle's high + $0.03**, working only if price trades through it on the next candle. No close above ORH = no trade; you wait.
4. **Gate 4 — Volume confirmation:** the signal candle's volume is **visibly larger** than the few candles before it (a real expansion, not a quiet drift over the line). Weak-volume breakouts fail — skip.
5. **Gate 5 — Reward:risk ≥ 2.0:** with the stop from Section 5 and the **nearest real resistance** (pre-market high, prior-day high, or obvious round number) as the target, `(target − entry) / (entry − stop) ≥ 2.0`. If the nearest resistance is less than 2R away, **reject it** — no moving the target up to force the math.

**Explicit "do not enter" list:** any gate fails · price below VWAP · the range is too wide (Gate 1) · the break has no volume · it's past **11:00 ET** (open window closed) · a "not today" news day · you've already taken your one trade · a circuit breaker is live.

---

## 5. Exit criteria — all defined BEFORE you enter

### (a) Stop-loss — set as a resting order the moment you're filled, non-negotiable
- **Placement:** `STOP = signal-candle LOW − $0.03`. This is the price that says "the breakout failed."
- **Hard rule:** the stop **only ever moves UP**, never down. Widening a stop to "give it room" is the cardinal sin — it uncaps your loss. (Same rule as the swing book.)
- If price closes a 5-min candle **back below the ORH**, the breakout has failed — exit at market even if the stop hasn't printed. (Failed breakouts reverse fast; don't wait.)

### (b) Profit target
- **Default — full exit at +2R** (a limit at `entry + 2 × (entry − stop)`), or at the nearest resistance if it sits just under +2R. With this book you'll usually hold ≤ 5 shares, so **one target, one clean exit (Mode A).**
- *(Scaling — sell half at +2R, trail the rest under the 5-min lows — is allowed only when a position is **≥ 6 shares**. It rarely will be. Don't force it.)*

### (c) Time-stop
- If the trade has **not reached +1R within 30 minutes** (six 5-min candles), **exit at market.** A real breakout works quickly; a stalled one is telling you it failed.

### (d) The hard flat rule (this is what makes it *day* trading)
- **You are flat — no open positions — by 11:00 ET if you traded the open**, and **under all circumstances by 3:55 ET.** **Never** hold overnight in this book. No exceptions, no "it'll come back tomorrow."

---

## 6. Money management & sizing (the part that quietly blows up small accounts)

### Risk per trade: **1% target, 2% hard ceiling**
At `DAYBOOK = $150`: **1% = $1.50** intended risk, **2% = $3.00** hard ceiling. Never exceed $3.00 of risk on a trade. If the smallest sane whole-share position still risks more than $3.00, **skip.**

### The sizing formula — and the cash cap that usually binds first
```
risk_dollars  = DAYBOOK × 1%              (= $1.50)
risk_shares   = floor( risk_dollars / (entry − stop) )
cash_shares   = floor( DAYBOOK / entry )   ← you cannot spend more cash than you have
shares        = min( risk_shares, cash_shares )
```
Then the **actual** risk you took:
```
actual_risk_$ = shares × (entry − stop)
actual_risk_% = actual_risk_$ / DAYBOOK
notional      = shares × entry            (will be ≤ DAYBOOK in a cash account)
```
Because the account is tiny, **`cash_shares` often caps you before `risk_shares` does** — you simply can't afford the full risk-based size. That's fine, as long as `actual_risk_$ ≤ $3.00`. If even `cash_shares` risks more than $3.00 (stop too wide for the price), **skip — the trade doesn't fit this book.**

### Granularity rules (same spirit as the swing plan)
1. Compute `shares` as above.
2. If `shares = 0` (even 1 share risks > $1.50): take **1 share only if** its risk ≤ **$3.00 (2%)**; otherwise **SKIP**.
3. Never average down or add to a loser — ever.
4. **Minimum viable trade — all three or SKIP:** `0.5% ≤ actual_risk_% ≤ 2%` ($0.75–$3.00) **and** ≥ 1 whole share **and** the trade clears Gate 5 (≥ 2R).

> **Always recompute `actual_risk_%` AFTER `floor()`.** Rounding + a wide stop silently inflating your real risk is the #1 small-account killer.

### Worked example (illustrative mechanics only — NOT proof of edge)
`DAYBOOK = $150`. A $20 stock breaks its opening range. Signal candle high $20.10, low $19.85 → **entry $20.13**, **stop $19.82**, risk/share **$0.31**.
- `risk_shares = floor(1.50 / 0.31) = 4`. `cash_shares = floor(150 / 20.13) = 7`. → `shares = min(4,7) = 4`.
- `actual_risk_$ = 4 × 0.31 = $1.24` (**0.83% of book** ✓). `notional = 4 × 20.13 = $80.52` ✓.
- Target +2R = `20.13 + 2 × 0.31 = $20.75`. If the prior-day high sits at/above $20.75, Gate 5 passes → **valid, 4 shares, Mode A, exit at $20.75 or stop $19.82.**
*(Made-up numbers to show the mechanics. They prove nothing about whether the strategy works — your journal does that.)*

### Daily limits
| Limit | Value at $150 | Rule |
|---|---|---|
| Trades per day | **1 target, 2 hard cap** | Cash settlement enforces ~1 anyway. After 2 round-trips, you're done regardless of outcome. |
| Max single notional | **≤ settled cash** (~$150) | A cash account can't exceed this; intraday concentration is OK because the stop defines the risk and you're flat by EOD. |
| Daily loss limit | **−2R or −3% of DAYBOOK (−$4.50), whichever first** | Hit it → **no more entries today.** |

---

## 7. When NOT to trade / circuit breakers

**Default state is FLAT. Most mornings the right action is no trade.** You act only when the open hands you a clean ORB.

**Stand aside when:** no name on your list sets a tradable range · price is below VWAP · the break has no volume · it's a major-news-at-the-open day · you're past 11:00 ET with no setup · you didn't sleep / are rushed / are trading to "make something happen."

**Circuit breakers (mechanical — stop means stop):**
| Breaker | Trigger | Action |
|---|---|---|
| Daily loss limit | −2R or −3% of DAYBOOK on the day | **Done trading for the day.** Close the platform. |
| Two-strike rule | **2 losing trades in a day** | **Done for the day** — even if you're not at the dollar limit. Two losses means it's not your day. |
| Weekly loss limit | −6% of DAYBOOK on the week, or 3 losing days | **Done for the week.** Resume at the weekend review. |
| Revenge-trade lock | The urge to "win it back" right after a loss | **Close the platform for the day.** This feeling is the single most expensive thing in day trading. |

---

## 8. Journaling (every trade, no exceptions)

Log each round-trip the same day, mapping to your existing journal fields:

| Field | Comes from |
|---|---|
| setup | "ORB-long" (+ which level: ORH break) |
| entry | buy-stop fill at signal-candle high + $0.03 |
| stop | signal-candle low − $0.03 |
| exit | target / failed-break / time-stop / hard-flat |
| shares | `min(risk_shares, cash_shares)` |
| planned risk | `actual_risk_$` and `actual_risk_%` |
| R-multiple | result ÷ 1R |
| **plan-followed (yes/no)** | did all 5 gates hold and were exits honored? ← the headline metric |
| result | realized P&L (note the spread/slippage paid) |
| lesson | one line |

A by-the-book trade that stops out at **−1R is a GOOD trade** (process correct). A winner you got by breaking a gate is a **bad trade you got paid for** — the most dangerous kind. Score process, not luck.

---

## 9. Review cadence & the immutability rule

| Cadence | When | What | Change a rule? |
|---|---|---|---|
| Daily | After the close | Journal every trade; note the biggest mistake. | **No.** |
| Weekly | Weekend | Tally trades, win rate, avg R, expectancy, **plan-followed %**, biggest rule-break. | **No.** |
| System | Every **20 closed trades** or monthly | Full metrics over the sample; decide what (if anything) changes, written here with a reason + date. | **Yes — only here.** |

**The plan is frozen mid-trade and between reviews.** You change rules at a scheduled review or not at all — never to "fix" the last loss. In the tuition phase, **`plan-followed %` (target ≥ 90%) matters more than P&L.** Judge the *system* over 30–50+ trades, never on a single day.

---

## 10. Beginner traps (reread before each session)

1. **Trading before the range is set.** No range, no setup. Wait the full 15 minutes.
2. **Chasing a breakout you missed.** If you didn't get filled at entry + $0.03, let it go. The next candle is not your entry.
3. **Widening the stop.** Forbidden. Stops move up only. The moment you lower one, you've uncapped your loss.
4. **Taking the second, third, fourth trade.** Cash settlement *and* your own rules cap you at 1–2. Overtrading is how small accounts die.
5. **Holding past the flat time** hoping it comes back. This is no longer day trading; it's an unplanned overnight gamble.
6. **Trading below VWAP because it "looks like it'll bounce."** That's a different (harder) setup you haven't earned yet.
7. **Revenge trading after a loss.** Close the platform. The market is open again tomorrow.
8. **Treating the $150 P&L as the point.** It isn't. The rep — wait, size, stop, walk away — is the point.

---

*v1.0 — a hypothesis to be tested, not a guarantee. Trade tiny, journal everything, and let 30–50 trades tell you whether you can execute. If the data says day trading isn't for you, that is a successful, cheap thing to have learned.*
