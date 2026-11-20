# RF6-DEVIR.md , v5 "Yenileme" devir notu

> İlk yazım: 16 Kasım 2026 09:45 PT. Son güncelleme: 20 Kasım 2026 14:00 PT (R5e ve dördüncü kurulum sonrası).
> Okuma sırası: önce bu belge, sonra `RF6-DEVAM.md` (kurulum kayıtları), `RF-PLAN-6.md` (Revizyon 14), gerekirse
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
   İhsan'a bir kez daha sorulacak. Bu söz verildi. Onay olmadan canlı emir yok.
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

## DURUM (20 Kasım 2026 öğleden sonrası itibarıyla)

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
  Claude belleğinde (`trading-bot-stock-engine`) durur.

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

### Borç (yapılmadı ya da doğrulanmadı)

- R5e HENÜZ GERÇEK BİR SEANS GÖRMEDİ. İlk seansı 23 Kasım Pazartesi: açılıştan sonra (20 Kasım satırları
  değişmemeli, uyarı olmamalı), 15:50 ET kararı (deneme botu 20 Kasım'da aldığı ek payı kurala göre satmalı ya
  da tutmalı), iki özet; salı sabahı pazartesi satırlarının aracının kapanış değeriyle onaylanması.
- R5f (üç küçük iş, `rf6/fix-r5f.md`, `RF-ISSUES-6.md` madde 33): şüphe uyarısı "güvenlik satışı" diyecek,
  düzeltilen kapanış mesajı özetin tarihini söyleyecek, sağlık aracı mesaj dosyasını okuyamasa da cevap
  verecek. Codex yazar, Claude denetler, sonra beşinci kurulum (yükseltme sırası
  `RF6-CALISMA-KILAVUZU.md` bölüm 7'de).
- Bildirim kanalının adı gerçek paradan ÖNCE yenilenecek; sahip önce telefonunda yeni kanala abone olmalı
  (ayrıntı Claude belleğinde, bu depoya yazılmaz).
- Canlıya geçiş: sahibe 20 Kasım'da SORULDU (söz verilmişti), cevabı bekleniyor. Öneri: R5e pazartesi seansını
  temiz geçsin, kanal adı yenilensin, sonra `LIVE_TRADING_ENABLED=true`. Cevap gelmeden bayrağa dokunulmaz.
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
- Raylar bütün hesabı izliyor (ANT dahil). ANT sert düşerse günlük kesici o gün botun alımını durdurabilir.
- Kesicinin taban çapraz kontrolü (%2) yarım günlerde ve para giriş çıkışında yanlış "veri şüphesi" verebilir.
- Kapanıştan sonra hesap okunamazsa günlük özet geç gelir ya da o gün hiç gelmez.
- Deneme hesabının mesajları artık sahibin telefonuna düşmüyor (ayrı konu). O konuyu yalnız Claude okur;
  oturum açılmazsa deneme hesabındaki bir sorun fark edilmeyebilir (bekçi yalnız sürecin canlılığını izler).
- R5e büyük bir tur (33 + 9 + 5 madde) ve sunucuda henüz bir seans geçirmedi. Prova gerçek kayıtlarla yapıldı ama
  aracı sahteydi: gerçek aracının pazartesi sabahı döndüreceği değerler (kapanış değeri, tarih alanı) provadakinden
  farklı davranabilir. Pazartesi kontrolü bu yüzden canlı bayraktan ÖNCE şart.
- R5d'nin 20 Kasım için yazdığı kapanış satırı "kesin" sayılıyor (R5e yalnız kendi yazdığı satırları ertesi gün
  aracının değeriyle onaylar). Deneme hesabının zirvesi o satırla 61.900,58'e çıktı; aracının resmi kapanışı
  birkaç dolar farklı olabilir. Etkisi yok denecek kadar küçük, ama bilinen bir geçiş izi.

## SIRADAKİ SOMUT ADIM

1. `git -C trading status` ve `git log --oneline -8` ile durumu doğrula. Kanıt komutunu koş (R5e ağacında 937
   geçmeli; R5f bittiyse daha fazla).
2. Sahibin ANT hissesine bak: fiyat nerede, iki çıkış emri yerinde mi
   (`C:\Users\ihsan\trading_lab_v5\scripts\ant_durum2.py <depo>`, salt okuma). Kârdaysa zarar kes seviyesini
   alışın üstüne çekmeyi ÖNER (kendiliğinden değiştirme).
3. Sunucuda iki konteynerin son seansına bak (`RF6-DEVAM.md` içindeki "first decision day" bölümü örnek:
   karar kaydı, emir, dolum, özet metni, raylar, günlük, bekçi). Sonucu `RF6-DEVAM.md` dosyasına yaz.
4. R5f bitmediyse bitir ve kur. Canlı bayraktan önce: R5e en az bir seansı temiz geçmiş olmalı, bildirim kanalı
   yenilenmiş olmalı, sahip AÇIKÇA onay vermiş olmalı.

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
