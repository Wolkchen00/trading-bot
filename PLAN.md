# Trading Bot — Geliştirme Planı (v4.6 sonrası)
_Hazırlanma: 2026-07-02 · Güncelleme: Railway TERK EDİLDİ → deploy evi Coolify VPS (91.99.9.121:8000)_

## Mevcut durum (özet)
- **LIVE** ($487 gerçek): Bugün hâlâ **v4.5 (5 Mayıs)** kodu işlem yapıyor. 3 aylık sonuç **+%1.4** (yatay). İşlemler ~$26 çünkü Kelly negatifken sizer %5 tabana iniyor.
- **PAPER** ($64k sanal): 6 Mayıs'tan beri ölüydü (2 ay veri kaybı). 2026-07-02'de **lokalde v4.6 ile yeniden başlatıldı** (watchdog'lu; durdurmak = `trading/` içine `STOP_BOT` dosyası).
- **Repo taşındı:** `Wolkchen00/trading-bot` (yeni origin; eski Wolkchen0 reposunun push token'ı ölmüştü). v4.6 burada.

## FAZ 0 — Coolify'a bağla (İhsan'ın panel adımları, ~5 dk)
Coolify: `http://91.99.9.121:8000`
1. **Kaynağı değiştir/oluştur:** Mevcut trading uygulaması varsa → Source'u
   `github.com/Wolkchen00/trading-bot` (branch `main`) yap. Yoksa → New Resource →
   **Docker Compose** → repo `Wolkchen00/trading-bot`, compose dosyası `docker-compose.yml`
   (iki servis birden gelir: `trading-live` + `trading-short`).
2. **Environment Variables** (Coolify bunları `.env` olarak yazar, compose `env_file: .env`
   ile okur — lokal `.env` git'e GİTMEZ, o yüzden panele girilmeli):
   - `ALPACA_LIVE_API_KEY`, `ALPACA_LIVE_SECRET_KEY`
   - `ALPACA_PAPER_API_KEY`, `ALPACA_PAPER_SECRET_KEY`
   - `ALPHA_VANTAGE_KEY`, `MARKETAUX_TOKEN`
   - (önerilen) `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` → al/sat/kill bildirimleri
3. **Deploy** butonu. Loglarda şunu doğrula: live banner'da
   `Boyut: SABİT $250/alım` + paper banner'da `PAPER AGGRESSIVE MODE`.
4. **ÇİFT BOT KONTROLÜ (kritik):**
   - Railway hesabında hâlâ çalışan servis varsa SİL (aynı live hesaba iki bot emir
     basar + boşuna fatura). Kod/konfigden Railway izleri zaten temizlendi.
   - VPS'te paper konteyneri ayağa kalkınca **lokaldeki paper botu durdur**
     (`trading/STOP_BOT` dosyası oluştur) — çifte paper işlem olmasın.
5. (İstersen) bana Coolify **API token** ver (panel → Keys & Tokens → API tokens):
   sonraki deploy/rollback/log işlerini terminalden ben yönetirim.

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

## FAZ 4 — Altyapı
- Dashboard'ı (trading/dashboard) aynı VPS'te Coolify'dan yayınla → günlük equity/işlem görünürlüğü.
- Coolify API token ile: otomatik günlük sağlık raporu (Alpaca equity + konteyner durumu → Telegram).
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
