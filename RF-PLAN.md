# RF-PLAN ,  Koruma Değişmezi ve Kilit Açma (v5, Codex R4 sonrası)

> Bu dosya `/codex` (Rocket Fuel) koşusunun plan dosyasıdır. Depoda zaten bir
> `PLAN.md` (sürüm geçmişi) olduğu için çakışma kuralı gereği `RF-` önekiyle yazıldı.

## Core Focus (tek cümle)

Her canlı **strateji** pozisyonunun **doğrulanmış** bir sunucu koruması olsun ve olmadığında bot
bunu gürültülü şekilde haber versin; performans onarımı ve kilit açma ancak ondan sonra.

## Kanıt ,  2026-07-28 itibarıyla ölçülen gerçek durum

Veri kaynağı: VPS 91.99.9.121, iki konteyner, `state_*/trade_history.json`,
`state_*/bot_positions.json`, `logs/bot_2026-07-15.log`, `logs/health_check.txt`.

| Hesap | İşlem | Kazanan | Kaybeden | Kazanma oranı | Net |
|---|---|---|---|---|---|
| LIVE | 4 | 1 | 3 | %25.0 | -$2.87 |
| PAPER | 8 | 0 | 8 | %0.0 | -$296.65 |
| **Toplam** | **12** | **1** | **11** | **%8.3** | **-$299.52** |

LIVE equity $489.26 → $480.52. **Hiç TAKE_PROFIT çıkışı kaydedilmemiş** (iki hesapta
da, hiç). Son alım: 2026-07-16, o günden beri sıfır alım.

⚠️ 12 gözlem "çıkışlar bozuk" tezini KANITLAMAZ ,  kötü girişler de aynı tabloyu
üretir (hedef %8-12, stop %4-6). Aşağıdaki kökler kaynak koddan tek tek doğrulandı;
performans nedenselliği R0-A ölçümüyle ayrıca kurulacak. **Ama R0-B/R0-F bu ölçümü
BEKLEMEZ:** korumasız pozisyon bırakan yollar, performans nedenselliğinden bağımsız
olarak kaynakta kanıtlanmış güvenlik kusurlarıdır.

---

## KÖKLER

### KÖK-0 ,  Pozisyon korumasız kalabiliyor, üstelik log tersini söylüyor ⚠️ EN CİDDİ
Kaynakta doğrulanmış **dört** ayrı yol:

**(a) Girişte** ,  `core/executor.py:167-179`: ayrı stop-limit emri `try/except` içinde;
gönderim başarısızsa yalnız `logger.warning` basılır, **akış devam eder** ve hemen
ardından `:181-185` `"STOP-LOSS: {symbol} @ ..."` satırını yazar. Yani stop hiç
konmamışken log "kondu" der, pozisyon `bot.positions`'a korunuyormuş gibi kaydedilir.

**(b) Güncellemede** ,  `core/position_manager.py:485-542` `_update_server_stop_loss()`:
önce iptal eder, sonra gönderir; tüm hataları `except Exception` ile yutar, **durum
döndürmez**; çağıran taraf yine de `breakeven_set = True` işaretler. İptal başarılı +
gönderim başarısız → pozisyon korumasız, bot "armed" sanıyor.

**(c) Çıkışta** ,  `core/executor.py:283-295` `execute_sell()`: önce sembolün TÜM açık
SELL emirlerini iptal eder (`except: pass`), sonra `close_position()` çağırır. Kapatma
hata verirse pozisyon korumasız kalır, geriye yalnız bir hata logu düşer.

**(d) Tetiklenmiş ama dolmamış stop-limit** ,  emir hâlâ "açık" görünür ve yön/durum/
miktar kontrollerinin hepsinden geçer, ama limiti piyasaya göre
**marketable olmadığı** için fiilen doldurmaz (long sell stop-limit'te limit stop'un
%0.5 altındadır; boşluklu düşüşte piyasa limitin de altına iner ve emir asılı kalır). Fiyat düşmeye devam ederken pozisyon **ekonomik olarak korumasız**
ve alarmsızdır. Salt "koruyucu emir var mı" kontrolü bunu asla yakalayamaz.

Kurtarma ağı da zayıf: `ensure_protective_stops()` (`:406-459`) yalnız *varlığa* bakar
(`if symbol in open_sell_syms: continue`) ,  yön, aktiflik, kalan miktar, stop fiyatı,
TIF veya pozisyonu kapsayıp kapsamadığı kontrol edilmez; ayrıca yerleştirme başarısız
olsa bile `placed += 1` sayar ve emir/pozisyon sorgusu hata verirse sessizce `return`
eder. Üstelik yalnız üç noktada çağrılır (`stock_bot.py:299, 374, 1865`) ,  gün içi
manuel/borsa kaynaklı bir iptal bir sonraki tetiğe kadar fark edilmez.

