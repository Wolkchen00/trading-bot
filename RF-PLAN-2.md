# RF-PLAN-2 , Cikis Zinciri ve Alarm Onarimi (2026-08-09 Clarity Break, rev-2)

> `/codex` kosusunun kanonik plan dosyasi. `PLAN.md` surum gecmisi, `RF-PLAN.md` onceki
> dongunun (R0-R5) plani oldugu icin bu dongu `RF-PLAN-2.md` olarak yazildi.
> Bulgu envanteri: `RF-ISSUES.md`. Toplanti logu: `RF-SAME-PAGE-LOG-2.md`.

## Core Focus (tek cumle)

Olcum doneminin 4 metrigi GERCEKTEN olculebilir olsun: kademeli satis erisilebilir,
stop asla geri gitmez, koruma alt sistemleri birbiriyle savasmaz ve kritik alarm
telefona botun kendisinden ulasir.

## Kanit (canli veri + broker emir anlik goruntusu, 2026-08-09)

PLTR 2026-08-07 (paper), log + Alpaca closed-orders dokumu birlikte:

1. 13:31:59 BE stop $159.50 (`e7094de1`, cid `r0b-PLTR-L-4876b118...`).
2. 13:32:00 trail bakimi stopu $156.56'ya INDIRDI (`4926c1d5`) , regresyon (I-2).
   Kanonik $159.50'ta kaldi (I-3). Trail zinciri $157.63'e tirmandi (`125a0a0e`).
3. 13:35:13 mutabakatci kanonigi ($159.50) geri kurmak istedi. deterministic id
   f(PLTR, LONG, 159.50, 15) = **tarihsel `e7094de1`in cid'i** -> unique cakismasi (I-4).
4. **Basarisiz replace mevcut saglam stopu YOK ETTI**: `125a0a0e` CANCELED,
   `replaced_by=null`. Deneme 2..6 no-leg submit ayni cakisan id ile 40010001 yedi.
   Pozisyon ~13:34:20-13:35:39 arasi GERCEKTEN ciplakti; CRITICAL alarm DOGRUYDU (I-5 rev).
   13:35:39'da trail dongusu farkli fiyat/id ile tesadufen yeniden kapatti.
