# RF-PLAN-3.md , v4.16 Clarity Break: "Once olculebilir, sonra karli"

**Tarih:** 2026-08-23/24 | **Surum hedefi:** v4.16 | **Rock'lar:** R8..R12
**Revizyon:** r2 (Codex Same Page Meeting tur 1 sonrasi , 26 bulgu islendi)
**Tetik (Ihsan):** "Son zamanlarda hic alis satis olmamis, neden donmus? Once bunu
duzelt. Sonra: aylar gecti ama para konusunda hala ayni yerde sayiyoruz. Ajanlarin
codinglerinde problem var mi? Butun kodlara bak. Sonra bana bir plan olustur."

**Bulgu envanteri:** `RF-ISSUES-3.md` (94 bulgu) | **Toplanti logu:** `RF-SAME-PAGE-LOG-3.md`

---

## CORE FOCUS (tek cumle)

**Botu once OLCULEBILIR ve DURUST yap , cunku su anda ne kazandigini, ne
kaybettigini soyleyen kanallarin hicbiri calismiyor; alfa karari ancak olcum
duzeldikten sonra veriye dayali verilebilir.**

---

## Neden bu Core Focus? (uc olcum, ayni sonuc)

1. **Strateji kendi kanitina gore SPY altinda.** `walk_forward_results.json`
   (2026-06-16): `beat_spy: 0/2`, `mean_alpha_pct: -11.48`, `worst_alpha_pct: -13.01`.
   In-sample backtest: +2.69% vs SPY +8.49% (alfa -5.80). Rejim deneyi: 5 modun
   5'i de 0/2, en iyi alfa -2.84. Canli paper (2 ay, gercek broker): -1.55% vs
   SPY +4.36%. Olcum penceresinde ozkaynak artisinin %89'u SPY parkindan, %11'i
   stratejiden.
2. **Backtest canli sistemi olcmuyor.** `backtest.py:33-34` canli koddan yalniz
   `plan_exit_pcts` + `should_exit_locally` aliyor; dosyada `AgentCoordinator`
   veya `check_all_gates` cagrisi YOK (Codex dogruladi). "Dogrulanmis strateji"
   ile "canlida calisan strateji" AYNI SISTEM DEGIL , strateji degisikligi su
   anda **yanlislanamaz**.
3. **Defter ve olcum kanali bozuk.** Kademeli satis bacaklari deftere HIC
   yazilmiyor (`position_manager.py:1061,1102-1108` yalniz intent/telemetriye
   yaziyor; `record_trade`/`update_loss_streak`/`record_outcome` cagrisi yok).
   Olcum doneminin 6 isleminin 3'unde toplam $282.93 gerceklesmis PnL kayip.
   Metrik-4 yapisal olarak yalniz FAZLA kaydi gorur (`olcum_raporu.py:523-537`
   tek yonlu esleme), EKSIK kaydi goremez , yani bu hatayi hicbir zaman
   yakalayamazdi.

**Sonuc:** Donmayi acmak, olculemeyen ve kendi kanitina gore SPY'in altinda kalan
bir stratejinin daha COK islem yapmasi demektir. Bu cycle'in isi kilidi acmak
degil, kilidin acilip acilmayacagina karar verebilecek DURUST bir olcum kurmak.

---

## Donmanin teshisi (Ihsan'in 1. sorusu)

Canli hesap 2026-07-30 -> 2026-08-21 arasi 17 islem gununde **0 giris, 0 cikis**;
son gercek canli islem 2026-07-16 (RIVN). Tek sebep yok, ust uste binmis ALTI kilit:

| # | Kilit | Olculen etki | Tipi |
|---|---|---|---|
| 1 | `min_confidence_score=50` koordinatorun uretim bandinin tepesinde | 1377 BUY sinyalinin buyuk cogunlugu burada oluyor | **Ayar** |
| 2 | `LOSS_STREAK_WARN` cikissiz kisir dongu (`_consecutive_losses=2`, 16 Tem'den beri) | Kalan sinyaller conf>=70 istiyor: 1377'nin 18'i (%1.3) | **HATA** |
| 3 | EMA200 kapisi tarayici/TechAgent ile ters yonde + split-duzeltmesiz veri | 08-21 paper: 1122/1122 sinyal EMA200'de blok | **HATA + tasarim celiskisi** |
| 4 | MTF kapisi canlida acik, paper'da kapali (+ hatada sessiz fail-open) | Paper'da olculen davranis canliyi temsil etmiyor | **Ayar + HATA** |
| 5 | `live_entries_enabled=False` (R5 kilidi) | 17 gunde yalniz 7 kez tetiklendi (zincirin EN SONUNDA) | **KASITLI GUVENLIK** |
| 6 | Kesirli adet + broker bracket reddi: canli equity $492'nin $345.90'i SPY parkinda -> nakit ~$96; mega-cap'te tam pay alinamiyor, broker bracket'i reddedince canlida sessiz `return False` | Evrenin 20 isminden 11'i pratikte alinamaz; huni sayaci yok | **HATA** |

**Onemli duzeltme (Codex turu 1):** 6 numarali kilit "kesirli gorunce kod doner"
DEGILDIR , `executor.py:228-274` kesirli adetle DAY bracket gondermeyi dener,
kilit BROKER REDDINDE olusur (`executor.py:276-288` canlida `return False`).
Sonuc ayni, mekanizma farkli; duzeltme buna gore yazildi.

**Karar (Visionary):** 2, 3, 6 numarali kilitler HATADIR. **Ancak 5 numarali
kasitli kilit (R5) kapali oldugu surece 1-4 ve 6'nin canliya marjinal etkisi
SIFIRDIR.** Bu yuzden bu cycle kilit ACMAZ; kilidin acilip acilmayacagina karar
verecek olcumu kurar. Kilitlerin acilma sirasi R12 sonrasi, veriyle belirlenir.

---

## ROCK'LAR (bagimlilik sirasinda)

### R8 , Guvenlik invarianti (once bu; kapsam kucuk, etki buyuk)

**Kok:** Iki guvenlik varsayimi kodda TUTMUYOR.
(a) "R5 kilidi canlida her yeni riski keser" iddiasi yanlis: kilit yalniz
`core/executor.py:147-157` icinde, yani `execute_buy`'da. `short_executor`,
`options_executor`, `index_parking` ve BearBrain giris yollari bu kontrolu
yapmiyor (`R5-CANLI-GIRIS-KILIDI-KISMI`). Bugun `BOT_MODE=long_only` + opsiyon
kapali oldugu icin fiilen acik degil , ama invariant kodla degil konfigurasyonla
tutuluyor, bu kirilgan.
(b) Ana dongudeki HER `Exception` "API hatasi" sayiliyor (`stock_bot.py:695-699`)
ve `max_consecutive_errors: 3` (config.py:449) esiginde
`close_all_positions(cancel_orders=True)` (stock_bot.py:2227) TUM pozisyonlari
piyasa emriyle tasfiye ediyor. Yani bir KOD hatasi portfoyu likide edebilir.

**Kapsam:**
- Tek merkezi `can_open_new_risk(reason) -> (bool, str)` guard'i; stock, short,
  options, BearBrain ve parking-giris yollarinin HEPSI bunu cagirir. Fail-closed.
- Kill switch: API/kod hatasinda otomatik tasfiye YOK. Davranis "yeni risk acmayi
  durdur + alarm". Otomatik `close_all_positions` yalniz gunluk-zarar esigi ve
  manuel kill icin kalir.
- Hata siniflandirmasi: broker/ag istisnasi ile kod istisnasi ayri sayilir; kod
  istisnasi asla tasfiye tetiklemez.

**Kapatilan bulgular:** R5-CANLI-GIRIS-KILIDI-KISMI (ORTA -> YUKSEK'e cikarildi),
KILLSWITCH-KOD-HATASINI-API-HATASI-SANIYOR, RISK-KILL-SWITCH-ACIL-TASFIYE,
CONSECUTIVE-ERRORS-BASARIDA-SIFIRLANMIYOR

**Done looks like:** Her risk-acan yol icin bir entegrasyon testi guard'in
cagrildigini kanitlar; uc ardisik YAPAY KOD hatasi tek bir emir bile gondermez.

**PROOF:** `py -m pytest tests/test_r8_safety.py -q` (yeni) **ve**
`py -m pytest tests/ -q` **ve** `py tests/test_full_system.py`
(conftest `collect_ignore` ile pytest'e girmiyor , ayri kosulmali)

---

### R9 , Defter dogrulugu: fill ledger + episode toplama

**Kok:** Partial cikis bacagi finansal olarak GORUNMEZ, ve mevcut `record_trade`
semasi bunu duzeltmeye YETMIYOR (order_id / strategy / trade_id / dedupe alani yok).

**Codex'in yakaladigi tasarim hatasi (kabul edildi):** partial fill bir MUHASEBE
bacagidir, tamamlanmis bir TRADE SONUCU degildir. Ilk plan ikisini ayni olay
saniyordu , karli bir partial'i "WIN" olarak ogretip sonradan zarar eden trade'i
yanlis etiketlerdi.

**Kapsam:**
- **Append-only fill ledger:** her dolum bir satir; anahtar broker `order_id`
  (fill id). `_partial_client_id` dedupe anahtari OLAMAZ , `position_manager.py:885`
  her cagride `uuid4()` uretiyor ve `:1290` retry'de yeniden yaziyor (dogrulandi).
- **Provenance submit ANINDA yazilir:** strateji / index_parking / DCA / opsiyon.
  Sonradan sembolden geri cikarilamaz (`Fill` modeli yalnizca symbol/side/qty/
  price/order_id tasiyor).
- **Episode toplama:** pozisyon TAMAMEN kapandiginda tek bir trade sonucu uretilir;
  `update_loss_streak` ve `agent_perf.record_outcome` YALNIZ burada, toplam
  episode PnL'i ile bir kez calisir.
- **Giris fiyati** broker ortalama giris fiyatidir, sinyal fiyati degil.
- **Tarihsel migration:** gecmiste dolmus ama yazilmamis bacaklar broker closed
  fills'ten idempotent, `--dry-run` cikisli, provenance isaretli tek seferlik
  backfill ile onarilir. Bu OLMADAN R10'un cift yonlu mutabakati KALICI FAIL verir.
- Sema surumlenir (`ledger_schema_version`).

**Kapatilan bulgular:** EXIT-PARTIAL-DEFTERE-YAZILMIYOR, OLCUM-DEFTER-KADEMELI-SATISI-YAZMIYOR,
EXIT-DEFTER-GIRIS-FIYATI-SINYAL-FIYATI, OLCUM-PARK-PNL-SIZINTISI,
REKONSTRUKSIYON-STRATEJI-DISI-ISLEMLERI-AYIRMIYOR (ORTA -> KRITIK), AGENT-OUTCOME-LIFO-YANLIS-ATAMA

**Done looks like:** Farkli fiyatli coklu partial + restart + retry + duplicate
replay + final exit senaryosunda ledger toplami, BAGIMSIZ SABIT bir oracle ile
kurusu kurusuna esittir (ayni yardimci fonksiyon iki tarafta kullanilmaz);
ayni dizi iki kez islenirse ledger degismez; streak yalniz episode kapanisinda
bir kez guncellenir.

**PROOF:** `py -m pytest tests/test_r9_ledger.py -q` (yeni) **ve**
`py -m pytest tests/ -q` **ve** `py tests/test_full_system.py`

---

### R10 , Olcum dogrulugu: cift yonlu mutabakat + durum sozlesmesi

**Kok:** 4/4 PASS kapisi gercek parayi acan tetiktir ve su anda YANLIS YESIL verir.

**Kapsam:**
- **Metrik-4 cift yonlu:** broker kapali emir kumesi <-> ledger kumesi, `order_id`
  tabanli kanonik multiset karsilastirmasi (qty/symbol eslemesi duplicate ve
  kismi dolumlari karistirir). Kimliksiz legacy satir PASS degil **UNKNOWN**.
- **Durum sozlesmesi:** PASS / FAIL / **NOT_READY** / **UNKNOWN** ayri exit
  kodlari. `n>=20` VE `>=30 islem gunu` saglanmadan **PASS YASAK**.
- **Metrik-1** yalniz R9'un provenance journal'i ile strateji olarak dogrulanmis
  fill'leri sayar (yerel etikete dayali filtre daireseldir , eksik kaydin etiketi
  de eksiktir).
- **Metrik-3** tautolojisi kaldirilir (reddedilebilir olur).
- **Metrik-2** paydasi yalniz KAPALI episode'lardir.
- **Rapor sozlesmesi:** hesap getirisi + strateji getirisi + SPY getirisi +
  **config/profile fingerprint (hash)** ayni raporda basilir. Agresif paper'in
  4/4'u canli R5 acma kanidi SAYILMAZ , kanit yalniz canli profil ile calisan
  ayrilmis paper mirror'dan gelir.
- Zaman damgalari acikca UTC.

**Kapatilan bulgular:** METRIK4-EKSIK-KAYDI-GOREMEZ, OLCUM-SPY-PARKING-TRADE-SAYILIYOR,
GENEL-PASS-N20-30GUN-KOSULUNU-UYGULAMIYOR, METRIK3-TAUTOLOJI,
METRIK2-PAYDA-KAPALI-ISLEM-KUMESI-DEGIL, CFG-PAPER-LIVE-AYRI-SISTEM (YUKSEK -> KRITIK),
RAPOR-BENCHMARK-VE-EQUITY-OKUMUYOR, OLU-KOD-VE-NAIVE-ZAMAN-DAMGASI,
BACKFILL-KORUMA-IHLALI-BILGIYE-INDIRILMIS

**Done looks like:** Dort fixture: (a) ledger'da EKSIK bacak -> FAIL; (b) SPY park
kari var, strateji zararda -> metrik-1 strateji zararini gosterir; (c) n=6 ->
NOT_READY, PASS ASLA; (d) kimliksiz legacy satir -> UNKNOWN.

**PROOF:** `py -m pytest tests/test_r10_olcum.py -q` (yeni) **ve**
`py -m pytest tests/ -q` **ve** `py tests/test_full_system.py`

---

### R11 , Huni durustlugu (teshis; davranis degisikligi YOK)

**Kok:** Huni sayaclari donmanin sebebini SOYLEYEMEDI , 19 ardisik NO_TRADE
alarmi faydasiz cikti.

**Kapsam:**
- `conf_below_min` BUY/SHORT ayrilir (canli long_only; alinamayacak SHORT redleri
  huniyi kirletiyor).
- Olay sayisi ile **benzersiz sembol sayisi** AYRI tutulur (sembol basina tek
  sayim blok sikligini gizler; ikisi birlikte raporlanir).
- Wash-sale 30-gun yasagi sayaca yazilir; index/ters-ETF sinyalleri sayaclanir.
- **Kesirli/bracket cikmazi , FAIL-CLOSED:** canlida broker bracket'i reddederse
  giris iptal edilir (bugunku davranis korunur) AMA
  `_funnel_bump("gate_block", reason="FRACTIONAL_NO_BRACKET")` yazilir.
  **Iki-adimli canli giris fallback'i ACILMAZ** (mevcut
  `test_live_bracket_rejection_does_not_submit_market_fallback` bilincli olarak
  korunur).
- `reached_executor` sayaci (ONCEKI ADI `would_enter` , yanilticiydi: kapiyi
  gecmek nakit/boyut/broker uygunlugunu garanti etmez). Durust isim, durust iddia.
- `dominant_stage`: **onceki iddia YANLISTI** , `max(...)` esitlikte oncelik
  demetini zaten uyguluyor (`core/funnel.py:303-321`,
  `test_dominant_stage_priority_breaks_ties` bunu kanitliyor). GERCEK sorun:
  `signal_hold` sayica her zaman baskin oldugu icin "en yuksek sayi" teshis
  degeri tasimiyor. Duzeltme: downstream asamalar (gate_block, conf_below_min,
  sector_block, queue_*) sifirdan buyukse `signal_*` asamalari dominant SECILMEZ.
- NO_TRADE alarm metni gercek blokeri ve DOGRU `last_entry_date`'i soyler
  (bugun 2026-07-30 diyor, gercek 2026-07-16).

**Kapatilan bulgular:** HUNI-CONF-SAYACI-KIRLI-NOTRADE-TESHISI-YANLIS,
GATE-HUNI-SAYACLARI-KARSILASTIRILAMAZ, FUNNEL-CONF-BELOW-MIN-KARISIYOR,
FUNNEL-CONF-BELOW-MIN-SHORT-SAYIMI, STRAT-FUNNEL-CONF-KARISIMI,
GATE-WASH-SALE-SAYACSIZ-30GUN-YASAK, GATE-LIVE-LOCK-ZINCIRIN-SONUNDA,
GATE-LIVE-FRACTIONAL-BRACKET-OLU-KAPI (telemetri kismi),
NOTRADE-ALARM-YORGUNLUGU-VE-TESLIM-ONCESI-DEDUPE

**Done looks like:** Sentetik bir gunun huni kayitlarinda dominant asama gercek
blokeri isimlendirir; canlida bracket reddi sessiz kaybolmaz, sayaca yazilir;
`reached_executor` R5 kilidi kapaliyken de dolar (kilidi acmadan once kac giris
olacagini ONCEDEN gosterir).

**PROOF:** `py -m pytest tests/test_r11_funnel.py -q` (yeni) **ve**
`py -m pytest tests/ -q` **ve** `py tests/test_full_system.py`

---

### R12 , SentAgent isaret hatasi (EN SON, AYRI commit)

**Kok:** `FinBERTAnalyzer.analyze()` ISARETLI skor dondurur
(`finbert_analyzer.py:368-371, 386`: negatif etikette `score = -raw_score`), fakat
iki tuketici bunu BUYUKLUK sanir:

1. `news_analyzer.py:308-309` , `nlp_score = -result["score"] * 30`. Skor zaten
   negatif oldugu icin cift negatiflik olusur. **Olculdu:** iyi haber -> `+27.6`,
   kotu haber -> `+26.4`. Yani FinBERT kanali **tek yonlu BUY** uretiyor; bearish
   haber sinyali yapisal olarak imkansiz. (`agent_coordinator.py:212-213`:
   `if combined >= 12: signal = "BUY"`.)
2. `news_analyzer.py:473` , `if result["label"] == "negative" and result["score"] > 0.6`.
   Negatif etikette skor negatif oldugu icin bu kosul **hicbir zaman dogru olamaz**;
   ELEVATED risk dali OLU KOD. (Bu madde denetim envanterinde YOKTU , dogrulama
   sirasinda bulundu.)

**Neden EN SON ve AYRI commit:** Bu duzeltme karar dagilimini degistirir. Codex
hakli olarak "olcum baseline'i kurulmadan davranis degistirme" dedi. Cozum:
duzeltmeyi cycle'dan atmak degil, SIRAYA koymak , R8-R11 baseline'i once
deploy edilir, R12 ayri commit olarak sonra gelir, boylece etkisi ATFEDILEBILIR olur.

**Kapsam:** yalnizca isaret dogrulugu. Esik, agirlik, ajan mimarisi DEGISMEZ.
Tercih edilen duzeltme: `analyze()` sozlesmesini netlestir (`score` = isaretli,
`confidence` = buyukluk) ve iki tuketiciyi sozlesmeye uydur.

**Kapatilan bulgular:** AGENT-SENT-TERS-ISARET, (yeni) SENT-ELEVATED-DALI-OLU-KOD

**Done looks like:** Kotu haber negatif `nlp_score` uretir; iyi haber pozitif;
notr sifir. ELEVATED dali gercek negatif haberde tetiklenir. Regresyon testi
her uc durumu de kilitler.

**PROOF:** `py -m pytest tests/test_r12_sentiment.py -q` (yeni) **ve**
`py -m pytest tests/ -q` **ve** `py tests/test_full_system.py`

---

## NON-GOALS (bu cycle'da KESINLIKLE yapilmayacak)

1. **R5 canli giris kilidini acmak.** `live_entries_enabled` False kalir.
   Acilis on kosulu: canli profil ile calisan mirror'da olcum PASS (n>=20,
   >=30 gun) + Ihsan onayi.
