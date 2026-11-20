# RF6-KANIT.md , v5 yenilemesinin kanit dosyasi (olculmus, kaynakli)

> Tarih: 2026-11-16. Derleyen: Claude. Kaynak calismalar: 10 ajanlik arastirma (wf_53481952-f13) +
> strateji turnuvasi (wf_5e7a3939-b26). Ham ciktilar oturum scratchpad'inde; buradaki sayilar ozet.
> Bu dosya "neden boyle yaptik" sorusunun cevabidir. Plan: `RF-PLAN-6.md`. Sahip kararlari: `RF6-VTO.md`.

## 1. Hesap ve platform gercekleri (2026-11-16, canli API + Alpaca/FINRA sayfalari)

- Canli hesap: equity = nakit = alim gucu = $490,06; multiplier 1 ("limited margin"); `shorting_enabled=false`
  (acik satis ve kaldirac icin >= $2.000 gerekir); opsiyon onayi yok; kripto ACTIVE (tier 1); kesirli islem acik.
- **PDT kurali KALKTI.** FINRA RN 26-10, yururluk 2026-06-04; Alpaca ayni gun uyguladi; `pattern_day_trader`,
  `daytrade_count`, `daytrading_buying_power` alanlari 2026-07-06'dan beri API'de yok (alpaca-py None doner).
  Ayni gun sinirsiz al-sat serbest; satis geliri ayni gun yeniden kullanilabilir (Alpaca destek sayfasi; bu
  hesapta gozlenmedi).
- Kesirli / tutarli (notional) emirler: yalniz basit emir (market, limit, stop, stop_limit), yalniz DAY.
  Bracket / OCO / GTC / MOC / MOO kesirli emirde YOK (canli red kodu 42210000, 2026-08-10).
- Ucretsiz veri: gercek zamanli yalniz IEX (hacmin ~%2,5'i); SIP gecmisi 15 dk gecikmeyle serbest;
  dakika gecmisi 2016'dan. Seans disi IEX kotasyonlari kullanilmaz (SPY 729,68 x 775,36 goruldu).
- Kripto: yalniz long, yalniz nakit, tier-1 ucret %0,15 yapici / %0,25 alici, 7/24.
- Bos nakit: Alpaca "High-Yield Cash" programi var (2026-09-22 itibariyle %3,56'ya kadar, degisken, kayit
  gerekir; hesapta acik mi bilinmiyor).

## 2. Eski sistem (v4) neden kazanmadi (broker kayitlari + uretim loglari)

- Canli: tek para yatirma $500 (2026-03-18). 114 dolum, 58 kapanis emri, kar faktoru 0,97, gerceklesen -$1,30;
  ucretler -$5,09; kripto donemi (18 Mar - 7 Nis) en buyuk kayip (-$14,6 ucret dahil). Hesap $490,06.
- Ayni $500 al-tut: SPY $574,74 (+%14,95), QQQ $573,38 (+%14,68), TQQQ $629,28 (+%25,86, yolda -%41,9).
- Sermaye calismadi: zaman agirlikli ortalama yatirim ~%32 (hisse %4,8 + SPY parki %25,7); ~%68 bos nakit.
  SPY'a gore $85 farkin ~$51'i bos nakitten.
- Karar motorunda avantaj yok: hisse 33 kapanis, kazanma %39, kar faktoru 0,99. R25'ten beri kilide ve
  kapilara takilan 71 aday botun kendi cikis kurallariyla ortalama -%0,80 (10 gun sonra -%3,7).
- Canli motor 8 ayin ~4'unde islem yapamadi: (a) 16 Tem - 12 Eyl zarar serisi kilitlenmesi; (b) 29 Tem'den
  beri canli yalniz bracket emir gonderiyor, Alpaca kesirli bracket'i reddediyor (220 red; kapilardan gecen
  31 adayin 26'si hic islenemezdi); (c) **2026-09-21 14:00:55 UTC'den beri OTOMATIK KILIT**: SMCI girisi
  sonrasi stop dogrulamasi `GetOrdersRequest(status=OPEN, nested=True)` ile yapiliyor, dolmus bracket
  ebeveyninin bacaklari bu sorguda gorunmuyor -> yanlis STOP_UNVERIFIED -> kalici kilit. 17 sn sonra stop
  dogrulandi ama kilidi kaldiran kod yok. Kilit SPY parki alimlarini da engelledi; satislar serbest oldugu
  icin park eridi (17 Eyl, 22 Eyl rezerv tamamlama; 5 Kas BearBrain "unwind") -> hesap %100 nakit.
- Izleme yalan soyledi: gunluk saglik raporu 56 gun "BOT SAGLIKLI" dedi; kilit yazilirken bildirim yok;
  her gun giden NO_TRADE bildirimi teknik ve yaniltici ("conf_below_min"); 30 Tem'den beri 4 saatte bir
  yanlis "trading-short KONTEYNER-YOK" alarmi (alarm yorgunlugu). ntfy'e 95 teslim var; sahibin gorup
  gormedigi bilinmiyor.
- Paper: $100k (2026-03-13) -> $62,5k. Kaybin %93'u opsiyon yurutucu hatasi (-$34.874; tek dakikada
  -$15.960), ~%8 kripto + ucret. Hisse stratejisi ~$0. Yani paper'in -%37'si strateji hakkinda bilgi vermez.