⚠️ **Doğrulanmamış:** "bracket'ın duran TP bacağı ensure'ü kalıcı olarak atlatır"
mekanizması. Alpaca bracket üyelerinden biri iptal edilince grubun kalanını da iptal
eder; dolayısıyla bu ancak yetim/yarış durumunda oluşur. `order_class`, parent/leg
ID'leri ve terminal durumlar incelenmeden iddia edilmeyecek.

### KÖK-1 ,  Break-even işaret hatası (yerel döngü)
`core/position_manager.py:108` `stop_loss_pct = be_offset` (işaretsiz büyüklük) →
`:140` `if pnl_pct <= -pos_sl_pct` ile karşılaştırılınca "girişin %0.3 ALTINDA sat"
olur. Niyet "+%0.3'ü kilitle" idi. Sunucu koruması ayaktayken gerçek zarar sunucu
tarafından sınırlanır; bu bir *fallback/split-brain* kusuru, tek koruma kaybı değil.
KÖK-0 ile birleştiğinde kritik olur.
Canlı tetik `config.py:423` `breakeven_trigger_pct: 0.025` (%2.5) ,  v1'deki %1.5
koddaki *varsayılan* değerdi, canlı config değil.

### KÖK-2 ,  Gap sıkıştırma yanlış referans alıyor
`core/gap_scanner.py:243-247` ,  yorum "mevcut fiyatın %1 altına çek" diyor, kod
`stop_loss_pct = 0.01` yazıyor; bu **girişin** %1 altı demek. Gerçek kusur iki yönlü:
(a) fiyat girişin altındayken yerel stop anında tetiklenir, (b) daha sıkı bir korumanın
(örn. armlanmış %0.3 break-even) üzerine yazıp onu **gevşetebilir**. Sıradan %4-6'lık
bir stopu ise normalde sıkıştırır ,  yani her koşulda gevşetme iddiası yanlıştı, geri alındı.
-%1.9 / -%4.2 / -%2.6 çıkışları (hepsi 13:30 UTC açılışta) (a) ile uyumlu.
Bu blok yalnız yerel metadata yazar, sunucu stop'una hiç dokunmaz.

