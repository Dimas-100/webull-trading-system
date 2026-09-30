# Webull Swing-Trading Plan

**Owner:** Dimas
**Account governed:** Webull swing-trading book ONLY (kept separate from all long-term buy-and-hold accounts).
**Status:** v1.0 — HYPOTHESIS to be validated by my own forward journal. Nothing in here is proven.
**Effective date:** 2026-06-05
**Next scheduled rule review:** after 20 closed trades, or 1 month, whichever comes first.

---

## 0. How to read this document

This plan is meant to be followed **mechanically**. Where a rule could be read two ways, it is written to force one action. If you ever find yourself "interpreting" a rule in the heat of a trade, that is the signal that the rule needs sharpening — write the ambiguity in the journal and fix it **at the next scheduled review, never mid-trade.**

The goal of this plan is **consistency and measurability, not profit.** The first 30–50 trades are tuition. You are buying data about whether you can execute a defined edge without breaking your own rules. P&L is the byproduct you judge last.

Two things to set before you trade:

- **`BOOK` = the hard dollar cap on this account.** This is the single most important number and it is *your* decision, not mine. Definition: `BOOK` is the total amount of money you are willing to fund this Webull account with and lose entirely without it affecting your life. It is the ceiling — the most that will *ever* be in this account. A good month does **not** justify raising it. Every formula below uses `BOOK` as a variable so the plan works at any size. Worked examples use **`BOOK = $500`** (your example figure). If you fund it differently, substitute your number; the rules don't change. Lock your actual `BOOK` at the first review and write it here: `BOOK = $500` (raised 2026-06-16 from an initial $300, to widen the price ceiling to $100). At `BOOK = $500`: risk 1% = $5, 2% ceiling = $10, max single-position notional 40% = $200, max total open risk 4% = $20, price filter $10–$100.
- **Whole shares only.** You decided against fractional shares. That is the right call for discipline, and it creates the "granularity problem" that Section 6 solves head-on.

---

## 1. Strategy selection

**Primary strategy (the one you master): PULLBACK-IN-TREND (buy the dip inside an established uptrend).**

You enter long *only* when a stock is in a confirmed daily uptrend and has pulled back to support, and only after price shows it is turning back up. You are buying a temporary discount inside a rising trend — not predicting a bottom, not chasing a breakout.

**Why this one, specifically, for a beginner on a small whole-share account:**

1. **Tight, well-defined stops → small dollar risk → it actually fits a $500 whole-share account.** You enter near support, so your invalidation point (just below that support) is close. Close stops mean each share risks few dollars, which is the only way to take a real position when whole-share granularity is fighting you. Breakouts, by contrast, enter *extended* from support, forcing wide stops you can't afford here.
2. **Better reward-to-risk by construction.** Entering near support and targeting the prior high gives you room to make 2R+ on the move. Buying a breakout at the high gives you less room before resistance.
3. **You trade *with* the dominant trend.** The trend is the one edge with a defensible base rate for a beginner. Mean-reversion and range-trading ask you to fight the trend or call turns — far harder and more punishing to learn on.
4. **It forces patience instead of FOMO.** You wait for price to come to *you* at support. This is the single most useful habit to build, and the setup trains it automatically. Breakout and momentum trading reward chasing, which is exactly the instinct a beginner needs to *suppress*.
5. **Clean invalidation = clean journaling.** "Support broke" is an unambiguous, bright-line failure. That produces the crisp win/loss data your journal needs.

**Why not the others (so you can defend the choice):** Breakout — high false-breakout/whipsaw rate, wide stops. Momentum — needs fast, near-intraday management you won't give it on an EOD schedule. MA-crossover — lags badly, whipsaws in chop, late entries. Range — needs a clearly bounded range and constant management. Mean-reversion — you're betting *against* the immediate trend; highest skill requirement, worst beginner outcomes (catching falling knives).