## 3. GitHub taramasi (yildiz, bakim, uygunluk)

- Benimsenebilir, kanitli bir Alpaca botu yok (Alpaca'ya ozel bot depolari < 30 yildiz).
- En cok yildizlilar bize uymuyor: freqtrade (56,9k; yalniz kripto borsalari), nautilus_trader (31,6k;
  Alpaca adaptoru yok), TradingAgents (122,8k) ve ai-hedge-fund (64,7k) gibi LLM ajan depolari dogrulanabilir
  ornek-disi canli sonuc yayimlamiyor (TradingAgents: 3 aylik, maliyetsiz backtest; ai-hedge-fund kendi
  ifadesiyle "gercek islem yapmaz"), qlib / FinRL arastirma platformu, LEAN imaji 14 GB.
- Kullanilanlar: `alpaca-py` (resmi SDK, uretimde tek bagimlilik); laboratuvar icin fikir ve yontem:
  yuruyen pencere + bootstrap dogrulama (pybroker, ML4T), ileriye-bakma denetimi (freqtrade
  lookahead-analysis fikri -> `lc.assert_causal`), paperswithbacktest replikasyon istatistikleri
  (medyan Sharpe 0,37 brut -> beklenti ayari). Strateji fikri: trend filtreli kaldiracli endeks cekirdegi
  (Gayed & Bilello 2016; QuantConnect strateji kutuphanesi) + oynaklik hedefi (Moreira & Muir 2017).
- Bagimlilik riski: v4 `requirements.txt` yalniz ">=" tabanlari iceriyor; v5 kilit dosyasiyla sabitlenir.

## 4. Strateji kaniti ozeti (yayin + bagimsiz yeniden uretim)

| Yontem | Hukum | Not |
|---|---|---|
| Oynaklik hedefli kaldiracli endeks trendi (QQQ > SMA200, E = min(3, hedef / vol20), TQQQ + nakit) | en guclu aday | iki bagimsiz kosu: VT25 2010-2026 CAGR ~%20, en derin dusus ~-%36 (1 gun gecikmeyle -%45) |
| Duz SMA200 -> TQQQ | kirilgan | 40 yillik sentetik testte -%89 / -%95 dusus, 15+ yil su alti |
| Ters ETF "short bacagi" | deger yok ediyor | TQQQ/SQQQ gecisi 2010-2026 CAGR -%1,2, dusus -%88; 2000-2009'da da kaybettiriyor |
| IBS < 0,2 ertesi gun (QQQ) | orta, kirilgan | 2014 sonrasi da +%0,25 / gun (t=3,6); 1 gun gecikme oldurur |
| RSI(2) dip alimi | orta, zayiflamis | yayin sonrasi ~1/3 eridi |
| Gun ici: ORB | zayif | bagimsiz replikasyonda maliyet sonrasi ~0 |
| Gun ici: "Beat the Market" gurultu bandi | orta, dogrulanmamis | yazarlarin 2007-2024 ornek-ici testi; bagimsiz ornek-disi yok |
| GEM / sektor rotasyonu / TSMOM tek varlik / HFEA / gece tutma / ay donumu | zayif | yayin sonrasi SPY'in altinda ya da maliyet oldurur |
| BTC/ETH trend | orta | dusuk korelasyon ama aylarin ~%35'i arti |

Beklenti ayari: yayimlanmis anomaliler yayin sonrasi ortalama %58 erir (McLean & Pontiff 2016); gunluk ve
aylik kazanc garanti edilemez; $490 ile yilda %20 yaklasik $100 demektir.

## 5. Laboratuvar (backtest duzeni)

- Yer: oturum scratchpad'i `lab/` (labcore.py, 27 oz-test, iki tur bagimsiz inceleme; bulunan 2 yuksek +
  3 orta kusur giderildi).
