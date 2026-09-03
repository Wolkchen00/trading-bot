# RF-PLAN-4.md , Clarity Break #4: ölü ağırlığı kaldır, kanıtı gölgede topla, kapıyı ölçüye bağla

> Tarih: 2026-09-03 (Los Angeles). Sürücü: Claude (Visionary) + Codex (Integrator).
> Baseline: `git HEAD = aa70225`, `py -m pytest tests/ -q` -> **250 passed** (Claude koştu, 9.39s).
> **Revizyon 4** , Codex Round 1-3 bulgularından sonra. Kayıt: RF-SAME-PAGE-LOG-4.md.
> Önceki döngüler: RF-PLAN.md, RF-PLAN-2.md, RF-PLAN-3.md (R1..R14 tamamlandı).

## CORE FOCUS (tek cümle)

**Canlı hesabın değerini ölçülmüş alfa ile büyütmek; ölçüm kapısı geçmeden canlı
giriş kilidi açılmaz ve hiçbir araç "çalışıyor" diye yalan söylemez.**

---

## TEŞHİS (2026-09-03, hepsi koda karşı doğrulandı)

Canlı hesap $494.69, 7 gündür 0 işlem. Sebep arıza değil, `LIVE_LOCK_R5` kilidi
(`core/risk_guard.py:82`, `config.py:466`). Kilit önceki döngüde kondu çünkü
strateji alfa üretmiyordu (paper 2 ay -%1.55 vs SPY +%4.36; canlı strateji katkısı ~0).

### Doğrulanmış ölçümler (bu oturum)

- `GET reddit.com/r/wallstreetbets/search.json` -> **HTTP 403**, 189908 bayt HTML.
- `GET query1.finance.yahoo.com/v10/.../AAPL` -> **HTTP 401**. `_get_yahoo_fallback`
  iki kat ölü: Yahoo cevap vermiyor VE yalnız anahtar YOKSA çağrılıyor
  (`fundamental_analyzer.py:54-55`), yani kota tükendiğinde asla devreye girmiyor.
- `GET alphavantage.co OVERVIEW AAPL` -> **HTTP 200, gerçek veri**. Kaynak sağlam,
  bütçe israf ediliyor.
- Üretim telemetrisi (26 Ağu, PLAN.md): FundAgent veri_yok=%100, SocialAgent veri_yok=%100.
- `py -m pytest tests/ -q` -> 250 passed.

### DÜZELTMELER , ilk teşhisimdeki üç hata (Codex yakaladı, hepsi doğrulandı)

**1. "Çoğunluk kapısı işlemleri engelliyor" YANLIŞTI.** Çoğunluk kapı değil.
`agent_coordinator.py:436` `buy_count >= 3` bir `elif` zincirinin ilk dalı;
satır 442 `weighted_score > 15` çoğunluk olmadan da işlem açar. Çoğunluğun tek
etkisi satır 475 `confidence *= 1.2`. Bu döngüde çoğunluğa DOKUNULMAZ.

**2. `WEIGHTS` sınıf sabiti üretimde ÖLÜ.** `stock_bot.py:1298` her sembolde
`coordinator.WEIGHTS = dynamic_weights` yapıyor; `agent_performance.py:219-221`
performansa göre ayarlanmış ham ağırlıkları toplamı 1.0 olacak şekilde normalize
ediyor. **Sonuç: SocialAgent'ın normalize payı SABİT 0.15 DEĞİL**, performansla
değişir. Sabit sayıya dayanan her invaryant yanlıştır.

**3. `tools/olcum_raporu.py` toplayıcısının yeri.** Satır 77-88 yalnız `Status`
enum'u ve çıkış kodları. Gerçek toplayıcı **`gate_status()`, satır 1160**, ve
zaten fail-closed: `FAIL` varsa FAIL, `UNKNOWN` ya da bilinmeyen durum varsa
UNKNOWN, `strategy_trade_count < 20 or elapsed_days < 30` ise NOT_READY, ancak
tam 4 metrik ve hepsi PASS ise PASS.

