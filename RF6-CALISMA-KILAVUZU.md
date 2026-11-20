# RF6-CALISMA-KILAVUZU.md , v5 işletme kılavuzu

> Kimin için: botu işleten Claude oturumu. Sahibe (İhsan) gösterilen her metin `RF-PLAN-6.md` Ek A'dan gelir;
> bu belge teknik bir kılavuzdur, sahibe okutulmaz. Sunucu adresi ve erişim bilgileri bu depoda YAZMAZ
> (Claude belleğinde, `trading-bot-stock-engine` kaydında).

## 1. Ne çalışıyor

İki hizmet, tek imaj, tek kod yolu (`python -u -m v5.runner`):

| hizmet | hesap | durum klasörü | emir gönderir mi |
|---|---|---|---|
| `trading-live` | gerçek | `/app/state_live/v5` | yalnız `LIVE_TRADING_ENABLED=true` iken; yoksa kuru çalışma |
| `trading-paper-livecfg` | deneme | `/app/state_paper/v5` | evet |

Bot günde bir kez, kapanıştan 10 dakika önce karar verir (olağan gün 15:50 ET, 12:50 PT). Kapanıştan 1 ile 5
dakika sonra günü kapatır ve sahibe günlük özeti yollar.

## 2. Ortam değişkenleri (adlar; değerler Coolify ortam deposunda)

`TRADING_MODE`, `ALPACA_KEY_PREFIX` (compose dosyası verir), `ALPACA_LIVE_API_KEY`, `ALPACA_LIVE_SECRET_KEY`,
`ALPACA_PAPER_API_KEY`, `ALPACA_PAPER_SECRET_KEY`, `NTFY_TOPIC` (sahibin telefonu), `NTFY_TOPIC_PAPER`
(deneme hesabının kendi konusu; sahibin telefonuna düşmez, Claude okur), isteğe bağlı: `LIVE_TRADING_ENABLED`
(yalnız `1`, `true`, `yes`, `on` açar), `STRATEGY_PACKAGE` (varsayılan `high`), `V5_OWNER_SYMBOLS` (sahibin
kendi tuttuğu semboller, virgülle; şu an `ANT`), `V5_ALERT_DD`, `V5_BREAKER_DAY`, `V5_FLOOR_DD`, `NTFY_BASE_URL`.
Host `.env` dosyasına satır eklerken `echo >>` KULLANMA (son satır yeni satırla bitmiyor); `printf "\n...\n"` kullan.

## 3. Günlük bakış (konteyner içinde)

```
python -m v5.health            # 0 sağlıklı, 1 aksak, 2 bloke; satır satır neden
python -m v5.health --json     # heartbeat_age_seconds, states, readiness
python -m v5.ops status        # ray durumu (zirve, taban durumu, duraklatma) + sağlık
python tools/v5_durum.py       # sahibe söylenebilecek sade Türkçe özet
```

Kayıtlar: `decisions/YYYY-MM-DD.json` (strateji), `decisions/YYYY-MM-DD.flr.json` (taban satışı),
`decisions_dryrun/` (kuru çalışma), `rails.json`, `overlay.json`, `outbox.jsonl`, `heartbeat.json`,
`calendar.json`, `runner.json`, `ops_log.jsonl`.

## 4. Operatör komutları

```
python -m v5.ops init-peak <deger> --source "<nereden>"          # bir kez; --force ile yeniden
python -m v5.ops pause  --by owner --note "<neden>"               # strateji emri durur, raylar çalışır
python -m v5.ops resume --by owner --note "<neden>"
python -m v5.ops clear-halt --by owner --note "<neden>" --new-peak <guncel hesap degeri>
python -m v5.ops decide-now --out /tmp/karar.json                 # şu an ne karar verirdi; EMİR GÖNDERMEZ
python -m v5.ops replay /tmp/karar.json                           # aynı girdiden aynı karar çıkıyor mu
```

Sahip "botu durdur" derse: `pause`. "Botu devam ettir" derse: önce durumun ne olduğuna bak. Taban durduysa
(`FLOOR_HALT`) `clear-halt` gerekir ve yeni zirve olarak O ANKİ hesap değeri verilir (yoksa ilk okumada yine durur).

## 5. Durumlar ve anlamları

| durum | ne demek | kendiliğinden geçer mi |
|---|---|---|
| `BREAKER_TODAY` | hesap bugün %15 düştü, bugün alım yok, satış serbest | evet, ertesi seans |
| `RAIL_CONFIRMING` | taban ilk kez aşıldı, ikinci okuma bekleniyor | evet, 60 sn sonra karar |
| `FLOOR_HALT` | taban teyit edildi, fon satıldı, bot durdu | HAYIR, `clear-halt` |
| `RAIL_LIQUIDATING` | taban satışı henüz bitmedi, her seans yeniden dener | satış bitince |
| `UNRESOLVED` | bir emrin akıbeti bilinmiyor, yeni emir yok | broker kanıtı gelince |
| `FOREIGN_ASSET` | hesapta botun tanımadığı varlık ya da emir var | varlık gidince |
| `PAUSED` | sahip durdurdu | `resume` |
| `PEAK_MISSING` | zirve hiç kaydedilmedi | `init-peak` |
| `NOTIFY_MISSING` | bildirim konusu yok: strateji emri yok | konu tanımlanınca |
| `CLOCK_DOUBT`, `MARKET_CLOSED` | saat ya da seans güvenilmez: emir yok | düzelince |
| `DRY_RUN` | canlı hizmet kuru çalışmada | bayrak açılınca |
| `STATE_CORRUPT`, `TICK_ERROR` | bir kayıt dosyası bozuk ya da döngü hata veriyor | elle incele |

