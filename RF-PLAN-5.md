# RF-PLAN-5.md , Canlandırma: karar hattındaki kalıcı kilitlenmeyi kaldır, İhsan'ın kararlarıyla canlıyı aç

> Tarih: 2026-09-11 (Los Angeles). Sürücü: Claude (Visionary) + Codex (Integrator).
> **Revizyon 3** , Codex Round 1-2 bulgularından sonra (kayıt: RF-SAME-PAGE-LOG-5.md).
> KOD baseline'ı: `1e731be`. Plan commit'leri: ilk `39239f9`, Revizyon 2 `d0ae983`, Revizyon 3 bu
> dosyayı taşıyan commit (worktree dalı `codex-canlandirma`).
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
- **Nakit gerçeği (Claude öz-inceleme):** executor boyutu `cash - cash_reserve_pct × equity` ile kırpıyor
  (`core/executor.py:299-301, 338`). Canlıda nakit $145.82, equity $491.65, gerisi SPY parkında
  (`index_parking_reserve_pct` 0.30). İlk giriş en fazla ~$96.65; ikinci giriş ancak ertesi günkü park
  dengelemesiyle. Tam bantlar ($150-300) park equity'nin çoğunu tutarken pratikte oluşamaz. Bu bir
  hata değil (park İhsan'ın 2026-07-05 kararı), beklentidir; bu döngüde değişmez.

### 7. Canlı emniyet açıkları (Codex Round 1, Claude doğruladı)

- **Equity floor kayıyor:** `stock_bot.py:230` her açılışta `equity * 0.85`. Her restart/deploy tabanı
  mevcut equity'ye göre yeniden kurar; kalıcı %15 kayıp tavanı DEĞİL.
- **R5 global:** `core/risk_guard.py:120-148` stock_long ile birlikte `bear_etf` (canlıda
  `BEAR_BRAIN_CONFIG.allow_live=True`, SH $100 / SQQQ 3x $150) yolunu da açar.
- **Stop doğrulanamazsa** pozisyon açık kalır, yalnız alarm (`core/executor.py:516-529`); koruma
  döngüsü sonraki turda yeniden dener, kapatmaz.
- **Kill tasfiyesi** broker kabulünü dolum sayıp yerel durumu siliyor (`stock_bot.py:2436-2447`);
  `cancel_orders=True` stopları da iptal ettiği için dolmayan kapanış stopsuz, botun bilmediği bir
  pozisyon bırakabilir. İhsan kararıyla BU DÖNGÜDE düzeltiliyor (R24).
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
5. **Kill tasfiyesi riski kabul EDİLMEDİ:** bu döngüde düzeltilir (R24).

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

### R24 , Canlı emniyet: kalıcı taban, BearBrain kilidi, kendi kendini kilitleme, dürüst kill tasfiyesi

**Kapsam:**
- **Kalıcı günlük yüksek-su tabanı:** `equity_floor = peak_equity * equity_floor_pct`. `peak_equity`
  kalıcı (state dizininde, atomik yazım), açılışta ve günlük resette `max(kayıtlı, mevcut equity)`.
  Gün içi zirve KASITLI olarak sayılmaz ("günlük yüksek-su"). Restart tabanı ASLA aşağı çekemez.
  Kayıt yoksa ilk değer mevcut equity (log). **Bozuk/okunamayan kayıt:** canlıda otomatik kilit
  (aşağıda) + kritik alarm, taban yeniden kurulmaz; paper'da mevcut equity ile yeniden kurulur + WARN.
  Taban altına düşülünce yeni girişler KALICI durur (tasarım); `saglik.py` bunu `TIKALI (EQUITY_FLOOR)`
  gösterir. Yeniden çapalama SAHİP işlemidir: kayıt dosyasını silmek (yok = mevcut equity, loglanır).
- **BearBrain canlı kilidi:** canlıda `bear_etf` HEM `LIVE_ENTRIES_ENABLED` HEM
  `LIVE_BEAR_ENTRIES_ENABLED` (varsayılan false) ister. Red sebepleri `LIVE_LOCK_R5` / `LIVE_BEAR_LOCK`.
  Paper etkilenmez.
- **Kendi kendini kilitleme (fail-closed):** canlıda şu durumlarda bot kalıcı otomatik kilit yazar
  (`state_live/live_auto_lock.json`: sebep, sembol, zaman):
  (1) giriş dolup kapsayan stop doğrulanamadı (`core/executor.py:516-529`);
  (2) koruma uzlaştırması `ProtectionSummary.failed > 0` (`FAILED_NAKED` VEYA `ELECTED_UNFILLED`,
      `core/protection.py:75-89`);
  (3) canlıda peak kaydı bozuk/okunamıyor;
  (4) kill tasfiyesi süre aşımında broker'ı flat göstermedi (aşağıda).
  Kilit varken `can_open_new_risk` canlı `stock_long`/`bear_etf`'i `LIVE_AUTO_LOCK` ile reddeder;
  çıkış/koruma sürer. **Yazım başarısızsa** bellek-içi risk halt kurulur, kritik alarm üretilir, yeni
  giriş yine reddedilir (dosya yazılamadı diye kilit kaybolmaz).
- **Temizleme komutu** (`tools/` altında): her açık canlı pozisyon için broker'dan koruma doğrulaması
  koşar; herhangi biri flat değil VE tam stop kapsamı doğrulanmamışsa kilidi KALDIRMAZ. `--zorla
  "<sebep>"` sahip geçersiz kılması sebebiyle loglanır.
- **`saglik.py`** otomatik kilidi DOĞRUDAN okur: kilit varsa `entry_authorization` = `KILITLI
  (OTOMATIK: <sebep>)` ve DEGRADED (çıkış 3), funnel'de henüz red görülmemiş olsa bile.
- **Dürüst kill tasfiyesi (İhsan kararı):** `_emergency_close_all` (`stock_bot.py:2436-2447`) artık
  `close_all_positions` kabulünde yerel durumu SİLMEZ. Her açık pozisyon `kill_close_pending` (zaman
  damgalı) işaretlenip kalıcı yazılır. Pozisyon yalnız broker o sembolde flat gösterdiğinde, mevcut
  dış-kapanış uzlaştırma yolundan (PnL, seri, ledger, wash-sale kayıtlarıyla) düşer. Süre aşımında
  (config, varsayılan 10 dk) hâlâ açık sembol: kritik alarm + sınırlı sayıda yeniden kapatma denemesi
  + otomatik kilit. İşaret broker flat olunca TEMİZLENİR; `close_in_progress` yapışkanlığını
  (RF-ISSUES-5) tekrarlamaz. Restart sonrası `kill_close_pending` korunur ve uzlaştırma sürer.
- **DAY stop yenilemesi:** fractional pozisyonun DAY stopu günlük reset ve açılış
  `ensure_protective_stops` çağrılarıyla yeniden yerleşir. 16:00 ET ile bir sonraki yerleşim arası
  stopsuz pencere KABUL EDİLMİŞ kalan risktir (RİSKLER 2).

**Done looks like:** Restart tabanı düşüremiyor; tek başına `LIVE_ENTRIES_ENABLED=true` ters-ETF
açmıyor; stopu doğrulanamayan ya da koruması başarısız canlı durumda bot yeni giriş yapmıyor ve bunu
kalıcı hatırlıyor; kill tasfiyesi broker flat olmadan hiçbir pozisyonun kaydını silmiyor.

**PROOF:**
```
ALPHA_VANTAGE_KEY=dummy-test-key py -m pytest tests/ -q
ALPHA_VANTAGE_KEY=dummy-test-key py -m pytest tests/test_r24_live_safety.py -q
```
Yeni suite:
(a) peak $491 kayıtlı, equity $450'de YENİ SÜREÇ (yeni bot nesnesi) -> floor $417.35, $382.50 DEĞİL;
(b) equity $520'de günlük reset -> peak 520, floor 442; gün içi 530 peak'i değiştirmez;
(c) canlıda bozuk peak dosyasıyla YENİ SÜREÇ -> auto-lock + alarm, taban düşmedi; paper'da yeniden kurulum;
(d) **dört kombinasyon** (`LIVE_ENTRIES_ENABLED` × `LIVE_BEAR_ENTRIES_ENABLED`): bear yalnız ikisi
    açıkken izinli; `stock_long` yalnız ilkine bağlı; paper'da bear serbest;
(e) canlı entry sonrası stop doğrulanamadı -> auto-lock; sonraki `stock_long` `LIVE_AUTO_LOCK`;
    YENİ SÜREÇTE kilit hâlâ geçerli;
(f) `FAILED_NAKED` VE `ELECTED_UNFILLED` ayrı ayrı auto-lock yazar; paper'da yazmaz;
(g) auto-lock dosya yazımı hata fırlatınca bellek-içi halt + alarm, giriş reddedilir;
(h) temizleme komutu: çıplak pozisyon varken reddeder; tam kapsamda ya da flat'te temizler; `--zorla`
    sebebi loglar;
(i) `saglik.py` auto-lock dosyası varken, funnel temizken bile `KILITLI (OTOMATIK)` ve çıkış 3;
(j) kill: `close_all_positions` kabul edildi ama broker pozisyonu hâlâ gösteriyor -> yerel kayıt
    DURUYOR, `kill_close_pending` kalıcı; broker flat olunca uzlaştırma yolu çıkışı PnL/seriyle kaydedip
    kaydı düşürüyor; süre aşımında alarm + yeniden deneme + auto-lock; YENİ SÜREÇTE işaret korunuyor;
    kısmi başarı (2 sembolden 1'i flat) yalnız flat olanı düşürüyor;
(k) fractional pozisyon: reset anında stop yerleşimi reddedildi -> açılış çağrısı yerleştirip
    doğruluyor (mock broker).

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
  Pencere: tarama yapılmış son K=3 işlem günü (`scanned == 0` atlanır). **Her gün AYRI sınıflanır;
  durum, eligible adayı olan EN YENİ güne göre belirlenir.** Eski bir günün girişi daha yeni bir günün
  durum blokerini maskeleyemez.
  - **Terminal-sonuç invaryantı** (strateji hattı, ET-gün başına): `eligible_buy` = eşik sonrası
    sektör bloğu + kapı/guard redleri + kuyruk sonuçları (`queued_pullback`, `queue_dup`) +
    `executor_block_reasons` toplamı + `entries`. Eşleşmezse o gün `UNKNOWN` (açıklanamayan fark
    gösterilir); asla yeşil sayılmaz. Sınıflandırılmamış terminal sebep (ör. yeni bir `return False`)
    `UNCLASSIFIED` olarak sayılır ve DURUM blokeri gibi davranır.
  - `AKIYOR`: en yeni eligible günde `entries > 0`.
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
(a2) gerçek LIVE fixture için bağımsız aynı test (eski-gün türetmesi dahil);
(a3) 1. gün `entries > 0`, 3. gün eligible var ama LOSS_STREAK_WARN -> `TIKALI` (eski giriş maskelemiyor);
(a4) invaryant: eligible 5, terminal toplamı 4 -> o gün `UNKNOWN`, fark 1 gösterilir;
     sınıflandırılmamış sebep `UNCLASSIFIED` -> `TIKALI`;
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
4. **Dağıtılan kod parmak izi:** R20-R24'te değişen her dosyanın sha256'sı iki konteynerde de ana
   daldaki dosyayla birebir (`SOURCE_COMMIT` varsa ek bilgi olarak gösterilir; Dockerfile commit
   gömmediği için tek başına kanıt sayılmaz).
5. Doğrulama (konteyner içinden): `min_confidence_score=45`; bantlar live VE paper-livecfg'de
   `[[45,100],[60,150],[70,200],[80,300]]`; seri göçü logu (live "söndü 2 -> 0, 2026-07-16"; paper
   `paper_aggressive`'e taşındı, `paper_live_config` 0); `peak_equity` yazıldı, floor = 0.85 × peak;
   `saglik.py` 4 boyut; ilk 15 dk GUARD_ERROR/traceback yok.
6. **Açılış kapısı (zorunlu, konteyner içinden):** canlıda `BOT_MODE=long_only`,
   `OPTIONS_CONFIG.options_enabled=False`, `LIVE_BEAR_ENTRIES_ENABLED` yok/false, auto-lock dosyası yok.
   Biri tutmazsa kilit AÇILMAZ (merkezi pozisyon tavanının ertelenmesi bu üç koşula dayanıyor).
7. Canlı açık emir kontrolü: beklenmedik açık emir varsa açılıştan önce İhsan'a sorulur.

**Adım 2 , kilit AÇIK yeniden oluşturma (ayrı operasyon):**
8. Coolify env `LIVE_ENTRIES_ENABLED=true` (`LIVE_BEAR_ENTRIES_ENABLED` YOK). `docker restart` eski
   env'le devam edebileceği için **Coolify redeploy (konteyner yeniden oluşturma, aynı commit)**.
9. Yeni konteyner içinde: env değeri `true`, adım 4 parmak izi yeniden birebir, adım 6 kapısı yeniden
   geçer; `saglik.py` live `entry_authorization` artık KILITLI değil, ters-ETF kilidi görünür.

**Adım 3 , ilk canlı giriş izlemi (Claude elle):**
10. Boyut: sizer'ın log gerekçesinden hesaplanan KESİN beklenen notional (bant × sektör katsayısı,
    `min(equity × 0.62, max_position_usd)` ve nakit tavanı) ile dolum notional'ı karşılaştırılır; aralık
    kontrolü DEĞİL.
11. Stop: broker'dan yeniden okunur: durum aktif, yön SELL, miktar pozisyonun tamamı, stop fiyatı planla
    aynı, TIF (fractional DAY / tam pay GTC). Provenance `strategy`, funnel `entries` arttı.
12. Uygunsuzlukta anında geri alma.

**Geri alma:** `LIVE_ENTRIES_ENABLED=false` + restart (girişler durur, çıkış/koruma sürer). Kod: `1e731be`
yeniden deploy. Otomatik kilit (`live_auto_lock.json`) bottan bağımsız son emniyet.

---

## KAPSAM DIŞI (bu döngüde YOK)

- Koordinatör güven formülü, ×2.0 remap, ajan ağırlıkları, çoğunluk mantığı.
- 45'in veriyle kalibrasyonu; kilit kapısının yeni rolü (RF-ISSUES-5).
- Paper MSFT `close_in_progress` yapışkan bayrağı (RF-ISSUES-5). R24'ün `kill_close_pending` işareti
  bu bayrağı YENİDEN KULLANMAZ.
- Merkezi toplam pozisyon tavanı (canlıda bu döngüde yalnız stock_long açılıyor; R23 adım 6 bu koşulu
  zorunlu kapı yapıyor; RF-ISSUES-5).
- Stop doğrulanamayınca otomatik kapatma; oversize/provenance watchdog'u (RF-ISSUES-5).
- Test hermetikliği, short/opsiyon/parking davranışı, BearBrain canlı açılışı.

## RİSKLER

1. **Gerçek para, ölçülmemiş alfa:** kilit ölçüm kapısı geçmeden, sahip kararıyla ve tam bantlarla
   açılıyor. Pozisyon $100-300, en fazla 3 pozisyon, günlük -%5 kill, kalıcı %85 yüksek-su tabanı.
2. **Tavan mutlak değil (KABUL EDİLMİŞ kalan risk):** açılış gap'i, stop-limit dolmaması ve fractional
   DAY stopun 16:00 ET ile bir sonraki yerleşim arasındaki stopsuz penceresi kaybı tabanın ötesine
   taşıyabilir. "%15'te durur" yeni GİRİŞLER içindir; açık pozisyonun kaybı stopa bağlıdır.
3. **45 eşiği kanıtsız:** 45-59 bandı en küçük boyutu ($100) alır.
4. **Sönme koruyucuyu zayıflatır:** 2 zarardan sonra seçicilik yalnız 24 saat. HALT ve kill aynen.
5. **Epoch değişir:** eşik değişikliği profil hash'ini değiştirir.
6. **Beklenen hız düşük:** dürüst güven dağılımında haftada birkaç aday; SPY parkı yüzünden canlıda
   pratikte günde en fazla ~1 giriş, ~$96. "Canlandı" = hat akıyor, "çok işlem" değil.
7. **Zamanlama:** Codex kotası 2026-09-11 10:15 PDT'de doldu (yenilenme 14:35 PDT). Round 3 ve inşa
   ondan sonra; piyasa cuma 13:00 PDT kapandığı için canlı açılış gerçekçi olarak 2026-09-14 Pazartesi.
