# RF-PLAN-5.md , Canlandırma: karar hattındaki kalıcı kilitlenmeyi kaldır, İhsan'ın kararlarıyla canlıyı aç

> Tarih: 2026-09-11 (Los Angeles). Sürücü: Claude (Visionary) + Codex (Integrator).
> **Revizyon 2** , Codex Round 1 bulgularından sonra (kayıt: RF-SAME-PAGE-LOG-5.md).
> KOD baseline'ı: `1e731be`. Planlama commit'i: `39239f9` (worktree dalı `codex-canlandirma`).
> Baseline kanıtı (Claude koştu): `ALPHA_VANTAGE_KEY=dummy-test-key py -m pytest tests/ -q` -> **592 passed**.
> Not: anahtar env'i OLMADAN 2 R16 testi Yahoo yedeğine düşüp gerçek ağa gidiyor; ana ağaçta `.env`
> olduğu için geçiyordu. Üretim hatası değil, test hermetikliği (RF-ISSUES-5).

## CORE FOCUS (tek cümle)

**Canlı botu yeniden işlem yapar hale getirmek: karar hattındaki kalıcı kilitlenmeleri kaldır,
İhsan'ın kararlarını (kilit açık, eşik 45, zarar serisi 24 saatte söner) güvenle uygula ve hiçbir
alet tıkalı bir hattı "sağlıklı" diye göstermesin.**

## İNŞA SIRASI

R20 (seri) -> R21 (eşik + bant) -> R24 (canlı emniyet) -> R22 (dürüst akış) -> R23 (deploy, operasyon).
R22, R24'ün executor red sebeplerini raporladığı için R24'ten sonra gelir.

---

## TEŞHİS (2026-09-11, canlı konteynerlerde ölçüldü)

İki konteyner 7 gündür ayakta (`trading-live`, `trading-paper-livecfg`), her 30 sn karar üretiyor.
`tools/saglik.py`: live `KILITLI`, paper `SAGLIKLI`. İkisi de yeni strateji girişi yapmıyor.

### 1. ASIL ENGEL: zarar serisi kalıcı kilitlenmesi

- Canlı `bot_positions.json`: `"consecutive_losses": 2`. Kaynak `trade_history.json`: NVDA -$0.31
  (2026-07-15), RIVN -$2.26 (2026-07-16).
- `core/trade_gates.py:176-183`: seri >= 2 iken güven >= 70 şart. Seri yalnız kârlı kapanışla sıfırlanır
  (`core/streak.py`). İşlem yok -> kâr yok -> **kalıcı**.
- Asimetri: 4 zararlık HALT 24 saatte söner (`trade_gates.py:164-174`), 2 zararlık WARN hiç sönmez.
  HALT saati (`_loss_halt_until`) yalnız bellekte; restart 24 saati baştan başlatır.
- Sembol filtresi (`trade_gates.py:115-120`, 3 zarar) aynı kalıcılığa sahip.
- Log: `META KAYIP KORUYUCU: 2 ardisik zarar, guven 53.9% < 70%` (09-09; live 285, paper 527 kez).
- Paper-livecfg serisi 09-04'te 0'dı; agresif dönemden devralınan AAPL (09-08) ve GOOGL (09-09)
  kapanışları 2'ye çıkardı. **Profil kirlenmesi.**

### 2. Güven nadiren 50'yi geçiyor

- 09-04'ten beri canlı BUY güveni tepe 66, çoğu 12-41. SentAgent BUY oranı %38.5 -> %11.5 (R12 düzeltmesi).
  v4.9 ×2.0 remap hatalı dağılımda kalibre edilmişti.

### 3. R5 kilidi , son engel, ama 8 günde hiçbir aday oraya ulaşmadı (`reached_executor = 0`).

### 4. "Birkaç gün log izleyelim" boş döndü

- R18 gölge defteri yalnız kilit reddinde yazıyor; `shadow_intents.jsonl` hiç oluşmadı.
- Paper-livecfg 09-04'ten beri 0 yeni strateji girişi.

