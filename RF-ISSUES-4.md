# RF-ISSUES-4.md , Clarity Break #4 ertelenen işler

> Bu döngüde (RF-PLAN-4.md) kapsam dışı bırakılan, ama gerçek olan işler.
> Kaynak: Codex Same Page Meeting Round 1 + Claude teşhisi, 2026-09-03.

## YÜKSEK , sıradaki döngünün ana adayları

### GOLGE-SONUC-ETIKETLEME (R18'den bilinçli olarak ayrıldı)
**Gölge defterin "ne olurdu" kaydını dürüst bir sonuca çevirmek ayrı bir rock.**

R18 bu döngüde yalnız TOPLUYOR. Toplanan niyeti gerçek bir sonuca çevirmek, aşağıdaki
sözleşmenin tamamı karşılanmadan yapılamaz; yarısı yapılırsa sistematik olarak
İYİMSER bir kayıt üretir ve gerçek parayla kilit açma kararını zehirler.

Sözleşme (Codex Round 2 bulgusundan, kabul edildi):
- **Dolum gerçekçiliği:** kaydedilen zaman damgalı bid/ask üzerinden muhafazakar
  dolum / dolmama kuralları. Spread, gecikme, emir reddi, hiç dolmama, kayma
  (slippage), kısmi dolum ve ücretler modellenmeli. Belirsiz sonuç `UNKNOWN`
  işaretlenir, iyimser varsayılmaz.
- **Çıkış gerçekçiliği:** getiri "N gün sonraki fiyat" ile hesaplanamaz. Gerçek
  stop / take-profit / trailing / kısmi-çıkış durum makinesi, piyasa yoluna bağlı
  olarak TEKRAR OYNATILMALI.
- **Benchmark hizalama:** SPY karşılaştırması aynı zaman penceresi ve aynı sermaye
  varsayımıyla hizalanmalı.
- **Olgunlaşma:** kapanmamış episode sonuç sayılmaz.
- **İleriye-bakış (lookahead) testi:** gelecekteki barlar bozulduğunda sonucun
  değişmediği kanıtlanmalı (R14'ün parity harness'ında kullanılan disiplin).
- **Idempotency:** aynı girdi iki kez etiketlendiğinde aynı sonuç, çift sayım yok.

Bu iş bitmeden R19 kapısı canlı-config gölge kanıtı için `PASS` üretemez.

### KILIT-KAPISI-ARACI (eski R19, Codex Round 3 DEFER ile ertelendi)
**Kilidi açma kararını makinece kontrol edilebilir kılan araç, ÜRETİCİLERİ
bekliyor.**

Neden ertelendi: kapının iki girdisi de bu döngüde üretilmiyor , gölge sonuç
etiketleyicisi (yukarıdaki `GOLGE-SONUC-ETIKETLEME`) ve canlı-config paper
çalıştırma kanıtı (bu döngüde R19 ile ÜRETİLMEYE başlıyor ama olgunlaşmamış olacak).
Sonucu garanti `NOT_READY` olan bir kapıyı şimdi yazmak, üreticiler geldiğinde
değişecek bir arayüzü erkenden dondurur. Kaybedilen tek şey erken CLI iskelesidir;
R5 kilidi bu süre boyunca güvenle kapalı kalır.

Sözleşme (yazıldığında birebir uygulanacak):
- `tools/olcum_raporu.py:1160` **`gate_status()` doğrudan sarılır**; yeni öncelik
  ya da karar mantığı YAZILMAZ.
- **İKİ AYRI EKSEN, her biri kendi `gate_status()` çağrısıyla:**
  1. **gölge-alfa ekseni** , R18 kayıtlarından, etiketleyici çalıştıktan sonra;
  2. **çalıştırma ekseni** , canlı-config paper epoch'unun GERÇEK dolumlarından
     (kısmi dolumlar, stop bütünlüğü, broker-defter mutabakatı).
  Her eksenin kendi örnek sayısı, süresi, epoch'u ve profil kimliği olur.
  **`AC` ancak İKİ eksen de PASS ve kimlikleri eşleşiyorsa** verilir.
- **Gölge kayıtları çalıştırma metriklerini PASS yapamaz.** Mevcut dört metrik
  gerçek dolumlara dayanıyor; eksik çalıştırma kanıtı **`UNKNOWN` kalır, asla
  `PASS`'e çevrilmez.**
- **`n`'in tanımı:** *olgunlaşmış, örtüşmeyen, durumlu gölge KAPANMIŞ episode*.
  `n >= 20` VE `>= 30` işlem günü. Tekrarlı taramalar `n`'i şişiremez.
- `measured_profile()` yürürlükteki profili raporlamalı; `PAPER_AGGRESSIVE` eseri
  görüldüğünde kapı `AC` diyemez.