### Ağırlık ölçeği hakkında kritik gerçek

`weighted_score` HAM bir toplamdır, toplam ağırlığa hiç bölünmez:

```python
weighted_score += signal_value * weight * vote.confidence   # satır 419-423
elif weighted_score > 15:                                    # satır 442 , MUTLAK eşik
confidence = abs(weighted_score) * 2.0                       # satır 473
```

Kapatılan bir ajanın payını kalanlara dağıtmak **her giriş kapısını gizlice
gevşetir.** Ölçülmüş örnek: tek başına TechAgent BUY conf 60 -> `ws = 0.25*60 = 15`
-> `> 15` değil -> HOLD. Ağırlıklar renormalize edilirse `ws = 0.294*60 = 17.65`
-> BUY, `confidence = 35.3` -> paper eşiği 30'u geçer. Aynı kanıt, farklı karar.
**Bu döngüde renormalizasyon YASAK.**

---

## İHSAN'IN KARARLARI

1. **Canlı kilit:** önce onar, sonra aç. `LIVE_ENTRIES_ENABLED` bu döngü boyunca kapalı.
2. **SocialAgent:** kaldır, Reddit OAuth yok.
3. **AV kotası:** AV haberi kapatılsın, 25 çağrının tamamı temel analize gitsin,
   haber Marketaux'tan gelsin, ikinci anahtar alınmasın.

---

## ROCK'LAR (bağımlılık sırasında)

### R15 , SocialAgent'ı maskele, ölçeği BOZMA, listeyi ÇÖKERTME

**Neden:** 0.15 nominal ağırlık kalıcı sıfır ve sembol başına 12 saniye bloklayıcı
uyku ödeniyor. Ama ölçek korunmalı ve liste kısalınca kod çökmemeli.

**ÖNCE DÜZELTİLMESİ GEREKEN ÇÖKME:** `core/agent_coordinator.py:427`
`risk_vote = votes[4]  # RiskAgent her zaman son`. SocialAgent oy kümesinden
çıkınca liste 4 elemana düşer ve bu satır **IndexError** fırlatır, her kararda.
-> RiskAgent **ada göre** bulunur; tam olarak bir RiskAgent oyu yoksa fail-closed
davranılır (kararı sessizce sürdürmez).

**Kapsam:**
- `config.py`: `AGENT_CONFIG["social_agent_enabled"]`, varsayılan **False**,
  env ile açılabilir. Kod silinmez.
- `agent_coordinator.py:427` indeks erişimi ada göre aramayla değiştirilir.
- **Maskeleme, renormalizasyon değil.** Önce mevcut BEŞ ajanlık normalize vektör
  DEĞİŞTİRİLMEDEN hesaplanır, SONRA SocialAgent maskelenir. Payı dağıtılmaz.
- **İki invaryant birlikte (biri tek başına yetmez):**
  1. `active_sum == 1.0 - maskeleme_oncesi_SocialAgent_agirligi`;
  2. **her etkin ajanın maskeleme sonrası ağırlığı, maskeleme öncesi normalize
     ağırlığına EŞİT** (tolerans dahilinde). Birinci invaryant tek başına,
     ağırlıkların kendi aralarında yeniden dağıtılmasına izin verir (toplam
     korunurken Tech 0.25 -> 0.30 olabilir); ikincisi bunu kapatır.
- Tek "enable-aware" ağırlık çözücü, hem `AgentCoordinator` hem
  `AgentPerformanceTracker` tarafından kullanılır.
- `stock_bot.py:1286-1288`: ajan kapalıyken `analyze_social` **hiç çağrılmaz**.
- `core/agent_stats.py`: şema sürümlenir ve göç ettirilir; `data_ok` üçe çıkar:
  `ok` / `SOURCE_UNAVAILABLE` / `DISABLED_BY_POLICY`.
- Çoğunluk mantığına DOKUNULMAZ.

