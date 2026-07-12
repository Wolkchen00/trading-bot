# Trading Bot — Geliştirme Planı (v4.6 sonrası)
_Hazırlanma: 2026-07-02 · Güncelleme: 2026-07-12 v4.12 paper agresif+ (stres gözlemi; LIVE kilitleri aynen)_

## Mevcut durum (özet)
- **LIVE** ($487 gerçek): **v4.8 CANLI** — `GÜVENE GÖRE $100-300` (yön-farkında yeni güven ölçeği: 50-59→$100, 60-69→$150, 70-79→$200, 80+→$300), kill %5, floor $414.
- **PAPER** ($64k sanal): **v4.12 AGRESİF+** — bant-boyutlandırma $2.5k-9k, 10 pozisyon,
  MTF kapalı/VOL %8, kill -%8/floor %75; amaç agresif rejimin stres gözlemi +
  hızlı öğrenme verisi. Index parking açık; lokal kopya durdurulmuş (STOP_BOT).
- **Repo:** `Wolkchen00/trading-bot` (public; eski Wolkchen0 push-ölü, o da public'ti).
- **Deploy akışı:** push SONRASI deploy OTOMATİK DEĞİL — API ile tetiklenir:
  `TOKEN=$(ssh root@91.99.9.121 cat /root/.coolify_claude_token)` →
  `curl -H "Authorization: Bearer $TOKEN" "http://91.99.9.121:8000/api/v1/deploy?uuid=dlyojlxudkezk2bze3f3ypp2"`
  (Claude'un SSH erişimi var: `~/.ssh/coolify_vps2` anahtarı, root@91.99.9.121)

## FAZ 0 — Coolify bağlantısı ✅ TAMAMLANDI (2026-07-02)
- Coolify app `trading-bot` kaynağı `Wolkchen00/trading-bot`'a çevrildi (API ile).
- Coolify REST API açıldı (`is_api_enabled`), `claude` API token'ı üretildi
  (`/root/.coolify_claude_token` — VPS'te, panel > Keys & Tokens'ta da görünür).
- 3 deploy yapıldı: v4.6 → kill auto-reset → opsiyon mükerrer-emir fix.
- 2 aylık paper agent verisi (145KB agent_performance.json) redeploy öncesi kurtarıldı
  → lokal `state_paper/` (meta_labeler girdisi).
- 27 öksüz AMD PUT emri iptal edildi (mükerrer-emir bug'ı; kod düzeltildi).
- Bildirimler: **Alpaca e-posta bildirimleri** (İhsan hallettti). Telegram RAFTA —
  boş env kayıtları panelden silindi; istenirse TELEGRAM_BOT_TOKEN/CHAT_ID ile açılır.

## v4.7.1 — Kritik güvenlik yaması (2026-07-04, genel bakım denetimi)
- **Canlı arıza giderildi:** eski metadata'daki `stop_loss_pct: null` pozisyon
  yönetimini 2 Tem'den beri her döngüde çökertiyordu (AMZN+META emirsiz/korumasız
  kalmıştı). None-güvenli okuma + `ensure_protective_stops` (startup/günlük/açılış)
  + tam-pay bracket'ler GTC. Deploy sonrası stoplar Alpaca'da doğrulandı.
- **Sessiz bozukluklar:** `get_stock_snapshot(str)` çağrısı geçersizdi → gap
  koruması hiç çalışmamış (düzeltildi); bracket TP/SL sunucu dolumlarının
  muhasebesi atlanıyordu → `_reconcile_external_exit` (P&L/seri/wash-sale/PDT);
  short cover P&L her zaman 0 kaydediliyordu (düzeltildi, COVER Kelly'ye girdi).
- **Yanlış yönetim:** SPY parking sleeve + opsiyon/kripto pozisyonları stok
  yöneticisinden dışlandı (paper'da $45k SPY -%5'te topluca satılacaktı);
  trade_gates'in "teknik sinyal BUY değilse tüm kapıları atla" bypass'ı kaldırıldı.
- **Davranış:** jenerik "BREAKING:" başlıkları CRITICAL üretip alımları
  kilitleyebiliyordu (somut olay listesine indirildi, latch her taramada sıfırlanır);
  AV/Marketaux sleep 15s/10s→2s (döngü stop takibini bloke ediyordu).
- Bakım: PAT gömülü `wolkchen0-old` remote silindi; testler 74/74.
- ~~⚠️ Bilinen açık konular~~ → **v4.8'de kapatıldı (aşağıya bak).**

## v4.8 — "Açık kapama" paketi (2026-07-05, İhsan onayı: "açıkları kapat")
v4.7.1'de raporlanan açık konuların TAMAMI kapatıldı; hedef: daha yüksek kazanç
için sistematik engellerin kaldırılması. Testler 82/84 (2 bilinen lokal uyarı).

1. **R:R gate + DİNAMİK TP** — Eski: TP sabit %8 + SL taban %4 → oran yapısal
   maks 2.0 → gate fiilen "ATR ≤ %2.22" filtresiydi (NVDA/TSLA/COIN tipi adaylar
   kategorik red); paper'da (TP6/SL5, maks 1.2) HER alım bloktu → hisse işlemleri
   aylardır ölüydü. Yeni: `plan_exit_pcts()` tek doğruluk kaynağı — TP =
   clamp(SL×min_rr, taban, tavan %12); pozisyon-başına `take_profit_pct` saklanır
   (manager + backtest + bracket aynı planı kullanır). Backtest A/B: mevcut
   kazanan işlemler birebir aynı kaldı (regresyon yok).
2. **Yön-farkında koordinatör güveni** — Eski formül tüm ajan güvenlerini yön
   bağımsız topluyordu: 2 BUY + 2 SELL çelişkisi bile 77 "güven" alıp $150 bant
   açabiliyordu. Yeni: güven = |ağırlıklı yön skoru| (karşı oy düşürür, HOLD
   katkısız). Monte Carlo: temiz mutabakatta yeni ≈ eski×0.83, çelişkide 10-30.
   Eşikler yeniden haritalandı: live min_conf 60→**50**, bantlar 50/60/70/80
   (seçicilik AYNI kaldı, çelişkili sinyaller artık hiç geçemiyor).
3. **RiskAgent nötr baseline** — risk-normalken artık BUY değil HOLD (bedava +1
   BUY oyu ve güven şişmesi kalktı; SELL vetosu aynen durur).
4. **Rejim + EMA200 kapısı GÜNLÜK barda** — saatlik EMA200 ≈ 8 işlem günüydü
   (rejim etiketi gürültüyle savruluyordu). Rejim: SPY günlük 400 gün. EMA200
   trend kapısı: hisse başına günlük bar, ET-günde 1 fetch cache'li; RS için SPY
   saatlik cache ayrı tutulur.
5. **Ters-ETF dirildi** — SQQQ/SH/SPXS BEAR rejimde analiz listesinin başına
   eklenir (bear-only alım mantığı zaten vardı, evrene hiç girmiyordu). Long-only
   canlının ayı piyasası aracı.
6. **Earnings gate güvenilir** — hisse-başına AV çağrısı (kota aşımı) + "son
   rapor+90 gün" tahmini + ölü Yahoo fallback yerine: TEK toplu AV
   EARNINGS_CALENDAR çağrısı (gerçek beklenen tarihler, 3 ay), state'e kalıcı
   cache, 24 saatte bir tazeleme, 7 güne kadar bayat tolerans, sonrası fail-open.
7. **SignalQueue beslendi** — uzamış girişte (RSI≥65 / BB üstü / VWAP ≥%2 prim)
   %1.5 pullback bekler (2 saat). Paper-first: `pullback_queue_enabled` paper'da
   açık, canlıda kapalı. Kuyruk-çıkış emirlerinde pozisyon/limit/sektör/wash-sale
   yeniden kontrol edilir.
8. **NYSE yarım-gün takvimi** — Alpaca get_calendar günde 1 fetch (gerçek
   açılış/kapanış; 27 Kas & 24 Ara 13:00 erken kapanışları), statik yarım-gün
   listesi fallback; güvenli bölge sonu = kapanış−15dk (yarım günde 12:45).
9. **Paper öğrenme hızı** — paper min_conf 60→45 + min_rr 1.5 (+kuyruk).
   Backtest: 44→127 işlem/6ay (~günde 1, meta_labeler 30-50 kapalı işlem hedefi
   haftalar içinde dolar); PF 0.96 başabaş = sanal parayla ucuz öğrenme bedeli.
   CANLI eşiği gevşetilmedi (kalibre edilmiş 50 = eski 60 seçiciliği).

## v4.9 — 24-saat denetimi düzeltmeleri (2026-07-07, İhsan onayı: "hepsini onaylıyorum")
06-07 Tem canlı loglar + Alpaca defteri + kod denetiminin bulguları. Testler 91
(84+7 yeni), 89 geçti / 0 fail / 2 bilinen lokal uyarı.

**Denetim bulguları (bağlam):**
- ✅ LIVE kusursuzdu: PARK BUY SPY $288.03 Pzt açılışta plana birebir; stoplar
  günlük yenileniyor; 45 saat sıfır hata. Ama yeni hisse girişi de SIFIRDI → sebep
  aşağıdaki kalibrasyon hatası (canlı fiilen parking-only çalışıyordu).
- 🔴 KALİBRASYON: v4.8 Monte Carlo tahmini (yeni≈eski×0.83) gerçek oy dağılımında
  tutmadı — 2 günde koordinatör ham |ws| max 15 (NVDA 3'lü BUY ~32), eşikler
  paper 45 / live 50 HİÇ ulaşılamadı. Backtest coordinator yolunu kullanmadığı
  için A/B'de görünmedi (bilinen sınırlama, aşağıda).
- 🔴 PAPER opsiyon churn (Pzt 50 dk, -$2,170): koordinatör 5/5 HOLD iken
  analyzer-SHORT arka kapısı sinyali eski-ölçek güvenle (60) geçiriyor → BULL
  rejim short'u engelliyor AMA PUT dalı kilidin dışında → snapshot str-bug'ı
  (('str' object has no attribute 'to_request_fields') yüzünden fiyatlar bayat
  close_price ($0.71; gerçek dolum $0.42) → stop tek taraflı bid'e ($0.20) bakıp
  anında "-%72" → sat → cooldown yok → 60-90 sn sonra tekrar al ×8.
  Kapanış PnL'i hep $+0.00 loglandı, ajan istatistiğine 8 sahte LOSS yazıldı;
  07 Tem'de dolmamış SMCI limit emri "ALINDI $920" diye loglandı.
- ⚠️ ALPACA-TARAFI OLAY (bot suçu değil): 06→07 Tem gecesi $45,269 SPY sleeve +
  18 SMCI PUT hesaptan DEFTERDE SIFIR KAYITLA silindi (fill/journal/corp-action
  yok; equity 62,116→14,638). 01-02 Tem'de equity'nin hiç kıpırdamadan
  64,247.28'de durması sleeve'in zaten hayalet olduğunu gösteriyor (Alpaca gece
  "gerçeğe çekti"). Sabah get_account hâlâ hayalet $64k dönerken parking $3,973
  notional SELL attı → gerçek pozisyon 0 → hesap 5.3 SPY SHORT'a düştü; kill
  switch 14:12'de Alpaca'nın raporladığı -%77'ye DOĞRU tepki verip kapattı.
  Paper gerçek bakiyesi $14,645 (<$25k → artık PDT'ye tabi).

**Yapılan düzeltmeler:**
1. **Güven KAYNAK-REMAP (×2.0)** — coordinator: conf = |ws|×2.0 (×1.2 çoğunluk,
   ×0.5 veto, tavan 100). Güçlü mutabakat (ws 25-35+çoğunluk) → 60-84 bandı,
   zayıf 2'li oylar → 20-30'da ölür. Live 50 + bantlar 50/60/70/80 AYNEN korunur
   (İhsan'ın onayladığı seçicilik anlamını geri kazandı; ×2.5 değil ×2.0 seçildi
   çünkü ×2.5 NVDA-tipi sinyali 97'ye taşıyıp $300 bandını fazla kolaylaştırırdı).
   Paper min_conf 45→**30** (= ham ws 15, koordinatörün kendi BUY/SELL tabanı) →
   öğrenme akışı gerçekçi. Coordinator loguna ham `ws:` eklendi → 1 hafta gerçek
   dağılım toplanıp eşikler yüzdelik bazlı ince-ayarlanacak.
2. **Analyzer-SHORT arka kapısı KALDIRILDI** (stock_bot) — SHORT yalnız
   koordinatör SELL mutabakatından türer; analyzer'ın tekil SHORT'u artık
   decision'ı ezemez.
3. **Opsiyon dalları executor-sonucuna kilitlendi** — PUT yalnız `execute_short`
   True dönerse (rejim/kara-liste/squeeze bloklarını bypass edemez), CALL yalnız
   `execute_buy` True dönerse; "gate'den geçemese bile opsiyon dene" fallback'i
   silindi. SignalQueue yolları dahil.
4. **OPSİYON MODÜLÜ KAPALI** (`options_enabled: False`) — yeniden açma şartları:
   snapshot fix doğrulama + fill-onaylı muhasebe + spread kapısı canlı testi +
   1 hafta churn'süz paper gözlemi.
5. **Opsiyon altyapısı düzeltildi (kapalı dursa da doğru):** snapshot çağrısı
   `OptionSnapshotRequest` objesiyle (str-bug fix); girişte CANLI bid/ask şart +
   spread >%10 = işlem yok; emir sonrası fill POLL edilir — dolmazsa iptal + kayıt
   YOK, dolarsa GERÇEK dolum fiyatı/adediyle defter; kapanış PnL'i kapanış emrinin
   gerçek dolumundan (bilinmiyorsa ajan istatistiğine kayıt atılmaz); underlying
   başına 4 saat re-entry cooldown; stop değerlemesi bid yerine MID.
6. **Parking short-imkânsız** — `_sell` artık `close_position` endpoint'i
   (DELETE /v2/positions: mevcut pozisyonu küçültebilir, short AÇAMAZ) + qty
   eldekiyle sınırlı; `maybe_rebalance` başında negatif-pozisyon self-heal
   (bekleyen emir kontrolüyle, emir yığmadan derhal buy-to-close).
7. **Health alarmı hafta sonu bilinci** — "X saattir işlem yok" artık Cts/Paz
   saatlerini saymaz (05 Tem "73.7h işlem yok 🔴" yalancı alarmı fix).

**Bilinen sınırlama (v4.9'da bilinçli dokunulmadı):** backtest.py sinyal yolu
koordinatörü değil analyzer'ı kullanır → koordinatör-eşik değişikliklerini
backtest DOĞRULAYAMAZ (haber/sosyal verisi tarihsel yok). Kalibrasyon artık
canlı `ws:` loglarından yapılır; backtest yalnız çıkış/gate mantığı için geçerli.

**Operasyonel:** paper kill dosyası + hayalet günlük baseline deploy sonrası
temizlendi; Alpaca paper hesabının dashboard'dan resetlenmesi İhsan'da (bakiye
$14.6k + PDT kısıtı öğrenme için dar).

## v4.10 — Giriş hunisi denetimi (2026-07-11, "açıkları bul ve düzelt")
08-10 Tem (v4.9 sonrası 4 işlem günü) canlı+paper log denetimi. Testler 97/97.

**Denetim bulguları:**
- LIVE: 4 günde SIFIR yeni giriş (tek işlem: 09 Tem META yönetim satışı; equity
  $487→$489, kazanç = SPY park betası). Remap ÇALIŞTI — eşiği geçen sinyaller
  VARDI ama HEPSİ seri kapılara öldü: MARA guven 50-62 (09 Tem, 3.5 saat kesintisiz)
  → "SEKTÖR ROTASYON normal rejiminde kaçınılıyor"; AMD 56 (09 Tem 14:03) → MTF
  4h düşüş; COIN ~55 (10 Tem) → EMA200. Güven zirvesi ile kapıların açık anı
  hiç çakışmadı.
- 🔴 VIX ANAHTAR BUG'I: `vix_data.get("value", 20)` — macro dict'in anahtarı
  `"vix"`; her gün varsayılan 20 okunuyordu → sektör rejimi KALICI "normal"
  (log kanıtı: `VIX: 16.40 ... (20.0)`). Çift yönlü arıza: normalde EV+Crypto
  kalıcı yasak + gerçek VIX-40 krizinde defansife GEÇEMEZDİ.
- 🔴 "normal" rejim avoid listesi (EV+CryptoMining) piyasanın VARSAYILAN halinde
  20 sembolün 6'sını (momentum katmanı) kalıcı yasaklıyordu; VIX 14.9→15.1
  geçişi 6 sembolü tam-boy↔tam-yasak arasında çeviriyordu (uçurum etkisi).
- 🔴 EARNINGS TAKVİMİ BOŞ: AV kota bittikten sonra (tarama ortası) yenilenince
  200+header-only CSV dönüyor, kod bunu "başarılı" sayıp DOLU cache üstüne {}
  yazıyordu → temmuz kazanç sezonu öncesi gate kör (fail-open). Manuel test
  (kota tazeyken): 5.680 satır, tüm evren sembolleri mevcut — API sağlam.
- 🔴 AJAN ÖĞRENMESİ ÖLÜ: record_prediction her taramada (işlemsiz) yazıyordu →
  4 günde 5.515 kayıt, TAMAMI actual_outcome:null (paper 1.4MB); outcome
  eşleşmesi giriş-anı oyunu değil rastgele geç taramayı yakalıyordu; cleanup_old
  hiç çağrılmıyordu. meta_labeler'ın beklediği WIN/LOSS satırları hiç oluşmuyordu.
- 🔴 PAPER DONUK: min_conf 30'a rağmen 4 günde 1 gerçek işlem (AMD, -$17).
  Blokerler: MARA sektör ~100×, META loss-streak ~96× (2 zarar → conf≥70 şartı,
  PF~0.96 beklentili öğrenme hesabında yapısal çelişki), TSLA sektör ~57×,
  SMCI/SOFI EMA200. Bu hızla meta_labeler 30-50 işlem kapısı AYLAR sürerdi.
- ⚠️ Log ÜÇLEMESİ: utils/logger console + stock_bot'un root handler'ı + bir
  bağımlılığın basicConfig'i → her satır 3× (disk + analiz kirliliği).
- ⚠️ Health cron 10 Tem'de sağlıklı-ama-seçici canlıya "32h işlem yok 🔴 redeploy
  edin" dedi (işlemsizlik ≠ ölülük).

**Yapılan düzeltmeler (koruma kilitleri GEVŞETİLMEDİ):**
1. **VIX anahtarı fix** — `get("vix") or 20`; rejim artık gerçek VIX'i izliyor
   (bugün 16 → normal; <15 → low; kriz → high/extreme tepkisi geri geldi).
2. **Sektör rotasyonu "reduced" katmanı** — normal rejimde EV+CryptoMining
   hard-veto DEĞİL boyut ×0.7 (MARA-62 artık $150 bandı × 0.7 = $105 girer);
   high/extreme VIX hard-avoid AYNEN. Bant (LIVE) boyut yolu sector_weight'i
   artık uyguluyor (eski kod yalnız Kelly yolunda çarpıyordu); weight_boost
   bant dolarlarını YUKARI esnetemez (İhsan'ın $100-300 sözleşmesi tavan).
3. **Earnings boş-CSV koruması** — boş sonuç = başarısız fetch (eski takvim
   korunur, fetched_at ilerlemez, 30dk retry + 7g bayat-tolerans devrede) +
   sabah taramasında `ensure_fresh()` (kota TAZEyken, 08:00 UTC) → gün-içi lazy
   yenileme kota-sonrası boşluğa denk gelmiyor. Mevcut zehirli {} cache ilk
   sabah taramasında kendini onarır.
4. **Ajan öğrenme kaydı işlem-anına taşındı** — `_record_trade_votes` yalnız
   `execute_buy/execute_short` True dönünce (kuyruk yolları dahil); outcome artık
   giriş-anı oy setine yazılır (doğru kredi ataması) → meta_labeler beslenmeye
   başlar. `prune()`: çözümsüz >3g + çözümlü >90g kayıtlar açılışta ve günlük
   reset'te budanır (mevcut 5.5k null migrasyonda temizlenir).
5. **Paper loss-streak öğrenme ayarı** — PAPER_AGGRESSIVE: warn 999 (conf-70
   şartı kapalı), halt 6 zarar / 6 saat (fren duruyor); kill switch -%5/gün aynen.
   **LIVE warn 2 / halt 4 / 24h / conf-70 DEĞİŞMEDİ.**
6. **Log üçlemesi fix** — TradingBot logger `propagate=False` + stock_bot'un
   root-handler bloğu kaldırıldı → satır başına tek emisyon.
7. **Health canlılık = heartbeat** — bot her heartbeat'te `state/heartbeat.json`
   yazar; health_check döngü canlıysa işlemsizliği ℹ️ bilgi notuna düşürür
   (yalancı 🔴 bitti), heartbeat >30dk eskiyse gerçek 🔴 "DURMUŞ" verir.
   Ek: notional emirlerin $0.00 görünme bug'ı fix.

**Beklenen davranış değişimi:** canlı hâlâ seçici (min_conf 50 + EMA200 + MTF +
VOL + R:R + earnings kapıları aynen) ama MARA-tipi çok-saatlik mutabakat artık
küçük boyutla ($70-105) işleme dönüşebilir; paper örnek akışı açılır (hedef:
FAZ 2'nin 30-50 işlem kapısını haftalar içinde doldurmak). Eşik/bant İNCE-AYARI
için 1 hafta daha ws dağılımı toplanacak — v4.9'daki plan geçerli.

## v4.11 — BEAR BRAIN: düşüş-kazanç beyni (2026-07-11, İhsan: "düşüşte de kazanalım, risk yüksek olsun")
**Amaç:** bot artık piyasa DÜŞERKEN de kazanç üretebilir. Canlıda gerçek short
İMKÂNSIZ (Alpaca marj şartı $2.000 > $487 equity) → düşüş tezi **ters-ETF
LONG'una** çevrilir; cash hesapta ve `BOT_MODE=long_only` bozulmadan çalışır.

**Neden yeni beyin gerekti:** v4.8'in ters-ETF yolu yapısal ölüydü — (1) yalnız
SPY < günlük-EMA200 (BEAR) rejiminde tetikleniyordu ki bu sinyal düşüşün %10-15'i
yaşandıktan sonra gelir; (2) BEAR'da BUY eşiği +10 yükseliyordu (ters-ETF alımı
daha da zorlaşıyordu — mantık hatası); (3) long kapıları ETF'nin KENDİ EMA200'ünü
ve ATR≤%5 tavanını arıyordu (3x ters-ETF erken düşüşte ikisini de geçemez).
Kanıt: özellik hiç işlem üretmedi.

**Yeni mimari (`core/bear_brain.py`):**
1. **Bileşik skor 0-100** (30dk'da bir, rejim güncellemesiyle):
   trend 0-30 (SPY günlük EMA9/21/50 dizilimi + fiyat konumu) + momentum 0-25
   (5g/10g getiri) + VIX 0-25 (seviye + gün-aşırı sıçrama) + genişlik 0-20
   (koordinatör ws dağılımı: evrenin % kaçı bearish + rejim detektörü).
   Veri eksiği = bileşen 0 (fail-neutral, skor yapay şişmez).
2. **Modlar:** OFF → WATCH(40) → **DEFENSE(55) → SH (1x ters S&P) $100** →
   **ATTACK(72) → SQQQ (3x ters NASDAQ) $150** (havuz tech-ağırlıklı; $150 3x
   ≈ $450 efektif short-delta ≈ equity'nin ~%92'si — bilinçli yüksek risk).
   Paper: $1500/$3000, 2 pozisyon, günde 2 giriş.
3. **Girişler executor'dan geçer** → floor/rezerv/PDT/bracket-stop AYNEN.
   Beyin-özel kapılar: 4h cooldown, canlıda günde 1 giriş, maruziyet ≤%35
   equity, wash-sale UYARI modunda (30g kilit stratejiyi öldürür; bilinçli).
4. **Çıkışlar:** bracket SL/TP (3x: SL %6-8 / TP %9-15; 1x: SL %4-5 / TP %6-8,
   R:R 1.5) + **skor-çıkışı** (<45, histerezis) + **zaman-stopu** (3x: 7 gün,
   1x: 15 gün — kaldıraçlı ETF'de günlük-rebalans erimesi) + trailing/partial
   (position_manager standart).
5. **Parking senkronu:** DEFENSE → yeni SPY parkı DURUR; ATTACK → sleeve
   ÇÖZÜLÜR (düşen piyasada long-beta + short-delta aynı anda tutulmaz; PDT
   koruması: aynı gün alınan SPY aynı gün çözülmez).
6. **Paper öğrenme:** bear modunda gerçek-short eşiği gevşer (DEFENSE −5,
   ATTACK −10, taban 25) → düşüş dönemlerinde öğrenme akışı hızlanır.
7. **Temizlik:** ters-ETF'ler tarama/koordinatör yolundan tamamen çıkarıldı
   (BearBrain tekeli); `_last_vix` artık gerçekten atanıyor (eskiden hep 0
   okunuyordu → gelişmiş rejim detektörü VIX'i hiç görmemişti).

**DOKUNULMAYANLAR:** kill %5/gün, equity floor %85, canlı hisse bantları
$100-300 + min_conf 50, loss-streak kilitleri, opsiyon kapalı — koruma
kilitleri gevşetilmedi; bear tarafı KENDİ tavanlarıyla eklendi.

**İzleme (ilk düşüş haftası):** (a) `🐻 BEAR BRAIN MOD:` geçiş logları makul mü
(sakin piyasada OFF/WATCH'ta kalmalı), (b) ilk DEFENSE girişinde boyut $100 ve
bracket stop Alpaca'da NEW mi, (c) ATTACK'ta parking unwind + SQQQ girişi
sıralı mı, (d) skor-çıkışı/zaman-stopu spam yapmıyor mu (30dk deneme aralığı).

## v4.11.1 — Cumartesi doğrulama denetimi (2026-07-12, "düzeltmeler doğru çalışıyor mu + açık tespiti")
Deploy doğrulandı: iki VPS konteyneri de e4096cf ile birebir aynı (md5), restart
yok, heartbeat `🐻 OFF(0)` (sakin piyasada beklenen), state_live kalıcı volume.
Denetimde bulunan 2 açık kapatıldı:
1. **VIX cache 6h → BearBrain gün-içi KÖRDÜ:** `_update_market_regime` 30dk'da
   bir VIX "okuyordu" ama macro cache 6h aynı değeri döndürüyordu → seans başına
   fiilen 1 okuma; skorun vix bileşeni (25p: seviye+sıçrama) gün-içi çöküşü
   göremiyordu (gün-1 DEFENSE tetiklenmesi çoğu senaryoda buna bağlı).
   Fix: `MACRO_CONFIG["cache_hours_overrides"] = {"vix": 0.5}` — yalnız VIX
   30dk TTL, diğer makro anahtarlar 6h kalır (tur başına ≤1 Yahoo isteği).
2. **Bear döngü hataları görünmezdi:** `run_cycle` istisnaları `logger.debug`'a
   gömülüydü (v4.10 dersi: debug'daki arıza = günlerce sessiz ölü sistem).
   Fix: 30dk rate-limitli WARNING (`_log_cycle_error`), arası debug.
Testler 105/105 (2 yeni). Ayrıca doğrulandı: earnings'in diskteki zehirli boş
takvimi (09 Tem) Pazartesi pre-market'te kendini tazeler (TTL 24h aşılmış,
v4.10 boş-CSV koruması yazımı engeller); `is_paper` bantları doğru ayrışıyor.

**Bilinen tasarım sınırları:** ilk ikisi ✅**v4.11.2'de kapatıldı** (İhsan 12 Tem:
"genel sistem kontrolü sonrası short/alım analizi lazım — iki taraftan kazanırız"):
- ~~DEFENSE bandında net LONG kalınıyor~~ → **DEFENSE'te de sleeve unwind**
  (`defense_parking_unwind: True`).
- ~~SH→SQQQ terfisi yok~~ → **ATTACK rotasyonu** (`attack_rotation: True`).
- **Tek-gün flash crash'te kill switch önce davranır:** gün −%5 (≈$24) kill →
  tüm pozisyonlar kapanır, bear girişi de durur. Bear kazancı ancak kademeli
  (çok-günlü) düşüşte realize olur — koruma hiyerarşisi bilinçli böyle (KALIYOR).
- **Nakit rezervi boyutu kırpar:** `cash_reserve_pct` %15 → nakit darsa SH/SQQQ
  hedefi kırpılır (long'lar stop'lanıp/sleeve çözülüp nakit açılınca tam boyut).
  Kırpma bilinçli: rezerv likiditesi > bant sadakati (KALIYOR; DEFENSE-unwind
  sleeve nakdi açtığı için pratikte tam boyut artık normdur).

## v4.11.2 — İKİ TARAF TAM AKTİF (2026-07-12, İhsan: "genel kontrol sonrası short/alım analizi — iki taraftan kazanırız")
Yön analizi zaten vardı (bear skoru piyasa-geneli, koordinatör hisse-bazlı);
eksik olan yönü SONUNA KADAR takip etmekti. İki değişiklik (testler 107/107):
1. **DEFENSE'te parking sleeve ÇÖZÜLÜR** — eskiden yalnız yeni park duruyordu,
   ~%60 equity SPY long kalıp DEFENSE bandında (55-71) net-LONG bırakıyordu.
   Artık skor 55+ iken beta tutulmaz; sleeve nakdi bear girişine açılır (rezerv
   kırpması da pratikte çözülür). Ek: mod 45-55 histerezis bandına düşerse ve
   bear pozisyonu hâlâ açıksa yeni park YAPILMAZ (hedge + taze beta çelişkisi).
   Whipsaw maliyeti sınırlı: park/unwind günde 1'er deneme, SPY spread ~kuruş.
2. **ATTACK rotasyonu (SH→SQQQ):** canlıda tek bear pozisyonu kuralı yüzünden
   DEFENSE'te SH girildiyse 72+ tırmanışta 3x eklenemiyordu. Artık ATTACK'ta
   SH kapatılır (execute_sell PDT koruması: aynı gün alındıysa yarın; 30dk
   deneme aralığı) → SQQQ sonraki turda NORMAL giriş kapılarından (cooldown
   4h/gün-tavanı/maruziyet/floor/kill) geçerek açılır; satış-dolumu otursun
   diye rotasyon sonrası 90sn giriş beklemesi. Bayat restore-moduyla asla
   rotasyon yapılmaz (skor bu süreçte ölçülmüş olmalı).
3. **Maruziyet tavanı kırpar, bloklamaz:** $150 hedef $147 tavanı aştı diye
   kriz girişini iptal etmek yerine tavana sığdırılır (tavan %35 AYNEN;
   dolu tavanda giriş yine yok).
Geri alma anahtarları: `defense_parking_unwind` / `attack_rotation` = False.
**KORUMA KİLİTLERİ YİNE DEĞİŞMEDİ:** kill %5, floor %85, %35 bear tavanı,
canlı günde-1-giriş, 4h cooldown, PDT korumaları aynen.

**İzleme (ilk düşüş günü):** DEFENSE'e geçişte `PARK BEAR-UNWIND` logu →
ertesi heartbeat'te sleeve 0; ATTACK tırmanışında `BEAR ROTASYON` → SH SELL →
~90sn sonra SQQQ BUY (bracket'li); 45-55 bandında SH açıkken `PARK DURDU` logu.

## v4.12 — PAPER AGRESİF+ (2026-07-12, İhsan: "paper'ı agresifleştirelim, sistemin agresif hâlinde neler yaşadığını görelim")
SADECE PAPER değişti; tüm değerler `PAPER_AGGRESSIVE_CONFIG` + bear `paper_*`
anahtarlarında. **LIVE kilitleri birebir aynen** (kill %5, floor %85, rezerv %10,
$100-300 bantları, MTF/VOL/sektör kapıları, bear canlı bantları/cooldown).
Amaç: (a) canlının mekanizmalarını büyük dolarlarla stres-gözlemek,
(b) meta_labeler/ajan-öğrenme örnek üretimini hızlandırmak.

1. **Paper bant-boyutlandırma (yeni):** paper artık canlının `conf_position_bands`
   kod yolunu kullanır — 30→$2.5k, 45→$4k, 60→$6k, 75→$9k (eskiden Kelly-negatif
   ~%5 tabanı boyutu sinyalden bağımsız ~$3.1k'ya sabitliyordu). max_position_usd
   $5k→$9k, max_open_positions 8→10, sektör tavanı 2→3.
2. **Kapı gevşetmeleri (paper):** MTF 4h kapısı KAPALI (karşı-trend girişler de
   örneklensin — canlıda en çok giriş yutan kapı), VOL kapısı ATR %5→%8
   (MARA/RIOT/SMCI sınıfı akışa girer), hisse devre-kesici 3→5 ardışık zarar,
   loss-streak halt 6→8 zarar / 6h→4h.
3. **Sermaye/fren (paper):** nakit rezervi %10→%5, equity floor %85→%75,
   günlük kill %5→%8 (kill KALKMADI — tasma uzadı; felaket freni duruyor).
4. **Short (paper):** max 4→5 pozisyon, $4k→$6k/pozisyon, maruziyet %40→%50,
   min güven 35→32.
5. **Bear brain (paper):** boyut bantları 2× ($3k/$6k), günde 2→3 giriş,
   cooldown 4h→2h (`paper_entry_cooldown_hours`, bear_brain paper-farkında).
6. **MERGE-SIRASI FIX (gerçek bug):** PAPER_AGGRESSIVE merge'i `__init__`
   BAŞINA taşındı — eski yeri KillSwitch/equity-floor kurulumundan SONRAydı,
   yani paper'a kill/floor override'ı yazılsa bile sessizce uygulanmıyordu
   (bu yüzden eski paper kill hep %5'te kalmıştı). Testte kaynak-sıra denetimi var.
7. Opsiyonlar paper'da da KAPALI KALDI (v4.9 açma şartları karşılanmadı).
8. `min_confidence_score` 30'da BIRAKILDI — bilinçli: koordinatör ws≤15'te zaten
   HOLD üretir (conf=|ws|×2 → taban 30); daha düşük eşik hiçbir şey açmaz.

Testler: **111/111** (4 yeni v4.12 testi: config+LIVE-kilit koruması, merge
sırası, bant boyutlandırma, bear paper cooldown; 2 uyarı lokal-ortam:
onnxruntime/ntscraper — Docker'da sorun değil).

**İzleme (ilk hafta):** paper banner `PAPER AGGRESSIVE MODE: Aktif (v4.12
agresif+)` + `Kill: -8%/gün | Floor: 75%`; alım loglarında
`PositionSizer [LONG-KADEMELI]: $2500-9000 | GÜVEN x → $y`; işlem sayısı
belirgin artmalı (MTF kapalı + VOL %8). Beklenen: PF<1 stratejide daha derin
paper DD — bu deneyin AMACI (agresif rejimin gerçek yüzü); kill -%8 günü ve
floor %75 dipleri not et. Öğrenme: meta_labeler 30-50 kapalı işlem kapısı
hızla dolar. Geri alma: v4.12 değerlerini önceki değerlere çek (git diff
config.py) — mekanizma değişiklikleri (merge sırası, bant yolu, paper cooldown
anahtarı) geri almasız kalabilir, davranışları config'e bağlı.

## FAZ 1 — v4.7 canlıda (deploy sonrası ilk hafta)
- İlk alımların güven bandına uyduğunu doğrula (log: `PositionSizer [LONG-KADEMELI]: $... | GÜVEN x → $y`).
- Beklenen davranış değişimi: min_confidence 30→60 → **daha az ama daha büyük ve seçici işlem**
  (backtest: PF 0.81→1.90). Günlük kill %5 (~$24), floor $414, pozisyon tavanı equity'nin %55'i.
- Tam-paylı alımlarda (NVDA/AMZN gibi) artık **GTC server-side stop** çalışır; kesirlilerde
  DAY stop + bot-loop koruması (eskiden GTC+fraksiyonel sessizce reddediliyordu — düzeltildi).

## FAZ 2 — Öğrenme hattı (paper, 2-6 hafta)
- Paper bot agent_performance v2 verisi biriktiriyor (yön-farkında kredi atama).
- **30-50 kapalı işlem** birikince: `py -X utf8 meta_labeler.py` → OOF AUC > 0.55 ise
  `MetaLabeler.predict_proba`'yı live trade-gate/size-çarpanı olarak bağla (WIRE kapısı).
- Haftada 1: `py -X utf8 walk_forward.py` → "SPY'ı geçti mi?" ölçümü. Graduation kapısı bu.

## FAZ 3 — Edge büyütme
- ✅ **Index parking LIVE AÇILDI (v4.8.2, 2026-07-05)** — İhsan kararı "hemen aç"
  (sermaye kuralı: katkı eklenmeyecek → boş nakdin betası tek yapısal düzeltme;
  regime deneyi: alpha −11.5% → −2.8%). %30 rezerv likit, günde 1 rebalance,
  floor ihlalinde park yok. İlk park beklentisi: Pzt açılışta ~$288 SPY BUY.
  Not: %30 parking rezervi + executor %10 nakit rezervi → ilk alım ~$97-100'e
  kırpılabilir (bilinçli kabul; rezerv eriyince parking ertesi gün SPY satarak tamamlar).
- **Günlük sağlık cron'u (VPS)**: hafta içi 21:30 UTC `trading_health_report.sh` →
  `/var/log/trading_health.log` (health_check.py konteyner içinde; --alert 30h).
- Bant güncelleme: hesap büyüdükçe `live_conf_position_bands` değerlerini yükselt (örn. $1k hesapta 200/300/400/600).
- Hesap $25k'ya kadar PDT/GFV-uyumlu swing duruşu korunur (pdt_tracker zaten yapıyor).

## FAZ 4 — Altyapı
- Dashboard'ı (trading/dashboard) aynı VPS'te Coolify'dan yayınla → günlük equity/işlem görünürlüğü.
- Coolify API token ile: otomatik günlük sağlık raporu (Alpaca equity + konteyner durumu → e-posta/log).
- State kalıcılığı: named volume'lar (`state-live`, `state-paper`) compose'da hazır —
  redeploy'da pdt/kill/agent_performance kaybolmaz.

## Bilinen riskler (kabul edilenler)
- **Boyut ≠ edge**: $250 boyut kârı da zararı da ~10x büyütür; walk-forward hâlâ "SPY'ı
  geçmiyor" diyor. Para kazandıracak şey Faz 2-3'teki edge işi; boyut sadece ölçek.
- Cash hesapta nadir **GFV** senaryosu: aynı-gün satış gelirimle aynı-gün alınan pozisyonun
  aynı-gün stop'lanması (nadir; 12 ayda 3 ihlal = 90 gün kısıt). pdt_tracker same-day
  satışları zaten blokluyor (stop hariç) → risk düşük, izlemede kalsın.
- Fraksiyonel pozisyonlarda gece server-side stop yok (Alpaca DAY-only) → gap riski
  bot-loop stop'una kalıyor; tam-pay tercihi bunu kısmen çözüyor.
