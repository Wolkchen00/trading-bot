# RF6-DEVAM.md , R6 (deploy) evidence log

Operational record of the v5 rollout. Server address, application id and notification topics are deliberately
not written here (the repository is public); they are in the operator's private notes.

## 2026-11-19 (Thursday) , first deploy: live in dry-run, paper trading for real

Owner decision, asked right before the step: "Yeni bot hazır. Çalıştırmaya başlayayım mı? Gerçek hesapta
şimdilik emir göndermez ..." -> "Başlat". The live flag (`LIVE_TRADING_ENABLED`) stays off; switching it on is a
separate question to the owner (promise recorded in `RF6-VTO.md` / `RF6-DEVIR.md`).

Before the deploy
- Proof on Windows: 764 passed. The same suite inside a separately built image on the server, network off:
  764 passed (190 s). Image content checked: only `v5/`, two tools, the watchdog, the tests and the packaging
  files; no v4 code, no env file.
- Independent review of the runner (12 findings) and its fix round: `RF-ISSUES-6.md` items 25 to 27.
- Everything the publish would add was scanned for keys, topics, addresses and personal data. The price levels
  of the owner's own order and three old infrastructure details were removed from the tree first.

Publish
- `main` got ONE commit, `96d510b`, whose tree is exactly the tree of the working branch at `bea0d3e`
  (`git commit-tree`, no merge). The working branch itself is never pushed: its older commits contain details
  that do not belong in a public repository.

Deploy (20:01 PT = 04:01 UTC on the 20th)
- Coolify deployment of `96d510b`: finished in about one minute. Two containers, both from the same image.
- Verified inside the containers:
  - live: mode `live`, `orders_enabled False` (dry-run), package `high`, owner symbols `ANT`, notification
    topic set, state directory `state_live/v5` (the v4 files beside it are untouched).
  - paper: mode `paper`, `orders_enabled True`, its own practice topic (differs from the owner's topic),
    state directory `state_paper/v5`.
- Both started after the close of the 19th: that session is marked as "before the engine existed" (no report).
- `init-peak`: live 491.08 (the owner's number of 16 November), paper 61784.59 (its last closing value).
- After that: live heartbeat states `['DRY_RUN']`, paper `[]`; no error lines in either log; about 22 MB each.

Accounts as the new engine sees them
- live: 490.06 cash, no position; one open order, the owner's own ANT buy (seen as an owner order, not as a
  foreign one).
- paper: one position and two open orders left by v4 (plus a dust-sized crypto remainder the engine ignores).
  Until they are cleared the paper engine holds with `FOREIGN_ASSET` (messages go to the practice topic).

Watchdog
- Script copied out of the image to the host, topic file with mode 600, cron every ten minutes
  (`sh /root/v5_watchdog.sh`). A run with both services healthy: silent, no log line, no stamp.
- Alert path proven without touching the owner's phone: a copy of the script with a made-up service name and
  the practice topic sent one message (read back from the topic), and stayed quiet on its second run.

Notifications
- `python -m v5.ops notify-test` in the paper container: delivered, read back from the practice topic with its
  "DENEME HESABI." label.
- The same command in the live container: delivered to the owner's topic (text starts with
  "DENEME, gerçek emir gönderilmedi.").

## 2026-11-19 , second deploy the same evening: fix round R5c

- Codex built the fixes from `rf6/fix-r5c.md` and `rf6/fix-r5c-2.md`; the reviewer read the whole diff, ran its
  own suite (`tests_v5/test_r5c_saldirgan.py`) and the proof: 795 passed on Windows, 795 passed inside a
  separately built image on the server with the network off (201 s). Codex's four original scripts and the
  first reviewer's scripts are clean on the new code.
- Publish: `main` got one more commit, `7196c39` (the tree of the working branch at `536ddcf`).
- Deploy at 20:54 PT (04:54 UTC): finished in about one minute. Both containers came back with their state
  (peaks 491.08 and 61784.59 kept), the new code is in the image, no error line in either log; live states
  `['DRY_RUN']`, paper `[]`. The watchdog stayed quiet through the restart (no log line, no stamp).
- The temporary test image and build folder were removed from the server. The two old v4 images are still
  there in case a rollback is needed.

## 2026-11-19 , third deploy the same night: fix round R5d

- Sources: the reviewer's second pass, Codex's complete whole-tree review and the first report of the
  six-reviewer sweep (`RF-ISSUES-6.md` items 29 to 31). Codex built groups A to F and ran out of quota in the
  proof; Claude wrote G1 to G6, brought the older pinned tests up to date and added its own suite
  (`tests_v5/test_r5d_saldirgan.py`, 33 tests).
- Proof: 855 passed inside a separately built image on the server with the network off (231 s). On Windows the
  whole run had 23 failures, all in test doubles (settings without a mode, a fake docker without the label
  filter); with the doubles completed the affected files pass.
- Before the deploy a copy of both state folders was read with the new loader: both `runner.json` files are
  accepted, no order record exists yet.
- Publish: `main` got one more commit, `2bf6d0e` (the tree of the working branch at `e71decf`).
- Deploy at 23:10 PT (07:10 UTC): finished in about a minute and a half. Both containers came back with their
  state (peaks 491.08 and 61784.59, `started_at` unchanged); the five sessions before the engine's first start
  are marked closed without a message, as designed; live states `['DRY_RUN']`, paper `[]`; no error line in
  either log.