**Done looks like:** SocialAgent kapalıyken bot çökmüyor, 4 ajan oy veriyor, her
etkin ajanın ağırlığı birebir korunuyor, `analyze_social` çağrısı 0, telemetri
`DISABLED_BY_POLICY` diyor, ve üretimdeki bugünkü karar çıktısı birebir aynı.

**PROOF:**
```
py -m pytest tests/ -q
py -m pytest tests/test_r15_agent_weights.py -q
```
Yeni suite:
(a) **SocialAgent kapalıyken `decide()` IndexError fırlatmıyor**; RiskAgent ada
    göre bulunuyor; RiskAgent oyu 0 ya da 2 olduğunda fail-closed;
(b) iki invaryant birden: `active_sum` doğru VE **her etkin ajanın ağırlığı
    maskeleme öncesiyle birebir aynı** (yeniden dağıtım yakalanıyor);
(c) **DONMUŞ ALTIN ÇIKTI regresyonu**, R15 ÖNCESİ üretim davranışından alınmış
    beklenen değerlere karşı (iki yeni yolun birbiriyle karşılaştırılması DEĞİL).
    Donmuş alanlar: `signal`, `confidence`, **`weighted_score` (yuvarlanmış hali
    dahil)**, `majority`, `risk_veto` ve **her etkin ajanın ağırlığı**.
    `weighted_score` ayrıca `stock_bot.py:972`'de BearBrain piyasa-genişliği
    bileşenini besliyor; yalnız signal/confidence eşitliği bu kaymayı kaçırır.
    Senaryolar zorunlu: **`ws = +15` ve `ws = -15` tam sınırları**, çoğunluk
    var/yok, RiskAgent veto, VIX short boost, `confidence` 100 doygunluğu;
(d) **`MIN_TRADES_FOR_EVAL = 5` geçiş sınırı** (`agent_performance.py:26,177-179`):
    her ajan için 4 ve 5 çözümlenmiş örnekli geçmişlerle, hem varsayılan ağırlık
    dalı hem hesaplanan dal donduruluyor;
(e) `analyze_social` mock çağrı sayacı 0 VE `time.sleep` çağrı sayacı 0;
(f) `DISABLED_BY_POLICY != SOURCE_UNAVAILABLE`, `ajan_raporu` çıktısında ayrı;
(g) eski şemalı `agent_stats.json` göç ediyor, çökmüyor;
(h) **entegrasyon: gerçek `stock_bot.py:1298` ağırlık atama yolundan** geçen test.

---

### R16 , AV kotası: ÜÇ tüketici, profil bütçesi, süreçler arası kilit

**Neden:** Kaynak sağlam, bütçe israf. `fundamental_analyzer.py:65` `time.sleep(15)`
başarısız çağrıda da koşuyor, başarısızlık cache'lenmiyor.

**ÜÇ TÜKETİCİ, iki değil.** Aynı `ALPHA_VANTAGE_KEY` şuralarda okunuyor:
`core/fundamental_analyzer.py` (OVERVIEW), `core/news_analyzer.py:92`
(NEWS_SENTIMENT), **`core/earnings_calendar.py:40,107` (EARNINGS_CALENDAR)**.
Üçüncüsü planın önceki sürümünde atlanmıştı; hesaba katılmazsa bütçe aşılır.

**Kapsam:**
- **AV haberi kapatılır** (İhsan kararı); haber Marketaux'tan gelir.
- **HER AV çağıranı aynı profil-bütçe rezervasyonundan geçer**, kazanç takvimi dahil.
  Takvim çağrıları temel analiz payını azaltır: profil başına toplam sabit, temel
  analize kalan **en fazla ~23** (takvim payı düşüldükten sonra) ve bu sayı
  config'de açık yazılır, koda gömülmez.
- **KONTEYNER-BELİRLİ KALICI BÜTÇE:** live 13 / paper 12 = 25. Paylaşımlı sayaç
  imkansız (ayrı state hacimleri), bu yüzden "anahtar geneli 25" İDDİA EDİLMEZ.
