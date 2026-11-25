# RF6-CALISMA-KILAVUZU.md , v5 işletme kılavuzu

> Kimin için: botu işleten Claude oturumu. Sahibe (İhsan) gösterilen her metin `RF-PLAN-6.md` Ek A'dan gelir;
> bu belge teknik bir kılavuzdur, sahibe okutulmaz. Sunucu adresi ve erişim bilgileri bu depoda YAZMAZ
> (Claude belleğinde, `trading-bot-stock-engine` kaydında).

## 1. Ne çalışıyor

İki hizmet, tek imaj, tek kod yolu (`python -u -m v5.runner`):

| hizmet | hesap | durum klasörü | emir gönderir mi |
|---|---|---|---|
| `trading-live` | gerçek | `/app/state_live/v5` | yalnız `LIVE_TRADING_ENABLED=true` iken; yoksa kuru çalışma. 25 Kasım 2026 16:31 UTC'den beri AÇIK |
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
R5d'den beri `clear-halt` yazılan zirveyi hesapla karşılaştırır: hesap okunamıyorsa ya da değer, hesap değeri ile
onun üç katı arasında değilse REDDEDER ve hiçbir şeyi değiştirmez (`--force` bu denetimi atlar).
`decide-now` karar penceresinin (kapanıştan 10 dakika önce başlar, 4 dakika önce biter) DIŞINDA 1 döner ve `blockers`
listesinde `OUTSIDE_DECISION_WINDOW` yazar; o gün karar verildiyse `ALREADY_DECIDED`. Karar ve emir planı
yine belgede durur, yani pencere dışında da "şu an ne yapardı" okunabilir.

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