2. **Kayip serisi (loss streak) kilidini zaman asimiyla eritmek.** Codex hakli:
   yeni kanit olmadan riski yeniden acar. Ustelik R9 dogru episode-bazli streak
   uretince sayac zaten dogru degerine oturur. R5 kapaliyken bu kilidin canliya
   marjinal etkisi SIFIRDIR.
3. **Strateji degistirmek.** Yeni sinyal, yeni evren, ML, esik oynamasi , hicbiri.
   (R12 yalnizca bir ISARET HATASI duzeltmesidir, strateji degisikligi degil.)
4. **Canli acik pozisyonlara dokunmak.** GOOGL/MSFT/NVDA bracket bacaklariyla
   korunuyor; cikis geometrisi (BE tavani, TP bandi, trailing) bu cycle'da degismez.
5. **Backtest/canli parity (eski R12).** Ayri bir cycle. Codex hakli: tarihsel
   haber/temel veri, enjekte edilmis clock, broker state ve fill modeli olmadan
   durust olamaz; `--selftest` spy'i mock'la kandirilabilir. Dogru cozum: saf
   `DecisionEngine` + golden event tape uzerinde ayni karar/exit trace'i.
6. **EMA200 cache zehirlenmesi, split-duzeltmeli bar, partial sonrasi TP
   restorasyonu.** Uçu de karar/cikis topolojisini degistirir; olcum baseline'i
   ve soak'tan sonra ayri safety rock'inda.