- **Süreçler arası kilit.** Atomik yer değiştirme tek başına YETMEZ: restart ya da
  deploy örtüşmesinde aynı profilden iki süreç eşzamanlı koşabilir ve
  oku-değiştir-yaz rezervasyonu yarışır. Rezervasyon bir interprocess lock
  altında yapılır.
- **Yenileme imleci (round-robin / en-eski-önce), kalıcı**, restart'ı atlatır.
- **Bayat veri sözleşmesi:** `stale-while-revalidate`, her karara kaynak zaman
  damgası + yaş, **maksimum bayatlık yaşı**, aşılınca `SOURCE_UNAVAILABLE`.
- **Tipli sonuçlar:** `OK` / `QUOTA_EXHAUSTED` / `RETRYABLE_ERROR` / `NO_DATA`.
  Yalnız `QUOTA_EXHAUSTED` gün sonuna kadar negatif cache'lenir. HTTP 200 + kota
  uyarı gövdesi `QUOTA_EXHAUSTED` sayılır.
- **Fail-closed sayaç:** okunamıyorsa tükenmiş sayılır; yük cache'i bağımsız ve
  temiz kurtarılır.
- **Kota muhasebesi UTC**, işlem-günü telemetrisi ayrıca America/New_York.
- `time.sleep(15)` yalnız gerçek ağ çağrısında.
- `fund_source_quota` `DailyFunnel.STAGES`'te (`core/funnel.py:23-38`) YOK; ya
  eklenip göç ettirilir ya `gate_block` sebebi olarak kaydedilir. Kanıt kalıcı
  üretim çıktısını okur.
- Ölü `_get_yahoo_fallback` kaldırılır ya da dürüstçe `NO_DATA` + tek WARN verir.

**Done looks like:** Üç AV tüketicisi de aynı bütçeden geçiyor, her profil kendi
payında kalıyor, eşzamanlı süreçler yarışmıyor, imleç her sembolü sırayla
tazeliyor, bayat veri yaşıyla raporlanıyor ve maksimum yaşı aşınca kullanılmıyor.

**PROOF:**
```
py -m pytest tests/ -q
py -m pytest tests/test_r16_fund_quota.py -q
```
Yeni suite:
(a) cache hit'te `requests.get` VE `time.sleep` çağrı sayacı 0;
(b) **ÜÇ tüketici (fundamental + news + earnings calendar) toplamı profil
    bütçesini geçmiyor**; takvim çağrısı temel payını gerçekten azaltıyor;
(c) **İKİ BAĞIMSIZ SÜREÇ** (live+paper) toplamı 25'i geçmiyor;
(d) **AYNI PROFİLDEN İKİ EŞZAMANLI SÜREÇ** (restart örtüşmesi) bütçeyi aşmıyor
    , interprocess lock kanıtı, yalnız atomik yazma değil;
(e) AV haberi kapalıyken haber yolunun AV sayacı 0, Marketaux çalışıyor, ve haber
    zorla açılırsa aynı rezervasyondan geçiyor;
(f) imleç: restart'lar boyunca her uygun sembol belirtilen ufukta tazeleniyor;
(g) bozuk/okunamaz sayaç -> yeni ağ çağrısı 0, yük cache'i okunabiliyor;
(h) HTTP 200 kota gövdesi `QUOTA_EXHAUSTED`, timeout `RETRYABLE_ERROR` ve
    ikincisi aynı gün tekrar deneniyor;
(i) maksimum bayatlık yaşı aşılınca `SOURCE_UNAVAILABLE`;
(j) UTC gün sınırında sayaç sıfırlanıyor;
(k) `fund_source_quota` kalıcı funnel çıktısında gerçekten görünüyor;
(l) kapsama sayıları BENZERSİZ SEMBOL sayıyor, yaş dağılımı profil başına.

---

### R17 , Yalan söyleyen aletleri düzelt: TEK SKALER DEĞİL, ÜÇ BOYUT