- **Fail-closed:** eksik veri, bozuk dosya, tutarsız tarih, uyumsuz epoch, eksik
  kimlik, eksik işaretli kayıt kümesi (R18 kapsama eşiği tutmadıysa) -> `NOT_READY`.
- **Otomatik açma YOK.** Kapı yalnız rapor eder; `LIVE_ENTRIES_ENABLED`'e yazmaz.
  Kilidi açmak İhsan'ın kararıdır.

### KALIBRASYON-VERI-BEKLIYOR (ertelendi, Codex DEFER)
**Eşik kalibrasyonu bu döngüde yapılamaz, veri yok.**
- Sebep: Eşik önerisi gerçekleşmiş sonuç, PnL, drawdown ve benchmark ister.
  Mevcut `agent_stats.json` marjinal histogram tutuyor (`core/agent_stats.py:128`),
  karar başına ortak örnek tutmuyor. R18 gölge defteri bu ortak örneği üretmeye
  başlayacak ama veri döngü sonunda henüz birikmiş olmayacak.
- Şart: R18 deploy edildikten sonra tanımlı bir toplama süresi. **`n`'in tek
  tanımı** (kapı sözleşmesiyle birebir aynı kelimeler, "karar" ya da "tarama"
  DEĞİL): *olgunlaşmış, örtüşmeyen, durumlu gölge KAPANMIŞ episode*.
  Eşik: `n >= 20` kapanmış episode VE `>= 30` işlem günü.
- Dürüst döngü sonu: `NOT_READY`, eşik önerisi YOK.
- Not: Sinyal sayısı tablosu üretilse bile adı "eşik duyarlılık raporu" olmalı,
  "kalibrasyon" değil, ve otomatik öneri içermemeli.

### BACKTEST-CANLI-CEKIRDEGI-KULLANMIYOR (R14'ten devir)
**Backtest motoru canlı botun karar yolunu ölçmüyor, dolayısıyla canlı strateji
hakkında yanlışlanabilir iddia taşımıyor.**
- Kanıt: RF-PLAN-3 / PLAN.md R14 parity harness. Yalnız canlı yolda bulunan
  kapı/aşama sayısı: 25. Beş ajanın hepsi backtest'te YOK.
- Sonuç: backtest sayıları güven formülü, ajan ağırlıkları veya çıkış geometrisi
  için kanıt olarak kullanılamaz.
- İş: ya backtest canlı çekirdeği çağıracak şekilde taşınmalı, ya da karar
  tamamen canlı/gölge telemetriyle verilmeli. Büyük iş, ayrı döngü.

## ORTA

### SOCIAL-KAYNAK-DIRILTME
SocialAgent bu döngüde politika gereği kapatıldı (`DISABLED_BY_POLICY`), silinmedi.
Diriltmek istenirse Reddit OAuth (`oauth.reddit.com`, ücretsiz, app kaydı gerekir).
Diriltmeden önce sinyal değerinin kanıtlanması gerekir; bugün kanıt yok.

### YAHOO-YEDEGI-OLU
`core/fundamental_analyzer.py:101` `_get_yahoo_fallback` iki kat ölü: Yahoo bugün
HTTP 401 veriyor (ölçüldü) ve fonksiyon yalnız AV anahtarı YOKSA çağrılıyor
(satır 54-55), yani kota tükendiğinde asla devreye girmiyor. R16 bunu ya kaldırıyor
ya dürüst `NO_DATA` yapıyor. Gerçek bir yedek veri kaynağı isteniyorsa ayrı iş.

### RISKAGENT-COGUNLUGA-SAYILIYOR
`core/agent_coordinator.py:405-441`: RiskAgent `votes` listesinde ve
`buy_count`/`sell_count`'a sayılıyor, yani hem veto hem oy. RiskAgent SELL'i
`sell_count >= 3` çoğunluğuna katkı verip `confidence *= 1.2` bonusu alabiliyor.
Rol karışıklığı. Bu döngüde DOKUNULMADI (çoğunluk mantığına dokunmama kararı).
Rol-farkında quorum ayrı kalibrasyon ister.

### AV-ANAHTARI-IKI-KONTEYNER
Live ve paper ayrı konteynerlerde ayrı state hacimleriyle koşuyor; tek AV
anahtarının 25/gün kotası state dosyası üzerinden koordine edilemez. R16 bütçeyi
bölüyor. Kalıcı çözüm: konteyner başına ayrı anahtar.

## DÜŞÜK

### CONFTEST-YAZILABILIR-TMP-ISTIYOR
`tests/conftest.py` yazılabilir bir geçici dizin istiyor, bu yüzden test suite'i
salt okunur kumda (Codex review ortamı) koşturulamıyor. Review eden modelin
baseline'ı kendi doğrulayamaması bir gözlemlenebilirlik eksiği. Testleri
salt okunur ortamda da koşabilir hale getirmek küçük ama faydalı iş.
