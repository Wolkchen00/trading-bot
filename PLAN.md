# Trading Bot — Geliştirme Planı (v4.6 sonrası)
_Hazırlanma: 2026-07-02 · Durum tespiti Alpaca API + kod incelemesi ile yapıldı_

## Mevcut durum (özet)
- **LIVE** ($487 gerçek): Railway'de **hâlâ v4.5 (5 Mayıs)** çalışıyor. 3 aylık sonuç **+%1.4** (yatay). İşlemler ~$26 çünkü Kelly negatifken sizer %5 tabana iniyor.
- **PAPER** ($64k sanal): Railway'de **6 Mayıs'tan beri ölüydü** (2 ay veri kaybı). 2026-07-02'de lokalde v4.6 ile yeniden başlatıldı (watchdog'lu).
- **v4.6 lokalde commit'li ama deploy BLOKE**: GitHub push token'ı yazma yetkisini kaybetmiş (401). `gh` hesabı Wolkchen00'ın Wolkchen0/trading-bot'a push izni yok.

## FAZ 0 — Kilidi aç (İhsan'ın yapması gerekenler)
1. **GitHub token yenile**: Wolkchen0 hesabıyla github.com → Settings → Developer settings →
   Personal access tokens → yeni token (repo **write**). Sonra terminale yapıştır:
   `git remote set-url origin https://YENI_TOKEN@github.com/Wolkchen0/trading-bot.git && git push origin main`
   - Alternatif: `! railway login` → sonra `railway up` ile git'siz doğrudan deploy.
2. **Railway paper servisi**: dashboard'da neden durduğuna bak (muhtemelen crash/OOM, 6 Mayıs);
   restart et VEYA kalıcı olarak lokal/VPS'e taşımaya karar ver.
3. **Telegram**: `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`'yi hem lokal `.env`'e hem Railway
   Variables'a ekle → al/sat/kill-switch bildirimleri açılır (kod hazır, sadece env eksik).

## FAZ 1 — v4.6 canlıda (deploy sonrası ilk hafta)
- İlk alımların **~$250** olduğunu doğrula (log: `PositionSizer [LONG-SABİT]`).
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
- **Index parking LIVE kararı**: regime deneyi gösterdi ki SPY açığının çoğu nakit sürüklemesi
  (alpha −11.5% → −2.8%). Paper'da 2-4 hafta sorunsuz çalışırsa live'da aç
  (`index_parking_enabled=True` + `index_parking_allow_live=True`). $250×3 pozisyon sonrası
  boş nakit azalacağı için etkisi sınırlı ama pozisyonsuz günlerde beta yakalar.
- Kademeli boyut: hesap $600'ü geçerse `live_fixed_position_usd` 250→300.
- Hesap $25k'ya kadar PDT/GFV-uyumlu swing duruşu korunur (pdt_tracker zaten yapıyor).

## FAZ 4 — Altyapı (opsiyonel)
- Railway yerine **VPS/Coolify'a taşı** (youtube sistemleriyle aynı yer): docker-compose.yml
  hazır (dual container + kalıcı volume). Avantaj: log/state erişimi, Railway faturası yok.
- Dashboard'ı (trading/dashboard) aynı VPS'te yayınla → günlük equity/işlem görünürlüğü.

## Bilinen riskler (kabul edilenler)
- **Boyut ≠ edge**: $250 boyut kârı da zararı da ~10x büyütür; walk-forward hâlâ "SPY'ı
  geçmiyor" diyor. Para kazandıracak şey Faz 2-3'teki edge işi; boyut sadece ölçek.
- Cash hesapta nadir **GFV** senaryosu: aynı-gün satış gelirimle aynı-gün alınan pozisyonun
  aynı-gün stop'lanması (nadir; 12 ayda 3 ihlal = 90 gün kısıt). pdt_tracker same-day
  satışları zaten blokluyor (stop hariç) → risk düşük, izlemede kalsın.
- Fraksiyonel pozisyonlarda gece server-side stop yok (Alpaca DAY-only) → gap riski
  bot-loop stop'una kalıyor; tam-pay tercihi bunu kısmen çözüyor.