### KÖK-3 ,  Kayıp serisi WARN dalında üst sınırsız kilit
`core/trade_gates.py:158-165` ,  2+ ardışık zararda güven ≥%70 şartı; **süre yok**.
Sayaç iki yerde sıfırlanır: kârlı kapanışta (`core/streak.py:35`) ve süresi dolmuş
HALT dalında (`core/trade_gates.py:155`). Sayaç 2'de takılıyken, **bot bir kez pozisyonsuz kaldıktan sonra** ikisine de
ulaşılamaz: HALT ≥4 gerektirir, WARN ise sayacı ilerletecek yeni girişi engeller.
(Açık bir pozisyon hâlâ duruyorken kârlı kapanıp seriyi sıfırlayabilir — kilit ancak
bot düzleştikten sonra kendini kapatır. Bot 2026-07-16'dan beri düz.)
2026-07-16'da RIVN zararıyla sayaç 2 oldu, 12 gündür orada.
Matematiksel olarak *kalıcı* değil ,  ≥%70 güvenli bir sinyal gelip kazanırsa açılır.
Doğru tanım: **veriye bağlı, üst sınırsız kilit.**
Ayrıca `_loss_halt_until` diske yazılmıyor: kayıt yapısı `stock_bot.py:1713-1725`,
yükleme `:1777` yalnız `consecutive_losses` alır → her restart yasağı sıfırdan kurar.
Süre canlıda **24 saat** (`config.py:399`); 4 saat paper override'ıdır (`:803`).

### KÖK-4 ,  `min_confidence_score: 50` ölçülmedi (root cause DEĞİL)
`config.py:378`. Gözlenen güvenler %30-45'ti, ama `core/agent_coordinator.py:473`
`confidence = |weighted_score| * 2.0` (çoğunlukta ×1.2, tavan 100) formülü %60-84'ü
mümkün kılıyor. v1'deki "yapısal olarak ulaşılamaz" iddiası 12 gözleme dayalı aşırı
genellemeydi, geri çekildi. **Ölçülmeden eşiğe dokunulmaz.**

### KÖK-5 ,  Sessiz arıza, ve alarm kanalı da sessiz
Health check "SON 7 GUNDE HICBIR ISLEM YOK!" yazdı, kimse görmedi. Repoda ntfy'ye
yayın yapan **hiç kod yok** (PLAN.md aksini iddia ediyor).
`core/notifier.py` bir `TelegramNotifier` sunuyor ve `stock_bot.py:232`'de örneği var,
ama **"hazır" demek fazla iyimserdi**: `:28` kimlik bilgisi yoksa `enabled = False`
olur, `:38-39` sessizce `False` döner, HTTP hatası `:53` ve istisna `:56` **debug**
seviyesinde loglanır. Dahası `notify_buy/sell/error/...` sarmalayıcılarının çoğu
`self._send(text)` sonucunu **döndürmez** (`None` döner) — çağıran taraf başarısızlığı
fark bile edemez. Yani alarm kanalının kendisi sessizce ölebilir.

### Issues List (bu onarımın kapsamı dışı, kayda geçti)
- `gap_scanner.py:258` `trailing_override` yazılıyor, **repoda tek okuyucusu yok** → ölü kod.
- `gap_scanner.py:261` `PARTIAL_SELL` sadece log basıyor, satış yapmıyor.
- Koruyucu emirler stop-**limit**, limit toleransı %0.5 (long `position_manager.py:519`,
  short `:521`) → boşluklu açılışta tetiklenip dolmayabilir.
- Index parking pozisyonları `ensure_protective_stops`'ta bilinçli olarak atlanıyor
  (`:437-438`) → SPY park kolu koruma değişmezinin DIŞINDA. Bu bilinçli mi, karara bağlanacak.

---

## KAYALAR

### R0 ,  Koruma değişmezi (BUGÜN, canlı alım kilidi AÇILMADAN, minimal kapsam)
Tek hedef: *hiçbir canlı **strateji** pozisyonu, doğrulanmış bir sunucu koruması
olmadan ve haber verilmeden duramaz.* Değişmez bilinçli olarak SPY index-parking
koluyla sınırlandırılmamıştır ,  park kolu şu an `position_manager.py:437-438` ile
dışarıda; **bu bir karar noktası ve İhsan'a sorulacak** (ya değişmeze dahil edilir,
ya da kapsam dışı olduğu açıkça yazılır). Karar verilmeden "her pozisyon korunuyor"
denmeyecek. Karşılaştırıcı düzeltmeleri ve kanonik refactor R1'e taşındı.