- The watchdog on the host was replaced by the new script (the old one is kept beside it); both containers are
  found by their service label; one run by hand stayed quiet.
- The temporary test image and build folder were removed from the server.

## 2026-11-20 (Friday) , the first morning: checks around the open (code on the server: R5d, `2bf6d0e`)

- 09:12 ET, before the open: both containers in the readiness phase, readiness ok with no problem, no error
  or warning line in either log since the deploy, the watchdog quiet. `last_equity` on both accounts is the
  close of the 19th and `balance_asof` names it (`RF6-PROBE.md`, last section).
- 09:38 ET, after the open: both engines had taken their row for 2026-11-19 from the broker (490.06 and
  61729.43); no brake entry, floor OK, peaks unchanged.
- The owner's own order (not the engine's) was filled at the open; its two exit legs are in place at the
  broker. Figures are kept in the operator's private notes. The engine sees the share as the owner's:
  `decide-now` in the live container lists no foreign asset and sizes on the account value less that share.
- Practice account, drill tool inside the practice container (real practice orders):
  `--inventory`: one old v4 position (SQQQ, 64 shares), a dust-size crypto remainder, one old open order.
  `--flatten-foreign --yes`: PASS, cancelled 1 order, closed 1 position, nothing held but the dust.
  `--round-trip --duplicate-id --restart --notify`: all four PASS. Round trip: entry DONE at weight 0.0499,
  exit DONE, flat. Duplicate id: the second submit of the same client id was refused with code 40010001.
  Restart: the process "died" at PLANNED, the restart ended DONE after two submit calls with ONE order at the
  broker, weight 0.0500, exit DONE. Notify: delivered (practice topic). Afterwards: no position but the dust,
  no open order. The drill took 65 seconds and cost about 0.28 dollars of the practice balance in spreads.
- `decide-now` in both containers at 09:40 ET: exit 1 with exactly `["OUTSIDE_DECISION_WINDOW"]` (as designed
  since R5d), breaker false, no foreign asset in either account (the dust is not counted). Both documents
  carry a READY decision for that moment (trend down, the day's dip rule wanted, a buy of one third) and its
  order plan. Cross check: the live document replayed in the practice container and the practice document
  replayed in the live container both gave exit 0 ("identical"); a copy with a changed plan amount gave exit 1.
- After the drill: both heartbeats fresh, no message queued by either engine, no error line, watchdog quiet.

## 2026-11-20 (Friday) , the first decision day (code on the server: R5d, `2bf6d0e`)

Read on the server at 16:09 ET, nine minutes after both summaries.

- The decision (15:50 ET). Both engines computed the same decision from the same inputs: trend down, so the
  core target is 0; the day's dip rule fired (the signal fell about 5 percent on the day and stood at 0.065 of
  its day range), so the target is one third as the extra share (`TREND_DOWN`, `OVERLAY_ENTER`).
- Practice account: record `DONE`. ONE order, `v5-paper-20261120-dec-1`, a notional buy of 20626.86 dollars,
  submitted at 20:50:22 UTC, filled completely (499.318566932 shares at 41.31) and reconciled 17 seconds
  later. Weight after the fill 0.3335 against the target 0.3333 (residual 0.0002). One submit call, no retry,
  no hold. `overlay.json` holds fraction 1.0 for the session.
- Real account (orders off): record `DRY_RUN` in `decisions_dryrun`; no submit call (`submit_count` 0, no
  broker order id). The plan was a buy of one third of the engine's own part of the account, which is the
  account value less the owner's share.
- After the close (21:01 UTC): both sessions closed, both summaries delivered on the first attempt and read
  back from the two topics.
  Practice: "DENEME HESABI. Gün sonu özeti (20.11.2026) / Hesap: 61.900,58 dolar. Bugün yüzde 0,3 arttı
  (171,15 dolar). / Elimde: yüzde 33 Nasdaq fonu, yüzde 67 nakit. / Bugün ne yaptım: 20.626,85 dolarlık Nasdaq
  fonu aldım. / Neden: Nasdaq düşüş eğiliminde, bu yüzden ana para nakitte bekliyor. Bugün sert bir düşüş oldu,
  yarın için küçük bir ek alım yaptım. / Bu ay: yüzde 0,3 artıda. Başlangıçtan beri: yüzde 0,3 artıda. /
  Durum: Her şey normal."
  Real: "DENEME, gerçek emir gönderilmedi. Gün sonu özeti (20.11.2026) / Hesap: 488,84 dolar. Bugün yüzde 0,2
  azaldı (1,22 dolar). / Elimde: yüzde 0 Nasdaq fonu, yüzde 18 senin aldığın hisse (ANT), yüzde 82 nakit. /
  Bugün ne yaptım: Gerçek emir açık olsaydı (tutar) dolarlık Nasdaq fonu alırdım. / Neden: Nasdaq düşüş
  eğiliminde, bu yüzden ana para nakitte bekliyor. Bugün sert bir düşüş oldu; gerçek emir açık olsaydı yarın
  için küçük bir ek alım yapardım. / Bu ay: yüzde 0,2 ekside. Başlangıçtan beri: yüzde 0,2 ekside. / Durum:
  Her şey normal." (The amount is left out here on purpose; it is one third of the engine's part.)
  Every number was recomputed from the records: the day's change against the row of the 19th (61729.43 and
  490.06), the bought amount as filled quantity times average price, the shares of line 3 adding up to 100.