### 5. Aletler yalan söylüyor

- `saglik.py` tıkalı paper için `SAGLIKLI`. NO_TRADE alarmı tarama sayısına göre "conf_below_min" diyor,
  gerçek blokeri gizliyor; hafta sonu "veri_yok" alarmı atıyor.

### 6. Eşik 45 ve bantlar

- `config.py:352-357` en düşük bant `[50, 100]`; `core/position_sizer.py:96-104` bandın altına SIFIR boyut.
- **Paper-livecfg canlı bantlarını HİÇ almıyor** (`stock_bot.py:219-228` bantları yalnız live dalında
  kuruyor). R19'un "canlı karar profili" iddiası boyutlandırmada yanlış; paper Kelly yolunda.

### 7. Canlı emniyet açıkları (Codex Round 1, Claude doğruladı)

- **Equity floor kayıyor:** `stock_bot.py:230` her açılışta `equity * 0.85`. Her restart/deploy tabanı
  mevcut equity'ye göre yeniden kurar; kalıcı %15 kayıp tavanı DEĞİL.
- **R5 global:** `core/risk_guard.py:120-148` stock_long ile birlikte `bear_etf` (canlıda
  `BEAR_BRAIN_CONFIG.allow_live=True`, SH $100 / SQQQ 3x $150) yolunu da açar.
- **Stop doğrulanamazsa** pozisyon açık kalır, yalnız alarm (`core/executor.py:516-529`); koruma
  döngüsü sonraki turda yeniden dener, kapatmaz.
- **Kill tasfiyesi** broker kabulünü dolum sayıp yerel durumu siliyor (`stock_bot.py:2436-2447`). ERTELENDİ.
- `reached_executor` floor/nakit/boyut kontrollerinden ÖNCE artıyor (`core/executor.py:273-275`).
- `wash_sale_block` güven hesaplanmadan ÖNCE koşuyor (`stock_bot.py:729-733`); sayaç cebiri güvenilmez.

---

## İHSAN'IN KARARLARI (2026-09-11)

