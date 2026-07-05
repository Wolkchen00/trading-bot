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