5. +3.01..3.62% arasi hicbir dongude kademeli satis tetiklenmedi (I-1); 14:07'de tam-qty
   TAKE_PROFIT +6.3% (sistemin ilk TP'si , cikis bandi calisiyor).
6. Olcum: n=1, gun=7/30; metrik-2 0/1 FAIL , I-1 kapanmadan 4/4 PASS imkansiz.

## Roklar (bagimlilik sirasiyla)

### ROCK 1 , Cikis karar zinciri onarimi (I-1, I-2, I-3, I-10, I-11)

`core/position_manager.py::manage_positions` long blogu (~259-396); short blogunda
3b muadili YOK , short'a YENI davranis eklenmez, yalniz monotonluk invariant testi yazilir.

1. **3b'yi karar zincirinden cikar.** Trailing sunucu-SL bakimi if/elif zincirinden
   ayrilir; karar degerlendirmesinden SONRA, yalniz o dongude HICBIR cikis denemesi
   yapilmadiysa calisir (`exit_action_attempted` bayragi: STOP/TP/TRAILING-SAT/KADEMELI
   herhangi biri denendiyse bakim o donguyu atlar , kademeli sonrasi bayat-qty stop
   guncellemesi olmaz). Karar onceligi korunur: STOP > TP > TRAILING-SAT > KADEMELI.
2. **Monotonluk klampi SINIRDA.** `_update_server_stop_loss` girisinde: LONG icin
   etkin hedef = max(istenen, aktif brokerdaki dogrulanmis kapsayan stop, kanonik
   `stop_loss_price`) (SHORT: min); istenen hedef aktif stoptan KOTUYSE emir yenilenmez,
   mevcut daha-iyi stop dogrulanip `NOOP_BETTER_PROTECTED` (verified=True,
   at_target=True) doner , saglikli koruma alarm uretmez. BE fiyati klampa yalniz
   `breakeven_set` dogrulanmissa girer. Boylece TUM cagiranlar (BE, trail, mutabakat,
   partial-sonrasi) regresyondan korunur. Kacis kapisi YOK: bilincli stop gevsetme
   gerekirse ayri, onaylanmis bir rock olur.
3. **Tek yazar / kanonik esitleme + `last_server_sl`in KALDIRILMASI.** Sunucu stop
   guncellemesi dogrulaninca `stop_loss_price` ayni degere cekilir + `_stash_exit_flags`
   cagrilir (I-11). Guncelleme esigi (`+0.10` histerezisi) dogrudan kanonik uzerinden
   hesaplanir; `last_server_sl` alani ve okuyup yazan yerler silinir (bayat ikinci kopya).
4. **Kademeli satis dogrulugu.** Sira ve semantik:
   a. SUBMIT'TEN ONCE kalici partial-intent yazilir (client_order_id dahil); restartta
      ayni id brokerdan uzlastirilmadan yeni yari-satis emri GONDERILMEZ (crash'te
      cift yari-satis olmaz).
   b. Yari-satis oncesi iptal terminal-dogrulanir (`_wait_exit_cancellations`); bekleme
      sirasinda TP/SL bacagi DOLMUS olabilir (terminal=FILLED) , submit'ten once pozisyon
      tarafi/adedi brokerdan YENIDEN okunur, bayat adetle SELL gonderilip net-SHORT
      acilamaz (bu senaryonun testi zorunlu).
   c. Dolum semantigi tanimli: beklenen yari-adet, birikimli `filled_qty`, terminal
      durum, adet toleransi (fractional yuvarlama); tek-hisselik kismi dolum "yari
      satildi" SAYILMAZ , kismi-dolum testi yazilir.
   d. `partial_sold=True` yalniz gozlenen (tolerans ici) dolumdan sonra; basarisiz
      submitte False kalir.
   e. Bounded dolum suresi: sure dolarsa emir iptal/terminal-dogrulanir; her sonucta
      (dolum, red, timeout) gercek kalan adet brokerdan okunup tam kapsayan stop GERI
      KURULUR; kurulamazsa CRITICAL , stoplar iptal edilmis pozisyon ciplak birakilamaz.
   f. Partial karari + emir id'si + esik-gozlem event'i kalici olarak `state_*`'a
      yazilir (Rock 3 metrik-2 bunu tuketir).
   g. **Intent durum makinesi tanimli:** `INTENT -> SUBMITTED -> (PARTIAL | FILLED |
      TERMINAL_NOFILL)`. Terminal kismi dolumda retry YALNIZ orijinal yari-hedefin
      eksik kalan adedi icin yapilir (yeni yari-hedef hesaplanmaz); FILLED intent'i
      kapatir; TERMINAL_NOFILL'de intent kapanir ve partial_sold False kalir , AMA
      kalici retry butcesi vardir (sembol-basina, ornegin gunde 3 deneme): butce
      asilinca o gun yeni intent ACILMAZ ve WARNING alarmi uretilir; boylece esik
      ustundeki her dongunun cancel-submit-restore churn'u engellenir (ardisik-ret
      testi zorunlu).

**Done looks like / test kosullari:** PLTR replay (gercek runtime config merge'iyle,
paper %3 ve canli %5 ayri ayri): BE kur -> trail dongusu stopu dusuremez (sunucuya
dusuk hedef gitmez); +%3.01 dongusunde yari satis tetiklenir; dolum yokken
`partial_sold` yazilmaz; dolum sonrasi kalan adet tam kapsanir ve TEK aktif stop kalir;
submit reddinde `partial_sold=False`; trail dogrulaninca kanonik == sunucu hedefi.
ZORUNLU ek senaryolar: (i) submit-sonrasi-crash/restart -> ayni intent cid'i brokerdan
uzlastirilir, IKINCI yari-satis emri cikmaz; (ii) dolum timeout -> emir terminal-dogrulanir,
gercek kalan adedi kapsayan TEK stop geri kurulur; (iii) submit reddi -> stop geri kurulur;
uc senaryoda da cift satis yok + tek kapsayan stop assertion'i. Short zinciri icin
monotonluk invariant testi. Mevcut testler yesil.

**PROOF (Git Bash):** `PYTHONIOENCODING=utf-8 py -m pytest tests -q --ignore=tests/test_full_system.py`
ve `PYTHONIOENCODING=utf-8 py tests/test_full_system.py` (yeni: `tests/test_exit_chain_repair.py`).

### ROCK 2 , Koruma kimlik cakismasi + ciplak pencere (I-4, I-5 rev)