Çalışan sistemi yeni sürüme yükseltme (20 Kasım'daki dördüncü kurulumda uygulanan sıra):
1. İki konteynerin kayıt klasörünün kopyasını al (depoya KOYMA, hesap değerleri içerir). Yeni sürümü
   simülatörde bu kopyanın üstünde hafta sonu ve sonraki iki seans boyunca yürüt: hiçbir mesaj ikinci kez
   gitmemeli, her dosya kabul edilmeli, elde tutulan pay kurala göre satılmalı ya da tutulmalı.
2. Yayınlanacak ağacın tamamını ayrı bir imajda, ağ kapalıyken sına. Yayının ekleyeceği her satırı anahtar,
   kanal adı, adres ve sahibin emir seviyeleri için tara (seviyelerden türeyen sayılar da sayılır: nakit ile
   alım gücü arasındaki fark gibi).
3. Kurulumu bekçinin on dakikalık turundan HEMEN SONRA başlat (kurulum yaklaşık 80 saniye sürer). Tur tam o
   boşluğa denk gelirse sahibe bir kez "bot çalışmıyor" mesajı gider. Başlamadan önce iki konteynerde
   bekleyen sahip mesajı olmadığına bak (`python -m v5.health --json` içinde `outbox_oldest_minutes` boş
   olmalı): bekleyen bir mesaj, yazıldığı sürümün cümleleriyle gider (`RF-ISSUES-6.md` madde 36).
4. Kurulumdan sonra iki konteynerde: değişen dosyaların özeti yayınlanan ağaçla aynı mı, kayıt dosyalarının
   değişme zamanı aynı kaldı mı (zirve, kapanış satırları, ek pay, `started_at`), kalp atışı taze mi,
   `python -m v5.health --json` ne diyor (deneme 0; canlı kuru çalışırken 1 ve `DRY_RUN`, bu beklenen durumdur),
   günlükte yalnız başlangıç satırı mı var, iki kanala da yeni mesaj gitmedi mi.
5. Bekçi betiğini imajdan hosta kopyala, özetini depodakiyle karşılaştır, `sh -n` ile sına, eskisini yanında
   bırak, bir kez elle koş (sessiz kalmalı, günlük ve sayaç dosyası oluşmamalı).
6. Sunucudaki geçici imajı, klasörü ve kayıt kopyasını sil.
7. Yükseltmeden sonraki İLK SEANSTAN ÖNCE, eski sürümün yazdığı son kapanış satırını aracının resmi
   kapanışıyla karşılaştır (hesabın `last_equity` değeri, `balance_asof` o günü adlandırdıktan sonra; ya da
   günlük portföy geçmişi). Yeni sürüm bir kayıt alanına yeni anlam verdiyse eski satırlar bu anlamı
   taşımaz: 21 Kasım'da R5d'nin işaretsiz kapanış satırı hiç onaylanmayacaktı ve ertesi günün özeti günlük
   değişimi ona göre ölçecekti (`RF-ISSUES-6.md` madde 35).

Ray dosyasına elle dokunma kuralı: `rails.json` üstüne dosya KOPYALANMAZ (okuyanlar kilit almaz, yarım
dosya görebilirler). Değişiklik motorun kendi kilitli, doğrulamalı, tek hamlelik yazımıyla yapılır (`python
-m v5.ops ...` komutları, ya da 21 Kasım'daki gibi `Rails._change` kullanan küçük bir betik); önce yedek
alınır, betik önce yalnız bakar, hangi hesabı ve hangi değeri beklediğini söyler, geri alma yolu da aynı
kilitten geçer.

Canlı bayrak 25 Kasım 2026'da böyle çevrildi (6. adım; kayıt `RF6-DEVAM.md`, betikler operatörün özel
klasöründe, çünkü sunucunun yollarını adlandırırlar):
1. Önce kanıt: gerçek hesabın kayıt klasörünün kopyası üstünde, bayrak farklı anlarda açılarak prova; sonra
   bağımsız denetçiler; sonra betiğin kendisi sahte bir sunucuda hata durumlarıyla.
2. Betik önce yalnız bakar (hiçbir şeyi değiştirmeyen kipi vardır): tek canlı konteyner, beklenen imaj, seans
   günü ve saat, çalışan bir kurulum yok, dosyada ve depoda bayrak yok, çalışan konteynerin ortamı dosyaların
   vereceği ortamla AYNI (yalnız adlar yazdırılır), motor düz kuru durumda ve `session` evresinde.
3. Sıra: ortam deposu (sonraki kurulum bayrağı korusun), host `.env` (yedek, son satırın yeni satırla
   bitmediğini unutma), bekçinin on dakikalık turundan 1 ile 6 dakika sonra YALNIZ canlı konteyner
   `docker compose ... up -d --no-build --no-deps trading-live` ile aynı imajdan yeniden oluşturulur.
4. Doğrulama: ortamın bayrak dışındaki özeti aynı, komut / günlük / ağ / bağlar aynı, deneme konteyneri
   dokunulmamış, yeni sürecin `session` evresinde TAM bir turu (ilk kalp atışı tur bitmeden yazılır, o
   sayılmaz), sağlık 0, başlangıç satırında `live_trading_enabled=True`, mesaj kuyruğa girmemiş, ray dosyası aynı.
5. Bayrağın yönü hangi yöne olursa olsun şu anlarda DEĞİŞTİRİLMEZ: karar penceresinde, ve karar bitiminden
   (kapanıştan 4 dakika önce) günün özeti gidene kadar. O arada öbür ayarla başlayan süreç gün için kendi
   türünden kayıt bulamaz ve "karar saatini kaçırdım" der (`RF-ISSUES-6.md` madde 38).

Geri alma: bayrağı İKİ yerden de kaldır (Coolify ortam deposu ve host `.env`; biri kalırsa sonraki deploy
bayrağı geri getirir) ve konteyneri yeniden oluştur; `pause`; açık emir varsa iptal et. Pozisyon sahibin kararı
olmadan satılmaz. Bayrak kapalıyken taban aşılırsa bot satamaz, ama "DENEME, gerçek emir gönderilmedi." etiketiyle
her gün haber verir. Bayrak kapalıyken elde fon varsa kuru süreç onu SATMAZ, yalnız ne yapacağını söyler;
bayrak yeniden açılınca kural kaldığı yerden satar (25 Kasım provasında görüldü). Geri alma yalnız sahibin
sözüyle ya da para kaybettiren açık bir arızada yapılır.

## 8. Testler

```
python -m pytest tests_v5 -q -p no:cacheprovider --basetemp=.pytest_tmp
```

Windows'ta yaklaşık 9 dakika sürer (çok sayıda dosya kaydı). `tests_v5/test_r*_saldirgan.py` denetçinin sözleşmeden
yazdığı sert testlerdir; inşa eden bunları inşadan önce görmez.