- The practice account's gain of the day is mostly not the engine's: 151.18 of the 171.15 dollars were there
  before the engine's order (the old v4 position, an inverse fund, was sold in the morning clean-up above its
  previous close); the engine's own purchase added about 20 by the close.
- Rails: practice row 61900.58 for the 20th and the peak raised from 61784.59 to it (a new closing high); real
  row 488.84, peak unchanged at 491.08. No breaker entry, floor OK, no alert, nothing paused.
- Logs: four lines each since the start (start, the close of the 19th taken from the broker, the decision, the
  close of the 20th); no warning, no error. The watchdog never had anything to say (no log file, no stamp).
  Heartbeats fresh; real `['DRY_RUN']`, practice `[]`; readiness ok on both.
- The owner's own share is still held and both exit legs rest at the broker (figures in the operator's private
  notes). The real account's change of the day is that share's move alone.

## 2026-11-20 , fourth deploy after the close: fix round R5e

- Sources: the six-reviewer sweep (`RF-ISSUES-6.md` items 31 and 32, plan Revision 14). Codex built
  `rf6/fix-r5e.md` and `rf6/fix-r5e-2.md` in two runs; when its quota ran out again Claude wrote the last five
  small items (`rf6/fix-r5e-3.md`) and its own suite (`tests_v5/test_r5e_saldirgan.py`, 29 tests).
- Proof: 937 passed on Windows; 937 passed inside a separately built image on the server with the network
  off, on exactly the published tree (347 s).
- Rehearsal before the deploy: a copy of both state folders as R5d left them after the first decision day was
  put under the simulator and the new engine ran on it through the weekend, Monday and Tuesday. Nothing was
  sent a second time and every file was accepted; with the extra share expiring on Monday the practice engine
  sold it with ONE order (also when the process was replaced in the middle of the chain); with a second dip it
  kept the share; on Tuesday Monday's provisional row was confirmed; the real account with orders off reported
  once a day and named the owner's share. (The rehearsal reads account values, so it lives in the operator's
  private folder, not here.)
- Independent review of the upgrade itself (four reviewers, each with its own scripts: the state files one by
  one including a roll-back to R5d, the timeline of the first week, everything the owner is told, the watchdog
  and the health tool): no blocker. Its six small findings are `RF-ISSUES-6.md` item 33.
