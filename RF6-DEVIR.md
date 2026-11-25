# RF6-DEVIR.md , v5 "Yenileme" devir notu

> İlk yazım: 16 Kasım 2026 09:45 PT. Son güncelleme: 25 Kasım 2026 08:45 PT (gerçek emir gönderme AÇILDIKTAN sonra).
> Okuma sırası: önce bu belge, sonra `RF6-DEVAM.md` (kurulum kayıtları), `RF-PLAN-6.md` (Revizyon 16), gerekirse
> `RF6-VTO.md`, `RF6-PROBE.md`.
> Bu belge emir listesi değildir. Niyeti ve kanıtı aktarır. Daha iyi bir yol görürsen onu seç,
> yalnız DEĞİŞMEZLER bölümüne dokunma.

## NİYET

İhsan aylardır kazanç üretmeyen Alpaca botunun baştan sona yenilenmesini istedi: tam otomatik, akıllı
yöntem, risk artabilir, mümkünse iki yön. Ölçüm şunu gösterdi: eski sistemin (v4) üstünlüğü yoktu,
parasının çoğu boşta bekledi ve üç kez sessizce dondu. v5 tek bir doğrulanmış kuralı uygular
(QQQ sinyali, TQQQ + nakit, oynaklık hedefi + bir günlük düşüş alımı), günde bir kez kapanışa yakın
karar verir, sessizce donamaz ve sahibine sade Türkçe ile haber verir.
Dürüst beklenti: düzenli günlük ya da aylık kazanç garanti edilemez. Yüksek pakette 490 dolar için
tipik yıl yaklaşık 60-75 dolar artı, kötü yılda derin düşüş olağandır.

## DEĞİŞMEZLER (bağlayıcı)

1. Sahip kararları (`RF6-VTO.md`, 16 Kas ~08:05 PT): paket YÜKSEK (hedef oynaklık 0,30). Düşüşte NAKİT,
   ters fon yok. Felaket tabanı zirvenin %60 altı (zirve 491,08 dolar, taban 196 dolar), düşüş uyarısı
   %37, günlük kesici %15 (kesici satmaz, yalnız o gün alımı durdurur). VPS tam kesinti riski kabul edildi.
2. "Doğrudan gerçek para" seçildi, ama canlı bayrağı (`LIVE_TRADING_ENABLED`) çevrilmeden HEMEN ÖNCE
   İhsan'a bir kez daha sorulacak. Bu söz verildi. Onay olmadan canlı emir yok. (25 Kasım 2026'da soruldu,
   cevabı "evet ac"; bayrak o gün 16:31 UTC'de çevrildi. Bayrağı KAPATMAK da sahibin kararıdır; tek
   istisna para kaybettiren açık bir arızadır, o zaman da hemen haber verilir.)
3. Yaklaşık 490 dolar tek paradır. Üstüne para eklemeyi asla önerme.
4. Gizlilik: `.env`, `master.env`, `credentials/` içeriği hiçbir yere gönderilmez ve okunmaz. Depo HERKESE
   AÇIK: fixture'lar maskeli kalır, ntfy konusu hiçbir dosyaya, loga ya da commit'e yazılmaz, anahtar asla
   ekrana basılmaz.
5. İhsan ile konuşma dili kök `CLAUDE.md` kurallarına uyar (kısa sade Türkçe, teknik kelime yok).
   Teknik kararları Claude verir, İhsan'a yalnız ürün ve iş kararı sorulur.
6. Kural (planın THE RULE bölümü) ve `tests_v5/golden/` dosyaları elle değiştirilmez.
7. İnşa eden ile denetleyen ayrıdır. Her rock yazılı sözleşmeden inşa edilir. Denetçi (ana oturum) her
   satırı okur, kanıt komutunu kendi koşar ve kendi saldırgan test paketini SÖZLEŞMEDEN yazar. İnşa eden,
   denetçinin saldırgan paketini inşadan önce görmez (bu yüzden o paketler depoda `v5-yenileme` dalında
   DEĞİL, aşağıdaki ayrı yerde durur).