**Neden:** İhsan bir haftadır botu bozuk sandı çünkü `health_check.py` "🔴 BOT
CALISMIYOR" diyordu; bot çalışıyordu, kilitliydi. Ama tersi de tuzak: tek bir
skaler durum kullanırsam **`KILITLI`, ölü bir karar hattını maskeler.** Kilitli
VE bayat bir bot yalnızca "kilitli" diye raporlanamaz.

**Kapsam:**
- **Üç BAĞIMSIZ boyut, tek skaler yerine:**
  1. `runtime` , süreç/konteyner ayakta mı, heartbeat taze mi;
  2. `decision_pipeline` , karar telemetrisi taze mi (tarama ve karar üretiliyor mu);
  3. `entry_authorization` , canlı giriş kilidi açık mı (`live_entries_enabled`).
  Her boyut kendi durumunu taşır; özet, üçünü birden gösterir ve
  **kilitli + bayat kombinasyonu asla yalnız `KILITLI` olarak özetlenmez.**
- **Fail-closed:** her boyutta belirsizlik `UNKNOWN`'a düşer, `SAGLIKLI`'ya değil.
  Bozuk/gelecek tarihli heartbeat, broker hatası, kill-switch/risk-halt, ardışık
  başarısız tarama -> `DEGRADED`.
- **Dolum sağlık kanıtı değildir.** Park, manuel işlem ya da çıkış olabilir (canlı
  hesapta bugün birebir bu durum). Dolumlar ayrı boyut, provenance ile.
- **Profil ayrımı dürüst:** tek süreç iki konteyneri gözleyemez; okunamayan profil
  `UNKNOWN` olur.
- **Süpürge , taahhüt edilmiş, örtüşme-güvenli yüksek-su işareti.** İşaret yalnız
  her broker sayfası eksiksiz VE gereken bütün defter yazmaları başarılı olduktan
  sonra ilerler. İlk açılışta işaret yoksa ölçüm epoch'undan/doğrulanmış backfill
  sınırından başlar; kesinti broker retansiyonundan eskiyse `DEGRADED`/`UNKNOWN`.

**Done looks like:** Kilitli bot "bozuk" diye raporlanmıyor AMA kilitli+ölü bot da
"sadece kilitli" diye raporlanmıyor; belirsizlik yeşil yanmıyor; süpürge işareti
yalnız tam başarıda ilerliyor.

**PROOF:**
```
py -m pytest tests/ -q
py -m pytest tests/test_r17_honest_health.py -q
py health_check.py
```
Yeni suite:
(a) **kilitli VE karar telemetrisi bayat -> özet `KILITLI` DEĞİL**, iki sorun da
    görünüyor (bu, tek skalerin maskeleme hatasını yakalar);
(b) üç boyutun her biri için durum testleri, ve proof'ta geçen her durum şemada
    tanımlı (şema/test uyumu doğrulanıyor);
(c) gelecek tarihli / bozuk heartbeat -> `DEGRADED`, asla `SAGLIKLI`;
(d) yalnız park dolumu + bayat karar telemetrisi -> `SAGLIKLI` değil;
(e) ikinci profil okunamıyor -> `UNKNOWN`;
(f) sayfa ortası broker hatası -> işaret İLERLEMİYOR, yeniden denemede boşluk yok;
(g) defter yazma hatası -> işaret İLERLEMİYOR;
(h) 73 saatlik kesinti -> işaretten başlıyor, 0 dolum kaçırıyor;
(i) işaret yok (ilk açılış) -> tanımlı sınırdan başlıyor;
(j) kesinti broker retansiyonundan eski -> `DEGRADED`/`UNKNOWN`, sessiz başarı yok.

---

### R18 , GÖLGE TOPLAYICI: EKLE-SADECE niyet/olay kaydı

**Neden:** `LIVE_LOCK_R5` girişleri kesiyor ama stratejinin açılınca ne yapacağını
kanıtlamıyor. Kanıt için işlem, işlem için açık kilit, kilit için kanıt gerekiyor.
Bu kısır döngüyü kıran tek şey gölge kaydıdır.

