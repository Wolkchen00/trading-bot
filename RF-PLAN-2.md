# RF-PLAN-2 , Cikis Zinciri ve Alarm Onarimi (2026-08-09 Clarity Break)

> `/codex` kosusunun kanonik plan dosyasi. `PLAN.md` surum gecmisi, `RF-PLAN.md` onceki
> dongunun (R0-R5) plani oldugu icin bu dongu `RF-PLAN-2.md` olarak yazildi.
> Bulgu envanteri: `RF-ISSUES.md` (I-1..I-11).

## Core Focus (tek cumle)

Olcum doneminin 4 metrigi GERCEKTEN olculebilir olsun: kademeli satis erisilebilir,
stop asla geri gitmez, koruma alt sistemleri birbiriyle savasmaz ve kritik alarm
telefona botun kendisinden ulasir.

## Kanit (canli veriden, 2026-08-09)

- PLTR 2026-08-07: BE $159.50 kuruldu, 1 sn sonra sunucu stop $156.56'ya INDI (I-2);
  +3.01..3.62% arasi hicbir dongude kademeli satis tetiklenmedi (I-1); mutabakatci
  $159.50 hedefine donerken 5x `client_order_id must be unique` yedi (I-4) ve pozisyon
  aktif $157.63 stopla korunurken CRITICAL "koruma kurulamadi" atildi (I-5).
- Olcum: n=1, gun=7/30; metrik-2 0/1 FAIL , I-1 kapanmadan 4/4 PASS imkansiz.

## Roklar (bagimlilik sirasiyla)

### ROCK 1 , Cikis karar zinciri onarimi (I-1, I-2, I-3, I-10, I-11)

`core/position_manager.py::manage_positions` (long: ~259-396, short: ~465-585):

1. **3b'yi karar zincirinden cikar.** Trailing sunucu-SL bakimi bir "cikis karari" degil,
   bakim isidir: if/elif zincirinden ayrilip karar degerlendirmesinden SONRA kosulsuz
   `if` olarak calisir (SELL/TP/TRAILING karari cikmadiginda). Boylece kademeli dal
   (+%3) erisilebilir olur. Karar onceligi korunur: STOP > TP > TRAILING-SAT > KADEMELI.
2. **Monotonluk klampi.** Long: sunucuya onerilecek stop = max(trail adayi, kanonik
   `stop_loss_price`, BE fiyati); mevcut kanonik stoptan DUSUK hicbir deger sunucuya
   gonderilmez. Short'ta ayna (min). `last_server_sl` baslangici 0 degil, dogrulanmis
   kanonik stop.
3. **Tek yazar / kanonik esitleme.** Sunucu trail guncellemesi dogrulaninca
   `stop_loss_price` da ayni degere cekilir + `_stash_exit_flags` cagrilir (I-11).
   Mutabakatcinin gordugu kanonik hedef ile sunucu stopu ayni kaynaktan beslenir.
4. Short blogu (~465-585) ayni kaliba sahipse ayni onarim orada da uygulanir.

**Done looks like:** PLTR replay senaryosu testte: BE kur -> trail dongusu stopu
DUSUREMEZ; +%3.01 dongusunde yari satis tetiklenir; trail dogrulaninca kanonik ==
sunucu hedefi. Mevcut testler yesil kalir.

**PROOF:** `set PYTHONIOENCODING=utf-8 && py -m pytest tests -q --ignore=tests/test_full_system.py`
ve `py tests/test_full_system.py` (yeni testler: `tests/test_exit_chain_repair.py`).

### ROCK 2 , Koruma kimlik cakismasi + alarm dogrulugu (I-4, I-5)

`core/protection.py` + `core/position_manager.py::_update_server_stop_loss` ve mutabakat:

1. **Cagri-basi tuz.** `deterministic_client_order_id` cagri icinde sabit, cagrilar
   arasinda benzersiz olacak: fonksiyon imzasina istege bagli `salt` eklenir;
   `_update_server_stop_loss` cagri basina BIR kez uretilmis kisa nonce'u salt olarak
   gecirir (ayni cagrinin retry'lari ayni id, yeni cagri yeni id). 40010001 alindiginda
   kor tekrar yerine: mevcut aktif stop adaylari yeniden okunur (zaten yapiliyor) ve
   id yeniden uretilmez , cakisma dogal olarak biter.
2. **Ciplaklik vs sapma ayrimi.** Deadline-sonu fallback'te aktif tam-qty kapsayan stop
   varsa sonuc VERIFIED-sapmali olmali: CRITICAL yerine WARNING "stop hedeften sapmis
   (aktif $X, hedef $Y)"; CRITICAL yalniz gercek ciplaklikta. PLTR vakasinin neden
   VERIFIED donmedigi once testle REPRODUCE edilir (fake client, log dizisi birebir),
   kok neden kapatilir.
3. **Gecici 500 toleransi.** Mutabakat sorgusu HTTP 5xx/baglanti hatasinda 1 kez kisa
   backoff'la yeniden denenir; ancak ikinci ardisik hata alarm uretir (I-5, 08-05 vakasi).

**Done looks like:** Fake-client testleri: (a) ayni hedefe ikinci cagri unique-reddi
YASAMAZ; (b) unique-reddi senaryosunda aktif eski stop varken sonuc CRITICAL degil
VERIFIED+WARNING; (c) tek 500 alarm uretmez, ikincisi uretir.

**PROOF:** Rock 1 ile ayni komutlar (yeni testler: `tests/test_protection_collision.py`).

### ROCK 3 , Olcum raporu guvenilirligi (I-6, I-8 gorunurlugu)

`tools/olcum_raporu.py`:

1. `--since` varsayilani: `state_paper/olcum_meta.json` varsa oradan, yoksa sabit
   `MEASUREMENT_START = 2026-07-30` (dosya basinda tek satir sabit). Bugune dusme tuzagi olmez.
2. Rapor basligi: "Olcum donemi: <since> -> <bugun> (gun=X/30, n=Y/20)".
3. Tempo projeksiyonu: mevcut tempoyla donem sonunda beklenen n; 20'ye ulasamayacaksa
   "TEMPO UYARISI" satiri (Ihsan karar maddesi I-8'i gorunur kilar).
4. Salt-okunurluk korunur; emir API'si cagrilmaz.

**Done looks like:** Bayraksiz kosum since=2026-07-30 ile n=1/gun=7 basar + tempo uyarisi.

**PROOF:** `py tools/olcum_raporu.py` (yerelde .env paper anahtarlariyla) + birim test
`tests/test_olcum_defaults.py` (arg-parse/projeksiyon; API cagrisi mock).

### ROCK 4 , Bot ici ntfy yayincisi (I-7, I-9)

`core/` notifier katmani (TelegramNotifier'in yaninda):

1. `NTFY_TOPIC` env degiskeni (bos -> kapali). Doluysa `notify_critical` alarmi
   alarms.jsonl'e yazdiktan SONRA `https://ntfy.sh/<topic>`e HTTP POST eder
   (timeout 5s, basarisizlik alarmi engellemez , jsonl + VPS koprusu backstop kalir).
2. Alarm-turu-basina istemci-ici cooldown (NO_TRADE gunde 1 zaten tasarim; digerleri
   4h , VPS koprusuyle ayni semantik).
3. "notifier DEVRE DISI (kimlik yok)" per-alarm ERROR'u gunde 1 WARNING'e indirilir
   (teslim artik ntfy ile saglaniyorsa ERROR yaniltici).
4. Telegram karari rafta kalir; bu rock ona dokunmaz.

**Done looks like:** Testte fake HTTP: kritik alarm ntfy POST uretir, POST hatasi
alarmi dusurmez, cooldown ikinci alarmi bastirir. Gercek topic'e testte POST YOK.

**PROOF:** Rock 1 ile ayni komutlar (yeni testler: `tests/test_ntfy_notifier.py`).
Deploy sonrasi tek elle dogrulama Ihsan kapisinda.

## Kisitlar

- Canli config'e ve strateji parametrelerine (TP bandi, EMA200, min_confidence) DOKUNULMAZ.
- `stop_loss_pct` isaretsiz mesafe kalir (6 okuyucu var); kanonik tetik `stop_loss_price` esastir.
- Testler: pytest yalniz birim dosyalar; `tests/test_full_system.py` dogrudan `py` ile kosulur.
- SPY parking istisnalari (SKIPPED_PARKING) aynen korunur.
- Deploy bu planin PARCASI DEGIL: iki konteyner birden yeniden kurulur, canli para
  hesabini etkiler -> Ihsan onayi + tercihen piyasa kapaliyken (ayri kapi).

## Non-goals

- R5 kilit acma, Telegram token karari, strateji/tempo degisiklikleri (I-8), dashboard,
  yeni giris sinyalleri, alinea-invest benzeri yeni ozellik gelistirme (ayri dongu).
