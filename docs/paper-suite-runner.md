# Paper suite runner — the whole paper book, one job

`run-paper-suite.bat` → `scripts/run_paper_suite.py` → `webull_web.runner_cli.run_suite` runs **all
13** code-backed paper-book steps in order (`PAPER_SUITE` in `webull_web/runner_cli.py` is the source of
truth; the list was nine when this doc was written in July 2026), in one weekday-EOD session:

1. **rsi2** — RSI(2) equity entries + exits (`rsi2_service`)
2. **paper_eod** — equity stop / target / SMA-break exits (`paper_exits_service`)
3. **pool_shadow** — the S6 shared paper pool in SHADOW: its own ledger, changes nothing in the books (spec 2026-09-11) (`pool_shadow_service`)
4. **paper_eod_options** — option exits: TP / stop / time-stop (`paper_options_exits_service`; alerts on unmarked units)
5. **options_entry** — option entries: RSI(2)<10 → ATM debit vertical (`options_entry_service`; **PAUSED since 2026-08-31** via `WEBULL_OPTIONS_ENTRY_PAUSED`)
6. **proven** — the Lab's `confirmed_proven` shelf on the isolated `proven.json` book (`proven_runner_service`)
7. **Scorecard** — weekly per-strategy scorecard (buckets, expression A/B, rule flags); appends `data/activity/scorecard_history.jsonl` and manager-note line (`scorecard_service`)
8. **Net-liq** — marks all three books, appends `data/activity/netliq_history.jsonl` for the Home
   equity curve; read-only (`netliq_snapshot_service`)
9. **rsi2_real** — the REAL-book RSI2 step: QUEUE-ONLY, writes executable BUY rows for the next morning's
   autopilot run and never places; off unless `WEBULL_RSI2_REAL_ENABLED` (armed 2026-09-14) (`rsi2_real_service`)
10. **Flows** — detects owner deposits / withdrawals and books them as flows so book returns stay clean
    (`flows_service`)
11. **Scan** — snapshots the nightly scanner picks and scores matured ones against SPY forward
    returns (efficacy ledger) (`scan_ledger_service`)
12. **Judgment** — the entry-judgment SHADOW: Claude's verdict per signal via the CLI, recorded, blocks
    nothing (`judgment_service`)
13. **Note** — composes the daily manager's note (what ran, fills, book deltas, resting
    protection, autopilot posture, proof-bar) from everything the prior twelve steps just wrote,
    appends `data/activity/manager_notes.jsonl`, and optionally pushes it to the owner's phone via
    `WEBULL_MANAGER_NOTE_NTFY` (unset → stored, not pushed); read-only vs the broker/paper
    stores (`manager_note_service`)

Each runner keeps its own file-lock + same-day guard + run-log, so this just **sequences** them; a
runner that fails never stops the rest, and all 13 run-log rows green from this one job.
**Places nothing** — no `trading` import in the path; the one real-book step (rsi2_real) only QUEUES rows
that the separately gated autopilot acts on the next morning; the order + autopilot gates are untouched. Exit
code: `1` if any runner hard-errored, `2` if any had soft per-symbol errors, else `0`.

The suite is the ONLY scheduled job for these steps — the old per-runner bats were removed (2026-07-26). To re-run a
single step by hand, use its `scripts/` shim (each also takes `--force` to bypass the same-day guard):
`rsi2_paper.py` · `paper_eod.py` · `paper_eod_options.py` · `options_entry.py` · `proven_paper.py` ·
`scorecard.py` · `scan_ledger.py` · `flows.py` · `rsi2_real.py` (pool-shadow, net-liq, judgment and the note
have no shim — rerun the whole suite), e.g.
`.venv\Scripts\python.exe scripts\scorecard.py run --force`.

## Schedule it — one Windows Task Scheduler job

The machine is Eastern, so 5:30 PM local = 5:30 PM ET (after the 4 PM close + a bar-settle margin).
Registered (weekdays 5:30 PM) as **"Webull Paper Suite"** via:

```
schtasks /create /tn "Webull Paper Suite" /tr "C:\path\to\webull-trading-system\run-paper-suite.bat" ^
  /sc WEEKLY /d MON,TUE,WED,THU,FRI /st 17:30 /f
```

- **Verify:** `schtasks /query /tn "Webull Paper Suite" /v /fo LIST`
- **Run now (manual):** `schtasks /run /tn "Webull Paper Suite"` — or double-click the `.bat`.
- **Pause / resume:** `schtasks /change /tn "Webull Paper Suite" /disable` · `/enable`.
- **Remove:** `schtasks /delete /tn "Webull Paper Suite" /f`.

## Caveats

- **PC must be on/awake at 5:30 PM ET** — a plain weekday trigger doesn't wake the machine. The
  always-on host (or a "wake to run" trigger) is the pending step for true PC-off operation.
- **Webull token** — the SDK token lasts ~15 days and needs an SMS re-auth when it lapses; while
  lapsed the runners degrade to `error` (red on the Monitor), not a crash.
- **Real money is NOT here.** This job is paper-only. The real-money autopilot is a SEPARATE,
  owner-gated path and is deliberately never scheduled by this.