**KAPSAM DÜZELTMESİ (Codex Round 3):** Önceki sürüm "durumlu gölge portföy" istiyordu
ama çıkış tekrarı ertelenmişken bu İMKANSIZ: nakdin ne zaman serbest kalacağını,
PDT sayacının ne zaman düşeceğini, kayıp serisinin ne zaman ilerleyeceğini bilmenin
yolu yok. Durum geçişleri çıkışa bağlıdır. -> R18 **EKLE-SADECE (append-only)
niyet/olay toplayıcısı** olur; varsayımsal portföy geçişleri
RF-ISSUES-4.md `GOLGE-SONUC-ETIKETLEME` sözleşmesine taşınır.

**Kapsam:**
- `core/shadow_ledger.py` (yeni), ekle-sadece.
- **Niyet MERKEZİ KİLİT REDDİNDE yakalanır**, coordinator kararında değil.
  `LIVE_LOCK_R5` stock/queue/short/option/bear-ETF yollarını farklı aşağı-akış
  kontrollerinden sonra koruyor; niyet, gerçek çalıştırmanın kullandığı AYNI
  deterministik emir planlama mantığı koşturulduktan sonra kaydedilir.
- **ŞİMDİ kaydedilmesi zorunlu alanlar** (sonra yeniden yazmamak için):
  kalıcı **`intent_id`**, **olay sırası (event sequence)**, kaynak zaman
  damgaları, **varlık yetenekleri** (fractionable, shortable, easy_to_borrow vb.),
  emir parametreleri (tip, limit, TIF, boyut), o anki durum anlık görüntüsü,
  ortak karar örneği (ajan oyları + güvenleri + ağırlıklar + veri erişilebilirliği
  + `weighted_score` + sinyal + güven + bloklayan kapılar), ve **şema-sürümlü
  etiket referansı** (etiketleyici sonra buraya bağlanacak).
- **Değişmez fiyat kaynağı ŞİMDİ adlandırılıp doğrulanır.** Yalnız giriş anındaki
  bid/ask, sonraki stop/trailing/kısmi-çıkış/dolum tekrarı için YETMEZ. Ya yaşam
  döngüsü boyunca gereken piyasa gözlemleri şimdi kaydedilir, ya da toplama
  başlamadan ÖNCE değişmez bir tarihsel kote kaynağı adlandırılır ve
  erişilebilirliği doğrulanır. Bu, toplamanın ön koşuludur.
- **Kimlik bağlama, eksiksiz olmazsa geçersiz:** `commit_sha` + `epoch_id` +
  etkin canlı karar profilinin kanonik hash'i (env, ajan, short, option, rejim,
  kapı ayarları dahil). Eksikse kayıt **kapı-uygunsuz** işaretlenir.
- **Sınırlı ve sürümlü, ama YANLI OLMADAN:** boyut tavanı mevcut epoch içinde de
  uygulanır; düşürme rastgele değil **deterministik tabakalı örnekleme** ile
  yapılır, kapsama eşiği tanımlıdır, düşürülen örnek sayısı açıkça kaydedilir ve
  **kapsama eşiği tutmuyorsa kayıt kümesi eksik işaretlenir** (sonraki kapı bunu
  `UNKNOWN` saymak zorunda).
- **Salt gözlem:** hiçbir emir metodu çağrılmaz, hiçbir kapı değiştirilmez.
  Kayıt arızası kararı DEĞİŞTİREMEZ (R13 disiplini).
- **İDDİA EDİLMEYEN:** sonuç, getiri, dolum, çıkış, SPY karşılaştırması, portföy
  durumu, kapanmış episode.

**Done looks like:** Kilit kapalıyken, gerçek emir planlama mantığından geçmiş
niyetler, sonradan etiketlenebilecek eksiksiz alan kümesiyle, tam kimlikle ve
yanlılık yaratmayan sınırla diske ekleniyor.

