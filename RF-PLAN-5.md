# RF-PLAN-5.md , Canlandırma: karar hattındaki kalıcı kilitlenmeyi kaldır, İhsan'ın kararlarıyla canlıyı aç

> Tarih: 2026-09-11 (Los Angeles). Sürücü: Claude (Visionary) + Codex (Integrator).
> Baseline: `git HEAD = 1e731be` (worktree dalı `codex-canlandirma`).
> Baseline kanıtı (Claude koştu): `ALPHA_VANTAGE_KEY=dummy-test-key py -m pytest tests/ -q` -> **592 passed**.
> Not: anahtar env'i OLMADAN 2 test düşüyor (`test_r16_review_fixes2::test_prefetch_gun_boyunca...`,
> `test_r16_review_fixes3::test_basarisiz_sembol...`): Yahoo yedeğine düşüp gerçek ağa gidiyorlar.
> Ana ağaçta `.env` olduğu için geçiyordu. Üretim hatası değil, test hermetikliği (RF-ISSUES-5).
> Önceki döngüler: RF-PLAN.md .. RF-PLAN-4.md (R1..R19 tamamlandı).

## CORE FOCUS (tek cümle)

**Canlı botu yeniden işlem yapar hale getirmek: karar hattındaki kalıcı kilitlenmeleri kaldır,
İhsan'ın kararlarını (kilit açık, eşik 45, zarar serisi 24 saatte söner) güvenle uygula ve hiçbir
alet tıkalı bir hattı "sağlıklı" diye göstermesin.**

---

## TEŞHİS (2026-09-11, hepsi canlı konteynerlerde ölçüldü)

İki konteyner 7 gündür ayakta (`trading-live`, `trading-paper-livecfg`), her 30 sn karar üretiyor.
`tools/saglik.py`: live `KILITLI`, paper `SAGLIKLI`. İkisi de yeni strateji girişi yapmıyor.

### 1. ASIL ENGEL: zarar serisi kalıcı kilitlenmesi (kanıtlı)

- Canlı `state_live/bot_positions.json`: `"consecutive_losses": 2`. Kaynağı `trade_history.json`:
  NVDA -$0.31 (2026-07-15), RIVN -$2.26 (2026-07-16). O günden beri seri 2.
- `core/trade_gates.py:176-183`: seri >= `loss_streak_warn` (2) ise güven >= `loss_streak_elevated_conf`
  (70) şart. Temel eşik 50 fiilen 70 oluyor.
- Seri yalnız KÂRLI bir kapanışla sıfırlanıyor (`core/streak.py`). İşlem yok -> kâr yok -> sıfırlanma
  yok. **Kalıcı kilitlenme.**
- Asimetri (hata): 4 zararlık `LOSS_STREAK_HALT` 24 saatte sönüyor (`trade_gates.py:164-174`), 2
  zararlık WARN HİÇ sönmüyor. Ağır durum iyileşiyor, hafif durum kalıcı. Ayrıca HALT saati
  (`_loss_halt_until`) yalnız bellekte; restart her seferinde 24 saati baştan başlatıyor.
- Aynı kalıcılık sembol filtresinde de var: `coin_max_consecutive_losses` (3) olan sembol, yalnız
  O sembolde kâr ile açılıyor; sembol bloklu olduğu için asla açılmıyor.
- Canlı log kanıtı: `META KAYIP KORUYUCU: 2 ardisik zarar, guven 53.9% < 70%` (2026-09-09, 285 kez).
  Paper-livecfg'de birebir aynı satır (527 kez).
- Paper-livecfg'nin serisi 4 Eylül'de 0 idi; agresif dönemden DEVRALINAN AAPL (09-08) ve GOOGL (09-09)
  pozisyonlarının zararlı kapanışı onu 2'ye çıkardı. **Profil kirlenmesi.**
- Son 22 işlem gününde (08-11..09-11) güveni >= 50 olan 27 sembol-gün: ~13'ü KAYIP KORUYUCU, 7'si
  EMA200, 1'i EARNINGS ile düştü.

### 2. Güven nadiren 50'yi geçiyor (kanıtlı)

