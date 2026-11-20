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

Still to do (in this order)
1. Done the same evening (see above).
2. Done on the morning of the 20th (see above).
3. 20 November 15:50 ET: watch the first paper decision and both evening summaries.
4. Codex whole-tree review: done and processed in R5d (see the third deploy above).
5. Fix round R5e: built and reviewed on the morning of the 20th (`RF-ISSUES-6.md` item 32, plan Revision
   14). Its deploy (the fourth) follows after the first session has closed and its two summaries were read.
6. The notification channel gets a new name before real money trades (the owner subscribes on the phone
   first; details are in the operator's private notes).
7. Ask the owner; only then the live flag.