**PROOF:**
```
py -m pytest tests/ -q
py -m pytest tests/test_r18_shadow.py -q
```
Yeni suite:
(a) kilit kapalıyken kayıt üretiliyor ve **emir metodu çağrı sayacı 0**;
(b) niyet merkezi kilit reddinde yakalanıyor: aşağı-akış kontrollerinde elenen
    aday gölgeye yazılmıyor;
(c) **`intent_id` kalıcı ve benzersiz**, olay sırası monoton, aynı niyet iki kez
    yazılmıyor (ekle-sadece idempotency);
(d) ortak örnek + varlık yetenekleri + emir parametreleri + durum anlık görüntüsü
    tek kayıtta; şema sürümü ve etiket referansı alanı mevcut;
(e) `commit_sha`/profil hash'i değişince epoch sıfırlanıyor; kimliği eksik kayıt
    **kapı-uygunsuz** işaretleniyor;
(f) **kayıt yazma hatası fırlatıldığında coordinator kararı değişmiyor**;
(g) boyut tavanı mevcut epoch içinde uygulanıyor, **düşürme deterministik tabakalı**
    ve kapsama eşiği tutmuyorsa küme **eksik** işaretleniyor;
(h) değişmez kote kaynağı adlandırılmış ve **erişilebilirliği testte doğrulanıyor**;
(i) bozuk/eksik dosyada çökmüyor.

---

### R19 , CANLI-CONFIG PAPER EPOCH'U: çalıştırma kanıtını ÜRETEN taraf

