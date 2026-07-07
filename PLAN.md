# Trading Bot — Geliştirme Planı (v4.6 sonrası)
_Hazırlanma: 2026-07-02 · Güncelleme: 2026-07-05 v4.8 "açık kapama" paketi_

## Mevcut durum (özet)
- **LIVE** ($487 gerçek): **v4.8 CANLI** — `GÜVENE GÖRE $100-300` (yön-farkında yeni güven ölçeği: 50-59→$100, 60-69→$150, 70-79→$200, 80+→$300), kill %5, floor $414.
- **PAPER** ($64k sanal): 2 ay kilitli kaldıktan sonra (bayat kill dosyası) **canlandı** —
  PAPER AGGRESSIVE + index parking VPS'te çalışıyor; lokal kopya durduruldu (STOP_BOT).
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