7. **Deploy.** Ayri ve Ihsan kapili adim (piyasa kapaliyken). Push deploy
   TETIKLEMEZ. Tek Coolify uygulamasi HER IKI konteyneri kaldirir.
8. **Strateji tuning degerleri.** `min_confidence_score`, EMA200/MTF kapilari,
   parking orani, TP/SL bantlari DONDURULMUSTUR. (Yeni GUVENLIK anahtarlari ve
   sema surum alanlari eklenebilir , bu yasak degildir.)

---

## Riskler ve ters etkiler

| Risk | Etki | Onlem |
|---|---|---|
| R9 migration gecmisi yanlis onarir | Olcum kalici bozulur | `--dry-run` zorunlu, provenance isareti, idempotent, once fixture uzerinde |
| R10 sertlesince olcum GERI GIDER (4/4 -> NOT_READY) | "Bozdunuz" gorunumu | Beklenen ve DOGRU sonuc; rapor bunu onceden soyler |
| R11 sayac semasi degisince mevcut testler kirilir | Suite kirmizi | `phantom_count` ve `print_report` imzalari geriye uyumlu (keyword/default) tutulur |
| Partial TP restorasyonu YAPILMADIGI icin kalan yarim sunucu TP'siz kalir | Bilinen acik | Bu cycle'da kabul edilir, RF-ISSUES-3'te KRITIK olarak durur; stop bacagi yerinde |
| Yeni testler gercek paper hesabini mutasyona ugratir (I-12) | Gercek hesap bozulur | `19bd7af` guard'i + conftest tmp state; yeni testler broker cagrisi YAPMAZ |
| `py -m pytest tests/` yesil olmasi kanit sanilir | Sahte yesil | `tests/conftest.py` `test_full_system.py`'yi collection'dan CIKARIR , her PROOF'ta ayrica kosulur |

