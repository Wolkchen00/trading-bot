# Trading Bot — Geliştirme Planı (v4.6 sonrası)
_Hazırlanma: 2026-07-02 · Güncelleme (aynı gün akşam): FAZ 0 TAMAMLANDI — v4.6 Coolify VPS'te CANLI_

## Mevcut durum (özet)
- **LIVE** ($487 gerçek): **v4.6 CANLI** — `SABİT $250/alım`, kill %5, floor $414.
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
