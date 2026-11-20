# Trading bot (v5 "Yenileme")

Küçük, dürüst bir motor: günde bir kez, borsa kapanışına 10 dakika kala, hesabı TEK bir doğrulanmış kuralın
gerektirdiği duruma getirir, sessizce donamaz ve sahibine sade Türkçe ile haber verir.

## Amaç

Alpaca hesabındaki parayı tam otomatik yönetmek. Kural: Nasdaq (QQQ) yükseliş eğilimindeyken ve piyasa sakinken
kaldıraçlı Nasdaq fonu (TQQQ) tutar, oynaklık artınca payı küçültür, eğilim bozulunca nakde geçer. Sert düşüş
günlerinde bir günlüğüne küçük bir ek alım yapar. Açığa satış, opsiyon, ters fon yoktur.
Dürüst beklenti: düzenli günlük ya da aylık kazanç garanti edilemez; derin düşüşler bu kuralın olağan parçasıdır.

## Kullanılan araçlar

- Python 3.12, yalnız `requests`, `tzdata` (çalışma) ve `pandas`, `numpy`, `pytest` (testler). Broker SDK'sı yok.
- Alpaca Trading API ve Market Data API (ham HTTPS).
- ntfy (sahibe telefon bildirimi).
- Docker (iki hizmet: gerçek hesap ve deneme hesabı, aynı imaj).

## Çalışma şekli

| parça | iş |
|---|---|
| `v5/strategy.py` | tek saf karar fonksiyonu (kural) |
| `v5/data.py` | karar anındaki fiyat verisi |
| `v5/executor.py` | tek, tekrarsız, çökmeye dayanıklı emir zinciri |
| `v5/rails.py` | düşüş uyarısı, günlük kesici, felaket tabanı, duraklatma |
| `v5/notify.py` | sahibe giden metinler, günlük özet, kalıcı giden kutusu |
| `v5/runner.py` | döngü: ne zaman ne olur, hangi kural önce gelir |
| `v5/health.py`, `v5/ops.py` | sağlık ve operatör komutları |
| `tools/v5_durum.py`, `tools/v5_tatbikat.py` | durum özeti, deneme hesabı tatbikatı |

Gerçek hesap hizmeti varsayılan olarak KURU çalışır (karar verir, bildirir, emir göndermez). Emir göndermesi
için sahibin açık onayı ve `LIVE_TRADING_ENABLED=true` gerekir.

## Kurulum

1. `.env.example` dosyasındaki adları ortam deposuna gir (anahtarlar asla depoya yazılmaz).
2. `docker compose up -d --build` (sunucuda Coolify bunu yapar).
3. Her hizmet için bir kez: `python -m v5.ops init-peak <hesap değeri> --source "<kaynak>"`.
4. Testler: `python -m pytest tests_v5 -q -p no:cacheprovider --basetemp=.pytest_tmp`

## Belgeler

- `RF-PLAN-6.md`: plan (kural, emir zinciri, raylar, bildirim metinleri).
- `RF6-VTO.md`: sahibin kararları (risk paketi, sınırlar).
- `RF6-KANIT.md`: kuralın neden seçildiğinin kanıtı. `RF6-PROBE.md`: broker ölçümleri.
- `RF6-CALISMA-KILAVUZU.md`: işletme kılavuzu. `RF6-DEVIR.md`: devir notu.
- `_legacy/v4/`: eski sistem (çalışmaz, yalnız tarihçe).