- Canlı koordinatör BUY güveni (4 Eylül deploy'undan beri): tepe 66 (META 09-09), çoğu 12-41.
- SentAgent BUY oranı 08-10..08-24'te %38.5 (tepe 100), 09-04 sonrası %11.5 (tepe 54). Sebep R12
  düzeltmesi ("kötü haber artık BUY itmiyor"). Ağustos'taki 74-85 güvenler kısmen o hatanın ürünü.
- v4.9 ×2.0 remap bu hatalı dağılımda kalibre edilmişti. Dürüst dağılımda 50 çok seçici.

### 3. R5 kilidi (kasıtlı) , sıradaki son engel

- `core/risk_guard.py:142-148`. Ama bu 8 günde HİÇBİR aday kilide ULAŞMADI (`reached_executor = 0`
  her gün). Kilit açılsaydı bile giriş olmazdı.

### 4. "Birkaç gün log izleyelim" neden boş döndü

- R18 gölge defteri yalnız kilit reddinde yazıyor (`risk_guard._golge_kaydet`). Kilide aday
  ulaşmadığı için `state_live/shadow_intents.jsonl` **hiç oluşmadı**. Paper'da da yok.
- Paper-livecfg 09-04'ten beri 0 yeni strateji girişi (funnel `entries`: 09-08..09-11 hep 0).
  Sağlık aracının "strateji=2" dolumu 09-03 (agresif dönem) girişleri.

### 5. Aletler yine yalan söylüyor

- `saglik.py` paper için `SAGLIKLI (uc boyut da temiz)` diyor; hat 5 işlem günüdür tıkalı.
- NO_TRADE alarmı "Baskın downstream bloker: conf_below_min (582)" diyor: TARAMA sayısına göre
  baskın aşamayı seçiyor. Eşiği geçen adayları öldüren gerçek bloker (LOSS_STREAK_WARN) görünmüyor.
  Ayrıca hafta sonu (09-06, 09-07) "veri_yok" alarmı atıyor ve "İşlemsiz iş günü" sayacı 22->21->20->3
  diye geriye sayıyor.

### 6. 45 eşiğinin gizli tuzağı (kanıtlı)

- `config.py:352-357` `live_conf_position_bands` en düşük bant `[50, 100]`.
  `core/position_sizer.py:96-104`: bandın altındaki güven "en düşük bandın altında" diye SIFIR boyut.
- Yani yalnız `min_confidence_score` 45 yapılırsa 45-49 adaylar bütün kapılardan geçer, executor'da
  sessizce ölür. Eşik değişikliği bant değişikliği OLMADAN işe yaramaz.

---

## İHSAN'IN KARARLARI (2026-09-11)

1. **Canlı kilit:** onarımla birlikte HEMEN açılır (Coolify env `LIVE_ENTRIES_ENABLED=true`).
   Risk sınırları: pozisyon $100-150 (güven bantları), günlük -%5 kill, equity tabanı %85 (~$414-418),
   en fazla 3 pozisyon. Tabana kadar en kötü durumda ~$75 kayıp, sonra yeni giriş durur.
2. **Zarar serisi:** son zarardan 24 saat sonra söner (4 zararlık HALT ile aynı süre). Seri her
   profil için ayrı tutulur; paper agresif botun zararlarını devralmaz.
3. **Güven eşiği:** 50 -> **45**.

---

## ROCK'LAR (bağımlılık sırasında)

### R20 , Zarar serisi: zamanla sönme, profil izolasyonu, sembol filtresi, eski durum göçü

**Kapsam:**
- Tek zaman kaynağı: kalıcı `last_loss_at` (UTC, tz-aware ISO). Global seri ve sembol başına ayrı.
  `bot_positions.json` içinde, mevcut `consecutive_losses` / `symbol_consecutive_losses` yanında.
- **Sönme kuralı (config'te açık, koda gömülü değil):**
  - `loss_streak_decay_hours: 24`: global seri, `now - last_loss_at >= 24h` ise 0'a iner. Bir kez log
    ("Zarar serisi söndü: 2 -> 0, son zarar ... önce") ve kalıcı yazılır.
  - WARN ve HALT AYNI saatten beslenir: HALT, `last_loss_at + loss_streak_halt_hours` dolana kadar.
    Bellek-içi `_loss_halt_until` kalkar; restart HALT'ı uzatamaz.
  - `symbol_loss_decay_hours: 168`: sembol serisi 7 gün sonra söner (STOCK_FILTER kalıcılığı biter).
- **Profil izolasyonu:**
  - Kalıcı `streak_profile` = `core.run_profile.aktif_profil()`. Yüklemede kayıtlı profil etkin
    profilden FARKLIYSA seriler 0 (log).
  - Her yeni pozisyon metadata'sına `entry_profile` yazılır (long executor, short executor).
  - Seri güncellemesi (`core/streak.update_loss_streak`, üç çağıran: `core/executor.py:804`,
    `core/short_executor.py:395`, `stock_bot.py:2047`) yalnız `entry_profile == aktif_profil()` olan
    pozisyonun PnL'ini sayar. `entry_profile` YOK (eski/devralınmış) pozisyon seriye dokunmaz, ne
    zararıyla ne kârıyla.
- **Eski durum göçü (timestamp yok):**
  - Kalıcı trade history'de (`trade_history.json`) en son ZARARLI kapanış varsa `last_loss_at` = o an
    (naive zaman damgası UTC kabul edilir). Canlıda bu 2026-07-16 -> ilk yüklemede söner.
  - Yoksa ya da okunamıyorsa saat YÜKLEME ANINDA başlar (en fazla 24 saat daha seçici, sonra söner).
  - Hangi yolun kullanıldığı loglanır.
- **Bozuk / gelecek tarihli damga:** gelecek tarih `now`'a kırpılır (WARN log), bozuk damga "eski
  durum" gibi işlenir. Hiçbir girdi seriyi KALICI yapamaz.
- **Görünürlük:** heartbeat satırı `Zarar serisi: 2 (söner: <UTC>)`.
- `tools/parity_harness.py:344-347` sahte bot'u yeni alanları taşır; parity çıktısı değişmemeli.

**Done looks like:** Temmuz'daki canlı durum (seri 2, trade history 07-16) canlı profilde yüklenince
seri söner ve 53.9 güvenli META BUY kayıp koruyucusundan geçer. Hiçbir girdi kombinasyonu seriyi 24
saatten (sembol için 168 saatten) uzun kalıcı yapamaz. Paper, agresif pozisyonların kapanışından
etkilenmez.

**PROOF:**
```
ALPHA_VANTAGE_KEY=dummy-test-key py -m pytest tests/ -q
ALPHA_VANTAGE_KEY=dummy-test-key py -m pytest tests/test_r20_streak_decay.py -q
```
Yeni suite:
(a) seri 2, son zarar 25 saat önce -> WARN yok, 47 güven geçer, seri 0 ve kalıcı;
(b) seri 2, son zarar 23 saat önce -> WARN var: 60 bloklu, 70 geçer;
(c) seri 4, 23 saat -> HALT; 25 saat -> serbest ve seri 0; **10. saatte restart HALT'ı uzatmıyor**;
(d) sembol serisi 3, son zarar 6 gün önce -> STOCK_FILTER; 8 gün -> geçer;
(e) eski durum + trade history'de 07-16 zararı -> ilk yüklemede sönmüş; eski durum + history yok ->
    saat yükleme anında başlar, 24 saat sonra söner;
(f) kayıtlı `streak_profile` farklı -> seriler 0;
(g) `entry_profile` farklı ya da YOK olan pozisyonun zararı VE kârı seriyi değiştirmez; kendi
    profilindeki zarar +1 ve `last_loss_at` yazar;
(h) **KİLİTLENME REGRESYONU:** gerçek canlı `bot_positions.json` + `trade_history.json` şekliyle
    fixture, canlı config ile `trade_gates.check_all_gates` üzerinden META 53.9 BUY'ı geçirir
    (loss-streak kapısında bloklanmaz);
(i) gelecek tarihli damga -> en fazla 24 saat sonra söner; bozuk damga çökertmez;
(j) naive ve tz-aware damga karşılaştırması hata vermez;
(k) `tools/parity_harness.py` çıktısı baseline ile aynı.

---

### R21 , Eşik 45 ve bant: kararı gerçekten işler hale getir

**Kapsam:**
- `STOCK_CONFIG["min_confidence_score"]`: 50 -> 45 (İhsan kararı, tarih ve gerekçe yorumda).
- `live_conf_position_bands`: `[[45, 100], [60, 150], [70, 200], [80, 300]]`. 45-59 -> $100.
- Canlı yoldaki her `min_confidence_score` tüketicisi ve her sabit "50" güven literali taranır
  (executor, sizer, signal_queue, stock_bot, trade_gates, backtest, parity_harness, olcum_raporu).
  Canlı eşik varsayımı olan yer ya config'e bağlanır ya da gerekçesiyle bırakılır; rapor edilir.
- BEAR rejimde `bear_buy_conf_increase` (+10) aynen: efektif 55.
- `paper_live_config` profili aynı değeri alır; `PAPER_AGGRESSIVE_CONFIG` (30) DEĞİŞMEZ.
- `tests/test_r19_live_config_paper.py::test_f_canli_esikler_degismedi` bilinçli güncellenir:
  45'i ve kilidin KOD varsayılanının `False` kaldığını doğrular (kilit yalnız env ile açılır).
- Profil hash'i değişir -> yeni `epoch_id`. Beklenen; ölçüm katmanı eski epoch'la karıştırmaz.

**Done looks like:** 47 güvenli, bütün kapılardan geçen bir canlı BUY adayı executor'da $100 emir
planına dönüşür, "en düşük bandın altında" reddine düşmez.

**PROOF:**
```
ALPHA_VANTAGE_KEY=dummy-test-key py -m pytest tests/ -q
ALPHA_VANTAGE_KEY=dummy-test-key py -m pytest tests/test_r21_threshold45.py -q
```
Yeni suite:
(a) canlı config ile güven 47 -> `PositionSizer` $100 döner (equity $491, nakit yeterli);
(b) 44.9 -> funnel `conf_below_min_buy`, executor çağrılmaz;
(c) 60 -> $150, 70 -> $200, 80 -> $300 (üst bantlar değişmedi);
(d) BEAR rejim: 50 bloklu (efektif 55), 55 geçer;
(e) `paper_live_config` = 45; `paper_aggressive` = 30;
(f) literal taraması: canlı yolda başka gizli 50 eşiği yok (test ya da rapor).

---

### R22 , Dürüst giriş akışı: tıkalı hat asla "sağlıklı" değil

**Kapsam:**
- **Veri zaten var:** kapılar yalnız güven eşiğini geçmiş adaylarda koşuyor (`stock_bot.py:1060-1116`),
  yani `sector_block + gate_block + wash_sale_block + queued_pullback + queue_dup + reached_executor`
  = eşiği geçen aday sayısı ve `gate_block_reasons` onların belirleyici blokeri. Mevcut funnel
  günleri geriye dönük okunabilir.
- Funnel'e ET-gün başına yeni alanlar: `max_buy_conf` (günün en yüksek koordinatör BUY güveni) ve o
  anki `effective_buy_conf`. Eski günlerde yoksa "bilinmiyor" denir, uydurulmaz.
- `tools/saglik.py` DÖRDÜNCÜ boyut `entry_flow` (giriş akışı). Pencere: tarama yapılmış son K=3 işlem
  günü (`scanned == 0` günler atlanır).
  - `AKIYOR`: pencerede `reached_executor > 0` ya da `entries > 0`.
  - `SESSIZ`: pencerede hiçbir aday eşiği geçmedi. Arıza değil; `max_buy_conf` / eşik gösterilir.
  - `FILTRELI`: eşiği geçen aday var, belirleyici bloker TASARIM kapısı (EMA200, EARNINGS, VOLATILITY,
    RR_GATE, MTF, MARKET_CLOSED, sektör). Arıza değil, adıyla gösterilir.
  - `TIKALI` (DEGRADED, çıkış 3): eşiği geçen aday var, `reached_executor == 0`, belirleyici bloker
    DURUM kapısı (LOSS_STREAK_WARN, LOSS_STREAK_HALT, STOCK_FILTER, GUARD_ERROR, KILL_SWITCH,
    RISK_HALT).
  - Belirleyici bloker `LIVE_LOCK_R5` ise `KILITLI` (entry_authorization ile tutarlı).
  - funnel yok/bozuk -> `UNKNOWN`. Özet `TIKALI` iken asla `SAGLIKLI` demez.
- NO_TRADE alarmı: eşiği geçen adayların belirleyici blokerini ve `max_buy_conf`/eşiği yazar (tarama
  sayısına göre baskın aşama DEĞİL). Tarama olmayan günde (hafta sonu/tatil) alarm ATILMAZ.
  "İşlemsiz iş günü" sayacı ardışık işlemsiz işlem günlerinde azalmaz; yalnız bir girişle sıfırlanır.

**Done looks like:** Bugünkü canlı ve paper funnel'i ile `saglik.py` `entry_flow=TIKALI
(LOSS_STREAK_WARN)` der; aynı girdide eski kod `SAGLIKLI` diyordu. Alarm gerçek blokeri söyler.

**PROOF:**
```
ALPHA_VANTAGE_KEY=dummy-test-key py -m pytest tests/ -q
ALPHA_VANTAGE_KEY=dummy-test-key py -m pytest tests/test_r22_entry_flow.py -q
```
Yeni suite (fixture: canlı paper funnel'inin 09-03..09-11 günleri, `tests/fixtures/` altına):
(a) gerçek fixture -> `TIKALI`, bloker `LOSS_STREAK_WARN`, çıkış kodu 3; özet `SAGLIKLI` değil;
(b) yalnız `conf_below_min` -> `SESSIZ`, çıkış 0, `max_buy_conf` varsa gösterilir;
(c) yalnız EMA200 -> `FILTRELI`, çıkış 0; (d) yalnız LIVE_LOCK_R5 -> `KILITLI`;
(e) `entries > 0` -> `AKIYOR`; (f) bozuk funnel -> `UNKNOWN`;
(g) hafta sonu/tatil günleri pencereden atlanır ve NO_TRADE alarmı üretmez;
(h) NO_TRADE mesajı fixture için `LOSS_STREAK_WARN` der, `conf_below_min` demez;
(i) işlemsiz sayaç ardışık işlem günlerinde monoton artar.

---

### R23 , Deploy ve kilit açılışı (OPERASYON: Claude yürütür, Codex DEĞİL)

Kod rock'u değil; R20-R22 incelenip ana dala alındıktan sonra İhsan'ın kararıyla koşulur.

1. Ana dalda tam kanıt (Claude koşar). `git rev-parse HEAD` yeniden doğrulanır (eş zamanlı oturumlar).
2. **Ön anlık görüntü** (yerel dosya): canlı hesap equity/nakit/pozisyonlar/açık emirler,
   `bot_positions.json` seri durumu, funnel. Geri dönüş referansı.
3. `git push origin main`.
4. Coolify API: uygulama env'ine `LIVE_ENTRIES_ENABLED=true`, sonra deploy tetiklenir
   (`/api/v1/deploy?uuid=dlyojlxudkezk2bze3f3ypp2`). Canlıda açık pozisyon YOK, restart güvenli.
5. **Deploy sonrası doğrulama (ilk 10 dk):**
   - `docker exec <live> python tools/saglik.py`: `entry_authorization` artık KILITLI değil,
     runtime/decision_pipeline SAGLIKLI, `entry_flow` raporlanıyor.
   - Log: seri göçü satırı ("söndü 2 -> 0, son zarar 2026-07-16"), heartbeat `Zarar serisi: 0`.
   - Paper: seri 0, profil `paper_live_config`.
   - İlk 10 dakikada `GUARD_ERROR` / traceback yok.
6. **İlk canlı giriş izlemi:** ilk girişte Alpaca'da koruyucu stop emrinin varlığı, boyutun
   $100-150 olduğu ve funnel `entries` sayacı doğrulanır.
7. **Geri alma:** tek adım `LIVE_ENTRIES_ENABLED=false` + restart (girişler durur, çıkış/koruma
   sürer). Kod geri alma: `1e731be` yeniden deploy.

---

## KAPSAM DIŞI (bu döngüde YOK)

- Koordinatör güven formülü, ×2.0 remap, ajan ağırlıkları, çoğunluk mantığı.
- 45'in veriyle kalibrasyonu (sonuç verisi birikince; RF-ISSUES-5).
- Kilit kapısı aracı (KILIT-KAPISI-ARACI), gölge sonuç etiketleme: kilit sahip kararıyla açıldığı
  için kapının rolü değişiyor (açan değil, izleyen); RF-ISSUES-5'te yeniden tanımlanacak.
- Paper MSFT `close_in_progress` yapışkan bayrağı ve KORUMA alarm tekrarı (RF-ISSUES-5).
- Test hermetikliği (AV anahtarı olmadan gerçek ağa giden iki test) (RF-ISSUES-5).
- Short, opsiyon, BearBrain, index parking davranışı.

## RİSKLER

1. **Gerçek para:** kilit ölçüm kapısı geçmeden, sahip kararıyla açılıyor. Strateji alfası
   ölçülmedi. Sınırlar: $100-150 pozisyon, -%5 günlük kill, %85 taban, 3 pozisyon.
2. **45 eşiği kanıtsız:** 45-49 bandının kalitesi ölçülmedi; bu band en küçük boyutu ($100) alır.
3. **Sönme koruyucuyu zayıflatır:** 2 zarardan sonra seçicilik yalnız 24 saat. İhsan'ın kararı;
   HALT (4 zarar) ve günlük kill aynen duruyor.
4. **Epoch değişir:** eşik değişikliği profil hash'ini değiştirir; eski epoch verisi ayrı kalır.
5. **Beklenen hız düşük:** onarımdan sonra bile dürüst güven dağılımında haftada birkaç aday.
   "Canlandı" = hat akıyor, "çok işlem" değil.