- Before the publish, three places that named the amount reserved by the owner's own resting order got
  neutral numbers; the scan of everything the publish adds (keys, topics, addresses, the owner's levels) was
  clean.
- Publish: `main` got one more commit, `42258c5` (the tree of the working branch at `b91fcaf`).
- Deploy at 13:30 PT (21:30:39 to 21:31:58 UTC, 79 seconds), started right after the watchdog's ten-minute
  mark so that no watchdog run met the gap. Verified in both containers:
  - nine files (the six engine modules that changed, the watchdog, the two tools) have the same hash as in the
    published tree;
  - no state file was rewritten by the start (modification times unchanged): peaks 491.08 and 61900.58, both
    equity rows, the practice account's extra share (fraction 1.0), `started_at` and the closed sessions;
  - heartbeats twenty seconds after the start: phase `after_close`, real `['DRY_RUN']`, practice `[]`;
    `v5.health --json`: practice level 0, real level 1 (orders off counts as "degraded" by design, the
    watchdog is silent about it), `owner_unreachable` false on both;
  - logs: the start line only; no message on either topic during or after the deploy.
- The watchdog on the host was replaced by the new script (copied out of the image, same hash as in the
  repository, checked with the host's `sh`; the previous one is kept beside it). One run by hand: silent, no
  log line, no stamp.
- The temporary test image, the build folder, its logs and the state copy were removed from the server.

## 2026-11-20 , fifth deploy the same afternoon: fix round R5f

- Four small items (`rf6/fix-r5f.md`, `RF-ISSUES-6.md` items 33 and 34, plan Revision 15): the doubt alert
  speaks of the safety sale and is not sent while the engine is halted; a corrected close names its summary by
  date; the health tool answers when it cannot read the message file. The reviewer built the round (the Codex
  tool ended at start on this machine that afternoon); the reviewer's suite was written from the contract
  first and failed on the old code; a fresh reader read the diff twice, and its one finding became the fourth
  item.
- Proof: 946 passed on Windows; 946 passed inside a separately built image on the server with the network
  off, on exactly the published tree (346 s). The rehearsal on the copy of the real state was repeated on the
  new code: unchanged.
- Publish: `main` got one more commit, `405d2fc` (the tree of the working branch at `2cfead1`).
- Deploy at 14:30 PT (22:30:13 to 22:31:31 UTC, 78 seconds), again right after the watchdog's ten-minute
  mark. Verified in both containers: the changed files have the same hash as in the published tree and the
  two new sentences are in the image; no state file was rewritten by the start (peaks, both equity rows, the
  practice account's extra share, `started_at`, the closed sessions); heartbeats twenty seconds after the
  start: phase `after_close`, real `['DRY_RUN']`, practice `[]`; health: practice level 0, real level 1 with
  `DRY_RUN`, `owner_unreachable` false on both; logs: the start line only; no message on either topic
  between the summaries of 21:01 UTC and the check.
- The watchdog script did not change in this round (the host's copy has the hash of the repository's); one
  run by hand after the deploy: silent, no log line, no stamp. Between the two deploys the watchdog's own
  runs (every ten minutes) stayed silent as well.
- The temporary test image and the build folder were removed from the server.

## 2026-11-21 (Saturday) , weekend check, and one small operator step on the rows of the 20th

The owner asked whether everything runs as it should. Read on the server at 23:17 UTC and again at 23:44 UTC.

- Both containers have run without a restart since the fifth deploy (started 22:31 UTC on the 20th, restart
  count 0, no memory kill). Their code is the published tree (sixteen files compared by hash: the fifteen
  engine modules and the watchdog). Heartbeats a few seconds old, phase `closed`; real `['DRY_RUN']` (health
  level 1 by design), practice `[]` (level 0); readiness ok and `owner_unreachable` false on both. Logs: the
  start line only. Nothing was sent since the two summaries of the 20th: both message files end with them,
  both delivered, and neither topic holds a newer message.
- The watchdog: its cron line is in place, it ran 156 times in the last 26 hours (every ten minutes) and
  never spoke (no log file, no stamp); one run by hand was silent. Disk 39 percent used, memory free.
- The calendar file was refreshed on the 21st and holds the coming week as it is: sessions on the 23rd, 24th
  and 25th, none on Thursday the 26th, an early close (13:00 ET) on Friday the 27th. The decision window is
  counted back from each day's close, so the short day needs nothing.
- The broker, read-only: both accounts active and not blocked. Practice: the extra share of the 20th is held
  (499.318566932 shares), no open order. Real: only the owner's own share with its two exit orders; no order
  of the engine.
- The practice account's cash went down by 0.21 dollars after the close of the 20th: the regulatory fees of
  the morning drill's sales, booked that evening (the account activities name them). Nothing of the engine's.

What was found: the rows of the 20th are not the broker's official close.

- R5d wrote its closing row about a minute after the close, from the prices of that moment. The broker's
  official closing value (its `last_equity` once `balance_asof` names the day, and its daily history) became
  readable only later that night. Practice: row 61900.58, official 61825.47, so 75.11 too high (the fund's
  official closing price was below the price a minute after the bell). Real: the row is a fraction of a
  dollar below the official close (the owner's share closed a little higher than it stood a minute later;
  the figures are in the operator's private notes). Both differences are far inside the engine's tolerance
  of 2 percent, and no safety rule uses these rows: the daily brake takes its baseline from the broker, the
  floor and the alert read the peak.
- But R5d rows carry no flag, so R5e and later never confirm them, and the summary of the next session
  measures "today's change" against the stored row. A rehearsal on the copy of the real state, with the
  simulated broker holding the official values, showed Monday's summaries for an account that does not move
  at all: real "Bugün yüzde 0,0 arttı (...)", practice "Bugün yüzde 0,1 azaldı (75,11 dolar)". Small, but a
  sentence about a change that did not happen. (The rehearsal before the fourth deploy had assumed that the
  broker's close equals the stored row; the handover note had called the difference a harmless trace.)
- The practice peak had been raised to 61900.58 by that row: a value that never was an official close.

What was done (a one-off step by the operator, 23:43 UTC):

- Both rows of the 20th got the flag `"provisional": true` and nothing else: exactly the shape R5e gives to
  its own on-time closing rows. In Monday's first brake step the engine itself compares them with the
  broker's official close and replaces them (silently, the difference being under 2 percent). No account
  value was typed into a row by hand. The write went through the engine's own lock, validation and atomic
  replace (`Rails._change`), from a small script fed to the container; the script names the account and the
  value it expects and refuses anything else. Each change left a line in `ops_log.jsonl`
  (`mark-close-provisional`).
- Practice account only: the peak was set to the broker's official close with the regular command
  (`init-peak --force`; it checked the value against the account): 61825.4749575993, the broker's own digits.
  The old peak is named in `peak_source` and in the operator log.
- Before the step: backups of both rails files on the server, outside the containers; a look-only pass of
  the script; the server's two files had the same hash as the copies the rehearsal ran on.
- Rehearsal (28 cases on the copy of the real state; not in the repository, the copy holds account values):
  the weekend stays silent; on Monday both rows become the broker's values and no message is sent; for an
  unchanged account both summaries say "Bugün değişmedi.", and when the owner's share moves by 13 dollars the
  summary says 13,00; the practice engine sells its extra share with one order, or keeps it on a second dip;
  Tuesday confirms Monday's row. A new process at any moment, a broker that is down in the morning, one odd
  account reading, a broker that does not name the day of its close: the same result. If the broker names an
  older day all Monday, nothing is confirmed and everything is as it would have been without the step.
- Three independent reviewers tried to refute the step before it was applied: one on the engine's paths (30
  side-by-side runs of its own, with and without the step, including price gaps of 16, 40 and 50 percent, a
  roll-back to R5d, a late close, the short Friday), one on the script and the procedure (28 hostile runs:
  corrupt files, a held lock, wrong arguments), one on whether it should be done at all (against leaving it,
  typing the values by hand, or waiting). No blocker; "safe" three times. What they found was built in before
  applying: the script names the account and the expected value, so the wrong container cannot be stepped;
  it writes a log line only for a change; it has a way back (`--undo`, through the same lock: a file copied
  over the rails file is NOT safe next to a running engine, because readers take no lock); and the practice
  peak was typed with the broker's own digits (typed in cents, the engine would have raised it by half a cent
  on Monday through a branch that sends a doubt message when the account has crashed at that very moment).
- After the step: both engines alive (same start time, fresh heartbeats), health as before, the engine's own
  reader and `ops status` accept both files, logs unchanged, the watchdog silent, nothing sent. The state as
  it now is on the server was copied and run under the simulator through Monday and Tuesday once more: as
  rehearsed, also when the broker answers with the very digits it printed on Saturday.
- The way back, should it be needed before Monday's open: the script with `--undo` in each container (it
  takes the flag off while the row still holds its old value) and, on the practice account, `init-peak
  61900.58 --force`. After Monday's first brake step there is nothing to take back: the rows then hold the
  broker's own values.

## 2026-11-23 (Monday) , the first session on the new code: the check after the open

Read-only, at 14:44 UTC (09:44 ET, fourteen minutes into the session). Nothing was changed on the server: the
watchdog was not run by hand and no helper file was put there.

- The engine confirmed both rows of the 20th by itself, in the first brake step of the session (both rails
  files were rewritten at 14:30:05 UTC and not since): the flag is gone from both rows. Practice: the row now
  holds 61825.4749575993, the broker's `last_equity` to the last digit (the broker printed
  61825.47495759929998 with `balance_asof` 2026-11-20); the peak is unchanged (same value, same `peak_time`,
  no peak event), so the engine took the path "equal, nothing to raise". Real: the row holds the broker's
  official close (its `last_equity` and its daily history agree to the cent); the peak is unchanged.
- Nothing was sent: both message files still end with the summaries of the 20th, no topic holds a message,
  there is no `CLOSE_CORRECTED` and no alert. The step of the 21st did what its rehearsal had shown.
- Rails on both accounts: no brake record, floor OK, no alert, no pause, not liquidating. The brake step keeps
  running every five minutes (the lock file is touched, the rails file is not written).
- Both containers as before: started 22:31 UTC on the 20th, restart count 0, no memory kill; the sixteen code
  files have Saturday's hashes (the published tree). Heartbeats 12 and 15 seconds old, phase `session`,
  session 2026-11-23; real `['DRY_RUN']` (health level 1 by design), practice `[]` (level 0); readiness ok and
  `owner_unreachable` false on both. Logs: the start line only. The calendar file was refreshed that morning
  (04:03 UTC).
- The watchdog: cron line in place, script unchanged, 234 runs in the last 39 hours (every ten minutes, the
  last one at 14:40), no log file and no stamp: it has still never spoken.
- The broker, read-only: both accounts active. Practice: the extra share of the 20th is held (499.318566932
  shares), no order since the 20th, no open order. The market opened far above Friday's close (at the time of
  the look the index fund about 6 percent, the leveraged fund about 17 percent), so the practice account
  stood at 65170.91, about 5.4 percent above its close of the 20th. Real: only the owner's own share with its
  two exit orders; no order of the engine.