`core/protection.py` + `core/position_manager.py::_update_server_stop_loss` ve mutabakat:

1. **Cagri-basi UUID tuzu.** `deterministic_client_order_id(..., salt)` , salt,
   `_update_server_stop_loss` cagrisi basina BIR kez uretilen TAM `uuid4().hex`
   degeridir ve hash GIRDISINE katilir (cikti uzunlugunu digest zaten sabitler;
   kisaltilmis 32-bit tuz kullanilmaz): ayni cagrinin retry'lari ayni id (korelasyon),
   yeni cagri yeni id (tarihsel cakisma biter). 48 karakter siniri korunur.
2. **40010001 = kesin yeniden-okuma.** Unique reddi alininca kor tekrar yerine cid ile
   emir sorgulanir (`get_order_by_client_id`): bizim onceki kabulumuzse ve aktif/kapsiyorsa
   VERIFIED; tarihsel/terminal ise bir SONRAKI attempt yeni salt'la id uretir.
3. **Replace-yikimi telafisi (PLTR vakasi).** Her replace denemesinden sonra eski emrin
   akibeti dogrulanir: eski stop CANCELED ve aktif replacement yoksa taze cid'li no-leg
   submit'e dusulur , AMA once kisa bounded poll ile eski emir + replacement zinciri +
   acik stoplar uzlastirilir (broker eventual-consistency gecikmesinde replacement aslinda
   dogmus olabilir; ikinci tam-adet stop yaratilmaz). Ciplak pencere retry-periyoduna
   degil saniyelere iner; alarm metnine olculen ciplak-pencere suresi yazilir.
4. **Kapsama vs hedef ayrimi.** `ProtectionResult`e `at_target` alani + yeni
   `DEGRADED_PROTECTED` outcome: aktif tam-qty stop var ama hedeften KOTU yonde sapmis.
   Sapma siddeti yon-bilinclidir: LONG'da (kanonik - aktif_stop) / entry, SHORT'ta
   (aktif_stop - kanonik) / entry; deger > `protection_drift_critical_pct` (config,
   varsayilan 0.01) ise CRITICAL, degilse WARNING , her iki yon icin esik-alti/ustu
   testleri yazilir. Gercek ciplaklik her zaman CRITICAL. `verified=True` yalniz gercek
   kapsama varken doner; cagiranlar `at_target` ile ayirt eder.
5. **Gecici 5xx toleransi.** Mutabakat sorgusunda islem-turu-basina sayac: 5xx/baglanti
   hatasi 1 kez kisa backoff'la denenir, basarida sifirlanir, ikinci ardisik hata alarm
   uretir; sayac bellek-ici (restart sifirlar, dokumante).

**Done looks like / test kosullari:** Fake-client testleri: (a) ayni hedefe ikinci
CAGRI farkli cid uretir, unique reddi yasanmaz; (b) unique reddinde cid-sorgusu onceki
kabulu bulursa VERIFIED; (c) replace eski stopu yok edip replacement birakmadiginda
bounded uzlastirma sonrasi taze cid'li submit kosulur (PLTR replay , birebir bu dizi);
(c2) GECIKMELI replacement: fake broker replacement'i poll sirasinda gorunur kilar ->
no-leg submit SAYISI SIFIR kalir (ikinci tam-adet stop dogmaz); (d) aktif-ama-sapmis
stop DEGRADED_PROTECTED + yon-bilincli esik testleri (LONG ve SHORT, esik alti/ustu);
(e) tek 500 alarm uretmez, ikinci ardisik 500 uretir, arada basari sayaci sifirlar.

**PROOF:** Rock 1 ile ayni komutlar (yeni: `tests/test_protection_collision.py`).

### ROCK 3 , Olcum raporu guvenilirligi (I-6, I-8 gorunurlugu)

`tools/olcum_raporu.py`:

1. `--since` varsayilani: dosya basinda tek sabit `MEASUREMENT_START = "2026-07-30"`
   (version-control altinda; ayri mutable meta dosyasi YOK). Acik `--since` override kalir.
2. Rapor basligi: "Olcum donemi: <since> -> <bugun> (gun=X/30, n=Y/20)". Tempo
   projeksiyonu: mevcut tempoyla donem sonu beklenen n; 20'ye ulasamayacaksa
   "TEMPO UYARISI" satiri.
