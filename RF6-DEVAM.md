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

Still to do (in this order)
1. Done the same evening (see above).
2. 20 November, at the open: read `last_equity` before and after 09:30 ET (when does it turn over?); check the
   owner's ANT order; paper: clear the v4 leftovers, run the drill, then `decide-now` in both containers and a
   cross `replay`.
3. 20 November 15:50 ET: watch the first paper decision and both evening summaries.
4. Codex whole-tree review: started again at 20:56 PT with a request worded as a correctness review
   (`rf6/review-codex-tree-2.md` in the operator's backup folder), in a copy of the tree outside the repository;
   it writes its findings to a file as it goes. Its findings are processed before the live flag.
5. Ask the owner; only then the live flag.