- The owner's own share is held and both exit legs rest at the broker; no change of its levels was proposed
  (figures in the operator's private notes).

## 2026-11-23 (Monday) , the first session on the new code: the decision, the close, both summaries

Read-only, the server at 21:09 UTC (nine minutes after the close), the broker at 16:12 ET. Nothing was
changed on the server.

- Both engines decided at 20:50 UTC (15:50 ET), two seconds apart, from the same inputs: 260 closes ending
  with the 20th at 608.45, the session's range 642.04 to 657.42, the price of that moment (651.83 and
  651.475). The price is below all three averages (709.04, 682.30, 669.23): trend down, core target zero. IBS
  0.64 and 0.61, not under 0.2: no second dip, so the extra share of the 20th is not kept. The repository's
  rule applied to the recorded inputs gives every recorded field again (18 of 18 in both records), and so
  does the arithmetic by hand.
- Practice: reason keys `TREND_DOWN`, `OVERLAY_EXIT`. ONE order (`v5-paper-20261123-dec-1`, sell all,
  499.318566932 shares, one submit), filled completely at 49.10. Events `DECISION_ATTACHED`, `SNAPSHOT`,
  `PLANNED`, `SUBMIT_STARTED`, `SUBMITTED`, `RECONCILING`, `DONE` (20:50:38 UTC, 28 seconds after the
  decision). Cash before, 41253.55, plus the proceeds, 24516.54, is the cash after: 65770.09. No position
  left; `overlay.json` holds fraction 0.0 for the 23rd. The broker shows exactly this one order since the
  20th, no fund position, cash and equity 65770.09. (The broker's `created_at` is 0.3 seconds after the
  engine's submit; its own `submitted_at` comes 18 seconds later and the fill 7 seconds after that. On the
  20th the same gaps were 10 and 4 seconds: the practice simulator's routing, not the engine's.) The extra
  share had been bought at 41.31 on the 20th and was sold at 49.10.
- Real: reason key `TREND_DOWN`, status `NO_TRADE`, `dry_run` true, no intent. A dry run holds nothing (the
  would-be purchase of the 20th was never a holding), so there was nothing to sell. The broker shows no
  order at all since the owner's own purchase.
