GOAL: Trading botunun karar hattindaki KALICI zarar-serisi kilitlenmesini kaldir. Bittiginde:
  canli profilde Temmuz'dan kalan `consecutive_losses=2` durumu yuklenince SONER (24 saat gecmis),
  53.9 guvenli bir META BUY adayi kayip-koruyucu kapisindan GECER; hicbir girdi kombinasyonu seriyi
  24 saatten uzun kalici yapamaz; seri her CALISMA PROFILI icin ayri tutulur, dolayisiyla
  paper_live_config agresif botun zararlarini devralmaz; zaman kaynagi gercek broker dolum zamanidir.

SPEC: Read RF-PLAN-5.md, rock **R20** (bolum basligi "### R20 , Zarar serisi..."). It is frozen and
  already reviewed in four adversarial rounds. Implement it exactly, including the PROOF suite
  (a)-(m) as `tests/test_r20_streak_decay.py`. If a detail is impossible as written but the intent is
  unambiguous, implement the closest faithful version and report the deviation. If the impossibility
  is MATERIAL (would change behavior, scope, or an interface), do not improvise: stop, output
  `BLOCKED: <reason>` as your report, and wait. Do not redesign.

  Iki nokta ozellikle onemli, cunku inceleme bunlari yakaladi:
  - Seri guncellemesi ile kalici yazim AYNI adimda olmali. Bugun `core/executor.py:795-804` ve
    `stock_bot.py:2034-2047` metadata'yi seri guncellenmeden ONCE kaydediyor; restart sonrasi
    `last_loss_at` diske yazilmamis olabilir. Test (i) bunu gercek yeniden-yukleme ile kanitlamali.
  - `entry_profile` alani TUM kalicilik ve geri-yukleme yollarindan gecmeli (stash, broker-sync,
    long/short metadata restore). Test (h) her yolu ayri ayri gezmeli.

KEY PATHS:
  Once oku: RF-PLAN-5.md (R20 bolumu), core/streak.py, core/trade_gates.py (_check_loss_streak,
  check_all_gates, coin_filter dali), core/run_profile.py, config.py (STOCK_CONFIG kayip serisi
  bloku ~418-426), stock_bot.py (_save_position_metadata ~2160-2185, metadata load ~2285-2300,
  _stash_exit_flags, broker-sync ve restore yollari ~2100-2265, heartbeat satiri ~1722,
  _reconcile_external_exit ~2034-2047), core/executor.py (~795-810 cikis muhasebesi, ~460-472 yeni
  pozisyon metadata'si), core/short_executor.py (~240-260, ~390-400), tools/parity_harness.py
  (~340-350), tests/fixtures/canlandirma_2026_09_11/ (gercek canli durum fixture'lari).
  Yeni dosya: tests/test_r20_streak_decay.py.

CONSTRAINTS:
  - Bu rock YALNIZ zarar serisi + sembol filtresi zamanlamasi + profil sahipligi. Guven esigini,
    bantlari, kilidi, equity tabanini, kill tasfiyesini, funnel/saglik semasini DEGISTIRME (onlar
    R21/R24a/R24b/R22).
  - Yeni bagimlilik yok, ag erisimi yok. Mevcut kod stili ve Turkce yorum dili korunur.
  - Kalici yazimlar atomik olmali (mevcut atomik yazim yardimcilarini kullan).
  - Zaman damgalari UTC ve tz-aware saklanir; naive okuma UTC kabul edilir; gelecek tarih `now`'a
    kirpilir (WARN log); bozuk damga "eski durum" gibi ele alinir.
  - Mevcut 592 testin HICBIRI kirilmayacak. `tools/parity_harness.py` cikti davranisi degismeyecek.
  - .env dosyasi YOK; testler ag cagrisi yapmamali.

NON-GOALS: R21 (esik 45 + bantlar), R24a (taban/bear kilidi/auto-lock), R24b (kill tasfiyesi),
  R22 (funnel/saglik akisi), R23 (deploy). RF-ISSUES-5'teki ertelenmis maddeler. Baska hicbir
  davranis degisikligi.

PROOF: Run exactly these and include full output in your report:
  ALPHA_VANTAGE_KEY=dummy-test-key python -m pytest tests/ -q
  ALPHA_VANTAGE_KEY=dummy-test-key python -m pytest tests/test_r20_streak_decay.py -q
  python tools/parity_harness.py
  (Baseline: tests/ -> 592 passed. parity_harness bilincli olarak exit 1 dondurur; ciktisi
  baseline ile AYNI olmali, yeni regresyon satiri eklememeli.)

OUTPUT: End with a report: files changed (one line each: path + what/why), the proof output, and any
  deviations from the spec with reasons. Do not commit; do not touch git.