8. Deploy, `main` dalına birleştirme ve canlı bayrak sahibin onayına bağlıdır. v4 konteynerlerine
   R6 adımlarına gelmeden dokunulmaz (canlı v4 kendi kendini kilitlemiş durumda, hesap %100 nakit).

## DURUM (25 Kasım 2026, gerçek emir gönderme açıldıktan sonra)

### Doğrulandı (kanıtıyla)

- Bütün rock'lar yazıldı ve `v5-yenileme` dalında commit edildi: R2 `ca44bfe`, R1 `9659901`, R4 `e5bb52e`,
  R3 `ca9999c`, R3b (sahibin kendi hisseleri) `f0075a4`, R5 + v4'ün `_legacy/v4/` altına taşınması `368e1f1`,
  hesap etiketleri + deneme hesabı konusu `39cd299`, paketleme `0155d30`.
- Bağımsız gözden geçirme yapıldı (taze bir Claude yardımcısı, salt okuma, kendi deneme düzeneğiyle): R5'te
  12 açık buldu, hepsi kanıtlıydı (3'ü ciddi: karar verilip gönderilmeyen emrin sessiz kalması, yarım kalmış
  emrin kapıları yeniden sınanmadan gönderilebilmesi, bir gün sonraki tek ölçümün satış başlatabilmesi).
  Hepsi aynı gün kapatıldı: plan Revizyon 11, `RF-ISSUES-6.md` madde 25, commit `67045c8`.
  Her açık için test var (`tests_v5/test_r5b_inceleme.py`, 49 test; hepsi eski kodda kalıyor). Gözden geçirenin
  kendi senaryoları düzeltmeden önce ve sonra koşuldu, çıktıları ve betikleri lab `rf6\review_r5\` altında.
- Gözden geçirenin rastgele senaryo üreticisi (`fuzz2.py`) aynı tohumlarda: düzeltmeden önce 35 ihlal, düzeltme
  turundan sonra 4 (hepsi "kapanışı kaçırılan seans raporsuz kaldı"), telafi kuralı genişletilince (commit
  `b4fed00`: en çok beş seans geriye, en eskiden başlayarak, yalnız yeni seansın kapanış adımı vazgeçer) 0.
- Kanıt: `python -m pytest tests_v5 -q -p no:cacheprovider --basetemp=.pytest_tmp` -> 764 geçti (Windows).
  Aynı paket sunucuda, imajın içinde, ağ kapalıyken de 764 geçti (Linux; ayrı `v5-pretest:branch` imajı,
  çalışan konteynerlere dokunulmadı). İmajda yalnız v5, iki araç, bekçi ve testler var; v4 ve `.env` yok.
  Denetçinin saldırgan paketleri: R1 146, R2 10, R3 94, R4 84, R5 77, gözden geçirme 49 test.
- Ölçüm (19 Kas 20:16 ET, deneme hesabı, salt okuma): Alpaca'nın `last_equity` değeri kapanıştan 4 saat sonra
  hala BİR ÖNCEKİ seansın kapanışı (`RF6-PROBE.md` sonu). Kapanış değeri kuralı buna dayanıyor.
- Kim yazdı: R1, R2, R4 Codex; R3 Codex (son doğrulamada kotası doldu, Claude devraldı: 6 inceleme düzeltmesi);
  R3b ve R5 Claude (sahip: "limit dolarsa devral"). R5'in test paketi koddan ÖNCE sözleşmeden yazıldı.
- Gerçek veri provası (19 Kas, salt okuma, emir yok): yeni kod gerçek Alpaca verisiyle uçtan uca çalıştı
  (260 kapanış, IEX fiyatı ve gün içi aralık, hesap okuması, anlık görüntü). O günün kararı: eğilim aşağı,
  IBS 0,098 -> bir günlük 1/3 ek alım.
- Plan Revizyon 9 (pay dağıtımı defter kuralı, Codex'in BLOCKED bulgusu) ve Revizyon 10 (sahibin kendi
  hisseleri, harcanabilir para, E2E-B sınırı, hesap etiketleri) plana işlendi.
- Sunucu (19 Kas): v4 CANLI konteyner durduruldu, 3 v4 cron satırı yorum yapıldı (yedek host
  `/root/crontab.backup.20261119`). Coolify ortam deposuna `V5_OWNER_SYMBOLS=ANT` ve `NTFY_TOPIC_PAPER`
  eklendi (konu adı hostta `/root/.v5_paper_topic`, izin 600). v4 paper konteyner hala çalışıyor.
- Sahibin ANT emri canlı hesapta bekliyor: 1 adet, kâr al ve zarar kes bacaklı GTC bracket,
  `owner-ant-20261119-1`. 20 Kas açılışında dolması beklenir. Fiyat seviyeleri bu herkese açık depoda değil,
  Claude belleğinde (`trading-bot-stock-engine`) durur. (Emir 20 Kasım'da doldu; hisse 24 Kasım'da sahibin
  kendi zarar kes emriyle satıldı, aşağıya bak.)

- 19 Kas akşamı KURULDU (sahip onayı: "Başlat"). Kayıt: `RF6-DEVAM.md`. Canlı konteyner KURU çalışıyor (emir
  göndermez), deneme hesabı konteyneri gerçek gibi çalışıyor. `main` iki commit aldı (`96d510b`, `7196c39`);
  her biri çalışma dalının ağacının tek commit'lik kopyası. Çalışma dalı (`v5-yenileme`) ASLA push edilmez.
- Codex'in yarım kalan incelemesinden çıkan bulgular (R5c) Codex'e yazdırıldı, Claude denetledi: commit
  `536ddcf`, plan Revizyon 12, 795 test (Windows ve sunucuda imaj içi).

- 19 Kas gece R5d: denetçinin ikinci turu (9 bulgu) + Codex'in tam denetimi (10 bulgu, 3 plan sorunu)
  kapatıldı. A-F gruplarını Codex yazdı (kanıt koşusunda kotası bitti), G1-G6'yı ve eski testlerin
  güncellemesini Claude yaptı. Commit `e71decf`, `main` `2bf6d0e`, plan Revizyon 13, 855 test (sunucuda imaj
  içi). Üçüncü kurulum 23:10 PT: iki konteyner de kayıtlarını koruyarak yeni sürümle çalışıyor.

- 20 Kas sabahı (R5d ile): açılış kontrolleri, sahibin ANT emrinin dolması, deneme hesabı temizliği ve tatbikat
  (4/4 geçti), `decide-now` + çapraz `replay`, `last_equity` ölçümleri (`RF6-PROBE.md` sonu): hepsi temiz.
- 20 Kas İLK KARAR GÜNÜ (R5d ile): iki bot aynı kararı verdi (eğilim aşağı, sert düşüş günü: 1/3 ek alım).
  Deneme hesabı TEK emirle aldı, emir tam doldu; canlı hesap emir göndermedi (DRY_RUN kaydı). İki özet ilk
  denemede gitti; her rakam kayıtlardan yeniden hesaplandı. Günlüklerde hata yok, bekçi hiç konuşmadı.
- 20 Kas R5e: altı denetçili taramanın bulguları (56 bulgu ayıklandı, 33 + 9 + 5 madde) kapatıldı. İlk iki
  turu Codex yazdı, son beş küçük maddeyi kotası bitince Claude yazdı. Commit `68a156f`, plan Revizyon 14,
  937 test (Windows ve sunucuda imaj içi). `RF-ISSUES-6.md` madde 31 ve 32.
- 20 Kas DÖRDÜNCÜ KURULUM (13:30 PT, seans kapandıktan sonra): `main` = `42258c5` (dalın `b91fcaf` ağacı).
  Kurulumdan ÖNCE iki kayıt klasörünün kopyası simülatöre konup yeni sürüm hafta sonu, pazartesi ve salı
  boyunca yürütüldü (hiçbir mesaj ikinci kez gitmedi, ek pay tek emirle satıldı); ayrıca dört bağımsız denetçi
  geçişin kendisini inceledi: engel yok (`RF-ISSUES-6.md` madde 33). Kurulumdan sonra iki konteynerde dosya
  özetleri, kayıtlar, kalp atışı, sağlık raporu ve günlük doğrulandı; bekçi betiği hostta yenilendi.
- 20 Kas R5f ve BEŞİNCİ KURULUM (14:30 PT): geçiş denetiminin üç küçük bulgusu ve o tur okunurken çıkan
  dördüncüsü kapatıldı: şüphe uyarısı "güvenlik satışı yapmadım" diyor ve bot güvenlik satışı yapmışken
  (HALT) hiç gönderilmiyor; düzeltilen kapanış mesajı özetin tarihini söylüyor; sağlık aracı mesaj dosyasını
  okuyamasa da cevap veriyor. Codex aracı o öğleden sonra bu makinede açılır açılmaz kapandı (6 denemenin 5'i,
  çıkış kodu 127), turu Claude yazdı; testler koddan ÖNCE sözleşmeden yazıldı ve eski kodda kaldı; taze bir okuyucu
  farkı iki kez okudu. Commit `2cfead1`, `main` = `405d2fc`, plan Revizyon 15, 946 test (Windows ve sunucuda
  imaj içi). `RF-ISSUES-6.md` madde 33 ve 34.
- 21 Kas HAFTA SONU KONTROLÜ ve KÜÇÜK OPERATÖR ADIMI (15:45 PT): iki konteyner kurulumdan beri kesintisiz
  çalışıyor, kodları yayınlanan ağaçla aynı (özetle karşılaştırıldı), bekçi sessiz, hiçbir mesaj gitmedi.
  Bulunan: R5d'nin 20 Kasım için yazdığı kapanış satırları aracının RESMİ kapanışıyla aynı değil (deneme
  61.900,58 yerine 61.825,47; gerçek hesapta bir doların küçük bir kesri). Güvenlik kuralları etkilenmiyordu
  ama pazartesi özeti "bugünkü değişim"i eski satıra göre ölçecek ve hiç oynamayan hesap için "arttı"
  ya da "azaldı" diyecekti. Çözüm: iki satıra yalnız `"provisional": true` işareti kondu (R5e'nin kendi
  satırlarına verdiği biçim); motor pazartesi ilk fren adımında satırı aracının değeriyle kendisi
  değiştirecek. Deneme hesabının zirvesi olağan komutla resmi kapanışa çekildi. Önce gerçek kayıtların
  kopyasında prova (28 durum), sonra üç bağımsız denetçi (engel yok), sonra uygulama ve geri okuma.
  Yedekler sunucuda (konteyner dışında). Geri alma yolu ve bütün ayrıntı `RF6-DEVAM.md` 21 Kasım bölümünde,
  `RF-ISSUES-6.md` madde 35.

- 23 Kas YENİ KODUN (R5e + R5f) İLK SEANSI TEMİZ. Sabah, ilk fren adımında (14:30:05 UTC) motor 20 Kasım
  satırlarını aracının resmi kapanışıyla KENDİSİ onayladı: işaret kalktı, deneme satırı aracının değeri
  (61825.4749575993), zirveler aynı, hiçbir mesaj gitmedi. 15:50 ET'de iki bot aynı kararı verdi (eğilim
  aşağı, ikinci düşüş yok): deneme botu 20 Kasım'da aldığı ek payı TEK emirle sattı (499,318566932 pay, dolum
  49,10; alışı 41,31 idi), gerçek hesap emir göndermedi (`NO_TRADE`, kuru; aracıda botun emri yok). İki özet
  ilk denemede gitti. Kararların her alanı kayıtlı girdilerden, özetlerin her rakamı kayıtlardan yeniden
  hesaplandı: hepsi tutuyor ve "Bugün ..." cümlesi ONAYLANMIŞ satırlara göre ölçülmüş. Pazartesi satırları
  "provisional" yazıldı, zirveler değişmedi, fren ve taban temiz. Günlüklerde yalnız üç olağan satır, bekçi
  hiç konuşmadı. Gerçek hesapta 1 Kasım'dan beri botun kimliğini taşıyan tek emir yok (aracıdan okundu).
  Üç bağımsız denetçi kanıtın kopyasını koda karşı okudu (kayıt dosyaları, rakamlar ve cümleler, aracı
  kayıtları ve önümüzdeki günler): ENGEL YOK. Buldukları bir ifade kusuru (`RF-ISSUES-6.md` madde 36) ve bir
  gözlem listesi (madde 37). Kayıt: `RF6-DEVAM.md`, 23 Kasım'ın iki bölümü.

- 24 Kas SABAHI: motor pazartesi satırlarını ilk fren adımında aracının kapanışıyla onayladı. Deneme
  hesabında zirve İLK KEZ kendiliğinden yükseldi (65769.57; pazartesi satışının 0,52 dolarlık düzenleyici
  ücreti gece işlendiği için 65770.09 değil; `peak_source` ve `peak_time` eski elle ayarı gösteriyor, bu
  olağan). Gerçek hesapta zirve 491,08 kaldı. Hiçbir mesaj gitmedi.
- 24 Kas SAHİBİN HİSSESİ SATILDI: sahibin kendi zarar kes emri 14:38 UTC'de çalıştı, aracı öbür çıkış emrini
  iptal etti. Bu botun emri değil. Motor sessiz kaldı (yazı yok, mesaj yok, hazırlık durumu temiz). Gerçek
  hesap yeniden tamamı nakit; botun payı artık hesabın tamamı. `V5_OWNER_SYMBOLS` olduğu gibi kalabilir.
  Hisse takibi bitti. Rakamlar Claude belleğinde.
- 25 Kas SABAHI: salı satırları ilk fren adımında onaylandı, zirveler aynı, mesaj yok. Sahip yazdı: durumu
  kontrol et, düzeltilecekleri düzelt, gerçek paraya aç.
- 25 Kas GERÇEK EMİR GÖNDERME AÇILDI (16:31 UTC, seans içinde, karar penceresinden 4 saat 19 dakika önce).
  Önce gerçek kayıtların kopyasında on senaryoluk prova, üç bağımsız denetçi (engel yok; prosedür için
  istenen altı düzeltme yapıldı), betiğin sahte sunucuda seksenden çok hata durumuyla sınanması, sahibe
  HEMEN ÖNCE son soru (cevap: "evet ac"). Bayrak ortam deposunda, host `.env` içinde ve çalışan canlı
  konteynerde; YALNIZ canlı konteyner aynı imajla yeniden oluşturuldu; bayrak dışındaki ortamı, komutu ve
  bağları aynı; deneme konteynerine dokunulmadı. Yeni süreç: `session`, durum listesi boş, emirler açık,
  sağlık 0; hiçbir mesaj gitmedi, ray dosyası aynı. Aracı: gerçek hesap tamamı nakit, emir yok. Kayıt:
  `RF6-DEVAM.md` 25 Kasım bölümleri, `RF-ISSUES-6.md` madde 38, kılavuz bölüm 7.
- 25 Kas R5g YAZILDI (madde 36, plan Revizyon 16): sahibe giden on bir cümle artık "yarın / dünkü / bir gün
  daha" demiyor, işlem gününü söylüyor. Codex o sabah kapalıydı, taze bir Claude yardımcısı sözleşmeden
  yazdı; denetçi paketleri inşadan önce yeni cümlelere çekildi; 964 test (Windows); taze okuyucu ve (kotası
  dönünce) Codex okudu. Commit `4156448` + `df2ee2a`. HENÜZ KURULMADI: sunucuda `405d2fc` (R5f) çalışıyor.

### Borç (yapılmadı ya da doğrulanmadı)

- Bu hafta takvimin iki özel günü sunucuda ilk kez yaşanacak: perşembe 26 Kasım kapalı, cuma 27 Kasım yarım
  gün (kapanış 13:00 ET, karar 12:50 ET). Cuma sabahı aracının son kapanış için hangi günü adlandırdığına
  bak (26'sını adlandırırsa çarşamba satırı onaylanmaz; güvenli ama bilinmeli). Cuma akşamı karar kaydında
  gün içi aralığın kapsamına bak.
- R5g'nin KURULUMU: kod ve testler hazır (yukarıda), sunucuya alınmadı. Seans DIŞINDA, kılavuz bölüm 7
  sırasıyla (kopya üstünde prova, imaj içi test, yayın taraması, bekleyen mesaj yok, on dakikalık işaretin
  hemen ardı). Kurulumdan sonra canlı motorda emirlerin AÇIK kaldığını doğrula (bayrak depoda durmalı).
  Kurulana dek özetler eski cümleleri kullanır: "yarın" bir sonraki işlem günü demektir.
- İlk gerçek emir: bayrak açıldıktan sonraki ilk alım ya da satım, gerçek hesapta emir çağrılarının İLK
  kez çalıştığı andır (okuma çağrıları altı gündür çalışıyor). İzle: kayıt, emir, dolum, özet, aracı.
- Codex 25 Kasım 16:03 UTC'den beri yeniden çalışıyor (20 Kasım'da açılır açılmaz kapanmış, sonra haftalık
  sınırı dolmuştu). Kod işini o yazar, Claude denetler; her kod işinden önce küçük bir denemeyle sına;
  çalışmıyorsa sahibin kuralı geçerli: Claude devralır, testi koddan önce sözleşmeden yazar ve farkı taze bir
  okuyucuya okutur.
- Bildirim kanalı konusu sahip tarafından ertelendi; ayrıntı operatörün özel notlarında.
- Canlıya geçişin ardından açık kalan notlar `RF-ISSUES-6.md` madde 38'de (bekçinin ağır durumları
  bildirmemesi, fren gününde özetin son satırı, emir reddinin kayıttaki kısa biçimi).
- Sahibin geliştirme listesinden seçimi bekleniyor (sunucu dışı ikinci bekçi, boşta nakde faiz, anahtar
  yenileme, bölünme koruması, günlük karşılaştırma, sunucu dışı yedek).
- Kabul edilen sınırlar: `RF-ISSUES-6.md` madde 26 ve plan Revizyon 14'ün "accepted limits" listesi.

## YAKLAŞIMIM (öneri)

Sıra: bağımsız gözden geçirme bulgularını kapat -> `main`'e al ve kuru çalışmayla deploy et (gerçek hesap emir
göndermez) -> imaj içi testler -> `init-peak` + bekçi -> ertesi sabah borsa açıkken deneme hesabı temizliği ve
tatbikat -> ilk paper kararını izle -> Codex tüm ağaç denetimi -> sahibe sor -> canlı bayrak.
İnşa eden: Codex kotası varsa Codex, dolarsa Claude devralır (sahibin açık talimatı, 19 Kas).
İşletme komutları `RF6-CALISMA-KILAVUZU.md` içinde.

## NEREDE YANILMIŞ OLABİLİRİM

- `v5/runner.py` ve onun test paketi aynı elden çıktı (Claude). Bu kaygı doğrulandı: 712 test geçerken
  bağımsız göz 12 açık buldu. Düzeltmeleri de ben yazdım; gözden geçirenin senaryoları temiz çıkıyor ama
  düzeltmelerin kendisi henüz ikinci bir gözden geçmedi. Codex denetimi bu yüzden canlıdan önce şart.
- E2E-B'de "altın yola yakınlık" sınırını %99'dan %98'e indirdim (bant kenarı yol bağımlılığı). Gerekçe ölçümle
  destekli ama sınırı gevşeten de benim.
- Sahibin hissesi için alım, kâr al ve zarar kes seviyelerini ben seçtim. Sahip yalnız "1 hisse,
  yarın açılışta, takibini sen yap, kârımızı koyalım" dedi.
- Raylar bütün hesabı izliyor (sahibin kendi hissesi varsa o da dahil). Öyle bir hisse sert düşerse günlük
  kesici o gün botun alımını durdurabilir. (24 Kasım'dan beri hesapta sahibin hissesi yok.)
- Kesicinin taban çapraz kontrolü (%2) yarım günlerde ve para giriş çıkışında yanlış "veri şüphesi" verebilir.
- Kapanıştan sonra hesap okunamazsa günlük özet geç gelir ya da o gün hiç gelmez.
- Deneme hesabının mesajları artık sahibin telefonuna düşmüyor (ayrı konu). O konuyu yalnız Claude okur;
  oturum açılmazsa deneme hesabındaki bir sorun fark edilmeyebilir (bekçi yalnız sürecin canlılığını izler).
- R5f'yi yazan da denetleyen de bendim (Codex çalışmadı). Taze okuyucu bir açık buldu ve o kapatıldı; yine de
  bu turun ikinci gözü öncekiler kadar bağımsız değildi.
- R5e büyük bir tur (33 + 9 + 5 madde). 23 Kasım'da sunucuda ilk seansını gerçek aracının cevaplarıyla temiz
  geçirdi, ama bir seans her yolu denemez. Görülenler: kapanış satırının onayı (eşit değer ve küçük fark), ek
  payın satışı, kuru "işlem yok" günü, iki özet, zirvenin kendiliğinden yükselmesi (24 Kasım). Sunucuda
  henüz GÖRÜLMEYENLER: ana pay alımı (eğilim yukarı dönünce), günlük fren, taban, uyarı, düzeltme mesajı,
  kısa gün (27 Kasım), gerçek hesapta gerçek emir (bayrak 25 Kasım'da açıldı, ilk emir kuralın ilk işlem
  gününde gider).
- Canlı geçişi planlayan da uygulayan da bendim. Üç bağımsız denetçi ve betiğin sahte sunucudaki sınaması
  hatalarımı yakaladı (betiğin "doğrulandı" demesi için yeterli olmayan ilk kalp atışı gibi), ama onların
  sahte araçları gerçek sunucunun birer modeliydi. Gerçekte görülen: bir kez, sorunsuz.
- Bayrağı seans İÇİNDE çevirdim; 23 Kasım notumda "seans dışında" yazıyordu. Gerekçe prova ile değişti
  (`RF6-DEVAM.md` 25 Kasım): gün içi geçişte o günün kuru kaydı yok ve ilk gerçek karar olağan bir günde,
  ben izlerken veriliyor. Bedeli: ilk gerçek alım sert düşüş gününe ve tatil öncesine denk gelebilir
  (sahibe son soruda söylendi).
- R5g'yi Codex yerine taze bir Claude yardımcısı yazdı (Codex o saatte kapalıydı), denetleyen bendim. Değişiklik
  yalnız cümle; Codex sonradan salt okuma ile baktı. Yine de "işlem günü" sözünün sahibe sade gelip
  gelmediğini sahibin kendisi söyler.
- R5d'nin 20 Kasım satırları için "etkisi yok denecek kadar küçük" demiştim; yanılmışım: fark pazartesi
  özetinde olmamış bir değişimi söyletecekti. 21 Kasım'da ölçüldü ve satırlar işaretlenerek kapatıldı
  (yukarıda). Ders: "küçük" dediğim bir izi sayıyla ölçmeden kapatma.
- 21 Kasım adımını planlayan da uygulayan da bendim. Sonucu 23 Kasım sabahı gerçek aracının cevabıyla
  görüldü: provadaki gibi. O adımdan geri alınacak bir şey kalmadı.
- 23 Kasım akşamının kontrolünü yapan da bendim; kanıtın kopyasını üç bağımsız denetçi ayrıca okudu
  (`RF6-DEVAM.md`). Yine de "temiz" sözü yalnız o gün yaşanan yollar için geçerli.

## SIRADAKİ SOMUT ADIM

1. `git -C trading status` ve `git log --oneline -8` ile durumu doğrula. Kanıt komutunu koş (964 geçmeli;
   sunucuda R5g kurulana dek 946 testlik `405d2fc` çalışır).
2. Sahibin ANT hissesi 24 Kasım'da kendi zarar kes emriyle satıldı; takip edilecek hisse kalmadı. Sahip
   yeniden kendi adına bir hisse alırsa kural aynı: her oturumda bak
   (`C:\Users\ihsan\trading_lab_v5\scripts\ant_durum2.py <depo>`, salt okuma), kardaysa zarar kes seviyesini
   alışın üstüne çekmeyi ÖNER (kendiliğinden değiştirme).
3. Sunucuda iki konteynerin son seansına bak (`RF6-DEVAM.md` içindeki "first decision day" bölümü örnek:
   karar kaydı, emir, dolum, özet metni, raylar, günlük, bekçi). Sonucu `RF6-DEVAM.md` dosyasına yaz.
   Her sabah ilk bakışta önce iki ray dosyasında bir önceki seansın satırına bak: işaret kalkmış ve değer
   aracının resmi kapanışı olmalı. Akşam iki özetin "Bugün ..." cümlesini bu satıra göre hesapla. Salt okuma
   araçları: lab `scripts\sunucu_bak.sh` (sunucuya dosya bırakmaz), `scripts\piyasa_bak.py`,
   `scripts\last_equity_olcumu2.py`.
4. GERÇEK EMİR GÖNDERME 25 Kasım 2026 16:31 UTC'den beri AÇIK. Her oturumda önce canlı konteynere bak:
   `orders_enabled true`, sağlık 0, durum listesi boş; sonra aracıdaki pozisyonu ve nakdi botun kaydıyla
   karşılaştır (salt okuma: lab `scripts\canli_emirler.py <depo> <gün>`). Bot bir emir gönderdiyse kaydın
   `intents` listesini, dolumu ve özet metnini oku. Kendiliğinden satış, emir iptali ya da bayrağı kapatma
   YOK: bunlar sahibin kararı (tek istisna para kaybettiren açık bir arıza; geri alma betiği lab
   `rf6\canli_gecis_20261125\`, satış yapmaz). Canlı konteyner karar penceresinde ve günün özeti gidene
   kadar yeniden başlatılmaz.
5. R5g'yi seans dışında kur (`RF6-CALISMA-KILAVUZU.md` bölüm 7; önce iki konteynerde bekleyen mesaj
   olmadığına bak). Kurulumdan sonra canlı motorda emirlerin açık kaldığını doğrula.

## DOSYA HARİTASI

- Depo `C:\Users\ihsan\Desktop\Antigravity\trading`, dal `v5-yenileme`.
  `RF-PLAN-6.md` plan, `RF6-VTO.md` sahip kararları, `RF6-KANIT.md` kanıt, `RF6-PROBE.md` broker ölçümleri,
  `RF-ISSUES-6.md` ertelenenler ve süreç notları, `RF-SAME-PAGE-LOG-6.md` toplantı kaydı,
  `rf6/contract-r1..r5.md` inşa sözleşmeleri, `rf6/fix-r1-1.md` R1 düzeltme listesi,
  `v5/` yeni kod, `tests_v5/` testler, fixture'lar ve golden dosyalar, `requirements-v5.lock.txt` sabit sürümler.
- Kalıcı yedek `C:\Users\ihsan\trading_lab_v5\`: araştırma laboratuvarı, turnuva, bağımsız referans,
  `rf6\` (sözleşmeler, saldırgan paketler, Codex çıktıları), dalların `git bundle` yedeği.
- Sunucu erişimi ve Coolify ayrıntıları Claude belleğinde (`trading-bot-stock-engine`). Bu depoya yazılmaz.

## MAKİNE TUZAKLARI

- Edit / Write kancası uzun tireyi virgüle çevirir. Ayrıca Write ile yazılan metinde ters bölü + u ile
  başlayan kaçış dizileri gerçek karaktere dönüşür: test içinde gerekiyorsa `chr(0x2014)` kullan.
- Bash heredoc ters bölüleri bozar. Yama betiğini Write ile yaz, tek eşleşmeli `replace` + `assert` kullan.
- pytest: `-p no:cacheprovider --basetemp=.pytest_tmp` (paralel koşularda ayrı basetemp).
- Antigravity kökünde arama donar, aramayı `trading/` ile sınırla.
- Yardımcılar kök `CLAUDE.md` kurallarını da görür. Onlara raporu İngilizce ve teknik yazmalarını açıkça söyle.