3. **Metrik-2 duzeltmesi:** payda, botun kalici partial-event telemetrisinden kurulur
   (Rock 1 event'i: esik gozlendi / tetiklendi / dolum); pay, bot event'indeki emir
   id'sinin broker dolumuyla eslesmesiyle sayilir (manuel satis / rastgele parcali dolum
   PASS uretemez). Iki durustluk kurali: (a) telemetri-oncesi donem islemleri (PLTR dahil)
   paydadan SILINMEZ , bar/log kaniti +%3'u gosteriyorsa "legacy miss" olarak payda ve
   basarisizlik hanesinde kalir (mevcut 0/1 kaniti korunur); (b) bar/log kaniti +%3'u
   gosterirken telemetri event'i YOKSA bu veri-butunlugu FAIL'idir (event ureticisi bozuk
   demektir), sahte PASS uretilmez.
4. **Metrik-4 kaynak durustlugu , otoriter kaynak hiyerarsisi:** Metrik-4'un OTORITER
   kaynaklari (1) broker closed-orders sorgusu (rejected stop emirleri dogrudan sayilir)
   ve (2) kalici telemetri/state (`state_*/telemetry.jsonl`, alarms.jsonl, trade_history)
   , ikisi de olcum araliginin tamamini yapisal olarak kapsar. Ham/rotate loglar yalniz
   YARDIMCI kanittir; yoklugu tek basina UNKNOWN uretmez ama otoriter kaynaklardan biri
   eksik/erisilemez/kapsami dogrulanamaz ise metrik UNKNOWN sayilir ve FAIL'e ceker.
   Telemetri-oncesi donem (2026-07-30..deploy) icin build sirasinda mevcut alarms.jsonl +
   broker kayitlarindan tek seferlik backfill/snapshot alinir ve rapor bunu "backfill"
   etiketiyle gosterir.