- The close: both engines wrote the row of the 23rd with the flag (`"provisional": true`) at 21:01 UTC;
  practice 65770.09. Peaks unchanged (491.08 and 61825.4749575993), no brake record, floor OK, no alert, no
  pause. `runner.json` lists the 23rd as closed on both.
- Both summaries were sent once and delivered at the first attempt (21:01:09 and 21:01:19 UTC); each topic
  holds exactly that one message of the day. Every number was recomputed from the records with the code's
  rounding. Practice: "Hesap: 65.770,09 dolar. Bugün yüzde 6,4 arttı (3.944,62 dolar)." is 65770.09 against
  the CONFIRMED row of the 20th, 61825.4749575993 (against the old row it would have said 6,3 and 3.869,51);
  "yüzde 0 Nasdaq fonu, yüzde 100 nakit"; "Bu ay: yüzde 6,5 artıda. Başlangıçtan beri: yüzde 6,5 artıda." is
  against the first row, 61729.43. Real: the day's change is measured against the confirmed row as well, to
  the cent ("Bugün yüzde 0,3 azaldı", with a small dollar amount in brackets that is kept in the operator's
  private notes; against the old row both figures would have been different); the holdings line gives the
  fund 0, the owner's share 18 and cash 82 percent; month and total
  0,5 percent down against the first row, 490.06. Action lines: practice "Elimdeki fonun tamamını sattım.",
  real "İşlem yapmadım.".
- Logs: three lines per container (the start, "decision 2026-11-23: ...", "session 2026-11-23 closed: ...");
  no warning, no error. Containers: same start time, restart count 0, the sixteen code files unchanged.
  Health: practice level 0, real level 1 with `DRY_RUN`; `owner_unreachable` false on both.