---

## Ihsan karar maddeleri (bu cycle DISINDA, rapora tasinir)

- **K1 , Stratejik:** Kendi kanitina gore SPY altinda kalan stratejiye devam mi,
  yeniden insa mi, yoksa sermaye SPY'de mi parklanir? (Backtest parity cycle'i sonrasi.)
- **K2 , Sermaye:** Canli hesapta parking %70 -> giris butcesi ~$96, evrenin
  yarisi alinamiyor. Parking dusurulsun mu, yoksa canli "kucuk hesap" kabul
  edilip dusuk fiyatli isimlerde mi calissin?
- **K3 , Cikis geometrisi:** BE +%2.5'te entry x1.003'e cekiliyor (trailing sonra
  yukari ratchet ediyor, mutlak tavan degil) ama risk %4-6. TP bandi canlida
  %8-12 / min_rr 2.0, paper'da %5-7.5 / 1.25. Hangi geometri olculecek?
- **K4 , Ajan mimarisi:** 5 ajandan 3'u fiilen bozuk (SocialAgent Reddit 403 ile
  kalici sessiz, FundAgent kotasi gunun ilk 5 dakikasinda bitiyor, SentAgent
  isareti tek yonlu). Onarilsin mi, yoksa mimari 2 saglam ajana mi indirilsin?
- **K5 , Kill switch politikasi:** Otomatik tasfiye yetkisi R8'de daraltiliyor.
  Gunluk zarar esigi kucuk hesap icin yeniden ayarlansin mi?