- Kalan bilinen sinir (kural olarak uygulanir): fon oncesi sentetik satirlarda gecikmesiz (delay 0) HIZLI
  sinyal sonuclari gecersiz sayilir (ortalamaya donusu yilda 2-8 puan kayirir); hizli sinyaller yalniz gercek
  fon satirlarinda (2010+) ve dakika verisinde (2016+) degerlendirilir.
- Zamanlama kurali: `weights[t]` kapanis t bilgisine kadar; t kapanisindan t+1 kapanisina tutulur.
  15:50 fiyatiyla sinyal + kapanis dolumu, kapanis sinyaliyle ayni sonucu veriyor (2016-2026).

## 6. Turnuva sonuclari (6 aile, ~5.700 konfigurasyon; onerilen her aile bagimsiz yeniden yazilip saldirildi)

| Aile | Test edenin sonucu | Bagimsiz dogrulama | Karar |
|---|---|---|---|
| F1 cekirdek: QQQ oynaklik hedefli kaldiracli trend (TQQQ + nakit), SMA 150/200/250 toplulugu | saglam plato (3.499 konfig.) | PLAUSIBLE: her sayi birebir uretildi, ileriye bakma yok; ama 2010-2026 basligi merkezin sansli yarisi | v5.0 CEKIRDEK |
| F2 kisa vadeli ek alim: QQQ IBS < 0,2 -> 1 gun +1/3 TQQQ (trend filtresiz) | tek ayakta kalan ek kural (153 konfig.) | CONFIRMED: bagimsiz kod, 15:50 sinyal / 15:55 dolumla +8,55 puan (2016-2026); ileriye beklenti +3 ile +4 puan | v5.0 EK KURAL |
| F3 ters ETF "short" bacagi | 435 konfig.in 0'i cekirdegi gecti | (dogrulama surerken yazildi) | REDDEDILDI; durust short tarafi = dusus trendinde nakit |
| F4 gun ici | yalniz "ayi seanslarinda gurultu bandi" (F4-A) ayakta; ORB ve son yarim saat momentumu maliyetten sonra olu | PLAUSIBLE | ERTELENDI (RF-ISSUES-6 madde 2) |
| F5 kripto (BTC trend, %10) | Kasim 2021'den beri katki yok | (dogrulama surerken yazildi) | ERTELENDI (madde 3) |
| F6 coklu varlik / altin kolu | altin kolu her pencerede arti | PLAUSIBLE: kazanc zamanlama degil altin betasi; +1 ile +2 puan sayilmali, kuyruk riski artar | ERTELENDI (madde 1) |

### 6.1 Cekirdek (F1), bagimsiz dogrulayicinin durust ozeti

- Kural: `E = min(3, hedef_vol / vol20) x (SMA150, SMA200, SMA250'den kacinin ustunde / 3)`; TQQQ agirligi `E / 3`;
  yalniz giris, cikis ya da 10 puanlik sapmada islem. Stop yok, dusus kurali yok, short yok.
- Merkezler (CAGR / en derin dusus; gecikmesiz ve 1 gun gecikmeli):
  VT20 2010-2026 %17,9 / -%24,6 ve %17,2 / -%35,5; 1990-2026 %13,3 / -%27,5 ve %12,9 / -%35,5.
  VT30 2010-2026 %24,1 / -%34,1 ve %21,6 / -%46,8; 1990-2026 %17,7 / -%39,6 ve %16,8 / -%46,8.
  VT45 2010-2026 %30,0 / -%42 ve %26,5 / -%54; 1986-2026 %21,1 / -%55 ve %19,3 / -%66.