5. Rapora bilgi amacli "sistem invariant" bolumu: donemdeki KORUMA alarmi sayisi,
   stop-regresyon gozlemi, unique-cakisma sayisi. Kaynak: rotate olabilen loglar DEGIL,
   kalici append-only telemetri , Rock 1/2 invariant ve rejection olaylarini
   `state_*/telemetry.jsonl`e (alarms.jsonl gibi named-volume'da yasayan) yazar; 30 islem
   gunu ~42 takvim gunune yayilsa da kapsama kaybolmaz, Metrik-4 kalici UNKNOWN'a dusmez.
   4-metrik kapisinin TANIMI degismez (Ihsan onayli kapi); bu bolum karar gorunurlugu icin.
6. Salt-okunurluk korunur; emir API'si cagrilmaz.

**Done looks like:** Bayraksiz kosum since=2026-07-30, n/gun dogru, tempo uyarisi;
eksik dizinle metrik-4 UNKNOWN/FAIL; birim testte payda bot-telemetrisinden kuruluyor.
ZORUNLU FAIL assertion'lari: (i) PLTR-tipi legacy miss paydada kalir ve basarisizlik
sayilir; (ii) bar/log +%3 gosterirken telemetri event'i yok -> veri-butunlugu FAIL.

**PROOF:** `py tools/olcum_raporu.py` (yerel .env paper anahtarlari) + `tests/test_olcum_defaults.py`
(arg-parse/projeksiyon/kaynak-durustlugu; API mock).

### ROCK 4 , Bot ici ntfy yayincisi (I-7, I-9)

Notifier katmani , tum kritik olaylar tek dayanikli yayincidan gecer:

1. **Kritik olay envanteri:** `notify_critical` VE `notify_kill_switch` dahil kritik
   yollarin tamami ortak publisher'a baglanir; `stock_bot.py` ana dongu ardisik-hata
   yolu (bugun yalniz `logger.critical`) da publisher'a baglanir. Envanter build
   sirasinda cikarilir, testte SABITLENIR (envanterdeki her yol icin publisher-cagrisi
   asserti); bypass kalmaz.
2. `NTFY_TOPIC` YALNIZ env'den okunur; YENI kod topic string icermez (bos -> kapali).
   Not: topic bugun `PLAN.md` dokumantasyonunda gecmektedir , build sirasinda repo
   dokumanlarindaki topic gecisleri `<NTFY_TOPIC>` yer tutucusuyla redakte edilir;
   rotasyon+auth ayri Ihsan karar maddesidir (RF-ISSUES'ta tarihli). Doluysa: once
   alarms.jsonl'e benzersiz `id` ile yazilir, sonra `https://ntfy.sh/<topic>`e POST
   (timeout 5s); basarida `{"kind":"DELIVERY","ref":<id>}` marker'i append edilir.
   POST hatasi alarmi dusurmez (jsonl + VPS koprusu backstop).
3. **Cift teslim bastirmasi , acik at-least-once sozlesmesi:** VPS koprusu DELIVERY
   marker'i olan kayitlari atlar (kopru script'i deploy adiminda guncellenir; degisiklik
   `tools/vps_bridge_patch.md` olarak repoya yazilir , kopru repo disi oldugu icin).
   POST-basarili-ama-marker-yazilamadan-crash penceresi KABUL edilir: sozlesme
   "at-least-once, best-effort dedup"tir; kacirilan alarm olamaz, nadir cift push
   olabilir. Bu sozlesme dokumante edilir ve testte adlandirilir.
4. Istemci-ici cooldown anahtari: tur+sembol+durum-kodu (KORUMA:PLTR:naked ile
   KORUMA:PLTR:drift birbirini susturmaz; KORUMA:AMZN ayri). NO_TRADE gunde 1 (mevcut
   tasarim), digerleri 4h. Cooldown YALNIZ basarili dogrudan gonderimden sonra baslar;
   basarisiz POST'ta bounded retry uygulanir, cooldown baslatilmaz.
5. Sonuc modeli ayrisir: `persisted` (jsonl yazildi) ve `direct_delivered` (telegram
   veya ntfy basardi) ayri raporlanir; `notify_critical` donusu direct_delivered'i
   yansitir. "TESLIM EDILEMEDI" ERROR'u yalniz persisted dahil TUM kanallar
   basarisizsa; jsonl basarili ama dogrudan teslim yoksa gunluk tek WARNING (kopru
   backstop'una dusuldugu acikca yazilir).
6. Telegram karari rafta kalir; bu rock dokunmaz.

**Done looks like / test:** Fake HTTP: kritik alarm POST uretir + DELIVERY marker
yazilir; POST hatasi alarmi dusurmez ve ERROR uretmez (jsonl basarili oldugu icin
WARNING); cooldown ayni tur+sembol+durum-kodunu bastirir, farkli sembolu VE ayni
sembolde farkli durum-kodunu (PLTR:naked -> PLTR:drift) BASTIRMAZ; basarisiz POST ->
retry -> basarili POST akisi assertion'lanir (fail-then-success) ve cooldown ancak
basarida baslar; kill-switch + ana-dongu-ardisik-hata yollari publisher'dan gecer
(envanter testi). Gercek topic'e testte POST YOK. Deploy kapisinda: konteynerden
benzersiz canary mesaji + Ihsan'in telefonda gorsel onayi (ayri, Ihsan-kapili adim).

**PROOF:** Rock 1 ile ayni komutlar (yeni: `tests/test_ntfy_notifier.py`).

## Kisitlar

- Canli config'e ve strateji parametrelerine (TP bandi, EMA200, min_confidence) DOKUNULMAZ.
- `stop_loss_pct` isaretsiz mesafe kalir (6 okuyucu var); kanonik tetik `stop_loss_price` esastir.
- Testler: pytest yalniz birim dosyalar; `tests/test_full_system.py` dogrudan `py` ile
  kosulur; tum proof komutlari Git Bash sozdiziminde (`PYTHONIOENCODING=utf-8 py ...`).
- SPY parking istisnalari (SKIPPED_PARKING) aynen korunur.
- 4-metrik kapisinin tanimi (Ihsan onayli) degistirilmez; yalniz olcum DURUSTLUGU onarilir.
- Deploy bu planin PARCASI DEGIL: iki konteyner birden yeniden kurulur, canli para
  hesabini etkiler -> Ihsan onayi + tercihen piyasa kapaliyken (ayri kapi).
  ntfy topic rotasyonu/auth = Ihsan karar maddesi (DEFER, RF-ISSUES'a islenir).

## Non-goals

- R5 kilit acma, Telegram token karari, strateji/tempo degisiklikleri (I-8), dashboard,
  yeni giris sinyalleri, alinea-invest benzeri yeni ozellik gelistirme (ayri dongu).
- Short zincirine yeni trailing-server davranisi eklemek (bugun o dal yok; yalniz invariant testi).