`UNRESOLVED` için: `decisions/` altındaki kaydın `intents` listesine bak, Alpaca'da o `client_order_id` ile emir
var mı sorgula. Emir bulunursa bot kendisi kapatır. Emir hiç yoksa ve pozisyon değişmediyse kapanıştan sonra
bot kendisi FAILED yazar. Elle kayıt düzeltme son çaredir; önce konteyneri durdur.

## 6. Sahibin kendi hissesi (ANT)

Sahip 19 Kasım 2026'da canlı hesapta bot dışı bir alım istedi: 1 adet ANT, kâr al ve zarar kes bacaklı tek emir
(`owner-ant-20261119-1`, GTC bracket; fiyat seviyeleri bu herkese açık depoda değil, operatörün özel notlarında
durur). `V5_OWNER_SYMBOLS=ANT` iken bot bu hisseyi ve emirlerini görmezden
gelir, kendi emirlerini (hesap değeri eksi ANT değeri) üzerinden boyutlar; raylar bütün hesabı izler; günlük
özet "senin aldığın hisse (ANT)" diye ayrıca söyler. Hisse satılınca değişkeni kaldırmak gerekmez.
Sahip başka bir hisse alırsa sembolü bu değişkene ekle ve konteyneri yeniden oluştur; yoksa bot
`FOREIGN_ASSET` deyip bekler.

## 7. Deploy (R6) sırası

1. `main` dalına birleştir ve gönder (yalnız sahibin onayıyla). Coolify API ile deploy tetikle.
2. İki konteynerde imaj kimliği aynı mı, v4 dosyası yok mu, `python -m pytest tests_v5 -q -p no:cacheprovider
   --basetemp=/tmp/pt` imaj içinde geçiyor mu (ağ kapalı da geçmeli).
3. Deneme hesabı: v4'ten kalan emir ve pozisyonları temizle (`tools/v5_tatbikat.py --inventory`,
   `--flatten-foreign --yes`), `init-peak`, tatbikat (`--round-trip --duplicate-id --restart --notify`,
   borsa AÇIKKEN), ilk kararı izle. `--flatten-foreign` açığa satılmış pozisyonları da kapatır. Tatbikat adımları
   hesapta fon pozisyonu varken çalışmayı reddeder (kendi almadığını satmaz).
4. Canlı: hesap durumu (yalnız nakit + sahibin ANT'ı), `V5_OWNER_SYMBOLS=ANT`, `init-peak 491.08 --source
   "v4 kaydi"`, `decide-now` ile kuru karar, iki konteyner arasında çapraz `replay`.
   `decide-now` yalnız karar hazır VE `blockers` listesi boşken 0 döner (zirve yok, bildirim yok, duraklatma,
   taban, yabancı varlık, saat şüphesi, açık emir kaydı birer engeldir). `replay` emir planını da yeniden hesaplar.
   `python -m v5.ops notify-test` konteynerin KENDİ konusuna bir deneme mesajı yollar: sahibin telefonu canlı
   konteynerde böyle kanıtlanır (tatbikatın `--notify` adımı deneme hesabının konusuna gider).
5. Bekçi: `ops/vps/v5_watchdog.sh` -> `/root/v5_watchdog.sh`, konu dosyası `/root/.v5_watchdog_topic` (izin 600),
   cron `*/10 * * * * sh /root/v5_watchdog.sh` (çalıştırma izni gerekmesin diye `sh` ile). Bir konteyneri durdurup
   telefona uyarı geldiğini gör. Konteyner düzelince bekçi sayacını sıfırlar, sonraki kesinti yeniden bildirilir.
   Deneme hesabı konteynerinin mesajı "DENEME HESABI." ile başlar. Konu dosyası yoksa bekçi bunu günlüğüne yazar.
6. Sahibe SOR. Onay gelirse `LIVE_TRADING_ENABLED=true` (Coolify ortam deposu + host `.env`), canlı konteyneri
   AYNI imajla yeniden oluştur, ilk canlı kararı izle (emir, dolum, kayıt DONE, günlük özet).

Geri alma: bayrağı İKİ yerden de kaldır (Coolify ortam deposu ve host `.env`; biri kalırsa sonraki deploy
bayrağı geri getirir) ve konteyneri yeniden oluştur; `pause`; açık emir varsa iptal et. Pozisyon sahibin kararı
olmadan satılmaz. Bayrak kapalıyken taban aşılırsa bot satamaz, ama "DENEME, gerçek emir gönderilmedi." etiketiyle
her gün haber verir.

## 8. Testler

```
python -m pytest tests_v5 -q -p no:cacheprovider --basetemp=.pytest_tmp
```

Windows'ta yaklaşık 9 dakika sürer (çok sayıda dosya kaydı). `tests_v5/test_r*_saldirgan.py` denetçinin sözleşmeden
yazdığı sert testlerdir; inşa eden bunları inşadan önce görmez.