1. **Canlı kilit:** onarımla birlikte açılır. Açılış **tam bantlarla**: en fazla 3 pozisyon, güvene
   göre $100 / $150 / $200 / $300 (Codex'in 1 pozisyon $100 canary önerisi İhsan tarafından reddedildi).
2. **BearBrain canlıda KAPALI kalır.** Ters-ETF yolu kendi anahtarıyla kilitli; sonra ayrıca açılabilir.
3. **Zarar serisi:** son zarardan 24 saat sonra söner (HALT ile aynı süre); seri profil başına ayrı;
   paper agresif botun zararlarını devralmaz. Aynı 24 saat ilkesi sembol filtresine de uygulanır.
4. **Güven eşiği:** 50 -> **45**.

---

## ROCK'LAR

### R20 , Zarar serisi: zamanla sönme, profil anahtarlı seri, eski durum göçü

**Kapsam:**
- **Profil anahtarlı kalıcı yapı:** `bot_positions.json` içinde `streaks_by_profile`:
  `{ "<profil>": {"consecutive_losses": int, "last_loss_at": <UTC ISO|null>,
  "symbols": {"<SYM>": {"losses": int, "last_loss_at": <UTC ISO|null>}}} }`.
  Profil = `core.run_profile.aktif_profil()`. Bot yalnız etkin profilin kaydını okur/yazar; diğer
  profillerin kayıtları korunur, silinmez. Eski düz alanlar (`consecutive_losses`,
  `symbol_consecutive_losses`) okuma-uyumluluğu için göçte tüketilir.
- **Sönme (config'te açık):** `loss_streak_decay_hours: 24` ve `symbol_loss_decay_hours: 24`.
  `now - last_loss_at >= 24h` ise seri 0'a iner; bir kez log ve kalıcı yazım.
  WARN ve HALT aynı saatten beslenir: HALT, `last_loss_at + loss_streak_halt_hours` dolana kadar.
  Bellek-içi `_loss_halt_until` kalkar; restart HALT'ı uzatamaz.
- **Zaman kaynağı gerçek dolum:** `update_loss_streak` çağıranları (`core/executor.py:804`,
  `core/short_executor.py:395`, `stock_bot.py:2047`) kapanışın broker `filled_at` / fill ledger
  zamanını geçirir; yoksa `now`. Gelecek tarih `now`'a kırpılır (WARN), bozuk damga eski durum sayılır.
- **Profil sahipliği:** her yeni pozisyon metadata'sına `entry_profile`. Alan TÜM kalıcılık ve
  geri-yükleme yollarından geçer: `_save_position_metadata`, `_stash_exit_flags`, broker-sync ve
  long/short metadata restore (`stock_bot.py:2109-2114`, `2200-2209`, `2255-2264` ve eşdeğerleri).
  Seri yalnız `entry_profile == aktif_profil()` pozisyonların PnL'iyle değişir; alan YOK
  (devralınmış/eski) pozisyon seriye dokunmaz, ne zararıyla ne kârıyla.
- **Kalıcılık sırası:** seri güncellemesi ile metadata diske yazımı AYNI adımda. Bugün long ve
  dış-kapanış yolları metadata'yı seri güncellenmeden ÖNCE kaydediyor (`core/executor.py:795-804`,
  `stock_bot.py:2034-2047`); sıra düzeltilir, restart sonrası seri ve `last_loss_at` diskte doğrulanır.
- **Eski durum göçü:**
  - Profil etiketi olmayan eski durum: `state_live` -> `live`, `state_paper` -> `paper_aggressive`
    (paper-livecfg ilk açılışta TEMİZ başlar; agresif serisi `paper_aggressive` altında korunur).
  - `last_loss_at` yoksa: kalıcı `trade_history.json`'daki en son zararlı kapanış zamanı (naive = UTC).
    Canlıda 2026-07-16 -> ilk yüklemede söner. Bulunamazsa saat yükleme anında başlar. Kullanılan yol loglanır.
- **Görünürlük:** heartbeat `Zarar serisi: 2 (söner: <UTC>)`.
- `tools/parity_harness.py:344-347` sahte bot'u yeni yapıyı taşır; parity çıktısı baseline ile aynı.

**Done looks like:** Temmuz'daki canlı durum canlı profilde yüklenince seri söner ve 53.9 güvenli META
BUY kayıp koruyucusundan geçer. Hiçbir girdi seriyi 24 saatten uzun kalıcı yapamaz. Paper-livecfg
agresif serinin etkisiyle başlamaz ve devralınan pozisyonların kapanışından etkilenmez.

**PROOF:**
```
ALPHA_VANTAGE_KEY=dummy-test-key py -m pytest tests/ -q
ALPHA_VANTAGE_KEY=dummy-test-key py -m pytest tests/test_r20_streak_decay.py -q
```
Yeni suite:
(a) seri 2, son zarar 25 saat önce -> WARN yok, 47 güven geçer, seri 0 ve DİSKTE 0;
(b) seri 2, 23 saat -> WARN: 60 bloklu, 70 geçer;
(c) seri 4, 23 saat -> HALT; 25 saat -> serbest; 10. saatte **gerçek restart** (yeni bot nesnesi,
    diskten yükleme) HALT'ı uzatmıyor;
(d) sembol serisi 3, 23 saat -> STOCK_FILTER; 25 saat -> geçer;
(e) eski canlı durum + trade history 07-16 -> `live` altında sönmüş; history yok -> saat yüklemede başlar;
(f) eski paper durumu (seri 2) -> `paper_aggressive` altına göç; `paper_live_config` seri 0;
    agresif kayıt silinmedi;
(g) `entry_profile` farklı ya da YOK olan pozisyonun zararı VE kârı seriyi değiştirmez;
(h) `entry_profile` stash/broker-sync/restore yollarının HER BİRİNDEN geçip restart sonrası korunur;
(i) zarar kapanışı sonrası restart -> seri ve `last_loss_at` diskte (kalıcılık sırası);
(j) `last_loss_at` = broker `filled_at` (bot kapalıyken dolan stop, restart anı DEĞİL);
(k) **KİLİTLENME REGRESYONU:** `tests/fixtures/canlandirma_2026_09_11/live_bot_positions.json` +
    `live_trade_history.json` ile canlı config, `trade_gates.check_all_gates` META 53.9'u loss-streak'te
    bloklamıyor;
(l) gelecek tarihli damga en fazla 24 saat sonra söner; bozuk damga çökertmez; naive/aware karışımı hata vermez;
(m) `tools/parity_harness.py` çıktısı baseline ile aynı.

---

### R21 , Eşik 45 ve bantlar: kararı gerçekten işler hale getir (live VE paper-livecfg)

**Kapsam:**
- `STOCK_CONFIG["min_confidence_score"]`: 50 -> 45 (İhsan kararı, tarih + gerekçe yorumda).
- `live_conf_position_bands`: `[[45, 100], [60, 150], [70, 200], [80, 300]]`.
- **Ortak bant kurulumu:** `stock_bot.py:217-228` live-only dalı, `live` VE `paper_live_config` için
  aynı fonksiyondan kurulur (bantlar, `fixed_position_usd`, `max_position_usd` = live değeri).
  `paper_aggressive` DEĞİŞMEZ.
- Canlı yoldaki her `min_confidence_score` tüketicisi ve her sabit "50" güven literali taranır; canlı
  eşik varsayımı olan yer config'e bağlanır ya da gerekçesiyle bırakılır, raporlanır.
- BEAR rejim +10 aynen (efektif 55).
- `tests/test_r19_live_config_paper.py::test_f_canli_esikler_degismedi` bilinçli güncellenir: 45 ve kilit
  KOD varsayılanı `False` (kilit yalnız env ile açılır).
- Profil hash'i değişir -> yeni `epoch_id` (beklenen).

**Done looks like:** 47 güvenli, kapılardan geçen bir BUY, hem live hem paper-livecfg profilinde
executor'da $100 emir planına dönüşür.

**PROOF:**
```
ALPHA_VANTAGE_KEY=dummy-test-key py -m pytest tests/ -q
ALPHA_VANTAGE_KEY=dummy-test-key py -m pytest tests/test_r21_threshold45.py -q
```
Yeni suite:
(a) live profil, güven 47 -> sizer $100; (b) **paper_live_config profil, güven 47 -> sizer $100**
    (Kelly yolu DEĞİL; bot kurulum yolundan geçerek);
(c) 44.9 -> `conf_below_min_buy`, executor çağrılmaz; (d) 60 -> $150, 70 -> $200, 80 -> $300;
(e) BEAR: 50 bloklu, 55 geçer; (f) `paper_aggressive` = 30 ve kendi boyutlandırması;
(g) literal taraması raporu.

---

### R24 , Canlı emniyet: kalıcı taban, BearBrain kilidi, kendi kendini kilitleme

**Kapsam:**
- **Kalıcı yüksek-su tabanı:** `equity_floor` artık `peak_equity * equity_floor_pct`. `peak_equity`
  kalıcı (state dizininde, atomik yazım): açılışta ve günlük resette `max(kayıtlı, mevcut equity)`.
  Restart tabanı ASLA aşağı çekemez. Kayıt yoksa ilk değer mevcut equity. Bozuk kayıt -> fail-closed:
  mevcut equity ile yeniden kurulmaz; son geçerli değer ya da açılış equity'si, hangisi YÜKSEKSE.
  Live ve paper aynı mekanizma.
- **BearBrain canlı kilidi:** `can_open_new_risk` canlıda `bear_etf` için ayrı env
  `LIVE_BEAR_ENTRIES_ENABLED` (varsayılan false) ister; `LIVE_ENTRIES_ENABLED=true` tek başına ters-ETF
  açmaz. Red sebebi `LIVE_BEAR_LOCK`. Paper etkilenmez.
- **Kendi kendini kilitleme (fail-closed):** canlıda giriş dolup kapsayan stop doğrulanamazsa
  (`core/executor.py:516-529`) ya da koruma uzlaştırması `FAILED_NAKED` üretirse bot kalıcı bir
  otomatik kilit yazar (`state_live/live_auto_lock.json`: sebep, sembol, zaman). Kilit varken
  `can_open_new_risk` canlı stock_long/bear_etf'i `LIVE_AUTO_LOCK` ile reddeder; çıkış/koruma sürer.
  Kilit YALNIZ açık bir işlemle temizlenir (`tools/` altında küçük bir komut). Otomatik kapatma
  (market satış) bu döngüde YOK: koruma döngüsü stopu yeniden dener.
- **DAY stop yenilemesi:** fractional pozisyonun DAY stopu günlük resette
  (`stock_bot.py:2424-2427` `ensure_protective_stops`) yeniden yerleşir; test ile kanıtlanır.

**Done looks like:** Restart tabanı düşüremiyor; `LIVE_ENTRIES_ENABLED=true` ters-ETF açmıyor;
stopu doğrulanamayan canlı girişten sonra bot yeni giriş yapmıyor ve bunu kalıcı olarak hatırlıyor.

**PROOF:**
```
ALPHA_VANTAGE_KEY=dummy-test-key py -m pytest tests/ -q
ALPHA_VANTAGE_KEY=dummy-test-key py -m pytest tests/test_r24_live_safety.py -q
```
Yeni suite:
(a) peak $491 kayıtlı, equity $450'de restart -> floor $417.35 (0.85 × 491), $382.50 DEĞİL;
(b) equity $520'ye çıkınca günlük reset -> peak 520, floor 442; (c) bozuk peak kaydı -> taban düşmez;
(d) live + `LIVE_ENTRIES_ENABLED=true` + bear env yok -> `bear_etf` reddi `LIVE_BEAR_LOCK`,
    `stock_long` izinli; paper'da bear serbest;
(e) canlı entry sonrası stop doğrulanamadı -> `live_auto_lock.json` yazıldı, sonraki `stock_long`
    `LIVE_AUTO_LOCK`; restart sonrası kilit hâlâ geçerli; temizleme komutundan sonra izinli;
(f) `FAILED_NAKED` koruma sonucu da auto-lock yazar; paper'da yazmaz;
(g) fractional pozisyon günlük resetten sonra stop emri alıyor (mock broker).

---

### R22 , Dürüst giriş akışı: tıkalı hat asla "sağlıklı" değil

**Kapsam:**
- **Açık yaşam döngüsü sayaçları (sayaç cebiri YOK):** funnel'e
  - `eligible_buy`: BUY güveni o anki efektif eşiği geçtiği an (stock_bot.py BUY dalı), sembol listesiyle;
  - `max_margin`: günün en büyük `confidence - effective_buy_conf` değeri, AYNI karar anından, sembol ve
    iki değerle birlikte (günlük bağımsız max/threshold çiftleri YOK);
  - `executor_block_reasons`: `reached_executor` sonrası her `return False` için terminal sebep
    (EQUITY_FLOOR, MARKET_CLOSED, CASH_RESERVE, SIZER_ZERO, BRACKET_REJECT, ALREADY_FLAT,
    STOP_UNVERIFIED, ...);
  - `entries` yalnız doğrulanmış girişte (bugünkü `bought=True`) kalır.
  - Strateji ve BearBrain sayaçları AYRI (`kind`/provenance); sağlık `strategy` hattını bağımsız değerlendirir.
- **Sağlık şeması:** `core/health_status.py`'ye yeni `EntryFlow` enum'u (`AKIYOR`, `SESSIZ`, `FILTRELI`,
  `TIKALI`, `KILITLI`, `UNKNOWN`), dördüncü dataclass alanı, özet ve çıkış-kodu toplaması açıkça.
  Pencere: tarama yapılmış son K=3 işlem günü (`scanned == 0` atlanır).
  - `AKIYOR`: pencerede `entries > 0`.
  - `SESSIZ`: `eligible_buy == 0`; arıza değil; `max_margin` gösterilir.
  - `FILTRELI`: eligible var, belirleyici bloker TASARIM kapısı (EMA200, EARNINGS, VOLATILITY, RR_GATE,
    MTF, MARKET_CLOSED, SECTOR); çıkış 0, adıyla.
  - `TIKALI` (DEGRADED, çıkış 3): eligible var, `entries == 0`, belirleyici bloker DURUM kapısı
    (LOSS_STREAK_WARN/HALT, STOCK_FILTER, GUARD_ERROR, KILL_SWITCH, RISK_HALT, EQUITY_FLOOR,
    LIVE_AUTO_LOCK, STOP_UNVERIFIED, SIZER_ZERO, CASH_RESERVE).
  - Belirleyici bloker `LIVE_LOCK_R5` -> `KILITLI`. funnel yok/bozuk -> `UNKNOWN`.
  - Belirleyici bloker = eligible adayları en çok durduran terminal sebep. Özet `TIKALI` iken asla
    `SAGLIKLI` demez.
  - Eski funnel günleri (yeni alanlar yok) için eligible, mevcut `gate_block_reasons` ve
    `sector_block` üzerinden ALT SINIR olarak türetilir (kapılar yalnız eşik sonrası koşar;
    `wash_sale_block` ÖNCE koştuğu için dahil edilmez) ve raporda "türetilmiş" diye işaretlenir.
- **NO_TRADE alarmı:** eligible adayların belirleyici blokerini ve `max_margin`'ı yazar; tarama olmayan
  günde (hafta sonu/tatil) alarm atmaz.

**Done looks like:** Bugünkü canlı ve paper funnel'iyle `saglik.py` `entry_flow=TIKALI
(LOSS_STREAK_WARN)` der; aynı girdide eski kod `SAGLIKLI` diyordu. Alarm gerçek blokeri söyler.
Emir imkânsızken (`SIZER_ZERO`, `EQUITY_FLOOR`) `AKIYOR` denemez.

**PROOF:**
```
ALPHA_VANTAGE_KEY=dummy-test-key py -m pytest tests/ -q
ALPHA_VANTAGE_KEY=dummy-test-key py -m pytest tests/test_r22_entry_flow.py -q
```
Yeni suite (fixture: `tests/fixtures/canlandirma_2026_09_11/paper_funnel.json` ve `live_funnel.json`):
(a) gerçek paper fixture -> `TIKALI`, bloker `LOSS_STREAK_WARN`, çıkış 3, özet `SAGLIKLI` değil;
    ve eski `saglik.py` mantığı aynı girdide `SAGLIKLI` diyordu (regresyonun kanıtı);
(b) yalnız `conf_below_min` -> `SESSIZ`; (c) yalnız EMA200 -> `FILTRELI`; (d) yalnız LIVE_LOCK_R5 -> `KILITLI`;
(e) `reached_executor > 0` ama `executor_block_reasons={SIZER_ZERO}`, `entries == 0` -> `TIKALI`, `AKIYOR` DEĞİL;
(f) `entries > 0` -> `AKIYOR`; (g) bozuk funnel -> `UNKNOWN`;
(h) BearBrain girişi strateji hattını `AKIYOR` yapmıyor;
(i) hafta sonu/tatil günleri pencereden atlanır ve NO_TRADE alarmı üretmez;
(j) NO_TRADE mesajı fixture için `LOSS_STREAK_WARN` der, `conf_below_min` demez;
(k) `max_margin` aynı karar anından: rejim gün içinde değişince yanlış eşik-geçişi üretmiyor.

---

### R23 , Deploy ve kilit açılışı (OPERASYON: Claude yürütür, Codex DEĞİL)

R20, R21, R24, R22 incelenip ana dala alındıktan sonra, İhsan'ın son onayıyla.

**Adım 1 , kilit KAPALI deploy:**
1. Ana dalda tam kanıt (Claude). `git rev-parse HEAD` yeniden (eş zamanlı oturumlar).
2. Ön anlık görüntü (yerel dosya): canlı hesap equity/nakit/pozisyonlar/açık emirler, `bot_positions.json`,
   funnel, paper hesabı aynı şekilde.
3. `git push origin main`; Coolify env `LIVE_ENTRIES_ENABLED` **false kalır**; deploy tetiklenir.
4. Doğrulama: konteynerde çalışan commit SHA = push edilen SHA (konteyner SHA fallback'i); config'te
   `min_confidence_score=45`, bantlar live+paper-livecfg'de doğru; seri göçü logu (live "söndü 2 -> 0,
   2026-07-16", paper `paper_aggressive`'e taşındı, `paper_live_config` 0); `peak_equity` yazıldı ve
   floor = 0.85 × peak; `saglik.py` 4 boyutu raporluyor; ilk 15 dk'da GUARD_ERROR/traceback yok.
5. Canlı açık emir kontrolü: beklenmedik açık emir varsa açılıştan önce İhsan'a sorulur.

**Adım 2 , kilit AÇIK restart (ayrı operasyon):**
6. Coolify env `LIVE_ENTRIES_ENABLED=true` (`LIVE_BEAR_ENTRIES_ENABLED` YOK), yalnız restart.
7. Doğrulama: `saglik.py` live `entry_authorization` artık KILITLI değil; ters-ETF kilidi raporda görünür.

**Adım 3 , ilk canlı giriş izlemi (Claude elle):**
8. İlk girişte Alpaca'da: koruyucu stop emri var, boyut bant içinde ($100-300), provenance `strategy`,
   funnel `entries` arttı. Uygunsuzlukta anında geri alma.

**Geri alma:** `LIVE_ENTRIES_ENABLED=false` + restart (girişler durur, çıkış/koruma sürer). Kod: `1e731be`
yeniden deploy. Otomatik kilit (`live_auto_lock.json`) bottan bağımsız son emniyet.

---

## KAPSAM DIŞI (bu döngüde YOK)

- Koordinatör güven formülü, ×2.0 remap, ajan ağırlıkları, çoğunluk mantığı.
- 45'in veriyle kalibrasyonu; kilit kapısının yeni rolü (RF-ISSUES-5).
- Kill tasfiyesinin broker-flat doğrulaması ve `close_in_progress` yapışkan bayrağı (RF-ISSUES-5, YÜKSEK).
- Merkezi toplam pozisyon tavanı (canlıda bu döngüde yalnız stock_long açılıyor; RF-ISSUES-5).
- Stop doğrulanamayınca otomatik kapatma; oversize/provenance watchdog'u (RF-ISSUES-5).
- Test hermetikliği, short/opsiyon/parking davranışı, BearBrain canlı açılışı.

## RİSKLER

1. **Gerçek para, ölçülmemiş alfa:** kilit ölçüm kapısı geçmeden, sahip kararıyla ve tam bantlarla
   açılıyor. Pozisyon $100-300, en fazla 3 pozisyon, günlük -%5 kill, kalıcı %85 yüksek-su tabanı.
2. **Tavan mutlak değil:** gap, stop-limit dolmaması ve kill tasfiyesi hatası (ertelendi) kaybı tabanın
   ötesine taşıyabilir. "%15'te durur" yeni GİRİŞLER içindir; açık pozisyonun kaybı stopa bağlıdır.
3. **45 eşiği kanıtsız:** 45-59 bandı en küçük boyutu ($100) alır.
4. **Sönme koruyucuyu zayıflatır:** 2 zarardan sonra seçicilik yalnız 24 saat. HALT ve kill aynen.
5. **Epoch değişir:** eşik değişikliği profil hash'ini değiştirir.
6. **Beklenen hız düşük:** dürüst güven dağılımında haftada birkaç aday. "Canlandı" = hat akıyor.
