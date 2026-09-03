# RF-PLAN-4.md , Clarity Break #4: ölü ağırlığı kaldır, kapıyı ölçüye bağla

> Tarih: 2026-09-03 (Los Angeles). Sürücü: Claude (Visionary) + Codex (Integrator).
> Baseline: `git HEAD = 5aea694`, `py -m pytest tests/ -q` -> **250 passed**.
> Önceki döngüler: RF-PLAN.md, RF-PLAN-2.md, RF-PLAN-3.md (R1..R14 tamamlandı).

## CORE FOCUS (tek cümle)

**Canlı hesabın değerini ölçülmüş alfa ile büyütmek; ölçüm kapısı geçmeden canlı
giriş kilidi açılmaz ve hiçbir araç "çalışıyor" diye yalan söylemez.**

## Bu döngünün teşhisi (2026-09-03, hepsi bu oturumda doğrulandı)

Canlı hesap $494.69, 7 gündür 0 işlem. Sebep arıza değil, `LIVE_LOCK_R5` kilidi
(`core/risk_guard.py:82`, `config.py:466`). Kilit önceki döngüde kondu çünkü
strateji alfa üretmiyordu. Alfanın neden üretilmediğinin mekanizması:

| Ajan | Ağırlık | Ölçülen durum |
|---|---|---|
| TechAgent | 0.25 | Sağlam |
| **FundAgent** | **0.20** | AV ücretsiz kota 25/gün; başarısızlık cache'lenmiyor, her tur yeniden çağrı |
| SentAgent | 0.20 | Sağlam (R12'de işaret hatası düzeltildi) |
| **SocialAgent** | **0.15** | Reddit HTTP 403 (189KB HTML), Nitter ölü. Kalıcı kör |
| RiskAgent | 0.20 | Sağlam, ama telemetride BUY=0 |

Bu oturumda alınan ölçümler:
- `GET reddit.com/r/wallstreetbets/search.json` -> **HTTP 403**, 189908 bayt HTML.
- `GET query1.finance.yahoo.com/v10/finance/quoteSummary/AAPL` -> **HTTP 401**
  (koddaki `_get_yahoo_fallback` yedeği ölü; ayrıca yalnız anahtar YOKSA çağrılıyor).
- `GET alphavantage.co OVERVIEW AAPL` -> **HTTP 200, gerçek veri**. Kaynak ölü değil,
  bütçe israf ediliyor.
- Üretim telemetrisi (26 Ağu, PLAN.md): FundAgent veri_yok=%100, SocialAgent veri_yok=%100.

**Sonuç:** ensemble ağırlığının %35'i anlaşmazlıktan değil veri yokluğundan HOLD
diyor. Üstüne `agent_coordinator.py:436` çoğunluk kapısı `buy_count >= 3` istiyor;
5 ajandan 2'si kalıcı susmuşken yaşayan 3 ajanın oybirliği gerekiyor ve RiskAgent
telemetride hiç BUY demiyor. Çoğunluk pratikte imkansız. Eşikler (paper 30 /
canlı 50) 5 oy veren ajana göre kalibre edilmiş, gerçekte 3 oy veriyor.

**Yan hasar:** `fundamental_analyzer.py:65` `time.sleep(15)` başarısız çağrıda da
koşuyor; `social_sentiment.py:188` `time.sleep(1)` 403'te de koşuyor (12 istek).
Hiç gelmeyen veri için sembol başına ~27 saniye bloklayıcı uyku.

## İhsan'ın bu döngü için verdiği iki karar

1. **Canlı kilit:** önce onar, sonra aç. Ölçüm kapısı geçene kadar
   `LIVE_ENTRIES_ENABLED` kapalı kalır. Hesap o zamana kadar SPY parkında durur.
2. **SocialAgent:** kaldır, ağırlığı yaşayan ajanlara dağıt. Reddit OAuth yok.

## ROCK'LAR (bağımlılık sırasında)

---

### R15 , Ölü ağırlığı kaldır, oyu yaşayan ajanlara ver

**Neden:** Ağırlığın %15'i kalıcı sıfır ve çoğunluk kapısı 5 ajanlık ölçekte
sabitlenmiş. İkisi birlikte girişleri yapısal olarak boğuyor.

**Kapsam:**
- `config.py`: yeni anahtar `AGENT_CONFIG["social_agent_enabled"]`, varsayılan
  **False** (ölçülmüş 403 sebebiyle). Env ile açılabilir kalsın, kod silinmesin.
- `core/agent_coordinator.py`: `WEIGHTS` tek gerçek kaynak kalsın ama kapalı
  ajanlar oy kümesinden çıkarılsın ve kalan ağırlıklar **toplamı 1.0 olacak
  şekilde yeniden normalize edilsin** (0.25/0.20/0.20/0.20 -> /0.85). Normalizasyon
  runtime'da hesaplansın, config'e sabit sayı yazılmasın.
- Çoğunluk kapısı `>= 3` sabiti **yaşayan ajan sayısından türetilsin**:
  `esik = (yasayan // 2) + 1` (5 ajan -> 3, 4 ajan -> 3). Sabit sayı kalmasın.
- `stock_bot.py:1286-1288`: ajan kapalıyken `analyze_social` **hiç çağrılmasın**
  (sadece skoru 0'lamak yetmez, 12 saniyelik uyku maliyeti kalkmalı).
- `AgentVote` üretilmeyen ajan için R13 `agent_stats` telemetrisi `veri_yok`
  değil **`kapali`** olarak ayrışsın. Yokluk susmayla karışmasın (R14 kuralı).

**Done looks like:** SocialAgent kapalıyken 4 ajan oy veriyor, ağırlık toplamı
tam 1.0, çoğunluk eşiği 3, `analyze_social` çağrı sayısı 0, telemetri `kapali`
diyor. Açıldığında (env ile) eski 5 ajanlık davranış birebir geri geliyor.

**PROOF:**
```
py -m pytest tests/ -q
py -m pytest tests/test_r15_agent_weights.py -q
```
Yeni suite şunları kanıtlamalı: (a) kapalıyken ağırlık toplamı 1.0 +- 1e-9,
(b) çoğunluk eşiği yaşayan sayıdan türüyor, (c) `analyze_social` çağrılmıyor
(mock çağrı sayacı 0), (d) telemetride `kapali` != `veri_yok`, (e) env ile
açıldığında 5 ajanlık ağırlıklar birebir eski değerlere dönüyor.

---

### R16 , FundAgent'ı 25/gün kotasına sığdır

**Neden:** Kaynak sağlam (bugün HTTP 200 ölçtüm), israf edilen bütçe. Temel
veriler çeyreklik değişir, tarama turu başına değil. 24 saatlik disk cache ile
25 çağrı/gün tam ~25 sembole yeter. Bu, 0.20 ağırlığı bedavaya geri kazandırır.

**Kapsam:**
- `core/fundamental_analyzer.py`:
  - **Disk cache**, `config.state_path("fundamentals_cache.json")`. TTL 24 saat.
    Süreç yeniden başlasa da hayatta kalmalı (bellek cache'i konteyner restart'ında
    ölüyor, kota da onunla birlikte yeniden yanıyor).
  - **Negatif cache:** kota tükendiğinde dönen `return None` de yazılsın
    (ayrı TTL, gün sonuna kadar). Aynı sembol aynı gün ikinci kez AV'yi çağırmasın.
  - **Günlük çağrı sayacı**, `av_calls_YYYY-MM-DD`. 25'e ulaşınca ağ çağrısı
    yapılmasın, doğrudan cache/None dönsün.
  - `time.sleep(15)` **yalnız gerçekten ağ çağrısı yapıldığında** koşsun. Cache
    hit'te ve sayaç doluyken uyku sıfır.
  - Ölü `_get_yahoo_fallback`: Yahoo bugün HTTP 401 veriyor. Ya kaldırılsın ya da
    çağrıldığında dürüstçe `None` + tek seferlik WARN log versin. Sessiz ölü kod kalmasın.
- Kota tükenmesi `logger.debug` ile yutulmasın: ardışık N başarısızlıkta **WARN**
  ve R11 funnel'ına `fund_source_quota` etiketi.

**Done looks like:** Bir tarama turunda aynı sembol için en fazla 1 AV çağrısı,
günde en fazla 25 AV çağrısı, cache hit'te 0 saniye uyku, kota tükendiğinde
sessizlik değil WARN. FundAgent `veri_yok` oranı ölçülebilir biçimde düşüyor.

**PROOF:**
```
py -m pytest tests/ -q
py -m pytest tests/test_r16_fund_quota.py -q
```
Yeni suite: (a) cache hit'te `requests.get` çağrılmıyor VE `time.sleep` çağrılmıyor,
(b) 26. çağrı ağa çıkmıyor, (c) negatif cache aynı gün ikinci çağrıyı engelliyor,
(d) disk cache dosyası bozuk/boş/eksik JSON ise çökmüyor, temiz başlıyor,
(e) TTL dolduğunda yeniden çağırıyor.

---

### R17 , Yalan söyleyen aletleri düzelt

**Neden:** İhsan bir haftadır botu bozuk sandı çünkü `health_check.py` "🔴 BOT
CALISMIYOR" diyordu. Bot çalışıyordu, kilitliydi. Ölçüm aleti yanlış okuyorsa
bütün döngü kör uçuyor. Ayrıca PLAN.md'de açık bırakılmış gerçek bir veri
kaybı riski var.

**Kapsam:**
- `health_check.py`: tek "çalışmıyor" kovası yerine **dört ayrı durum**:
  1. `KAPALI` , süreç/konteyner yok ya da son heartbeat eski.
  2. `KILITLI` , bot koşuyor ama canlı modda `live_entries_enabled=False`.
     Mesaj kilidi ve açma şartını söylesin, "bozuk" demesin.
  3. `SESSIZ` , bot koşuyor, kilit açık, ama N gündür sinyal yok.
  4. `SAGLIKLI` , son işlem taze.
  Paper ve live ayrı ayrı raporlansın (tek `TRADING_MODE` okuyup tek hesap
  göstermek yanıltıcı; bugün ikisi taban tabana zıt durumda).
- **Süpürge penceresi deliği** (PLAN.md v4.18'de "ACIK KALAN RISK" olarak yazılı):
  açılış süpürgesi geniş pencere (varsayılan 72 saat, config'lenebilir), periyodik
  süpürge 24 saatte kalsın. Bot 24 saatten uzun kapalı kalırsa dolumlar menzil
  dışında kalıyor ve defterde **kalıcı delik** oluşuyor.

**Done looks like:** `py health_check.py` dört durumu ayırt ediyor, kilitli botu
"bozuk" diye raporlamıyor, paper ve live'ı ayrı gösteriyor. Açılış süpürgesi
72 saatlik pencereyle koşuyor, periyodik 24 saatte kalıyor.

**PROOF:**
```
py -m pytest tests/ -q
py -m pytest tests/test_r17_honest_health.py -q
py health_check.py
```
Yeni suite: dört durumun her biri için bir test (sahte broker/state ile), artı
açılış süpürgesi 72s pencere kullanıyor ve periyodik 24s kullanıyor testi.

---

### R18 , Yeni ağırlıklarla eşik kalibrasyonu (ölçüm aracı, uydurma yok)

**Neden:** Eşikler (paper 30 / canlı 50) 5 oy veren ajana göre kalibre edilmişti.
R15 ve R16 sonrası oy dağılımı değişiyor. Yeni eşiği hisle değil ölçüyle koymak
lazım. R14 dersi: yanlış yeşil üretmektense "n yetersiz" demek.

**Kapsam:**
- `tools/esik_kalibrasyon.py` (yeni, salt okunur): R13 `agent_stats.json` üretim
  verisi + `tests/fixtures/parity_tape.json` gerçek bandı üzerinden, YENİ
  ağırlıklarla her eşik değeri için (30, 35, 40, 45, 50, 55, 60) kaç giriş sinyali
  üretildiğini tablolar.
- **Dört durumlu sözleşme** (R10 disiplini): `PASS` / `FAIL` / `NOT_READY` /
  `UNKNOWN`. Örnek sayısı yetersizse **NOT_READY** dönsün ve bir eşik ÖNERMESIN.
  Sahte kesinlik üretmek yasak.
- Araç emir vermesin, broker'a yazmasın. Salt okunur.
- Çıktı `logs/esik_kalibrasyon_<tarih>.json` + insan okur özet.

**Done looks like:** Araç koşuyor, yeni ağırlıklarla eşik/sinyal tablosunu
üretiyor, veri yetersizse NOT_READY diyor ve öneri vermiyor. Yeterliyse
gerekçeli bir eşik öneriyor.

**PROOF:**
```
py -m pytest tests/ -q
py -m pytest tests/test_r18_kalibrasyon.py -q
py tools/esik_kalibrasyon.py
```
Yeni suite: (a) n<esik_minimum iken NOT_READY ve öneri alanı boş, (b) yeterli
sentetik veride tablo doğru sayıyor, (c) araç hiçbir emir metodu çağırmıyor
(mock broker'da çağrı sayacı 0), (d) bozuk/eksik agent_stats.json'da çökmüyor.

---

### R19 , Canlı kilit açma kapısı: şart yazılı ve otomatik kontrol edilebilir

**Neden:** Bugün kilidin açılma şartı PLAN.md'de düzyazı ("20 işlem / 4 metrik
4/4 PASS"). Düzyazı şart, ölçülemeyen şarttır. İhsan'ın kararı "önce onar sonra
aç" olduğuna göre "sonra"nın ne demek olduğu makinece kontrol edilebilmeli.

**Kapsam:**
- `tools/olcum_raporu.py` içine ya da yanına **`kilit_kapisi` kontrolü**: dört
  metriğin durumunu okuyup tek bir karar döndürsün, `AC` / `ACMA` / `NOT_READY`.
- Kapı **fail-closed**: veri eksik, dosya bozuk, tarih tutarsız ya da metrik
  belirsizse `ACMA` değil **`NOT_READY`** dönsün ve asla `AC` demesin.
- `LIVE_ENTRIES_ENABLED` bu kapıya bağlanmasın (otomatik açma YOK). Kapı yalnız
  **rapor eder**; env'i açmak İhsan'ın elinde kalır. Kilit açma insan kararıdır.
- README niteliğinde kısa bir bölüm: kapı `AC` dediğinde Coolify'da hangi env
  set edilecek, hangi sırayla restart edilecek, ilk 24 saat neye bakılacak.

**Done looks like:** `py tools/olcum_raporu.py --kilit-kapisi` üç durumdan birini
döndürüyor, eksik veride asla `AC` demiyor, ve env'i kendi kendine değiştirmiyor.

**PROOF:**
```
py -m pytest tests/ -q
py -m pytest tests/test_r19_kilit_kapisi.py -q
py tools/olcum_raporu.py --kilit-kapisi
```
Yeni suite: (a) dört metrik PASS -> `AC`, (b) bir metrik FAIL -> `ACMA`,
(c) metrik dosyası yok/bozuk/gelecek tarihli -> `NOT_READY` (asla `AC`),
(d) araç `LIVE_ENTRIES_ENABLED`'i yazmıyor (env yazma çağrısı sayacı 0).

---

## KAPSAM DIŞI (bu döngüde YOK)

- `LIVE_ENTRIES_ENABLED` açmak. İhsan'ın kararı: önce onar, sonra aç.
- Reddit OAuth ile SocialAgent'ı diriltmek. İhsan'ın kararı: kaldır.
- Backtest motorunu canlı çekirdeğe taşımak (R14'ün işaret ettiği büyük iş).
  RF-ISSUES-4.md'ye ertelenir.
- Güven formülüne, çıkış geometrisine, pozisyon boyutlandırmaya dokunmak.
  R18 ölçümü gelmeden bunlara dokunmak kör atıştır.
- Yeni strateji, yeni sembol, yeni varlık sınıfı eklemek.

## RİSKLER

1. **R15 ağırlık normalizasyonu davranışı değiştirir.** Paper'da canlı bir bot
   koşuyor; deploy sonrası ilk turlarda oy dağılımı değişecek. Bu istenen etki,
   ama R18 ölçümü gelmeden "daha iyi" denemez.
2. **R16 disk cache'i state dizinine yazar.** Live/paper izolasyonu
   `state_path()` ile korunmalı; ortak dosya iki modu birbirine karıştırır.
3. **R17 süpürge penceresini genişletmek** daha çok broker sorgusu demek.
   Açılışta bir kez koştuğu için maliyet kabul edilebilir, ama sorgu sayısı
   loglanmalı.
4. **Hiçbir rock canlı hesaba emir göndermiyor.** R5 kilidi bu döngü boyunca
   kapalı kalıyor, bu kasıtlı.
