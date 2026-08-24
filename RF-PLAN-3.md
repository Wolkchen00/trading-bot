# RF-PLAN-3.md , v4.16 Clarity Break: "Once olculebilir, sonra karli"

**Tarih:** 2026-08-23 | **Surum hedefi:** v4.16 | **Rock'lar:** R8..R12
**Tetik (Ihsan):** "Son zamanlarda hic alis satis olmamis, neden donmus? Once bunu
duzelt. Sonra: aylar gecti ama para konusunda hala ayni yerde sayiyoruz. Ajanlarin
codinglerinde problem var mi? Butun kodlara bak. Sonra bana bir plan olustur."

**Bulgu envanteri:** `RF-ISSUES-3.md` (94 bulgu: 32 KRITIK, 35 YUKSEK, 24 ORTA, 3 DUSUK)
**Toplanti logu:** `RF-SAME-PAGE-LOG-3.md`

---

## CORE FOCUS (tek cumle)

**Botu once OLCULEBILIR ve DURUST yap , cunku su anda ne kazandigini, ne
kaybettigini soyleyen kanallarin hicbiri calismiyor; alfa karari ancak olcum
duzeldikten sonra veriye dayali verilebilir.**

---

## Neden bu Core Focus? (uc olcum, ayni sonuc)

1. **Strateji kendi kanitina gore SPY altinda.** Projenin KENDI walk-forward'i
   (`walk_forward_results.json`, 2026-06-16 tarihli): `beat_spy: 0/2`,
   `mean_alpha_pct: -11.48`, `worst_alpha_pct: -13.01`. In-sample backtest:
   +2.69% vs SPY +8.49% (alfa -5.80). Rejim deneyi: 5 modun 5'i de 0/2, en iyi
   alfa -2.84. Canli paper (2 ay, gercek broker): -1.55% vs SPY +4.36%.
   Olcum penceresinde toplam ozkaynak artisinin %89'u SPY parkindan, %11'i
   stratejiden geliyor.
2. **Backtest canli sistemi olcmuyor.** `backtest.py` canli koddan yalniz
   `plan_exit_pcts` + `should_exit_locally` import ediyor; AgentCoordinator ve
   check_all_gates hic calismiyor, cikislar yalniz gunluk kapanista olculuyor.
   Yani "dogrulanmis strateji" ile "canlida calisan strateji" AYNI SISTEM DEGIL.
   Bu yuzden strateji degisikligi su anda **yanlislanamaz**.
3. **Defter ve olcum kanali bozuk.** Kademeli satis bacaklari deftere HIC
   yazilmiyor: olcum doneminin 6 isleminin 3'unde toplam $282.93 gerceklesmis
   PnL kayip (SMCI +189.48 yerine +3.49; AAPL +41.96 yerine +2.72; PLTR +62.75
   yerine +4.91 yazilmis). Metrik-4 "kayit butunlugu" yapisal olarak yalniz FAZLA
   kaydi gorebiliyor, EKSIK kaydi goremiyor , yani bu hatayi hicbir zaman
   yakalayamazdi. Ayni bozuk defter kayip-serisi sayacini ve ajan ogrenmesini de
   besliyor.

**Sonuc:** Donmayi acmak, olculemeyen ve kendi kanitina gore SPY'in altinda kalan
bir stratejinin daha COK islem yapmasi demektir. Bu cycle'in isi kilidi acmak
degil, kilidin acilip acilmayacagina karar verebilecek DURUST bir olcum kurmak.

---

## Donmanin teshisi (Ihsan'in 1. sorusu)

Canli hesap 2026-07-30 -> 2026-08-21 arasi 17 islem gununde **0 giris, 0 cikis**;
son gercek canli islem 2026-07-16 (RIVN). Tek sebep yok, ust uste binmis ALTI kilit var:

| # | Kilit | Olculen etki | Tipi |
|---|---|---|---|
| 1 | `min_confidence_score=50` koordinatorun uretim bandinin tepesinde | 1377 BUY sinyalinin 1161'i (%84) burada oluyor | **Ayar** |
| 2 | `LOSS_STREAK_WARN` cikissiz kisir dongu (`_consecutive_losses=2`, 16 Tem'den beri) | Kalan sinyallerin conf>=70 olmasi gerekiyor: 1377'nin yalniz 18'i (%1.3) | **HATA** |
| 3 | EMA200 kapisi tarayici/TechAgent ile ters yonde (oz-iptal eden huni) + split-duzeltmesiz veri | 08-21 paper: 1122/1122 sinyal EMA200'de blok | **HATA + tasarim celiskisi** |
| 4 | MTF kapisi canlida acik, paper'da kapali (+ hatada sessiz fail-open) | Paper'da olculen davranis canliyi temsil etmiyor | **Ayar + HATA** |
| 5 | `live_entries_enabled=False` (R5 kilidi) | 17 gunde yalniz 7 kez tetiklendi (zincirin EN SONUNDA) | **KASITLI GUVENLIK** |
| 6 | Sermaye/kesirli adet cikmazi: canli equity $492'nin $345.90'i SPY parkinda -> kullanilabilir nakit ~$96; mega-cap'te `whole_qty=0` -> kesirli -> canlida bracket reddi -> `return False` | Evrenin 20 isminden 11'i **yapisal olarak alinamaz**; huni sayaci bile yok | **HATA** |

**Karar (Visionary):** 2, 3, 6 numarali kilitler HATADIR ve bu cycle'da duzeltilir
(R10 + R11). 5 numarali kilit KASITLIDIR ve bu cycle'da ACILMAZ. 1 ve 4
numarali ayarlar R12 backtest'i gercek sistemi olcmeye baslayana kadar
degistirilmez , cunku su anda hangi esigin dogru oldugunu soyleyecek bir kanit yok.

---

## ROCK'LAR (bagimlilik sirasinda)

### R8 , Defter dogrulugu (ledger truth)

**Kok:** Kademeli (partial) satis bacagi finansal olarak GORUNMEZ.
`_handle_long_partial` / `_finish_partial_attempt` telemetriye yaziyor ama
`record_trade`, `update_loss_streak`, `agent_perf.record_outcome` ve
`trades_today`'in hicbirine dokunmuyor.

**Kapsam:**
- Dolumu dogrulanmis her partial bacak icin `record_trade` cagrilir; gerceklesen
  PnL broker dolum fiyati x dolan adet uzerinden hesaplanir.
- Ayni bacak iki kez yazilamaz: kalici partial intent `client_order_id` dedupe anahtaridir.
- Giris fiyati SINYAL fiyati degil, broker ortalama giris fiyatidir
  (`EXIT-DEFTER-GIRIS-FIYATI-SINYAL-FIYATI`).
- Index parking / DCA / opsiyon bacaklari `strategy` etiketiyle isaretlenir ve
  strateji islemi SAYILMAZ (`OLCUM-PARK-PNL-SIZINTISI`).
- **Bilinerek kabul edilen yan etki:** karli bir partial `_consecutive_losses`'i
  sifirlar , yani 2 numarali kilit R8'den sonra kendiliginden de acilabilir.
  Bu GUVENLIDIR: R5 kilidi (`live_entries_enabled=False`) canlida yeni girisi
  bagimsiz olarak kesmeye devam eder.

**Kapatilan bulgular:** EXIT-PARTIAL-DEFTERE-YAZILMIYOR, OLCUM-DEFTER-KADEMELI-SATISI-YAZMIYOR,
EXIT-DEFTER-GIRIS-FIYATI-SINYAL-FIYATI, OLCUM-PARK-PNL-SIZINTISI, AGENT-OUTCOME-LIFO-YANLIS-ATAMA

**Done looks like:** Sentetik bir partial+final cikis dizisinden sonra deftere
yazilan toplam gerceklesen PnL, broker dolumlarindan hesaplanan toplamla KURUSU
KURUSUNA esittir; ayni dizi iki kez islenirse defter degismez.

**PROOF:** `py -m pytest tests/test_r8_ledger.py -q` (yeni) **ve**
`py -m pytest tests/ -q` (mevcut 138 test yesil kalir)

---

### R9 , Olcum dogrulugu (measurement truth)

**Kok:** 4/4 PASS kapisi gercek parayi acan tetiktir ve su anda YANLIS YESIL
verebilir. Metrik-4 eksik kaydi yapisal olarak goremez; metrik-1 SPY park
al/sat dongulerini strateji islemi sayar; GENEL PASS kendi n>=20 / 30-gun on
kosulunu hic uygulamaz (n=6, gun=8 iken exit 0 dondu).

**Kapsam:**
- Metrik-1: `reconstruct_closed_trades` strateji disi bacaklari (park, DCA,
  opsiyon, pencere disi) AYIRIR; net PnL yalniz stratejidir.
- Metrik-4: cift yonlu kume karsilastirmasi , broker kapali emir kumesi <-> defter
  kumesi. EKSIK kayit da FAIL uretir (bugun uretemiyor).
- GENEL PASS: `n>=20` VE `>=30 islem gunu` sert on kosul; saglanmazsa exit != 0
  ve basligi "ON KOSUL SAGLANMADI" der.
- Metrik-3 tautolojisi kaldirilir (reddedilebilir hale gelir).
- Metrik-2 paydasi yalniz KAPALI islemlerdir (acik PLTR pozisyonunun yari satisi
  paydayi sismemelidir).
- Zaman damgalari acikca UTC (naive timestamp kaldirilir).

**Kapatilan bulgular:** METRIK4-EKSIK-KAYDI-GOREMEZ, OLCUM-SPY-PARKING-TRADE-SAYILIYOR,
GENEL-PASS-N20-30GUN-KOSULUNU-UYGULAMIYOR, METRIK3-TAUTOLOJI, METRIK2-PAYDA-KAPALI-ISLEM-KUMESI-DEGIL,
REKONSTRUKSIYON-STRATEJI-DISI-ISLEMLERI-AYIRMIYOR, OLU-KOD-VE-NAIVE-ZAMAN-DAMGASI,
RAPOR-BENCHMARK-VE-EQUITY-OKUMUYOR, BACKFILL-KORUMA-IHLALI-BILGIYE-INDIRILMIS

**Done looks like:** Uc fixture: (a) defterde EKSIK bacak var -> arac FAIL verir;
(b) SPY park kari var, strateji zararda -> metrik-1 FAIL verir; (c) n=6 -> GENEL
PASS "ON KOSUL SAGLANMADI" ile exit != 0 doner.

**PROOF:** `py -m pytest tests/test_r9_olcum.py -q` (yeni) **ve** `py -m pytest tests/ -q`

---

### R10 , Olumcul dogruluk hatalari (strateji degisikligi YOK)

**Kok:** Stratejiden bagimsiz olarak YANLIS olan davranislar. Hicbiri "ayar"
degildir; hepsi hata.

**Kapsam (her biri ayri, kucuk):**
1. `AGENT-SENT-TERS-ISARET`: haber isareti ters , kotu haber maksimum BUY guveni
   uretiyor (`core/news_analyzer.py:303-309`). **ONCE DOGRULA, sonra duzelt.**
2. `KILLSWITCH-KOD-HATASINI-API-HATASI-SANIYOR`: ana dongudeki HER istisna "API
   hatasi" sayiliyor ve 5 tanesi tum pozisyonlari piyasa emriyle tasfiye ediyor.
   Yalniz gercek broker/API istisnalari sayilir; kod hatasi ayri siniftir.
   `CONSECUTIVE-ERRORS-BASARIDA-SIFIRLANMIYOR`: basarili turda sayac sifirlanir.
3. `BARS-BOS-DF-EMA200-GUN-BOYU-ZEHIRLENME`: bos DataFrame gun boyu None olarak
   cache'lenip kapiyi bozuk degere dusuruyor. Veri yoksa cache YAZILMAZ, kapi
   fail-closed olur ve huniye sayac islenir.
4. `GATE-EMA200-SPLIT-DUZELTMESIZ-VERI`: bar verisi split-duzeltmeli istenir
   (`adjustment='split'` veya saglayici esdegeri).
5. `EXIT-PARTIAL-SONRASI-TP-BACAGI-KAYBOLUYOR`: partial sonrasi yalniz STOP degil
   TP bacagi da geri kurulur.
6. `POZISYON-METADATA-ATOMIK-DEGIL-VE-DEBUG-YUTULUYOR`: `bot_positions.json`
   atomik yazilir (temp+rename); bozulma DEBUG degil ERROR + alarm.

**Kapsam DISI (bilincli):** `EXIT-BE-TAVANI-0.3PCT` ve cikis geometrisi
(`STRAT-CIKIS-MERDIVENI-RR-YIKIMI`, `CONFIG-CANLI-CIKIS-GEOMETRISI-OLCULMEMIS`).
Bunlar ACIK CANLI POZISYONLARIN davranisini degistirir ve R12 olcmeden once
degistirilmemelidir. RF-ISSUES-3'te "Ihsan karar maddesi" olarak kalir.

**Done looks like:** Her madde icin bir dusmanca test; tam suite yesil; hicbir
config degeri degismemis (`git diff config.py` yalniz yorum/yeni anahtar gosterir).

**PROOF:** `py -m pytest tests/test_r10_correctness.py -q` (yeni) **ve** `py -m pytest tests/ -q`

---

### R11 , Kilit cikisi + huni teshisi (donmanin GERCEK duzeltmesi)

**Kok:** Cikisi olmayan kilit bir hatadir; kasitli kilit degildir. Ve huni
sayaclari donmanin sebebini SOYLEYEMEDIGI icin 19 ardisik NO_TRADE alarmi
faydasiz cikti.

**Kapsam:**
- **Kayip serisi cikisi:** WARN kolunda zaman sonumu (HALT kolunda zaten var);
  `_loss_halt_until` diske kalici yazilir (bugun restart'ta yeniden armlaniyor);
  operatorun sayaci sifirlayabilecegi acik bir yol (env veya arac) + alarm.
- **Kesirli/bracket cikmazi:** canlida kesirli adet cikarsa (a) bracket denemeden
  once iki-adimli emir + sunucu tarafi stop yoluna gecilir VEYA (b) giris iptal
  edilir , AMA her iki durumda da `_funnel_bump("gate_block",
  reason="FRACTIONAL_NO_BRACKET")` yazilir. Sessiz `return False` kalkar.
- **`funnel.dominant_stage` hatasi:** oncelik demeti tanimlanmis ama secim
  `max(key=count)` ile yapiliyor -> her zaman `signal_hold` donuyor
  (`core/funnel.py:303-321`). Oncelik demeti uygulanir.
- **Huni sayaclari karsilastirilabilir olur:** `conf_below_min` BUY/SHORT ayrilir
  (canli long_only); sembol basina sogutma (ayni sembol gunde ~120 kez sayilmaz);
  wash-sale 30-gun yasagi sayaca yazilir; index/ters-ETF sinyalleri sayaclanir.
- **R5 kilidinin teshis gucu:** kilit zincirin sonunda kalir (guvenlik) ama
  "kilit olmasa girecekti" sayaci (`would_enter`) ayri yazilir , boylece kilidi
  acmadan once kac giris olacagi ONCEDEN bilinir.
- NO_TRADE alarm metni gercek blokeri ve dogru `last_entry_date`'i soyler.

**Kapatilan bulgular:** GATE-LOSS-STREAK-CIKISSIZ-KILIT, GATE-LOSS-STREAK-WARN-KILITLI-KISIR-DONGU,
STREAK-KILIDI-CIKISI-YOK, STREAK-KILIT-KENDINI-BESLEYEN, RISK-FRACTIONAL-IKINCI-SESSIZ-KILIT,
GATE-LIVE-FRACTIONAL-BRACKET-OLU-KAPI, HUNI-CONF-SAYACI-KIRLI-NOTRADE-TESHISI-YANLIS,
GATE-HUNI-SAYACLARI-KARSILASTIRILAMAZ, FUNNEL-CONF-BELOW-MIN-KARISIYOR, GATE-WASH-SALE-SAYACSIZ-30GUN-YASAK,
GATE-LIVE-LOCK-ZINCIRIN-SONUNDA, NOTRADE-ALARM-YORGUNLUGU-VE-TESLIM-ONCESI-DEDUPE,
RESTART-KAYBOLAN-DURUM-HALT-KUYRUK-CACHE (kismen: halt suresi)

**Done looks like:** Sentetik bir gunun huni kayitlari uzerinde `dominant_stage`
gercek blokeri isimlendirir; `_consecutive_losses=2` + N saat gecmis -> kapi
gecirir; canlida kesirli adet -> huni sayaci artar ve sessiz kayip olmaz;
`would_enter` sayaci R5 kilidi kapaliyken bile dolar.

**PROOF:** `py -m pytest tests/test_r11_funnel_lock.py -q` (yeni) **ve** `py -m pytest tests/ -q`

---

### R12 , Yanlislanabilirlik: backtest CANLI kodu calistirir

**Kok:** `backtest.py` ayri bir strateji test ediyor. Bu yuzden "stratejiyi
gelistirelim" cumlesi su anda olculebilir bir sey ifade etmiyor.

**Kapsam:**
- Backtest karar yolu canli koddan gecer: `AgentCoordinator.decide` +
  `check_all_gates` + `plan_exit_pcts` + gercek cikis zinciri.
- Cikislar gun-ici bar ile olculur (bugun yalniz gunluk kapanis; kazananlarin
  ~%90'i TP tavaninin USTUNDE "kapanmis" gorunuyor , karin buyuk kismi kurgu).
- Gercek boyutlandirma ve sermaye kisiti modellenir (nakit rezervi, kesirli/tam
  pay, parking).
- SPY buy-and-hold benchmark her kosumda ayni raporda yazilir.
- Walk-forward gercek sistemle YENIDEN kosulur; sonuc dosyasi tarihlenir.

**Done looks like:** Backtest bir kosumda AgentCoordinator ve check_all_gates
cagrilarini gercekten yapar (selftest bunu kanitlar) ve yeni
`walk_forward_results.json` gercek sistemin alfasini gosterir , sayi ne cikarsa
ciksin. **Bu rock'in basarisi "alfa pozitif olsun" degil, "alfa OLCULEBILIR olsun".**

**PROOF:** `py backtest.py --selftest` (yeni bayrak: canli karar fonksiyonlarinin
cagrildigini assert eder) **ve** `py -m pytest tests/ -q`

**Not:** R12 tek basina bir cycle buyuklugunde olabilir. Codex BLOCKED derse
veya kapsam tasarsa, R12 bir sonraki cycle'a ayrilir ve R8-R11 tek basina
teslim edilir.

---

## NON-GOALS (bu cycle'da KESINLIKLE yapilmayacak)

1. **R5 canli giris kilidini acmak.** `live_entries_enabled` False kalir.
   Acilis on kosulu degismedi: olcum 4/4 PASS (R9'dan SONRA anlamli) + Ihsan onayi.
2. **Strateji degistirmek.** Yeni sinyal, yeni evren, ML, esik oynamasi , hicbiri.
   R12 olcum kanalini kurmadan strateji degisikligi yanlislanamaz.
3. **Canli acik pozisyonlara dokunmak.** GOOGL/MSFT/NVDA bracket bacaklariyla
   korunuyor; cikis geometrisi bu cycle'da degismez.
4. **Deploy.** Deploy ayri ve Ihsan kapili bir adimdir (piyasa kapaliyken).
   Push deploy TETIKLEMEZ. Tek Coolify uygulamasi HER IKI konteyneri kaldirir ,
   "yalniz paper deploy" diye bir sey yok.
5. **Config degeri degistirmek.** `min_confidence_score`, EMA200 kapisi, MTF,
   parking orani , hepsi R12'nin olcumunu bekler.

---

## Riskler ve ters etkiler

| Risk | Etki | Onlem |
|---|---|---|
| R8 kayip serisini sifirlar -> 2 nolu kilit acilir | Canlida giris olabilir | R5 kilidi bagimsiz olarak kapali; test bunu dogrular |
| R9 sertlesince olcum GERI GIDER (4/4 -> FAIL) | Ihsan "bozdunuz" diye gorebilir | Beklenen ve DOGRU sonuc; rapor bunu onceden soyler |
| R11 kesirli yolu iki-adimli emre gecerse canlida bracket'siz pozisyon acilabilir | Ciplak pozisyon riski | Iki-adimli yol YALNIZ sunucu tarafi stop dogrulandiktan sonra; R5 kilidi zaten kapali |
| R12 kapsam tasmasi | Cycle bitmez | R12 ayrilabilir; R8-R11 kendi basina teslim edilebilir |
| Yeni testler gercek paper hesabini mutasyona ugratir (I-12) | Gercek hesap bozulur | `19bd7af` guard'i yerinde; yeni testler broker cagrisi YAPMAZ, fixture kullanir |

---

## Ihsan karar maddeleri (bu cycle DISINDA, rapora tasinir)

- **K1 , Stratejik:** Kendi kanitina gore SPY altinda kalan stratejiye devam mi,
  yeniden insa mi, yoksa sermaye SPY'de mi parklanir? (R12 sonrasi veriyle.)
- **K2 , Sermaye:** Canli hesapta parking orani %70 -> giris butcesi $96.
  Parking dusurulsun mu, yoksa canli hesap "kucuk hesap" olarak kabul edilip
  yalniz dusuk fiyatli isimlerde mi calissin?
- **K3 , Cikis geometrisi:** BE tavani +%0.3 iken risk %5-6; TP bandi canlida
  %8-12 / min_rr 2.0 ama paper'da %5-7.5 / 1.25. Hangisi olculecek?
- **K4 , Kill switch:** Kucuk hesapta esik anlamsiz ama tetiklendiginde her seyi
  piyasa emriyle satip canli botu KALICI kilitliyor. Esik/davranis gozden gecirilsin mi?
- **K5 , Ajan mimarisi:** 5 ajandan 3'u fiilen olu (SocialAgent Reddit 403,
  FundAgent kota, SentAgent isaret ters). Ajanlar onarilsin mi, yoksa mimari
  2 saglam ajana mi indirilsin?