- 2010-2026'da QQQ al-tut'a gore GETIRI ustunlugu YOK (VT20 %17,9 / Sharpe 0,84; QQQ %18,9 / 0,86); 17 yilin
  13'unde QQQ'nun gerisinde. Fayda uzun ayi piyasalarinda: 2000-03..2003-03 -%18 (QQQ -%76, TQQQ -%99,9),
  2007-10..2009-03 -%19 (QQQ -%41), 2022 -%8,5 (QQQ -%33) [VT20].
- Ileriye beklenti (dogrulayici): VT20 yilda ~%8-10 (aralik %5-13), VT30 ~%10-12 (aralik %5-17); QQQ 2010-2026'yi
  tekrarlarsa %15 ve %20. Planlanacak en derin dusus: VT20 -%35..-%40 (kuyruk -%45..-%50), VT30 -%47..-%55
  (kuyruk -%60..-%70).
- Duzenlilik: aylarin ~%61'i arti; 4 yilda ~1 zararli yil; 12 aylik getiri zamanin %19-28'inde eksi.
  Bu aile duzenli gunluk/aylik kazanc VERMEZ; verdigi sey -%80'lik kuyrugun kaldirilmasidir.
- Denenip reddedilenler: gun ici felaket stopu (%8/%12/%15/%20), dususte riski yarilama, %3 bant, 2-3 gun teyit,
  yavas oynaklik pencereleri, hedef > %45, parametrelerin yuruyen pencereyle yeniden secilmesi.
- 2026: yilbasindan beri VT20 +%3,9 / VT30 +%1,0 / VT45 -%7,4 (QQQ +%11,1); 30 Eyl - 13 Kas arasi
  -%11,2 / -%16,8 / -%25,7 (QQQ -%8,1).

### 6.2 Ek alim kurali (F2), bagimsiz dogrulayicinin durust ozeti

- Kural: karar aninda QQQ `IBS = (fiyat - gun dibi) / (gun tepesi - gun dibi) < 0,2` ise hesabin 1/3'u kadar ek
  TQQQ, bir gun; ertesi karar aninda sinyal yinelenmiyorsa satilir. Trend filtresi yok (cekirdek nakitteyken de calisir).
- Uygulanabilir kosullarda (15:50 sinyal, 15:55 dolum, 2016-2026): cekirdege +8,55 puan CAGR, +0,14 Sharpe,
  11 yilin 10'unda arti (2022: -9,3 puan). Esik 0,10-0,30 ve boyut 0,25-0,5'in 15 komsusunun 15'i arti.
- Zayif nokta GECIKME: bir kapanis gec uygulanirsa +3,4 puan, t 0,6. Kural: gec kalan ek alim ATLANIR, ertesi
  gun uygulanmaz.
- Kazanc topak topak: en iyi 10 gun artisin ~2/3'unu, en iyi 20 gun neredeyse tamamini tasir.
- Dusus sigligi BEKLENMEZ: cekirdegin en derin dususu + 0 ile 10 puan varsayilir; -%5'ten kotu gun sayisi artar.
- Ileriye beklenti: yilda +3 ile +4 puan (yaklasik %60 kesinti); 3 yilda onde olma olasiligi ~%70-75.
- Canli veri riski olculdu: IEX dakika verisiyle sinyal SIP ile gunlerin %99,2'sinde, SIP (15:35'e kadar) +
  IEX karmasiyla %99,6'sinda ayni (2020-08..2026-11, 1.580 seans).

### 6.3 Claude'un bagimsiz referansi (`lab/reference/ref_v5.py`, turnuva kodundan bagimsiz)

Cekirdek + ek alim, tek hesapta, canli kuralin birebir tarifi (nakit %0 faiz):

| Paket | 2010-2026 kapanis ideali | 2016-2026 gercekci (15:50/15:55, 10 bps) | en kotu gun | en kotu ay | arti ay |
|---|---|---|---|---|---|
| orta (VT20) | %26,3 / -%25,3 | %22,9 / -%32,0 | -%10,2 | -%14,5 | %64 |
| yuksek (VT30) | %32,4 / -%33,8 | %30,5 / -%37,7 | -%11,2 | -%21,1 | %62 |
| cok yuksek (VT45) | %38,1 / -%41,9 | %35,8 / -%48,7 | -%15,3 | -%25,8 | %62 |

