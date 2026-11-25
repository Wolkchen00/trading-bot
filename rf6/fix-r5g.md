# Fix round R5g: owner sentences that say "yesterday", "tomorrow" or "one more day" (plan Revision 16)

You are the builder. I am the reviewer: I will read your whole diff, run the proof myself and run my own tests
written from this text. Work in this folder (a git worktree of the repository on its own local branch). Do not
commit, do not push. Do not open `.env`. No network is needed.

Background: `RF-ISSUES-6.md` item 36. The owner is not technical and reads every message on a phone: every
sentence must be literally true whatever the calendar looks like. The engine decides once per SESSION, and the
session after today is not always tomorrow: every Friday it is three days away, and before a market holiday two.
On Monday 23 November 2026 the practice account's summary said "Dünkü düşüş için tuttuğum ek payı sattım."
about the fall of Friday the 20th. The trade and every number were right; the word was not. This week the same
happens around the holiday of Thursday the 26th. This round changes WORDS ONLY: no number, no order, no rule,
no key, no control flow.

## Rules

- Files you may edit: `v5/notify.py` (only the texts of the eleven entries named below, in the two tables
  `REASON_SENTENCES` and `TEMPLATES`), ONE new test file `tests_v5/test_r5g_duzeltme.py`, and existing tests
  ONLY where they pin the OLD text of one of the eleven sentences. Known places: `tests_v5/test_r4_notify.py`,
  `tests_v5/test_r5b_inceleme.py`, `tests_v5/test_r5c_duzeltme.py`, `tests_v5/test_r5e_duzeltme.py`; search for
  others. Name every changed assertion in your report.
- Do not edit the reviewer's suites (`tests_v5/*_saldirgan.py`): they already pin the NEW sentences, so a number
  of their tests fail on the code as it is and must pass after your change.
- Do not touch any other module of `v5/`, `v5/strategy.py`, the golden files or the plan documents
  (`RF-*.md`, `rf6/*.md`). The reviewer updates the plan's sentence table.
- The keys, the placeholders (`{pct}`, `{cause}`, `{product_pct}`) and every other sentence stay exactly as they
  are. No template may begin with a placeholder. No new dependency, no new function.
- If an item contradicts the plan in a way that matters, answer `BLOCKED:` with the reason. Do not guess.
- Proof at the end (about ten minutes on Windows):
  `python -m pytest tests_v5 -q -p no:cacheprovider --basetemp=.pytest_tmp_r5g`

## T1. The eleven sentences

Each line gives the key and the exact new text. Turkish letters matter (dotless "ı", "ü", "ş", "ğ", "ç", "ö").

`REASON_SENTENCES`:
- `OVERLAY_ENTER`: "Bugün sert bir düşüş oldu, sonraki işlem günü için küçük bir ek alım yaptım."
- `OVERLAY_WOULD`: "Bugün sert bir düşüş oldu; gerçek emir açık olsaydı sonraki işlem günü için küçük bir ek alım yapardım."
- `OVERLAY_HELD`: "Bugün sert bir düşüş oldu, bu yüzden elimdekinin bir kısmını sonraki işlem gününe kadar tutuyorum."
- `OVERLAY_KEEP`: "Düşüş sürdü, ek payı bir işlem günü daha tutuyorum."
- `OVERLAY_EXIT`: "Düşüş için tuttuğum ek payı sattım."

`TEMPLATES`:
- `BREAKER`: "Hesap bugün yüzde {pct} düştü. Bugün yeni alım yapmıyorum. Sonraki işlem gününde normal çalışmaya dönerim. Senden bir şey beklemiyorum."
- `HOLD_NOT_READY`: "Bugün karar veremedim, çünkü {cause}. Elimdekilere dokunmadım. Sonraki işlem gününde yeniden denerim."
- `MISSED`: "Bugünkü karar saatini kaçırdım. Elimdekilere dokunmadım. Sonraki işlem gününde normal devam ederim."
- `ORDER_UNFILLED`: "Emrim gerçekleşmedi ve iptal oldu. Şu an elimde yüzde {product_pct} Nasdaq fonu var. Sonraki işlem gününde yeniden karar veririm."
- `ORDER_PARTIAL`: "Emrimin tamamı gerçekleşmedi. Şu an elimde yüzde {product_pct} Nasdaq fonu var. Sonraki işlem gününde duruma yeniden bakarım."
- `NOT_SENT`: "Bugün bir işlem yapmam gerekiyordu ama emri gönderemedim, çünkü {cause}. Elimdekilere dokunmadım. Sonraki işlem gününde yeniden karar veririm."

Why these words: "sonraki işlem günü" names the next session whatever the calendar does. `OVERLAY_EXIT` names
no day at all: the extra share may be sold one session after the fall, or later (after `OVERLAY_KEEP`, or after
a session in which nothing could be decided), and "the fall" is true in each case.

## T2. No sentence of the engine names a calendar day relative to today

After T1 no text in `REASON_SENTENCES`, `TEMPLATES`, `CAUSE_SENTENCES`, `FLOOR_WAIT_CAUSES` or
`OUTCOME_SENTENCES` may contain a word that begins with "yarın" or "dün" (any case), nor "ertesi gün", nor
"bir gün daha". ("Bugün" stays: a sentence that says it is only sent on the day it speaks about, or inside a
summary that carries that day's date.)

## Tests (new file `tests_v5/test_r5g_duzeltme.py`; reuse `Sim`, `window`, `D1`, `D2`, `D3` and the other
helpers of `tests_v5/test_r5_saldirgan.py` by importing them)

1. The table: the eleven keys have exactly the texts of T1; T2 holds for all five tables.
2. A dip before a holiday: `Sim`; Wednesday 2026-11-25 is a dip day (`sim.ibs[day] = 0.0`), Thursday the 26th
   has no session, Friday the 27th closes early. Wednesday's summary has "sonraki işlem günü için küçük bir ek
   alım yaptım." in its fifth line and no word beginning with "yarın"; Friday's summary has "Düşüş için tuttuğum
   ek payı sattım." and no word beginning with "dün"; the orders are one buy and one sell.
3. The same with a second dip on Friday: Friday's summary has "Düşüş sürdü, ek payı bir işlem günü daha
   tutuyorum." and there is still only the one buy.
4. A missed window on a Friday (the process starts after `decide_until`, like
   `test_window_missed_is_reported_and_the_next_day_is_normal`, on Friday 2026-11-20): the alert and the
   status line of the summary are the new `MISSED` text.

Each of the four must fail on the code as it is and pass after your change.

## Report

What you changed, every existing assertion you changed (file and line), and the last line of the proof command.