**Neden (Codex Round 3 CLARIFY'ına Visionary cevabı):** Codex haklı olarak sordu:
canlı-config paper çalıştırma kanıtını KİM üretecek? Mevcut paper botu
`PAPER_AGGRESSIVE` profiliyle koşuyor ve `tools/olcum_raporu.py:1179-1203`
`measured_profile()` bunu sabitliyor; rapor bu profilin canlı kanıtı olmadığını
kendisi söylüyor. Üretici yoksa kapı **kalıcı olarak** `NOT_READY` kalır, kilit
hiç açılmaz ve Core Focus çöker.

**Ve kapının kendisi bu döngüde YAPILMAZ.** Codex'in DEFER'i kabul edildi: girdileri
var olmayan, sonucu garanti `NOT_READY` olan bir kapıyı şimdi yazmak, üreticiler
gelince değişecek bir arayüzü erkenden dondurur. Kapı sözleşmesi RF-ISSUES-4.md'ye
taşındı. Bu döngüde **tüketici değil ÜRETİCİ** inşa ediliyor.

**Kapsam:**
- **Canlı-config paper profili:** paper broker'ında, ama `PAPER_AGGRESSIVE_CONFIG`
  yerine **canlı karar profiliyle** (eşikler, boyutlandırma, kapılar) koşan bir
  çalıştırma modu. Gerçek Alpaca paper dolumları üretir: gerçek kısmi dolumlar,
  gerçek stop davranışı, gerçek broker-defter mutabakatı , **sıfır dolar riskle**.
  Bu, gölge defterin üretemeyeceği tek kanıt türüdür.
- **`measured_profile()` gerçeği söylesin:** sabitlenmiş `PAPER_AGGRESSIVE` yerine
  o an gerçekten yürürlükte olan profili raporlasın; profil kimliği R18'in
  kanonik hash'iyle aynı üretilsin ki iki eksen eşleştirilebilsin.
- **Epoch kimliği:** bu profilin gözlemleri kendi `epoch_id`'siyle etiketlenir ve
  `PAPER_AGGRESSIVE` gözlemleriyle **karışamaz**.
- **Mevcut paper botuna dokunulmaz.** Agresif paper profili olduğu gibi kalır;
  canlı-config epoch'u ayrı ve açıkça işaretli koşar.
- **Canlı hesaba hiçbir emir gitmez.** R5 kilidi kapalı kalır.

**Done looks like:** Paper broker'ında canlı karar profiliyle koşan, kendi
epoch'uyla etiketlenmiş bir çalıştırma var; `measured_profile()` gerçek profili
raporluyor; agresif paper gözlemleriyle karışma yok.

**PROOF:**
```
py -m pytest tests/ -q
py -m pytest tests/test_r19_live_config_paper.py -q
py tools/olcum_raporu.py
```
Yeni suite:
(a) canlı-config paper modunda kullanılan eşik/boyut/kapı değerleri **canlı
    profille birebir aynı**, `PAPER_AGGRESSIVE` değil;
(b) `measured_profile()` sabit dönmüyor; yürürlükteki profili raporluyor;
(c) profil kimliği R18'in kanonik hash'iyle **aynı algoritmadan** üretiliyor
    (iki eksen eşleşebiliyor);
(d) canlı-config epoch'u ile `PAPER_AGGRESSIVE` epoch'u **ayrı** ve gözlemler
    karışmıyor;
(e) bu modda **canlı broker istemcisine hiçbir emir çağrısı yapılmıyor**
    (canlı emir metodu çağrı sayacı 0);
(f) mevcut agresif paper davranışı regresyona uğramıyor (donmuş karşılaştırma).


## KAPSAM DISI (bu dongude YOK)

- `LIVE_ENTRIES_ENABLED` acmak.
- **Kilit kapisi araci (eski R19).** Codex Round 3 DEFER kabul edildi: girdileri
  (golge etiketleyici + calistirma kaniti) var olmayan, sonucu garanti
  `NOT_READY` olan bir kapiyi simdi yazmak, ureticiler gelince degisecek bir
  arayuzu erkenden dondurur. Tam sozlesmesiyle RF-ISSUES-4.md'de. Kaybedilen tek
  sey erken CLI iskelesi; R5 kilidi guvenle kapali kaliyor.
- **Golge sonuc etiketleme:** dolum/dolmama kurallari, spread, gecikme, ret,
  kismi dolum, ucretler, gercek stop/TP/trailing/kismi-cikis durum makinesinin
  tekrari, benchmark hizalama, olgunlasma ve ileriye-bakis testleri. Varsayimsal
  portfoy gecisleri (nakit serbest birakma, PDT, kayip serisi) de buraya ait.
  Tam sozlesmesiyle RF-ISSUES-4.md'de.
- **Esik kalibrasyonu.** Gerceklesmis sonuc/PnL/benchmark olmadan esik onerilemez.
- Cogunluk mantigi (teshis yanlisti) ve agirlik renormalizasyonu (kapilari gevsetir).
- Reddit OAuth ile SocialAgent'i diriltmek.
- Backtest motorunu canli cekirdege tasimak.
- Guven formulu, cikis geometrisi, pozisyon boyutlandirma.

## RISKLER

1. **R15 olcegi KORUYOR, tek basina islem sayisini artirmaz.** Kasitli.
   Kazanc: cokme duzeltmesi (`votes[4]`), 12 sn/sembol olu uyku, durust telemetri.
2. **AV butcesi 13/12 bolunuyor ve ucuncu tuketici (kazanc takvimi) payi azaltiyor.**
   Kapsama imlecle siraya girer; her sembolun ne siklikla tazelendigi raporlanir.
3. **R18 disk yaziyor.** Boyut tavani mevcut epoch icinde de uygulanir ve dusurme
   deterministik tabakali olur; kapsama esigi tutmazsa kume eksik isaretlenir.
4. **R18 yalniz TOPLUYOR.** Bu dongu sonunda alfa hakkinda hicbir iddia YOK.
   Bu beklenen ve durust sonuc, kusur degil.
5. **R19 kanit URETIYOR, kanit DEGERLENDIRMIYOR.** Canli-config paper epoch'u
   dolum uretmeye baslar; o dolumlarin kapiya donusmesi sonraki dongudur.
6. **Hicbir rock canli hesaba emir gondermiyor.** R5 kilidi kapali kaliyor.