Islem sikligi: ayda ~7,5 islem gunu; ek alim yilda ~36 giris, gun kazanma orani %55-58.
$490 icin 1 yillik blok-bootstrap (gercekci seri, yilda 12 puan kesintiyle): orta medyan $541 (%5: $370, %95: $766),
yuksek medyan $576 ($345 - $922), cok yuksek medyan $602 ($314 - $1.099); zararla kapanan yil olasiligi ~%30.

### 6.4 Hakem paketleri ve raylar (turnuva hakemi; ~150 parametre seti, ~1.350 kosu, hicbir sey secilmedi)

Uc paket ayni kuraldir (cekirdek + ek alim), yalniz `target_vol` degisir. Hakem paketleri Claude'un bagimsiz
referansiyla 1e-15 duzeyinde eslesti (golden dosyalar).

| Paket | 2010-2026 en iyi tahmin CAGR / dusus | Kesintili plan getirisi | $490'dan 1 yil: %5 / medyan / %95 | Yil zararda biter | 3 yil medyan | 3 yilda -%50'den derin dusus |
|---|---|---|---|---|---|---|
| orta (0,20) | %24,2 / -%30 | %12,1 | $369 / $549 / $814 | %31 | $695 | %7 |
| yuksek (0,30) | %30,8 / -%36 | %14,6 | $334 / $563 / $930 | %33 | $734 | %20 |
| cok yuksek (0,45) | %35,9 / -%47 | %15,6 | $294 / $568 / $1.070 | %36 | $755 | %40 |

Kesintiler: her karar bir kapanis gec; Nasdaq-100 getirisi QQQ yilda %10,5 yapacak sekilde dusuruldu (1990-2026
ortalamasi %14,5); ek alimin katkisi olculenin %41'i sayildi. Kesintisiz, son on yil tekrarlanirsa 1 yil medyan
$620 / $654 / $674.

Hakem onerisi: **YUKSEK**. Cok yuksek onerilmiyor: yuksekten cok yukseğe tipik sonuc 1 yilda $5 artarken 3 yilda
yaridan fazla kaybetme olasiligi iki katina cikiyor ve 2022 QQQ'yu tutmaktan kotu olurdu. Yedek: orta (yuksegin
kesintili buyumesinin %83'u, bir yilda -%30'dan derin dusus olasiligi yarisi).

Raylar (paketin kendi 132 kosusundan): uyari esigi 31 / 37 / 47 (zirveden %), gunluk kesici -%12 / -%15 / -%20
(yalniz alimi durdurur; kriz gununde satis kurallari test edildi, kaybettiriyor ya da etkisiz), felaket tabani
50 / 60 / 70 (her seyi sat, dur, sahibe sor; 1990'dan beri hic dokunulmadi, kesintili bootstrap'te 3 yilda
~%6-7 olasilik). Eski raylar (gunluk -%5, taban zirvenin %15 alti) bu kuralla bagdasmaz: yuksek pakette -%5'ten
kotu gun yilda ~5 kez olur, hesap gunlerin %35'inde zirvenin %15'ten fazla altindadir.

Hakemin diger hukumleri: short tarafi HAYIR (ters ETF'e sermaye %0; durust short tarafi trend kapaliyken nakit);
gun ici kol paketlerde YOK (gercekci dolumla +1,0 puana, kotu dolumla -2,2 puana dusuyor); kripto ilk surumde YOK
(son 5 yilda katki eksi); coklu varlik YOK, altin kolu ileride ilk aday (+1..+2 puan, altin betasi).

13 Kasim 2026 kapanisinda rejim: QQQ 679,89, SMA200'un %0,2 altinda, SMA150'nin %4,1 altinda, SMA250'nin %1,8
ustunde; 20 gunluk oynaklik %35,4. Trend puani 1/3: paketler kucuk bir cekirdek tutar (%6,3 / %9,4 / %14,1 TQQQ),
ek alim yok. QQQ Cuma kapanisinin %1,72'den fazla altina inerse trend puani 0 olur ve cekirdek satilir.

Hakemin calisma kosullari: karar her islem gunu 15:50 ET, emir 15:55'e kadar; gec kalan ek alim atlanir; bos nakit
faiz kazanmali (SGOV ya da Alpaca nakit faizi; %0 faizle yilda 0,1-2,2 puan kayip); ~30 ek alim turundan sonra
dolumlar 15:55 fiyatlariyla karsilastirilir, toplam maliyet tur basina ~20 bps'yi asarsa ek alim kapatilir.