**Secondary setup:** None. By your own choice, this is a single-strategy plan. Master one setup across your first 30–50 trades, then *consider* adding a second — but only at a scheduled review, never by drifting into it mid-week. **Setup drift (taking a trade that isn't this setup because it "looks good") is a rule violation, not a secondary strategy.**

---

## 2. Market & timeframe

Candlestick charts. Two timeframes, and they must agree before any trade is valid.

| Role | Timeframe | What it decides |
|---|---|---|
| **Trend definition** | **Daily** | Is this stock in an uptrend at all? (50/200 SMA + price location.) |
| **Entry / exit timing** | **Daily** | The pullback, the trigger candle, the stop, the target. |

- **Weekly chart** is used only as a *context glance* (is the bigger structure also up or at least not broken?). It is not a hard gate — but if the weekly is in a clear downtrend, treat that as a disqualifier under Section 3.
- **No intraday timeframes.** No 4h, no 15m, no day trading. You manage and decide at **end of day (EOD)**, after the close, on completed daily candles only. This matches your daily EOD schedule and removes the temptation to react to intraday noise.

**The agreement rule:** a trade is only valid when the **daily trend is up (Section 4, Gate 1)** *and* the **daily timing trigger fires (Section 4, Gate 3)** on the same chart. One timeframe defines the bias (long-only), the other defines the moment. If the trend gate fails, there is no trade to time — stop there.

---

## 3. Watchlist / screening (run at EOD, daily)

You check EOD daily, so screening is a short nightly pass that maintains a small watchlist, plus a final pre-entry check on any name that triggers.

**Universe:** liquid, US-listed common stocks and broad/sector ETFs. No OTC, no pink sheets, no ADRs of thinly traded foreign names.

**Hard liquidity & price filters (all required):**

| Filter | Threshold | Why |
|---|---|---|
| Price | **$10 ≤ price ≤ `0.20 × BOOK`** (at BOOK=$500: $10–$100) | Floor: avoids penny-stock junk and fragile sub-$5 names. Ceiling: with whole shares, a stock priced above ~20% of your book means you can afford only 1–2 shares, which wrecks position sizing and granularity. Keep names cheap enough to buy a workable share count. |
| Avg daily volume | **≥ 1,000,000 shares/day (20-day avg)** | Tight spreads, reliable fills, you can exit. |
| Avg daily dollar volume | **≥ $20,000,000/day** | Confirms real liquidity, not just a high share count on a cheap stock. |
| Daily ATR(14) as % of price | **≤ 7%** | Above this the stock is so volatile your stop gets hit by random noise. Filters out names too wild to manage on an EOD schedule. |

**Hard disqualifiers (any one → skip, no exceptions):**

- **Earnings inside 10 trading days** of today (check the calendar). Earnings is a binary gap event; a swing plan must never *hold into* it. If a name you already hold has earnings scheduled inside your hold, you exit before the event (Section 8).
- **Daily downtrend:** 50 SMA below 200 SMA, *or* price below the 200 SMA. (That is not a pullback candidate; it's a falling stock.)
- **Leveraged or inverse ETFs** (2x/3x, anything with "Bull/Bear/Ultra"). Decay and gap risk make them unfit for this plan.
- **Binary-event names:** pre-revenue biotech / clinical-trial or FDA-decision names, anything whose price is driven by a coin-flip announcement.
- **Recent gap shock:** a gap up or down greater than ~10% in the last 5 sessions (price is unstable; support/resistance levels are unreliable).
- **Weekly chart in a clear downtrend** (context check from Section 2).

**Output of screening:** a short watchlist (aim for ~5–15 names) of stocks in confirmed daily uptrends that are *near or approaching* support. You are not entering yet — you are queuing candidates and setting price alerts. Entry happens only when Section 4 fires.

---

## 4. Entry criteria

**Indicator set (deliberately minimal — five tools, each with one job). Do not add a sixth.**

| Tool | Exact parameter | Its one job | Why it earns a slot |
|---|---|---|---|
| Simple moving averages | **50 SMA & 200 SMA** (daily) | Define the trend | The single most important filter; trades only happen *with* the trend. |
| RSI | **RSI(14)** (daily) | Measure pullback *depth* | Tells you the dip is a healthy dip (35–45), not a collapse (<30). Not used for divergence games. |
| Volume | **20-day average volume** (daily) | Confirm real buying on the trigger | A reversal on rising volume is buyers stepping in, not drift. |
| ATR | **ATR(14)** (daily) | Place the stop + volatility filter | Quantifies "noise" so the stop sits beyond it, and powers Section 3's volatility filter. |
| MACD | **MACD(12,26,9)** (daily) | *Optional* tie-breaker only | Momentum-turn check. **Not a hard gate** — see the warning below. |
| Support/resistance | Horizontal levels you mark | Stop placement + profit target | Defines where the trade is wrong and where it's "done." |

> **Over-stacking warning (a known failure mode, flagged per your request):** MACD is a *tie-breaker*, not a sixth gate. If you require MACD to also align on every trade, you will filter yourself into near-zero trades and analysis paralysis. Use it only to break a tie between two otherwise-equal candidates, or to skip a marginal one. The four hard gates below are the system.

**The entry checklist — ALL of Gates 1–5 must be true, in order. If any fails, there is no trade.**

1. **Gate 1 — Trend is up (daily):** `50 SMA > 200 SMA` **AND** `price > 200 SMA`. (Confirmed uptrend.)
2. **Gate 2 — Valid pullback to support:** price has pulled back so that **either**
   - the pullback low came within **1 × ATR(14)** of the **50 SMA** (price tagged its trend support), **OR**
   - the pullback low came within **1 × ATR(14)** of a **marked horizontal support level** (a prior swing low / consolidation shelf),
   **AND** `RSI(14)` dipped into the **35–45** band during the pullback (a dip, not a crash). If RSI went below 30 *and* price is testing the 200 SMA, this is not a pullback — it's a possible trend break. **Skip.**
3. **Gate 3 — Reversal trigger fires (daily):** the most recent **completed** daily candle is a **bullish reversal candle** at/above support — defined as a candle that **closes above the prior day's high** *or* is a bullish engulfing / hammer that **closes in the upper half of its own range**. **Entry order = a buy-stop at $0.05 above the high of that completed trigger candle.** You enter only if price trades through that level the next session. (No trigger = no entry; you wait.)
4. **Gate 4 — Confirmation:** trigger-day volume **≥ the 20-day average volume.** (Primary confirmation.) If volume is marginal (within 10% below average), MACD histogram turning up — i.e., rising / crossing toward positive — *may* substitute as the confirmation. Use one, not both as a requirement.
5. **Gate 5 — Reward-to-risk clears the bar:** with the stop from Section 5 and the nearest meaningful resistance as the target, **`(target − entry) / (entry − stop) ≥ 2.0`.** If the nearest real resistance is less than 2R away, the trade is rejected no matter how good it looks.

**State explicitly when NOT to enter:**

- Any of Gates 1–5 fails.
- The "pullback" closed **below the 200 SMA** or below the prior swing structure → that's a trend break, not a dip.
- `RSI(14) < 30` while price is below the 50 SMA → downtrend behavior, not a buyable dip.
- Earnings inside 10 trading days, or any Section 3 disqualifier present.
- The trigger candle gapped up so far that your stop distance now blows past **2 × ATR** (entry too extended above support — see Section 5).
- R:R < 2:1.
- You **cannot afford a whole-share position that keeps risk within the ceiling** (Section 6). No affordable, valid size = no trade.
- **Market regime risk-off:** `SPY < its own 200-day SMA` → at most **one** open position at a time, or stand aside entirely (the long-only pullback edge degrades in bear regimes).

---

## 5. Exit criteria

Both exits are defined **before** you enter and entered as resting orders where possible.

### (a) Stop-loss — set before entry, non-negotiable

- **Placement (deterministic):** `STOP = (pullback swing low) − 0.25 × ATR(14)`.
  The pullback swing low is the lowest low of the dip that produced your trigger candle. The 0.25×ATR buffer keeps you from being wicked out by noise right at the level.
- **Sanity cap:** if `entry − STOP > 2.0 × ATR(14)`, the entry is too far above support → **skip the trade.** (Either price ran too far from the level, or the support is too loose to define risk.)
- **Why here:** the swing low is the price that *invalidates the thesis*. If support breaks, "the dip held" was wrong and you want out — that's the whole point of the setup.
- **The stop only ever moves UP, never down.** Moving a stop lower to "give it room" is forbidden. This is the cardinal sin of the plan (see Section 10). Raising it to lock in gains is allowed and encouraged (see trailing, below).

### (b) Profit target / exit

You decide **before entry** which of two exit modes this trade uses, based on share count (because tiny positions can't be scaled):

- **Mode A — Full exit at target (default; required when position ≤ 3 shares):**
  Sell the entire position at **+2R** (a limit order at `entry + 2 × (entry − stop)`), provided that price is at/below the nearest resistance. If resistance sits just under +2R, take the exit at resistance instead. One target, one exit, clean.
- **Mode B — Scale + trail (allowed only when position ≥ 4 shares):**
  At **+2R**, sell **half** (round to whole shares; sell the larger half). Immediately move the stop on the remainder to **breakeven (= entry).** Let the rest run on a **trailing stop = the higher of (i) breakeven, or (ii) the highest high since entry − 3 × ATR(14)**, recomputed at each EOD. Exit the remainder when that trailing stop is hit or price closes below the **20-day SMA**, whichever comes first.

> With `BOOK = $500`, most positions will be small, so **Mode A is your normal mode.** Don't force scaling on a 2-share position — there's nothing to scale.

### (c) Time-stop (for trades that go nowhere)

- If, **10 trading sessions after entry**, the trade has **not reached +1R** and has **not been stopped out**, **exit at that day's close.**
- **Why:** dead capital is opportunity cost, and because gains here are short-term (ordinary income) taxed, a trade has to actually *work* to be worth the tax drag. A position drifting sideways is failing the bar — free the capital.

---

## 6. Money management & position sizing

This is where small accounts quietly blow themselves up. Read it twice.

### Risk per trade: **1% target, 2% hard ceiling**

- **Use 1%, not 2%.** The math: at 1%, a brutal 10-trade losing streak costs ~10% of the book — survivable, and you stay in the game collecting data. At 2%, that same streak is ~20% and tends to end the experiment emotionally before the sample is complete. **Your objective is sample size and consistency, not maximum growth.** 1% is the risk setting that keeps you trading long enough to learn whether you have an edge. (At `BOOK=$500`, 1% = **$5** of intended risk per trade; the 2% ceiling = **$10**.)
- **2% is a hard ceiling, never a target.** Because whole shares make real risk lumpy (below), actual modeled risk will often land above 1%. That's tolerated **up to 2%.** It must **never** exceed 2%. If the smallest sane whole-share position still risks more than 2% of `BOOK`, **skip the trade.**

### The sizing formula

```
risk_dollars = BOOK × 1%
shares       = floor( risk_dollars / (entry − stop) )
```

`floor()` because you can only buy whole shares — always round **down**, never up. Then compute the **actual** risk you just took:

```
actual_risk_$   = shares × (entry − stop)
actual_risk_%   = actual_risk_$ / BOOK
position_notional = shares × entry
```

### The small-account granularity problem (handled explicitly)

Because you round shares down to whole numbers, your real risk almost never equals exactly 1% — it's lumpy. Decision rules:

1. **Compute `shares` with the formula above.**
2. **If `shares = 0`** (i.e., even 1 share risks more than `risk_dollars`): check whether **1 share** risks **≤ 2% of BOOK.**
   - If 1 share's risk ≤ 2% → you *may* take exactly 1 share. (Real risk lands between 1% and 2% — acceptable.)
   - If 1 share's risk > 2% → **SKIP.** The stock is too expensive or its stop too wide for this account. This is the correct, common outcome at `BOOK=$500`; it is not a failure, it's the constraint doing its job.
3. **If `actual_risk_% < 0.5%`** (cheap stock, tight stop — your position is trivially small): you *may* size up toward the 2% ceiling, **but** never let `position_notional` exceed the **40%-of-book concentration cap** (below). A sub-0.5% position is mostly noise and tax paperwork — either size it up within the caps or skip it.
4. **Never** average down or add to a loser to "fix" sizing.

**Minimum viable trade, defined:** a trade is only worth taking if, after the rules above, **`0.5% ≤ actual_risk_% ≤ 2%`** *and* you can hold at least **1 whole share** *and* `position_notional ≤ 40% of BOOK`. If you can't satisfy all three, there is no trade.

### Worked example (illustrative only — NOT proof the strategy works)

`BOOK = $500`, risk 1% → `risk_dollars = $5`.

- **Stock A:** entry $30.00, stop $28.50 → risk/share $1.50. `shares = floor(5 / 1.50) = 3`. Actual risk = 3 × $1.50 = **$4.50 (0.9% of book)** ✓. Notional = 3 × $30 = $90 (18% of book) ✓. → **Valid, Mode A (3 shares).**
- **Stock B:** entry $80.00, stop $76.00 → risk/share $4.00. `shares = floor(5 / 4) = 1`. 1 share risks $4.00 = **0.8%** ✓, notional $80 = 16% ✓. → **Valid, 1 share.**
- **Stock C:** entry $95.00, stop $86.00 → risk/share $9.00. `shares = floor(5/9) = 0`. 1 share risks $9 = **1.8% (≤2%)** ✓ but notional $95 = **19%** ✓ → technically takeable at 1 share, *but* note how a single wide-stop name eats your whole risk budget. Prefer cheaper, tighter setups. If the stop were $9 on a $40 stock you'd still be fine on notional; the killer is when 1-share risk > $10 (2%) → then **skip.**

*(These numbers are made up to show the mechanics. They are not a backtest and prove nothing about edge.)*

### Portfolio-level caps

| Cap | Value (at BOOK=$500) | Rule |
|---|---|---|
| Max concurrent open positions | **3** | Focus + clean data on an EOD schedule. |
| Max single-position notional | **40% of BOOK** ($200) | No single name dominates the book. |
| **Max TOTAL risk across all open trades** | **4% of BOOK** ($20) | Sum of every open trade's `actual_risk_$` must stay ≤ 4%. If a new trade would breach it, size down or don't take it. |
| Minimum reward:risk per trade | **2.0 : 1** | Reject anything below (Gate 5). |
| Risk-off regime (SPY < 200 SMA) | **1 position max** | Overrides the "3" cap. |

---

## 7. Profit targets & expectancy

**Trade level — what "good" means:** A trade is **good if you followed the plan**, regardless of outcome. A by-the-book trade that stops out at **−1R** is a *good trade* (process correct, journal `plan-followed = yes`). A winner you took by *breaking* a rule is a *bad trade* you got paid for — and it's the most dangerous kind, because it teaches the wrong lesson. Outcomes are scored in **R-multiples**:

- **1R** = the dollars you risked = `shares × (entry − stop)`.
- A win to +2R = **+2R**. A stop-out = **−1R**. Time-stop exit = whatever R it closed at.

**System level — the only thing that actually matters:** you judge the *system*, not any trade, over a sample of **30–50+ trades** using **expectancy**:

```
Expectancy (R per trade) = (Win% × Avg Win R) − (Loss% × Avg Loss R)
```

You want expectancy **positive after costs.** Note what the 2:1 minimum buys you: you can be **wrong more than half the time and still make money.**

*Illustrative arithmetic (NOT a promise, NOT a backtest):* at a 40% win rate with avg win 2.5R and avg loss 1R → `E = 0.40×2.5 − 0.60×1.0 = +0.4R` per trade. The point of the example is only to show the *shape*: with 2:1+ R:R, a sub-50% win rate can still be net positive. Whether *your* system actually clears zero is the open question your journal exists to answer.

**Hard rule:** you do **not** evaluate the system on fewer than ~30 closed trades. A 5-trade winning streak means nothing; a 5-trade losing streak means nothing. Track **rolling expectancy** and let the sample speak.

---

## 8. When NOT to trade / circuit breakers

**The default state is FLAT. Doing nothing is the correct action most days.** You only act when the plan affirmatively fires.

**Stand aside when:**

- No candidate passes all of Gates 1–5. (Most common reason — and a non-event.)
- No clear daily trend: 50/200 SMA tangled together or price chopping around the 200 SMA.
- `SPY < its 200-day SMA` (risk-off regime) → max 1 position or fully flat.
- A name has earnings or a known major scheduled event inside 10 trading days. If a held name's earnings get scheduled inside your hold, **exit before the event** — never hold through binary news.

**Loss-limit circuit breakers (mechanical — stop means stop):**

| Breaker | Trigger | Action |
|---|---|---|
| Daily loss limit | Realized losses on the day reach **−2R or −4% of BOOK** (whichever first) | **No new entries the rest of the day.** Manage open trades only. |
| Weekly loss limit | Realized losses on the week reach **−6% of BOOK**, **or** 3 consecutive losing trades | **No new entries the rest of the week.** Manage existing only. Resume at next weekend review. |
| Drawdown halt | Book is **−15% from its peak value** | **Halt ALL new trades.** Full journal review required before resuming. |
| Revenge-trade lock | You feel the urge to "win it back" right after a loss | **Mandatory 24-hour cool-down** before any new entry. |

These are about **realized** losses and account drawdown — open trades you're managing per plan are not "new entries." When a breaker trips, the only allowed activity is managing existing positions to their predefined stops/targets.

---

## 9. Review cadence

| Cadence | When | What you do | Can you change a rule? |
|---|---|---|---|
| **Daily (EOD)** | After every close | Update the journal for any closed trade; check stops/targets/time-stops on open trades; run the Section 3 screen and set alerts for tomorrow. | **No.** |
| **Weekly** | Each weekend | Tally the week: # trades, win rate, avg R, expectancy so far, **plan-followed %**, and the single biggest rule-break. Note patterns. | **No.** |
| **System review** | Every **20 closed trades** or **monthly**, whichever first | Compute full system metrics over the whole sample. Decide if anything changes. | **Yes — this is the only time.** |

**The immutability rule (the spine of the whole plan):** the plan is **frozen mid-trade and between scheduled system reviews.** You may only change a rule at a scheduled system review, and every change must be **written here with a reason and an effective date.** This exists to stop you curve-fitting the plan to your most recent loss — the fastest way to destroy a system is to "fix" it after every losing trade.

**Metrics tracked every review:**

- Number of closed trades
- Win rate (%)
- Average win (R) and average loss (R)
- **Expectancy (R per trade)**
- Profit factor (gross wins ÷ gross losses)
- Max drawdown (%)
- **Plan-followed % ← the headline metric for your first 50 trades**
- % of trades that were valid Gate-1–5 setups vs. forced/drifted

> In the tuition phase, **`plan-followed %` matters more than P&L.** Target **≥ 90%.** A profitable month with 60% plan-adherence is a warning sign, not a success — it means you're getting paid for undisciplined behavior that will eventually reverse.

---

## 10. Parts most likely to break (beginner traps — reread these)

1. **Whole-share granularity silently inflating your risk.** You *think* you risked 1%; rounding and a wide stop mean you actually risked 3%. **Always compute `actual_risk_%` after `floor()` and obey the 2% ceiling.** This is the #1 small-account killer.
2. **Moving the stop down to "give it room."** Forbidden. Stops move **up only.** The moment you widen a stop, you've abandoned the plan and uncapped your loss.
3. **Holding through earnings "just this once."** The exact trade that gaps against you overnight. The 10-day earnings rule is absolute.
4. **Over-stacking indicators.** Adding a 6th condition (or demanding MACD align every time) until nothing ever qualifies. Four gates. That's the system.
5. **Abandoning the system after a normal losing streak.** A string of by-the-book −1R stops is *expected*, not evidence the plan is broken. Judge over 30–50+ trades, change rules only at scheduled reviews.
6. **Mislabeling a downtrend as a "pullback"** and catching a falling knife. If price closed below the 200 SMA or RSI is <30 in a downtrend, it is NOT this setup.
7. **Setup drift.** Taking something that isn't pullback-in-trend because it "looks strong." You chose a single-strategy plan; a different setup is a rule violation, not improvisation.
8. **Treating the illustrative numbers in Sections 6–7 as a promise.** They're arithmetic demonstrations of mechanics. Your forward journal is the only evidence that counts.
9. **Counting a rule-breaking winner as a success.** It's a bad trade you got paid for. Journal it as `plan-followed = no` and don't repeat it.

---

## Appendix — Journal field mapping

Every closed round-trip populates these (your existing fields), and the plan is built to fill them cleanly:

| Journal field | Comes from |
|---|---|
| setup | "Pullback-in-trend" (+ which support: 50 SMA or horizontal level) |
| entry | buy-stop fill at trigger-candle high + $0.05 (Gate 3) |
| exit | target / trailing stop / time-stop (Section 5) |
| shares | `floor()` formula (Section 6) |
| stop | `pullback swing low − 0.25×ATR` (Section 5a) |
| planned risk | `actual_risk_$` and `actual_risk_%` (Section 6) |
| R-multiple | result ÷ 1R (Section 7) |
| plan-followed (yes/no) | did all Gates 1–5 hold and were exits honored? (Section 7) |
| result | realized P&L |
| lesson | one line; feeds the weekly/system review (Section 9) |

---

*v1.0 — a hypothesis to be tested, not a guarantee. Set your `BOOK`, paper-or-tiny-size your first trades, and let the journal tell you the truth over 30–50 trades.*