- **R0-B (çekirdek):** üç yazma noktası tek bir doğrulanmış değişmeze bağlanır , 
  `execute_buy` (giriş), `_update_server_stop_loss` (güncelleme), `execute_sell`
  (kapatma başarısızlığı), artı `ensure_protective_stops` (uzlaştırma).
  - Güncelleme **cancel+submit yerine patch**: `TradingClient.replace_order_by_id` +
    `ReplaceOrderRequest` (SDK'da doğrulandı; alanlar: `qty, time_in_force, limit_price,
    stop_price, trail, client_order_id`). Fractional'da `qty` **gönderilmez**, DAY TIF
    korunur, stop-limit için hem `stop_price` hem `limit_price` verilir.
  - **HTTP 200 kanıt değildir:** yeni emir ID'si döner ama nihai başarıyı garanti etmez.
    Yeni ID ve replacement zinciri poll edilir (ya da `trade_updates` dinlenir) ve durum
    dört sınıftan birine yazılır: *aktif değişim / eski stop hâlâ aktif / pozisyon zaten
    kapanmış / çıplak başarısızlık*.
  - **Retry kör olmaz:** her denemeden önce eski emrin `status`/`replaced_by`, dönen
    replacement ve güncel pozisyon yeniden okunur; korelasyon için deterministik
    `client_order_id` kullanılır.
  - **Stop bacağı hiç yoksa** patch işe yaramaz; çakışan çıkış grubu iptal edilir,
    terminal iptal beklenir, yeni koruma konur ve doğrulanır; sınırlı süre içinde
    koruma kurulamıyorsa acil kapatma.
  - `ensure_protective_stops` artık *varlığa* değil **kapsamaya** bakar: yön, aktif
    durum, kalan miktar, stop fiyatı, TIF ve pozisyonu kapsama. Başarısız yerleştirme
    `placed` saymaz; emir/pozisyon sorgusu hatası sessiz `return` değil alarmdır.
  - **Durum ayrımı:** `breakeven_set` yerel strateji durumu olarak KALIR (restart göçü,
    kısmi-stop yeniden kurulumu ve ensure onu okuyor). Yeni ve ayrı
    `server_stop_verified` + `server_stop_order_id` alanları retry ve alarmı sürer.
  - **Elected-but-unfilled kuralı:** tetiklenmiş fakat piyasaya göre marketable
    olmayan stop-limit **başarısız koruma** sayılır ,  sınırlı süre içinde ya market
    kapatma, ya değiştirme, ya alarm.
  - **Canlıda iki adımlı giriş kaldırılır:** atomik bracket kabul edilmiyorsa pozisyon
    **hiç açılmaz**. Market-alım kabulü ile ayrı stop doğrulaması arasındaki çökme
    penceresi başka türlü kapatılamaz.
  - **Kapatma da kabul ≠ icra:** `close_position()` kabulü nihai değildir. `close-in-progress`
    durumu kalıcı yazılır, pozisyonun gerçekten düzleştiği doğrulanır, düzleşmediyse
    koruma geri kurulur ve alarm basılır.
  - Gün içi kayıp koruma için periyodik uzlaştırma (ya da trading-stream) eklenir;
    alarmlar tekrarsızlaştırılır, onarım sınırlıdır.
- **R0-E (alarm kanalı, R0'ın parçası):** `TelegramNotifier.enabled` doğrulanır, her
  `send` dönüşü kontrol edilir, başarısızlık **ERROR** olarak yerel diske de yazılır,
  ve sürüm öncesi bir kontrollü uçtan uca bildirim gönderilir. Kanıtlanmamış kanal
  tek alarm yolu olamaz.
  ⚠️ Yerel diske ERROR yazmak **kimseyi uyarmaz** ve süreç öldükten sonra hiç çalışmaz.
  Koruma değişmezi "kanıtlandı" sayılmadan önce **süreç dışı** ikinci bir yol şart.
- **R0-F (süreç dışı nöbetçi):** fractional DAY koruması kapanışta düşer; bot gece
  ölürse ertesi açılış korumasız başlar ve **süreç içi hiçbir alarm çalışamaz**.
  Bot ölümünü ve Alpaca'da korumasız pozisyonu **dışarıdan** görecek bir nöbetçi
  gerekir ve bu **zorunludur, seçenek değil**: tam-lot GTC koruması da kaybolabilir ve
  `execute_sell` çıkışları iptal ettikten sonra süreç/VPS ölürse pozisyon yine korumasız
  ve alarmsız kalır ,  yani gecelik fractional yasağı bu deliği kapatmaz, olsa olsa
  ek azaltmadır. Elimizde hazır aday var: `Projeler/Akilli_Watchdog` (#35, filo
  nöbetçisi, canlı). Trading bot bu nöbetçiye kayıtlı DEĞİL ,  R0-F bu kaydı yapar.
- **R0-D:** paper hesapta soak ,  replacement ve çıplak-emir kurtarma senaryoları
  yakalanmış açık emir durumuyla. Canlıya dokunulmaz.
- **R0-A (paralel, R0-B'yi BLOKLAMAZ):** kapanan her işlem için giriş, intraday MFE,
  stop-arm olayı ve gerçek dolum çıkarılır. Not: MFE order history'den **türetilemez**
  ,  dolumlar, tutma aralığı boyunca dakikalık/işlem verisiyle birleştirilecek (botun
  kullandığı normal seans pencereleri esas alınarak). Stop-arm olayı da yalnız order
  history'den kanıtlanamaz: başarısız replacement denemeleri ve yerel `breakeven_set`
  geçişleri Alpaca'da iz bırakmayabilir → VPS bot loglarıyla ve pozisyon metadata'sıyla
  korele edilir, kayıt yoksa vaka *bilinmiyor* etiketlenir. R0-A, atıf ve kilit açma
  için ZORUNLU; koruma hotfix'i için değil.

### R1 ,  Tek kanonik tetik (yeni yüzde alanı EKLEMEDEN) + karşılaştırıcı düzeltmeleri
v1'deki "işaretli `stop_trigger_pct` ekle" tasarımı **iptal**. `stop_loss_pct`
işaretsiz mesafe olarak `plan_exit_pcts`, long/short executor, kısmi-stop yeniden
kurulumu, `ensure_protective_stops`, R:R hesabı ve backtest tarafından okunuyor;
işaret sızdırmak altısını birden bozar. Mutlak fiyat alanı **zaten var**:
`core/executor.py:194` `stop_loss_price` (short: `core/short_executor.py:170`).
Tetik kararı tek yardımcıya toplanır, kanonik kaynak `stop_loss_price`, yüzdeler
yalnız planlama girdisi kalır. Türetme: long `entry*(1-stop_loss_pct)`,
short `entry*(1+stop_loss_pct)` ,  negatif yüzde türetmesi yok.
KÖK-1 ve KÖK-2 karşılaştırıcı düzeltmeleri burada yapılır (gap tetiği mevcut fiyattan
ve **asla gevşetmeyecek** şekilde; short'ta `(entry - current*1.01)/entry`).
Short break-even'in bilinçli olarak `-be_offset`'e karşılık geldiği yazıya geçer.
**Kalıcılık zorunlu:** `_stash_exit_flags` (`stock_bot.py:1695-1708`), Alpaca sync
(`:1501-1537`) ve metadata yükleme (`:1739-1772`) sabit alan listeleriyle çalışıyor;
tetikle ilgili her alan üçüne birden eklenmezse restart'ta sessizce kaybolur.
Göç: `breakeven_set=True` ama tetik alanı yoksa break-even tetiğine taşınır.

### R2 ,  Gap sıkıştırması sunucuya da yazılır
Yerel metadata + sunucu koruması birlikte, yalnız koruma yönünde. İki taraf için test.

### R3 ,  Kanıt kapısı
Backtest **tek başına kapı olamaz**: `backtest.py:579` break-even'i %1.5 sabit kodlar
(canlı %2.5), gap tarayıcı modeli yok, sabit tarih/sembol argümanı yok. Üç parçalı kapı:
1. Emir yaşam döngüsü mock testleri: replacement zinciri, replace reddi + retry,
   fractional DAY süresi dolması, restart kalıcılığı, stop-limit tetiklenip dolmama,
   kısmi pozisyon, short tarafı, çıplak-kurtarma.
2. Deterministik ve config-sadık backtest (eski/yeni çıkış bayrağıyla).
3. Paper soak, açık emir durumu kayıt altında.
Geçme ölçütü **işlem başına doğru tetik davranışı**. "TAKE_PROFIT > 0 ve oran düzeldi"
tek başına ne gerekli ne yeterli; performans güven aralığı olarak raporlanır.

### R4 ,  Kalan gözlem
R0-E kritik alarmları zaten kurdu. Burada N iş günü sıfır işlem alarmı ve günlük huni
özeti tamamlanır. Eşik sayımı ve notifier çağrısı ayrı ayrı test edilir.

### R5a ,  Kilit mekanizmasını yaz ve doğrula (kilit HÂLÂ KAPALI)
`loss_streak_decay_hours` eklenir; çürüme `_check_loss_streak` sayacı okumadan ÖNCE
uygulanır. Gerekçe (düzeltildi): WARN **kapanışları değil yeni girişleri** engeller , 
mevcut pozisyonlar kapanıp seriyi sıfırlayabilir; ama pozisyonsuz ve kilitli bir bot
hiç yeni çıkış üretmez, dolayısıyla `update_loss_streak` içine konan çürüme hiç
çalışmaz. `_loss_halt_until` diske yazılır (24s canlı / 4s paper ikisi de test edilir).
`_symbol_consecutive_losses` için çürüme tanımlanır ya da kalıcı karantina bilinçli
karar olarak yazıya geçer. `min_confidence_score` yalnız `tools/conf_histogram.py`
ölçümü destekliyorsa değişir; desteklemiyorsa 50'de kalır.

### R5b ,  Kilidi aç (ayrı adım, ayrı onay)
R5a'nın getirdiği değişiklikler R3 kanıt kapısından SONRA geldiği için kendi kapısına
tabidir: **tam olarak açılışta kullanılacak config** ile testler ve paper soak
tekrarlanır. Kilit ancak o soak temiz çıkarsa ve İhsan onay verirse açılır.

---

## KAPSAM DIŞI
Strateji değişikliği yok. EMA200 kapısı gevşetilmiyor. Alinea özellikleri bu onarıma
karışmıyor. **Canlı deploy yok** ,  İhsan'ın açık onayı ve tercihen piyasa kapanışı sonrası.

## RİSK
Kilit şu an koruma görevi görüyor. Açılması için gereken sıra: R0-R4 tamamlanır,
R5a doğrulaması biter, R5b'de tam olarak açılışta kullanılacak config ile soak temiz
çıkar ve İhsan onay verir. Bunlardan biri eksikken açılırsa %8 kazanma oranlı bir
strateji gerçek parayla yeniden başlar. Sıra bilinçli.