- The watchdog: silent all day (no log file, no stamp); its cron runs every ten minutes.
- The owner's own share is held and both exit legs rest at the broker; no change of its levels was proposed
  (figures in the operator's private notes).
- The real account, every order since 1 November, read from the broker: two parents, a sale of the old
  system on the 5th and the owner's own purchase with its two legs. No order carries the engine's client id
  (`v5-...`): the engine has never sent an order on the real account.

Three independent reviewers then read copies of the evidence (both server dumps, the four decision records,
the broker readings) against the code, without network access, each with its own lens: the state files
against what the code must have written; every number and every sentence; the broker's records and the days
ahead. No blocker.

- Reproduced by them independently: the hashes of the sixteen code files; the branch the morning
  confirmation took on each account (practice: the official value equals the peak, so nothing is raised);
  the state files byte for byte (sizes rebuilt with the engine's own writer); all 18 fields of all four
  decision records (20th and 23rd, both accounts) from their stored inputs; the order of the writes at the
  close; both summaries rebuilt character for character with the engine's own text builder; the practice
  orders matched with the broker's to the second; Tuesday's first brake step run with the repository's own
  rails class on a copy of the evening state (practice: row confirmed and peak raised, real: row confirmed
  and peak kept, no message in either case; with a broker that still names the 20th: nothing confirmed,
  nothing raised, no message).
- What they found is wording and things worth knowing, recorded as `RF-ISSUES-6.md` items 36 and 37: the
  summary sentences that say "dün" or "yarın" (item 36, the only one that needs code), and a list of
  observations (item 37), among them that the regulatory fees of the sale are booked overnight, so the
  practice account's official close of the 23rd will probably be some cents below 65770.09.

## 2026-11-24 (Tuesday) , the check after the open: Monday's rows confirmed, the first automatic peak raise

Read-only, at 14:42 UTC (09:42 ET, twelve minutes into the session). Nothing was changed on the server.

- The engine confirmed both rows of the 23rd in the first brake step (rails files rewritten at 14:30:10 and
  14:30:17 UTC, not since): the flag is gone from both. Practice: the row went from 65770.09 to
  65769.57000000094, the broker's `last_equity` to the last digit (`balance_asof` 2026-11-23). The 0.52 are
  the regulatory fees of Monday's sale, booked overnight (the account activities name them: 0.51 and 0.01),
  as the reviewers had expected. Real: the row holds the broker's official close, a few cents from the
  evening's reading.
- The practice peak was raised by the engine itself for the first time on the server: from 61825.4749575993
  to 65769.57000000094, the confirmed close. As expected, `peak_source` and `peak_time` still name the manual
  setting of the 21st, and no event line was written (`RF-ISSUES-6.md` item 37). The real peak is unchanged
  at 491.08.
- Nothing was sent: both message files still end with the summaries of the 23rd; no `CLOSE_CORRECTED`, no
  alert. No brake record, floor OK, no pause, on both accounts.
- Both containers as before (started 22:31 UTC on the 20th, restart count 0, the sixteen code files
  unchanged). Heartbeats 17 and 7 seconds old, phase `session`, session 2026-11-24; real `['DRY_RUN']` (health
  level 1 by design), practice `[]` (level 0); `owner_unreachable` false on both. Logs: no new line since the
  close of the 23rd. The calendar file was refreshed in the night. The watchdog ran every ten minutes and
  never spoke.
- The owner's own share left the real account that morning: the owner's own resting stop order was
  triggered at 14:38 UTC and sold it, and the broker cancelled the other exit leg. This is the owner's order,
  not the engine's: the real account still has no order with the engine's client id. The engine took it
  quietly: the brake step two minutes after the sale wrote nothing and sent nothing (the account moved by
  less than two percent on the day), readiness stayed ok. The real account now holds cash only, so the
  engine's part is the whole account again; `V5_OWNER_SYMBOLS` can stay as it is. (Figures in the operator's
  private notes.)
- The broker, read-only: both accounts active; practice all cash, no order since Monday's sale; real: no
  position, no open order.

## 2026-11-24 (Tuesday) , the close of the session (read in the night, 05:01 UTC on the 25th)

- Both engines decided at 20:50 UTC: the price is below all three averages (708.93, 682.42, 669.35) and IBS
  is 0.52 and 0.54, so no trade on either account (`NO_TRADE`; the practice account holds cash and sent
  nothing). The rule on the recorded inputs gives all 18 fields again in both records. Minute coverage of
  the range 0.99.
- Both rows of the 24th were written with the flag at 21:01 UTC. Peaks unchanged (491.08 and
  65769.57000000094), no brake record, floor OK.
- Both summaries were delivered at the first attempt and recomputed from the records. Practice: "Hesap:
  65.769,57 dolar. Bugün değişmedi." (against the confirmed row of the 23rd, which already carries the fee of
  Monday's sale, so that fee is not shown as a change of Tuesday). Real: the first summary after the owner's
  share left the account: the holdings line shows the fund 0 and cash 100 percent, and the day's change in
  it is that sale, while the action line rightly says "İşlem yapmadım.". The summary does not say why the
  account fell that day; the owner was told by the operator (an idea for later is in `RF-ISSUES-6.md` item 37).
- Logs: two new lines per container (the decision, the close), no warning. The watchdog stayed silent. The
  broker: both accounts all cash; still no order with the engine's client id on the real account.

## 2026-11-25 (Wednesday) , the check after the open, and the owner's word: "open real money"

- Read-only at 14:56 UTC. Tuesday's rows were confirmed in the first brake step (14:30 UTC): both flags are
  gone; the practice row holds the broker's `last_equity` to the last digit, the real row the broker's
  official close (the same as the evening's reading). Peaks unchanged, nothing sent, no brake record, floor
  OK. Both containers as before (started 22:31 UTC on the 20th, restart count 0), the sixteen code files
  equal to the published tree (compared by hash), heartbeats fresh, phase `session`; real `['DRY_RUN']`
  (level 1 by design), practice `[]` (level 0). The watchdog ran every ten minutes and never spoke. The
  engine's cached calendar has no session on the 26th and closes the 27th at 18:00 UTC.
- The owner wrote at about 06:50 PT, the session already open: look at the state, fix what needs fixing, and
  open real money. The rotation of the notification channel was postponed by the owner; details are in the
  operator's private notes.
- By the promise of 16 November he was asked once more right before the step, with what he had to know: the
  engine now manages the whole account; the day was a hard fall, so the rule might buy the extra third that
  evening and hold it over the holiday until Friday; the first order of the engine ever on the real account.
  Three choices: now, after the close, not yet. His answer: "evet ac".

## 2026-11-25 (Wednesday) , the live flag: real orders are on since 16:31 UTC

- When: during the session, four hours and nineteen minutes before the decision window. The note of the 23rd
  had "outside a session"; the rehearsal changed that. (1) A switch before the day's decision finds no dry
  record of that day, so nothing of the day exists twice. (2) The first real decision then falls on a
  regular session with the operator watching, not on Friday's half day. (3) The wrong moments are known and
  far away: from the decision cutoff (20:56 UTC) until the old process has closed the day, a new process with
  orders on finds no real record of the day and tells the owner "karar saatini kaçırdım" although the dry
  process had decided; inside the window, after the dry decision, it would decide a second time, for real.
- Rehearsal first (in the operator's lab, not in this repository: the files hold account values). The engine
  of the published tree on a copy of the real account's state folder taken that morning, with the fake
  broker and clock of the test suite, and the flag switched at different moments. Mid-session, quiet close:
  no order, a real `NO_TRADE` record, a summary without the dry-run label, Thursday silent, Friday decides at
  12:50 ET, Wednesday's row confirmed on Friday. Mid-session, a dip: one notional buy of a third of the
  account under the id `v5-live-20261125-dec-1`, the books at fraction 1.0, the whole share sold on the half
  day at 12:50 ET (with a second dip: kept, and sold on Monday). A restart inside the window: one order.
  After the close: silent, no "missed", Friday the first real day. The two wrong moments. And back to dry
  with a share held: a dry process does not sell it and says what it would have done. Ten scenarios, all as
  the plan says.
- Three independent reviewers (fresh agents; one lens each: the procedure on the host, the engine's paths at
  and after the switch, the real broker path) read the code, the evidence of that morning and the scripts.
  Two of them ran scenarios of their own: the engine reviewer twenty-six on the same state copy, the
  procedure reviewer the switch script under dash against mock tools. Verdicts: go, go, and go after fixes.
  Every finding of the third was in the switch script, none in the engine: a verification that could say
  "verified" on the first beat of a new process (the engine writes a beat before its first tick ends); the
  wrong words after a store call that timed out; a transcript that could be lost with the connection; an
  undo that could leave the flag in the store and still end with 0; no proof that the recreated container
  differs by the flag only (compose on the host wanted to recreate even without a change, because its paths
  are not the deploy tool's); no check that the store's row is a runtime row. All of it was built in. A
  second tester then ran the rewritten script through more than eighty failure cases on a mock host (every
  guard fails closed, nothing is changed before the last guard has passed, no secret in any transcript)
  and asked for five smaller changes, also built in; the most relevant cases were run again on the final
  text. The engine reviewer's notes are `RF-ISSUES-6.md` item 38.
- The step itself was one script on the host, its guards first (its guards-only mode had passed twice that
  hour). (1) The flag as a row of the deploy tool's env store, so that a later deploy keeps it: answered
  201; a production and a preview row, both runtime rows. (2) The flag as the last line of the host env
  file, after a backup: the old bytes unchanged, one flag line, the last old line intact; compose, asked
  what a recreate would now change, named exactly `LIVE_TRADING_ENABLED`. (3) Right after a ten-minute mark
  of the watchdog, at 16:31:04 UTC, ONLY the real-money container was recreated with compose and without a
  build: same image id, same name. The old process was given ten seconds (it has no handler for the stop
  signal and is ended), so the account was about twelve seconds without the engine. (4) Verified by the same
  script: the environment without the flag has the same hash as before (30 entries); command, user, restart
  policy, logging, network and mounts unchanged; the practice container untouched (same id, same start time
  of the 20th). The first full tick of the new process three seconds after its start: phase `session`,
  states `[]`, readiness ok, orders on, health level 0. The start line says `live_trading_enabled=True`. No
  message was queued (the message file has the same six lines), the rails file and the runner file are
  byte-equal, there is no real record yet and no temp file. The transcript is kept on the host.
- Read back independently. The broker, read-only: the real account active, all cash, no position, no open
  order, no order of that day. `decide-now` inside the new container: the only blocker is
  `OUTSIDE_DECISION_WINDOW`, the rule is READY (trend down; IBS 0.21 at that minute, a hair above the
  threshold of 0.2).
- What changes for the owner: messages about the real account no longer begin with "DENEME, gerçek emir
  gönderilmedi."; the summary can now say that a purchase or a sale was made.
- Going back, if ever needed (on the owner's word, or for an evident fault that loses money): the operator's
  undo script takes the flag out of the store and the file and recreates the container with orders off. It
  sells nothing and cancels nothing: a position stays until the owner decides. The practice container was
  not recreated and does not have the flag in its environment; it gets it with the next deploy, where it
  means nothing (a practice process sends orders anyway).

## 2026-11-25 (Wednesday) , fix round R5g: eleven sentences (built and reviewed; not deployed yet)

- `RF-ISSUES-6.md` item 36: the owner's sentences said "yarın", "dünkü" and "bir gün daha" where the engine
  means the next or the previous SESSION. The contract is `rf6/fix-r5g.md` (plan Revision 16). Words only:
  eleven texts of `v5/notify.py`; no number, order, key or rule.
- Codex's quota was closed until that morning, so by the owner's rule a fresh Claude agent built it from the
  contract, in a separate working copy. Before the build the reviewer's own suites were moved to the new
  sentences (they failed on the old code); the reviewer's new suite `tests_v5/test_r5g_saldirgan.py` was
  written from the contract before reading the build (12 of its 14 tests fail on the old code). The whole
  suite passes on Windows: 950 tests in the builder's run, 964 with the reviewer's suite in the reviewer's own
  run. A fresh reader checked the diff (no finding that had to be fixed; its wider guard pattern was taken). At 16:04 UTC Codex worked again and reviewed the commit
  read-only: the diff is exactly the eleven texts; one finding, answered with a step of the upgrade list and
  not with code (a message queued before an upgrade keeps its old words; item 36).
- Commits on the working branch: `4156448` (the fix) and `df2ee2a` (Codex's review and the upgrade step).
  The server still runs the published tree `405d2fc` (R5f). The deploy follows the upgrade steps of
  `RF6-CALISMA-KILAVUZU.md`, section 7, outside a session; after it the real-money engine must still have
  orders on (the flag is in the store).

Still to do (in this order)
1. Done: first deploy, the morning checks, the first decision day, the Codex whole-tree review (R5d), fix
   rounds R5e and R5f and their deploys, the weekend check and the step on the rows of the 20th, Monday the
   23rd (the first whole session on the new code), the confirmation of its rows on the 24th, and on the 25th
   the live flag (all above).
2. Wednesday 25 November, 20:50 UTC: the first decision with real orders on. Watch the record, the order if
   there is one (its size against the rule, the fill, the books), the closing row, the summary (no dry-run
   label), and the broker's own view of the account. Anything bought that day is held over Thursday and
   decided on Friday at 12:50 ET.
3. Deploy R5g outside a session (the upgrade steps of the guide; nothing waiting in either message file;
   afterwards: orders still on in the real-money engine).
4. The week's odd days, seen on the server for the first time: no session on Thursday the 26th; an early
   close (13:00 ET) on Friday the 27th with the decision at 12:50 ET. On Friday morning look at what day the
   broker names for its last close after the holiday (`balance_asof`): if it names the 26th, Wednesday's row
   stays unconfirmed, which is safe but worth knowing. On Friday evening look at the minute-bar coverage in
   the decision record (`range_coverage`, `range_known`).
5. The rotation of the notification channel was postponed by the owner; details are in the operator's
   private notes.
6. The owner's choice from the improvement list; the clean-up after go-live (`RF-ISSUES-6.md` item 12); the
   notes of item 38.
