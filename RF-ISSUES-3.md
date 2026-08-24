# RF-ISSUES-3.md - Trading Bot Clarity Break, tam bulgu listesi

> Kaynak: 7 boyutlu paralel kod denetimi (workflow wf_b7177be4-725), 2026-08-23.
> **DOGRULAMA DURUMU: BEKLIYOR.** Denetimin dogrulama fazi oturum limitinde dustu.
> Bu listedeki her madde TEK ajan iddiasidir; Codex Same Page Meeting bunlari koda karsi sinar.
> Asagidaki 5 madde Claude tarafindan BAGIMSIZ dogrulandi (canli broker verisi + kod okumasi):
> EXIT-PARTIAL-DEFTERE-YAZILMIYOR, OLCUM-DEFTER-KADEMELI-SATISI-YAZMIYOR,
> GATE-LOSS-STREAK-WARN-KILITLI-KISIR-DONGU, STRAT-KENDI-KANITI-SPY-ALTI, HUNI-CONF-SAYACI-KIRLI-NOTRADE-TESHISI-YANLIS.

| Siddet | Adet |
|---|---|
| KRITIK | 32 |
| YUKSEK | 35 |
| ORTA | 24 |
| DUSUK | 3 |
| **TOPLAM** | **94** |


## KRITIK

### AGENT-SENT-TERS-ISARET  (Ajanlar)
**SentAgent'ın haber işareti TERS: kötü haber maksimum BUY güveni üretiyor**

- Yer: `core/news_analyzer.py:303-309 (+ core/finbert_analyzer.py:367-372, 401-415, 435-441)`
- Ne oluyor: FinBERTAnalyzer.analyze() zaten İŞARETLİ bir skor döndürüyor: `if label == "negative": score = -raw_score` (finbert_analyzer.py:370-371). Üç yolun üçü de (ONNX FinBERT, VADER fallback `_analyze_vader` score=compound, `_analyze_simple` score=(bull-bear)*0.2) aynı sözleşmeyi kullanıyor: negatif etiket ⟺ negatif skor. Ama tüketen taraf bu skoru bir kez DAHA negatifliyor:

```python
# core/news_analyzer.py:305-309
result = self.finbert.analyze(text[:512])
if result["label"] == "positive":
    nlp_score = result["score"] * 30
elif result["label"] == "negative":
    nlp_score = -result["score"] * 30   # -(-0.9)*30 = +27  ← İŞARET DÖNÜYOR
```

Sonuç: nlp_score (haber skorunun %40'ı, news_analyzer.py:320) haberin polaritesi ne olursa olsun HER ZAMAN pozitif. Doğru işaretli tek bileşen keyword_score (%30) kalıyor ve +12'lik NLP katkısını taşıyamıyor. Bu skor stock_bot.py:1220'de `news_score` olarak SentAgent'a gidiyor, SentAgent de `combined >= 12 → BUY`, `confidence = min(|news_score|*1.5, 100)` yapıyor (agent_coordinator.py:192, 212-217). NOT: doğru işaretli `elif self.vader:` dalı (satır 313-314) yalnızca FinBERT import'u BAŞARISIZ olursa çalışır; container'da onnxruntime+tokenizers kurulu olduğu için her zaman BUGLI dal koşuyor.
- Neden onemli: Ensemble ağırlığının %20'si haberin iyi mi kötü mü olduğunu ayırt edemiyor ve sistematik boğa ofseti üretiyor. Ölçtüğüm değerlerle: TechAgent SELL (conf 49) derken SentAgent ters-okumayla BUY 100 derse ağırlıklı skor -12.25 yerine +7.75 çıkıyor — yani gerçek bir teknik satış sinyali sadece işareti bozuk haber ajanı yüzünden iptal ediliyor, hatta işaret değiştiriyor. Paper'ın büyük kaybedenleri (AMZN -127.98, SMCI -97.03) tam olarak bu profilde: zayıf teknik + yüksek 'sentiment' güveni ile eşiği geçen girişler. Kapalı işlemlerdeki asimetri (kazananlar $2.72/$3.49/$4.91, kaybedenler $97/$128) girişlerin bir bilgi kaynağına değil sabit bir boğa ofsetine dayandığının imzası.
- Onerilen duzeltme: news_analyzer.py:307-309'u tek satıra indir: `nlp_score = result["score"] * 30` (etikete bakma, skor zaten işaretli). Regresyon testi ekle: bilinen bearish başlık → news_score < 0, bullish → > 0. Aynı sözleşme ihlalini `_check_geopolitical_risk` içinde de kontrol et. Düzeltme sonrası SentAgent'ın oy dağılımını yeniden ölç — düzeltilmiş haliyle SentAgent muhtemelen çoğu sembolde HOLD'a düşecek, dolayısıyla eşikler (paper 30 / live 50) yeniden kalibre edilmeli.

### AGENT-SOCIAL-KALICI-SESSIZ  (Ajanlar)
**SocialAgent kalıcı olarak susmuş (Reddit 403), ama her sembolde 12 saniye bloklayıcı uyku maliyeti ödeniyor**

- Yer: `core/social_sentiment.py:151-198, 204-207; core/agent_coordinator.py:230-263`
- Ne oluyor: SocialAgent'ın iki veri kaynağı da üretimde ölü:
1) Reddit: kimliksiz `https://www.reddit.com/r/{sub}/search.json` çağrısı (social_sentiment.py:154-163). Reddit 2023'ten beri datacenter IP'lerini bloklar. Az önce ölçtüm: HTTP 403 + HTML gövde. `if response.status_code == 200:` (satır 165) hiç girilmiyor, hata `logger.debug` ile yutuluyor (satır 190-191) → post_count=0, score=0.
2) X/Nitter: `if not self.nitter: return {"score": 0, "tweet_count": 0}` (satır 206-207). Kodun kendi yorumu itiraf ediyor: 'ntscraper sık sık "Cannot choose from an empty sequence" hatası verir... beklenen davranış' (satır 231-232).

Sonuç `social_score=0` → SocialAgent HOLD, `confidence = min(0*2,100) = 0` (agent_coordinator.py:232) → ağırlıklı skora katkısı 0. Ama maliyeti sıfır değil: 6 sub × 2 terim = 12 istek, her birinin ardından `time.sleep(1)` (satır 188) — bu sleep `if status_code == 200` bloğunun DIŞINDA, yani 403'te de çalışıyor. Her istek ~190KB HTML indiriyor.
- Neden onemli: Nominal ağırlığın %15'i kalıcı olarak sıfır. Bu sadece 'bilgi eksikliği' değil, güven ölçeğini de bozuyor: koordinatör 5 ajanlık bir ölçekte kalibre edilmiş eşiklerle (paper 30 / live 50 / boyut bantları 50-80) çalışırken gerçekte 2 ajan oy veriyor. Ayrıca sembol başına 12 saniyelik bloklayıcı uyku + 2.3MB boşuna indirme ana döngüyü yavaşlatıyor (bkz. AGENT-DONGU-BOGULMASI). `logger.debug` ile yutulan 403 klasik sessiz başarısızlık: bot 'SocialAgent nötr' diye rapor ediyor, gerçekte 'SocialAgent kör'.
- Onerilen duzeltme: Kısa vade: SocialAgent'ı ve `analyze_social` çağrısını (stock_bot.py:1230-1234) tamamen devre dışı bırak; ağırlığı kalan ajanlara yeniden dağıt ve eşikleri yeniden kalibre et. Uzun vade: veri kaynağı istenirse Reddit OAuth (client_id/secret ile oauth.reddit.com) kullanılmalı. Her iki durumda da kaynak sağlığı bir sayaçla ölçülmeli: ardışık N başarısız fetch → WARN log + funnel'a `social_source_down` etiketi, `logger.debug` ile yutma.

### AGENT-FUND-KOTA-VE-CACHE  (Ajanlar)
**FundAgent kotası günün ilk 5 dakikasında bitiyor; başarısızlık cache'lenmediği için her turda 15 saniye bloklanıyor**

- Yer: `core/fundamental_analyzer.py:53-99, 245-248; core/news_analyzer.py:151-153, 214-225`
- Ne oluyor: Tek bir ALPHA_VANTAGE_KEY üç tüketici arasında paylaşılıyor: fundamental_analyzer (OVERVIEW), news_analyzer (NEWS_SENTIMENT), earnings_calendar. Bunlardan yalnız Marketaux'un günlük sayacı var (`_marketaux_max_daily = 50`, news_analyzer.py:104); Alpha Vantage'in HİÇBİR kota sayacı yok. news_analyzer cache'i 5 dakika (NEWS_CONFIG['cache_minutes']=5), yani ~25 sembol × saatte 12 tur = saatte ~300 AV çağrısı. AV ücretsiz katman GÜNLÜK 25 çağrı — kodun kendi yorumu bunu biliyor (news_analyzer.py:80-81: 'AV free tier artık dakikalık değil GÜNLÜK kota (25/gün)'). Yani kota her sabah ilk birkaç dakikada tükeniyor.

Kota bittiğinde AV, HTTP 200 + içinde 'Symbol' olmayan bir uyarı JSON'u döner → `if "Symbol" not in data: return None` (fundamental_analyzer.py:70-71). Kritik nokta: BAŞARISIZLIK CACHE'LENMİYOR — `self.cache[cache_key] = overview` yalnız başarı yolunda (satır 89). Dolayısıyla her sembol, her tarama turunda AV'yi yeniden çağırıyor ve her çağrıda:

```python
# core/fundamental_analyzer.py:64-65
response = requests.get(url, params=params, timeout=15)
time.sleep(15)  # Rate limit (5 req/min)
```

15 saniye ana döngüyü BLOKLUYOR (bot tek thread). analyze_fundamentals None alınca `{'fundamental_score': 0, 'metrics': {}}` döndürüyor (satır 150-156) → FundAgent score=0 → HOLD, confidence=0 (agent_coordinator.py:145-146).
- Neden onemli: İki ayrı hasar: (1) Ensemble ağırlığının %20'si günün ilk dakikalarından sonra kalıcı sıfır — SocialAgent'ın %15'iyle birlikte nominal ağırlığın %35'i sessiz. (2) Sembol başına her turda 15 saniyelik bloklayıcı uyku, ~25 sembolde tur başına ~375 saniye eder; bot bu sürede stop/trailing takibi de yapamıyor. Bu, funnel'daki 441 tarama/gün rakamının doğrudan açıklaması (bkz. AGENT-DONGU-BOGULMASI). Ayrıca FundAgent'ın tasarımı zaten momentum stratejisiyle çelişiyor: P/E > 40 → -10 puan (fundamental_analyzer.py:167-169), yani botun avladığı büyüme hisselerini yapısal olarak cezalandırıyor.
- Onerilen duzeltme: 1) `time.sleep(15)`'i kaldır (kod yorumunun kendisi zaten 'uzun sleep kotayı korumuyor, sadece botu yavaşlatıyor' diyor). 2) Negatif cache ekle: başarısızlıkta da `self.cache[key] = None` + kısa TTL (ör. 60 dk) yaz ki tur başına tekrar denenmesin. 3) Marketaux'daki gibi AV için günlük sayaç ekle (max 20/gün) ve sayacı fundamental + news + earnings arasında PAYLAŞTIR. 4) NEWS_CONFIG['cache_minutes']=5 ile 25/gün kotası uyumsuz — AV haber yolunu ya kapat ya günde 1 sembol/1 çağrıya indir. 5) FundAgent'ın P/E cezasının bu stratejiye uygunluğunu ayrıca karara bağla.

### AGENT-GUVEN-AYIRT-ETMIYOR  (Ajanlar)
**Nihai güven iki değişkene çöküyor (0.5×Tech + 0.4×Sent) ve Sent sabit ofset olduğu için kazananı kaybedenden ayıramıyor**

- Yer: `core/agent_coordinator.py:418-424, 473-485`
- Ne oluyor: Ağırlıklı skor `weighted_score = Σ signal_value × weight × confidence` (satır 421-424), nihai güven `confidence = abs(weighted_score) * 2.0` (satır 473). FundAgent ve SocialAgent üretimde HOLD/0, RiskAgent normal durumda HOLD (satır 341) → her üçünün `signal_value=0` → ağırlıklı skora katkı SIFIR. Geriye tek formül kalıyor:

  confidence = 2 × (0.25×TechConf + 0.20×SentConf) = 0.5×TechConf + 0.4×SentConf

SentConf ise haber polaritesinden bağımsız (AGENT-SENT-TERS-ISARET) — yani ~sabit bir boğa ofseti. Dolayısıyla güven skoru, pratikte TechAgent'ın indie_score'unun monoton bir dönüşümü + sabit bir sayı. Bu 'ensemble' değil, kılık değiştirmiş tek göstergeli bir eşik.

İkinci çöküş: TechAgent güveni `15 + min(|indie|*0.85, 85)` (satır 130) — yani asla 15'in altına inmiyor ve 25-60 bandına sıkışıyor; koordinatörde ×0.25 ile çarpılıp ×2 ile açılınca 12-30 bandına oturuyor. Ölçek genişliği yok → çıktı dağılımı dar.

Üçüncü çöküş (paper'da eşik NO-OP): koordinatör çoğunluk yokken BUY'ı ancak `weighted_score > 15` ise üretir (satır 442). O halde her BUY için matematiksel olarak `confidence = ws×2 > 30`. Paper eşiği `min_confidence_score = 30` (config.py:764). Yani PAPER'da güven kapısı hiçbir BUY'ı bloklayamaz — koordinatörün kendi sinyal tabanı zaten kapının üstünde.
- Neden onemli: Sorunun doğrudan cevabı: kaybeden RIVN'in 54 alması ve çoğu kazananın altında kalması tesadüf değil, formülün sonucu. Güven skoru pozisyon boyutunu belirlediği için (config.py:335-341 live_conf_position_bands) bot en büyük pozisyonları en 'güvenli' değil, en yüksek teknik momentumlu + en çok haberi olan hisselere veriyor — ki bunlar tam olarak tepe kovalanan girişler. Paper'daki eşik NO-OP'u da funnel'la uyumlu: paper'da conf_below_min neredeyse yok, tek gerçek filtre EMA200 (08-21: 1122 sinyalin 1122'si).
- Onerilen duzeltme: 1) Önce AGENT-SENT-TERS-ISARET'i düzelt; sabit ofset kalkmadan güven dağılımı düzelmez. 2) Sessiz ajanları ağırlık paydasından DÜŞ: `weighted_score`'u yalnızca oy veren (signal != HOLD) ajanların ağırlık toplamına normalize et — böylece 'iki ajan hemfikir' ile 'beş ajan hemfikir' aynı sayıyı üretmez. 3) TechAgent'ın `15 +` taban güvenini kaldır (sinyal yoksa güven de olmamalı). 4) `weighted_score > 15` sinyal eşiği ile `min_confidence_score` kapısını birbirinden bağımsızlaştır, aksi halde paper'da kapı NO-OP kalır. 5) Kalibrasyon için: son 20 işlemin giriş güveni ile PnL'i arasındaki Spearman korelasyonunu ölç ve rapora koy; korelasyon ~0 ise güven kapısı canlıya terfi kararında kullanılamaz.

### AGENT-DONGU-BOGULMASI  (Ajanlar)
**Ajan veri toplama yolundaki bloklayıcı sleep'ler ana döngüyü tasarlanan hızın ~%1'ine düşürmüş**

- Yer: `core/fundamental_analyzer.py:65; core/social_sentiment.py:188; core/news_analyzer.py:225; stock_bot.py:630, 656-660, 892-953`
- Ne oluyor: Bot tek thread ve `_analyze_and_trade` her sembol için sırayla ajan verisi topluyor. Bu yolda üç bloklayıcı uyku var:
- fundamental_analyzer.py:65 → `time.sleep(15)` (kota bittikten sonra HER sembolde HER turda, çünkü başarısızlık cache'lenmiyor)
- social_sentiment.py:188 → `time.sleep(1)` × 6 sub × 2 terim = 12 sn/sembol (403 dönse bile)
- news_analyzer.py:225 → `time.sleep(2)`
Toplam sembol başına ~29 saniye net uyku + ~15 HTTP isteğinin gecikmesi. Yapılandırılmış tarama aralığı ise 30 saniye (config.py:439, paper 15 — config.py:765).

Ayrıca bu maliyet, işlem GÖRMEYECEK semboller için de ödeniyor: `_get_agent_decision` satır 907'de çağrılıyor, endeks/ters-ETF filtresi ise satır 946 ve 953'te — yani QQQ, SQQQ, SH, SPXS için tüm Alpha Vantage + Reddit + haber zinciri koşuluyor, sonra `return` ediliyor.
- Neden onemli: Ölçülen etki devasa: 30 saniyelik aralık ve ~24 sembolle günde ~46.000 sembol-taraması beklenirken funnel LIVE'da 441-2224 gösteriyor. 441/24 ≈ 18 tam tur/gün = 6.5 saatlik seansta tur başına ~22 dakika. Bot piyasayı günde 18 kez örnekliyor. Bunun iki sonucu var: (1) sinyaller bayat — 22 dakika önceki saatlik bar okumasıyla market emri veriliyor, AMZN'in -%6'lık (-$14.22/adet) çıkışı tasarlanan stop mesafesinden bu kadar geniş olmasının en olası mekanik açıklaması stop takibinin de aynı döngüde bloklanması. (2) Giriş fırsatları yapısal olarak kaçıyor — 'entries=0' sonucunun bir kısmı kapılar değil, botun bakmıyor olması. Üstelik bu maliyetin tamamı HİÇ VERİ ÜRETMEYEN iki ajan için ödeniyor (Fund kotasız, Social 403).
- Onerilen duzeltme: 1) fundamental_analyzer.py:65'teki `time.sleep(15)`'i sil, negatif cache ekle. 2) SocialAgent devre dışı bırakılana kadar Reddit döngüsündeki `time.sleep(1)`'i sadece HTTP 200'de çalıştır ve sub sayısını 6'dan 2'ye indir. 3) Endeks/ters-ETF filtresini `_get_technical_analysis` çağrısından ÖNCEye taşı (satır 900 civarı). 4) Ajan veri toplamayı ana döngüden ayır: haber/temel/sosyal veriyi ayrı bir thread veya tur-başı toplu ön-yükleme ile hazırla, karar döngüsü yalnız cache okusun. 5) Tur süresini ölç ve funnel'a `loop_seconds` alanı yaz — 60 saniyeyi aşınca alarm.

### AGENT-GUVEN-OLCEGI-LOSS-STREAK-KILIDI  (Ajanlar)
**Koordinatör güven ölçeği ile LOSS_STREAK_WARN'ın istediği %70 uyumsuz; canlı bot 16 Temmuz'dan beri çıkışı olmayan bir kilitte**

- Yer: `core/trade_gates.py:162-184; core/streak.py:26-37; config.py:404-408; core/agent_coordinator.py:473-485`
- Ne oluyor: Canlı state'te `consecutive_losses=2` ve LIVE config `loss_streak_warn=2`, `loss_streak_elevated_conf=70` (config.py:405, 408). Kapı şunu istiyor:

```python
# core/trade_gates.py:176-184
elif loss_count >= config.get("loss_streak_warn", 2):
    elevated_conf = config.get("loss_streak_elevated_conf", 70)
    if analysis["confidence"] < elevated_conf:
        return True, "LOSS_STREAK_WARN"
```

Ama bu 'confidence' artık koordinatör güveni (stock_bot.py:1034 `analysis["confidence"] = decision["confidence"]`). Üretimdeki iki-ajanlı formülle (0.5×TechConf + 0.4×SentConf) 70'e ulaşmak için TechAgent'ın indie_score'unun ~53+ VE SentAgent'ın ~100 olması gerekiyor — nadir bir kesişim.

Kilit, kapanmayan bir döngü: sayaç yalnız KAZANÇLA sıfırlanıyor (`core/streak.py:34-37: elif pnl_usd > 0: bot._consecutive_losses = 0`) veya sayaç 4'e ulaşıp halt süresi dolarak (trade_gates.py:170-174). Zaman aşımıyla sönümlenme YOK. 2'de takılı bir bot giriş yapamaz → kazanamaz → sayaç sıfırlanamaz. Emici durum.
- Neden onemli: LIVE'ın 17 iş günü boyunca entries=0 olmasının doğrudan mekanik açıklaması ve funnel bunu doğruluyor: gate_block_reasons'ta LOSS_STREAK_WARN 08-04'te 144, 08-10'da 60, 08-12'de 85 kez. Bot 5+ haftadır sadece SPY park pozisyonu tutuyor; 3 aylık +%0.49'un tamamı o. 'Strateji katkısı fiilen sıfır' ifadesinin kod karşılığı burası. Ayrıca bu, ölçüm penceresini de sahte kılıyor: canlı hattın hiç işlem yapmaması 'kayıp yok' gibi okunabilir ama gerçekte sistem ölü.
- Onerilen duzeltme: Acil: `loss_streak_warn` kapısına zaman aşımı ekle (ör. 3 iş günü işlem yoksa sayacı 1 azalt) — emici durum bir devre kesicide kabul edilemez. Orta vade: `loss_streak_elevated_conf`'u gerçek güven dağılımının p80'ine göre belirle (sabit 70 değil), veya kapıyı güvene değil pozisyon boyutuna bağla (2 zarar sonrası tam boyut yerine yarım boyut). Her durumda AGENT-SENT-TERS-ISARET düzeltilmeden bu eşiği düşürmek kaybeden sinyalleri serbest bırakır — önce işaret hatası, sonra eşik.

### EXIT-BE-TAVANI-0.3PCT  (Cikislar/Defter)
**Break-even stop kazananları +%0,3'e kilitliyor; risk %5-6 — matematiksel olarak kaybeden geometri**

- Yer: `core/position_manager.py:355-380 + 436-455; config.py:430-432, 376, 766-773`
- Ne oluyor: BE +%2,5'te armlanır ve sunucu stopunu entry×1,003'e çeker (position_manager.py:358-372). Sunucu-taraflı TRAILING bakımı ise ayrı bir blokta (position_manager.py:437-455) ve `trailing_sl_price = highest*(1-0.04)` yalnız `canonical_stop + 0.10`ı geçerse yazılır. Aritmetik: highest*0,96 > entry*1,003 → highest > entry*1,0448. Yani tepe +%2,5 ile +%4,48 arasındaki HER pozisyonun etkin stopu +%0,3'te ÇAKILI kalır (monoton klamp de onu aşağı indirmez, position_manager.py:1543). Aynı anda zarar tarafı `plan_exit_pcts` ile paper'da [%5, %6] (config.py:766, STOCK_CONFIG stop_loss_max_pct=0.06). Sonuç: kazanç %0,3 — %1,65 (kademeli satış da tetiklenirse), kayıp %5 — %6.
- Neden onemli: Başabaş kazanma oranı: kademeli satış tetiklenirse p×1,65 = (1−p)×5,5 → p = %76,9; tetiklenmezse p×0,3 = (1−p)×5,5 → p = %94,8. Ölçülen paper kazanma oranı 4/14 = %28,6. Yani strateji sinyal kalitesinden BAĞIMSIZ olarak zarar etmek zorunda. Defterdeki +$2,72 / +$4,91 / +$3,49 küçük kazançlar tam olarak bu tavandır; -$97,03 / -$127,98 ise tam olarak %5-6 stop mesafesidir. Asimetri tesadüf değil, konfigürasyonun zorunlu sonucu.
- Onerilen duzeltme: (a) BE offsetini gerçek bir kâr kilidine çıkar (ör. +%1,0-1,5) VEYA BE tetiğini TP'nin en az yarısına taşı; (b) BE ile trailing arasındaki boşluğu kapat: BE armlandığı andan itibaren stop `max(entry×(1+offset), highest×(1−trail))` olarak HER döngüde güncellensin (dead-zone yok); (c) trailing_stop_pct'i ATR'ye bağla (sabit %4, %5-6 stopla tutarsız); (d) düzeltmeden önce backtest/walk_forward ile p_breakeven < gözlenen kazanma oranı olduğunu doğrula.

### EXIT-PARTIAL-DEFTERE-YAZILMIYOR  (Cikislar/Defter)
**Kademeli (yarı) satışın gerçekleşen PnL'i deftere, performansa ve kayıp serisine HİÇ yazılmıyor**

- Yer: `core/position_manager.py:1178-1370 (tüm _handle_long_partial); karşılaştır core/executor.py:585-615`
- Ne oluyor: _handle_long_partial kendi MarketOrderRequest'ini gönderir (position_manager.py:1332-1339) ve dolumu doğrular; fakat `bot.trades_today.append(...)`, `bot.performance.record_trade(...)` ve `update_loss_streak(...)` çağrılarının HİÇBİRİ bu yolda yok. Repo genelinde bu üç çağrının tüm bulunduğu yerler: executor.py:385/585/600/611, short_executor.py:216/313/320/326, stock_bot.py:1820/1833/1853 — position_manager'da sıfır.
- Neden onemli: 1) Ölçüm çelişkisinin doğrudan kaynağı: tools/olcum_raporu.py broker fill'lerinden yeniden kurduğu için kademeli satışın +%3 kârını SAYAR, defter SAYMAZ → aynı 6 işlem için +$218,89 vs -$64,04. 4/4 PASS kapısı canlıya terfi ön koşulu olduğundan bu doğrudan yanlış-yeşil. 2) Kelly/PositionSizer, meta_labeler ve ajan öz-değerlendirme (agent_perf) kazançlı yarı çıkışları hiç görmüyor → öğrenme verisi sistematik olarak kayıp-yanlı. 3) Kârlı yarı satış `_consecutive_losses`'i SIFIRLAMIYOR (streak yalnız tam çıkışta güncelleniyor) → kayıp serisi kapısı gereksiz yere armlı kalıyor.
- Onerilen duzeltme: _finish_partial_attempt içinde terminal-doğrulanmış dolum için: realized = (fill_avg_price − broker_avg_entry) × attempt_filled hesapla; trades_today'e action="SELL_PARTIAL" satırı yaz, performance.record_trade ve update_loss_streak çağır, wash_sale ve PDT kaydını da uygula. Ölçüm aracıyla mutabakat için birim test ekle: defter toplamı ile broker rekonstrüksiyonu ±$1 içinde olmalı.

### OLCUM-SPY-PARKING-TRADE-SAYILIYOR  (Cikislar/Defter)
**Ölçüm aracı SPY index-parking al/sat döngülerini strateji işlemi sayıyor — 4/4 PASS kapısı yanlış yeşil**

- Yer: `tools/olcum_raporu.py:175-196 (broker_fills), 235-293 (reconstruct_closed_trades), 783-788 (metrik-1); core/index_parking.py:205-232; core/position_manager.py:286-290`
- Ne oluyor: broker_fills() hiçbir sembol/asset-class filtresi uygulamıyor; `grep -n "SPY|parking|exclude" tools/olcum_raporu.py` SIFIR eşleşme veriyor. index_parking her gün notional SPY BUY / close_position SELL gönderiyor (index_parking.py:205-232) ve bunlar CLOSED emir listesine giriyor. reconstruct_closed_trades bunları sembol bazlı net pozisyon döngüsü olarak kapalı işleme çeviriyor, metrik-1 `net_pnl = sum(trade.pnl for trade in period)` ile PASS/FAIL veriyor. Botun kendi kodu ise parking'i açıkça "trade DEĞİL" sayıp yönetim dışı bırakıyor (position_manager.py:286-290).
- Neden onemli: Ölçüm penceresinde SPY +%4,36 idi; parking rebalance'ları realize kâr üretir. Yani metrik-1'in gördüğü "net +$218,89", stratejinin değil beta parkının kârını içeriyor. Kapı 4/4 PASS olduğunda canlı alım kilidi (executor.py:145-158, live_entries_enabled) açılacak — kaybeden bir strateji, SPY'ın yükselişiyle yeşile boyanarak gerçek paraya terfi edecek. Aynı filtre yokluğu opsiyon ve short fill'lerini de karıştırıyor.
- Onerilen duzeltme: broker_fills()'e dışlama listesi ekle: STOCK_CONFIG["index_parking_symbol"] ve asset_class != us_equity olan fill'ler atılsın; ayrıca bear_brain ETF sembolleri ayrı raporlansın. Metrik-1 kapısı yalnız strateji girişlerinden doğan kapalı döngüleri saysın ve rapora "dışlanan parking döngüsü sayısı/PnL'i" satırı bassın ki filtrenin çalıştığı görünür olsun.

### STREAK-KILIT-KENDINI-BESLEYEN  (Cikislar/Defter)
**Kayıp serisi sayacı zamanla sönmüyor — canlı bot 2'de takılıp kendi kendini kilitlemiş (5+ hafta sıfır giriş)**

- Yer: `core/trade_gates.py:156-186; core/streak.py:20-37; stock_bot.py:2084-2085`
- Ne oluyor: _check_loss_streak: `loss_count >= loss_streak_halt (4)` olursa süreli fren kurulur ve süre dolunca sayaç SIFIRLANIR (trade_gates.py:164-174). Ama `warn (2) <= loss_count < halt (4)` dalında hiçbir zaman aşımı yok — sadece güven eşiği 70'e çıkar ve orada KALIR. Sayacı sıfırlayan tek şey `pnl_usd > 0` olan TAM bir çıkış (streak.py:34-37). Sayaç diske yazılıp restart'ta geri yükleniyor (stock_bot.py:1963, 2084).
- Neden onemli: Canlı state: consecutive_losses=2, 16 Temmuz'dan beri sabit, pozisyon yok. Kilit kapalı bir döngü: sayacı sıfırlamak için kârlı bir çıkış gerek → çıkış için giriş gerek → giriş için conf ≥ 70 gerek → funnel'a göre canlıda conf neredeyse hiç 70'e ulaşmıyor (LOSS_STREAK_WARN 08-04'te 144, 08-10'da 60, 08-12'de 85 blok). Halt (4) da asla tetiklenmiyor çünkü onun için 2 yeni ZARAR gerekiyor, o da giriş gerektiriyor. Bot yapısal olarak kendini kapattı; 5+ haftalık sıfır girişin doğrudan mekanizması budur. Aynı kusur sembol bazında daha kalıcı: `_symbol_consecutive_losses[sym] >= 3` olan hisse SONSUZA DEK devre dışı (trade_gates.py:113-119), zaman aşımı yok → evren zamanla eriyor.
- Onerilen duzeltme: warn dalına zaman sönümü ekle: son realize çıkıştan bu yana N iş günü (ör. 5) geçtiyse sayaç 0'a düşsün; ya da sayaç kapalı-işlem tabanlı yerine kayan-pencere (son 10 işlemin kaybı) tabanlı olsun. Sembol filtresine de aynı sönüm (ör. 30 gün) uygulanmalı. Ek olarak kârlı KADEMELİ satış da seriyi sıfırlamalı (bkz. EXIT-PARTIAL-DEFTERE-YAZILMIYOR).

### RISK-CANLI-NAKIT-CEKISMESI-98USD  (Config/Risk)
**Index parking + cash_reserve aritmetiği canlıda işlem bütçesini $98'e kilitliyor — $100-300 güven bantları ULAŞILAMAZ**

- Yer: `config.py:337-362, core/executor.py:183-184,222, core/index_parking.py:181-201, stock_bot.py:547-558`
- Ne oluyor: Index parking her döngüde stratejiden ÖNCE koşuyor (stock_bot.py:555-558) ve rezerv üstündeki TÜM nakdi SPY'ye park ediyor: `reserve = self.reserve_pct * equity; delta = cash - reserve; if delta > 0: self._buy(...)` (index_parking.py:185-199) — canlı rezerv %30 (config.py:360). Sonra executor kendi rezervini bir kez DAHA düşüyor: `cash_reserve = equity * config.get("cash_reserve_pct", 0.15); available_cash = max(cash - cash_reserve, 0)` (executor.py:183-184, canlı cash_reserve_pct=0.10) ve `max_invest = min(max_invest, available_cash)` (executor.py:222). Yani canlıda kullanılabilir nakit yapısal olarak (0.30 - 0.10) × equity = equity'nin %20'si. Parking'in ters yönü (`_sell`) YALNIZCA nakit rezervin altına düştüğünde tetikleniyor (delta<0); kodda "strateji giriş yapacak, SPY'den nakit çöz" diye bir yol YOK — executor.py'de index_parking'e tek bir referans bile yok.
- Neden onemli: Canlı equity $491.72 → maksimum işlem bütçesi 0.20 × 491.72 = $98.34. config.py:337-342'deki `live_conf_position_bands` [[50,100],[60,150],[70,200],[80,300]] bantlarının EN DÜŞÜĞÜ ($100) bile bu tavanın üstünde; yani "güvene göre boyut" mekanizması canlıda hiçbir zaman devreye giremez, her giriş $98'e kırpılır. Dahası tek bir giriş nakdi $145.82 → $47.48'e indirir, kalan available_cash = 47.48 - 49.17 = 0 → İKİNCİ pozisyon imkânsız. Yani `max_open_positions: 3` (config.py:346) canlıda fiilen 1. Bot 5+ haftadır giriş yapamamasının sermaye tarafındaki nedeni bu: strateji ile park aynı nakit havuzu için yarışıyor ve park her sabah kazanıyor.
- Onerilen duzeltme: (a) İki rezervi tek yerde birleştir: executor canlıda ikinci kez rezerv düşmesin, ya da index_parking_reserve_pct canlıda cash_reserve_pct + hedeflenen işlem sermayesini (örn. 3 × $300 = $900 > equity, yani bu hesapta parking canlıda KAPATILMALI) kapsayacak şekilde hesaplansın. (b) `index_parking_allow_live` (config.py:362) $25k altı hesapta False'a çekilsin — $492'lik hesapta park, stratejinin tüm sermayesini yutuyor. (c) Alternatif: executor'a giriş öncesi "unpark" çağrısı eklensin (parking._sell(gereken_tutar)), ama bu SPY'de aynı-gün AL-SAT/PDT riski yaratır; (b) daha güvenli.

### RISK-FRACTIONAL-IKINCI-SESSIZ-KILIT  (Config/Risk)
**Kesirli adet + bracket emri: canlı kilit açılsa bile bot emir geçiremez (ikinci, sessiz kilit) — KODDA HÂLÂ VAR**

- Yer: `core/executor.py:228-234, 259-268, 279-289`
- Ne oluyor: Sizer dolar bazlı çalışıyor ve adedi `qty = round(position_usd / price, 4)` (position_sizer.py:123) ile kesirli üretiyor. Executor tam paya YALNIZCA şu koşulda yuvarlıyor: `whole_qty = int(max_invest / price); if whole_qty >= 1 and whole_qty * price >= 0.75 * max_invest: qty = float(whole_qty)` (executor.py:232-234). Sonra emir `order_class="bracket"` ile gönderiliyor (executor.py:262-274). Alpaca kesirli adette bracket kabul etmez ("fractional orders must be simple orders", hata 42210000). Canlıda bracket reddi fallback'siz: `if is_live: logger.error(...); return False` (executor.py:283-288) — pozisyon AÇILMAZ.
- Neden onemli: Bulgu-1'deki $98.34 bütçeyle `whole_qty >= 1` şartı ancak fiyat <= $98.34 olan hisselerde sağlanır; ek olarak `whole_qty * price >= 0.75 * max_invest` şartı fiyatı ~$73-98 bandına daraltır. Havuzun ana isimleri (AAPL, MSFT, GOOGL, AMZN, NVDA, META, TSLA, AMD, PLTR, COIN, SHOP, CRWD) bu bandın çok üstünde → whole_qty = 0 → kesirli qty → bracket reddi → sessiz `return False`. RF-ISSUES.md I-13 bunu 2026-08-10'da gerçek hesapta kanıtlamış (AMZN 0.3491 adet, $96.30, 42210000 reddi) ve "kilit tesadüfi bir yan etkiye yaslı" demiş; I-13 "KAPALI" işaretli ama kapatılan şey yalnızca `live_entries_enabled` bayrağı — kesirli/bracket sorunu koda dokunulmadan duruyor. Sonuç: İhsan R5 kilidini açtığında bot 'çalışıyor' görünecek, log'a ERROR basacak ama ana isimlerde HİÇ pozisyon açamayacak; yalnızca ucuz/volatil isimler (RIVN, NIO, LCID, MARA, RIOT, SOFI, SMCI) geçecek — yani canlı portföy istemeden en riskli uçlara kayacak.
- Onerilen duzeltme: Canlıda kesirli adet üretmeyi tamamen bırak: `qty = max(1, int(max_invest / price))` yap ve `qty * price > available_cash` ise sembolü ATLA (log'la, huniye `PRICE_TOO_HIGH` sebebi yaz). Aksi hâlde bracket'i bırakıp iki adımlı (market BUY + ayrı stop) yola düşmek gerekir ki bu da gece koruma emrini kaybettirir. Ek olarak bracket reddi funnel'a ayrı sebep koduyla (`BRACKET_REJECT`) yazılsın — şu an bu ret hiçbir sayaçta görünmüyor, tam da sessiz başarısızlık tanımı.

### GATE-LOSS-STREAK-CIKISSIZ-KILIT  (Config/Risk)
**Canlı ardışık-zarar sayacı 2'de kalıcı takılı: her giriş conf>=70 istiyor, sayacı sıfırlayacak kârlı işlem ise giriş olmadan imkânsız (deadlock)**

- Yer: `config.py:404-408, core/trade_gates.py:156-185, core/streak.py:26-37, stock_bot.py:1963-1964,2084-2085`
- Ne oluyor: config.py:405 `loss_streak_warn: 2`, config.py:408 `loss_streak_elevated_conf: 70`. trade_gates.py:176-183: `elif loss_count >= config.get("loss_streak_warn", 2): elevated_conf = config.get("loss_streak_elevated_conf", 70); if analysis["confidence"] < elevated_conf: return True, "LOSS_STREAK_WARN"`. Sayaç YALNIZCA gerçekleşmiş pozitif PnL ile sıfırlanır (core/streak.py:34-37 `elif pnl_usd > 0: bot._consecutive_losses = 0`) veya halt eşiğine (4) ulaşıp süre dolarsa (trade_gates.py:172-174). Sayaç bot_positions.json'a kalıcı yazılıyor (stock_bot.py:1963) ve restart'ta geri yükleniyor (stock_bot.py:2084), yani yeniden başlatma da çözmüyor.
- Neden onemli: Canlı state'te consecutive_losses=2, 16 Temmuz'dan beri sabit. 2 < 4 olduğu için halt zaman aşımı yolu HİÇ tetiklenmiyor; sıfırlamanın tek yolu kârlı bir kapanış, kârlı kapanışın tek yolu ise bir giriş — ve giriş conf>=70 (koordinatör ölçeğinde |ws|>=35, agent_coordinator.py:473 `confidence = abs(weighted_score) * 2.0`) istiyor. Huni verisi bu kilidin ısırdığını kanıtlıyor: LOSS_STREAK_WARN 08-04'te 144, 08-12'de 85, 08-10'da 60 blok. Yani canlı bot yalnızca R5 bayrağıyla değil, kendi risk kapısıyla da KENDİNİ kilitlemiş durumda ve bu kilidin kod içinde bir kaçış yolu yok. Paper'da bu sorun görünmüyor çünkü PAPER_AGGRESSIVE `loss_streak_warn: 999` ile kapıyı kapatmış (config.py:824) — yani hatanın canlıya özgü olması ölçüm penceresinde gizlenmiş.
- Onerilen duzeltme: Sayaca zaman aşımı ekle: warn seviyesindeki seri N iş günü (örn. 5) yeni kapanış olmadan geçerse otomatik sıfırlansın (`_loss_streak_since` zaman damgası + `_daily_reset` içinde budama). Ayrıca warn kolunda giriş tamamen bloklanmak yerine POZİSYON BOYUTU küçültülsün (sizer'da damping zaten var) — böylece sayaç kendi kendini besleyen bir kilide dönüşemez.

### GATE-LIVE-FRACTIONAL-BRACKET-OLU-KAPI  (Kapilar/Huni)
**Canlıda kesirli bracket reddi = sessiz giriş yasağı; R5 kilidi açılsa bile mega-cap alınamaz**

- Yer: `core/executor.py:255-290 + core/executor.py:180-235 + config.py:355-362`
- Ne oluyor: execute_buy pozisyon boyutunu hesaplayıp `whole_qty = int(max_invest / price)` ile tam paya yuvarlamayı deniyor; `whole_qty >= 1 and whole_qty * price >= 0.75 * max_invest` sağlanmazsa qty KESİRLİ kalıyor. Kesirli emirde Alpaca bracket (order_class='bracket') kabul etmiyor ve kod canlıda fallback'i bilerek kapatmış: `if is_live: logger.error(...'LIVE bracket reddedildi; pozisyonu AÇILMADI'...); return False`. Canlı hesapta equity $491.72'nin $345.90'ı index parking'de (config.py:362 `index_parking_allow_live: True`), nakit $145.82; executor `cash_reserve_pct=0.10` düşünce kullanılabilir nakit ~$96.65. Fiyatı $96'nın üstündeki her hisse için whole_qty=0 → kesirli → canlı bracket reddi → return False. Fiyatı $50-72 arasındaki isimler de `0.75 * max_invest` şartına takılıp kesirli kalıyor.
- Neden onemli: Bu, LIVE_ENTRIES_ENABLED=true yapıldıktan SONRA da duracak ikinci ve görünmez bir kilit. Havuzdaki 20 ismin AAPL(309), NVDA(215), AMD(473), MSFT(483), META(550), GOOGL(345), AMZN(259), SHOP(149), COIN(186), CRWD(192), TSLA(363) — yani 11'i kalıcı olarak alınamaz durumda. Alınabilen tek grup $50 altı düşük fiyatlı isimler (RIVN, SOFI, NIO, LCID, MARA, SMCI); nitekim canlı hesabın son gerçek strateji işlemi 16 Temmuz RIVN (~$14). Yani canlı strateji, 'ucuz hisse' filtresine dönüşmüş durumda ve bu hiçbir yerde raporlanmıyor — huni sayacı bile yok, sadece bir logger.error satırı var.
- Onerilen duzeltme: İki yoldan biri: (a) canlıda kesirli emir tespit edilince bracket denemeden ÖNCE 'iki adımlı emir + sunucu tarafı stop' yoluna geç (paper'da zaten var, position_manager._update_server_stop_loss ile stop doğrulanıyor) veya (b) kesirli çıkarsa girişi tamamen iptal et AMA bunu `_funnel_bump("gate_block", reason="FRACTIONAL_NO_BRACKET")` ile sayaca yaz. Ayrıca canlıda index parking rezervini (%30) düşürüp giriş sermayesi bırak, yoksa boyut bantları ($100-300) fiilen erişilemez.

### GATE-LOSS-STREAK-WARN-KILITLI-KISIR-DONGU  (Kapilar/Huni)
**LOSS_STREAK_WARN kilitlenmiş kısır döngü: sayaç yalnız kârla sıfırlanıyor, kâr için de giriş gerekiyor**

- Yer: `core/trade_gates.py:156-185 + core/streak.py:20-38 + config.py:404-409`
- Ne oluyor: `_check_loss_streak` iki kollu: HALT kolu (loss_count >= loss_streak_halt = 4) ve WARN kolu (loss_count >= loss_streak_warn = 2). Zaman aşımıyla otomatik sıfırlama SADECE HALT kolunda var (trade_gates.py:172-174 `bot._consecutive_losses = 0`). WARN kolunda hiçbir zaman aşımı yok; sadece `analysis['confidence'] < 70` ise blok. Sayacın tek diğer sıfırlama yolu `core/streak.py:34-37`: `elif pnl_usd > 0: bot._consecutive_losses = 0` — yani KÂRLI BİR ÇIKIŞ. Canlıda sayaç 2'de takılı (16 Temmuz'dan beri, stock_bot.py:1963 ile diske yazılıp 2084'te geri yükleniyor, restart'ta da hayatta kalıyor). Kâr için pozisyon, pozisyon için conf>=70 gerekiyor; normal eşik 50, yani WARN kolu eşiği %40 yükseltiyor. 4'e (HALT) ilerlemesi de imkânsız çünkü ilerlemek için ZARAR gerekiyor, zarar için de giriş gerekiyor.
- Neden onemli: Canlı hesabın 5+ haftadır hiç giriş yapmamasının kalıcı sebeplerinden biri bu. Şiddet sıralaması TERS: daha ağır sayılan HALT durumu 24 saatte kendiliğinden açılıyor, daha hafif sayılan WARN durumu sonsuza kadar sürüyor. Funnel'da LOSS_STREAK_WARN 08-04'te 144, 08-12'de 85 kez sayılmış — bunlar EMA200 ve earnings kapılarını GEÇMİŞ, yani gerçekten alınabilir sinyallerdi.
- Onerilen duzeltme: WARN koluna da zaman aşımı ekle (ör. `loss_streak_warn_hours`, 24-48 saat sonra sayaç 1 azalır) veya sayacı işlem-sayısı yerine takvim-günü bazlı çürüt. Alternatif: eşiği mutlak 70 yerine `min_confidence_score + 10` gibi göreli yap, böylece canlı eşik 50 iken WARN 60 olur ve ulaşılabilir kalır. Hangi düzeltme seçilirse seçilsin, sayacın kaç gündür değişmediğini watchdog'a bildiren bir alarm şart.

### GATE-CONF50-KOORDINATOR-OLCEGININ-USTUNDE  (Kapilar/Huni)
**Canlı güven eşiği 50, koordinatörün pratik üretim bandının tepesinde — sinyallerin %72'sini yiyor**

- Yer: `stock_bot.py:957-985 + core/agent_coordinator.py:419-485 + config.py:383`
- Ne oluyor: Canlı config `min_confidence_score: 50`. Koordinatör güveni `confidence = abs(weighted_score) * 2.0` (çoğunlukta ×1.2). weighted_score = Σ(yön × ağırlık × ajan_güveni). BUY yönünde gerçekte kaç ajan katkı verebiliyor: RiskAgent TASARIM GEREĞİ asla BUY oyu vermiyor (agent_coordinator.py:337-345: risk_score ne olursa olsun sinyal ya SELL ya HOLD) → 0.20 ağırlık BUY'a hiç katkı vermiyor. SocialAgent kalıcı ölü: Reddit'in kimliksiz JSON ucu 403 döndürüyor (test ettim) ve X/Nitter da devre dışı → social_score hep 0 → HOLD, güven 0 → 0.15 ağırlık da ölü. Geriye BUY yönünde yalnız Tech (0.25) + Fund (0.20) + Sent (0.20) = 0.65 ağırlık kalıyor. conf>=50 için |ws|>=25 (veya 3'lü çoğunlukla >=20.8) gerekiyor; bu, Tech'in tek başına ~100 güvenle oy vermesi ya da üç ajanın aynı anda yüksek güvenle BUY demesi demek.
- Neden onemli: Ölçülen huni bunu doğruluyor: 08-21'de 232 BUY sinyalinin 168'i (%72) conf_below_min ile düşmüş. Yani zincirin İLK kapısı en büyük katili — LIVE_LOCK_R5'ten (17 günde 7) çok önce akışı bitiren yer burası. Config yorumları (config.py:380-383) eşiğin v4.9'da ×2.0 remap ile 'anlamlı hale geldiğini' iddia ediyor ama ölçüm bunun sadece kısmen doğru olduğunu gösteriyor: eşik hâlâ dağılımın kuyruğunda. Ayrıca ölü SocialAgent'ın ağırlığı normalize edilirken (agent_performance.py:216-222) diğerlerine dağılmıyor — get_dynamic_weights ölü ajana DEFAULT_WEIGHTS'i veriyor çünkü `len(recent) < MIN_TRADES_FOR_EVAL` → ağırlık havuzunun %15'i kalıcı çöp.
- Onerilen duzeltme: Önce ölçüm yap: 30 günlük coordinator `weighted_score` dağılımını logla ve eşiği yüzdelik dilime (ör. üst %20) göre belirle — sabit 50 tahmin. Sonra ölü ajanı sistemden çıkar veya ağırlığını dinamik olarak sıfırlayıp kalan ajanlara yeniden dağıt (aksi halde |ws| tavanı yapay olarak %15 kısılı kalıyor). RiskAgent'ın asla BUY vermemesi doğru bir tasarım ama o zaman ağırlığı BUY tarafında normalize edilmemeli.

### GATE-EMA200-SPLIT-DUZELTMESIZ-VERI  (Kapilar/Huni)
**Bar verisi split-düzeltmesiz çekiliyor: EMA200 kapısı split yapan isimlerde kalıcı sahte blok, RSI'da sahte 'aşırı satım'**

- Yer: `stock_bot.py:1343-1350 + core/stock_screener.py:156-160 + backtest.py:329-335`
- Ne oluyor: Hiçbir `StockBarsRequest` çağrısında `adjustment=` parametresi verilmemiş; alpaca-py varsayılanı `raw`, yani split-düzeltmesiz ham fiyat. Son 400 günde split yapan bir hissede seri, split gününde yapay bir uçurumla kırılıyor: EMA200 split ÖNCESİ yüksek fiyatları taşımaya devam ederken güncel fiyat split SONRASI seviyede → `current_price > ema200` kalıcı olarak False → EMA200 kapısı o hisseyi süresiz blokluyor. Aynı ham seri RSI/MACD/BB/ATR'yi de besliyor, yani split anındaki yapay çöküş 'aşırı satım' olarak okunuyor.
- Neden onemli: Aynı hata hem SAHTE BUY sinyali üretiyor hem de o sinyali kapıda kesiyor — yani huninin 'sinyal var ama EMA200 kesiyor' desenini birebir üretiyor. CRWD'de ölçtüm: ham veriyle EMA200=423.54, fiyat 191.95 → BELOW (kalıcı blok) ve RSI=31 (tarayıcıda 'RSI aşırı satım' +25 puan, TechAgent'ta +18 puan). Split-düzeltilmiş veriyle EMA200=151.41 → ABOVE ve RSI=43 (nötr). Yani CRWD hem yanlışlıkla top-10'a itiliyor hem de asla alınamıyor. Daha kötüsü: backtest.py de aynı ham veriyi kullanıyor, dolayısıyla stratejinin tüm kalibrasyonu (eşikler, stop mesafeleri, R:R) bozuk seriler üzerinde yapılmış.
- Onerilen duzeltme: Üç çağrı yerine de `adjustment=Adjustment.SPLIT` (veya `ALL`) ekle: stock_bot.get_stock_bars, stock_screener._analyze_stock, backtest. Sonra `_daily_ema200_cache`'i temizle ve backtest'i yeniden koş — mevcut backtest_results.json ve walk_forward_results.json bozuk veriyle üretildiği için çöpe atılmalı.

### OLCUM-DEFTER-KADEMELI-SATISI-YAZMIYOR  (Olcum Sistemi)
**$283 farkın kaynağı: defter kademeli satış bacaklarını HİÇ yazmıyor, sadece son kapanış bacağını yazıyor**

- Yer: `core/position_manager.py:1049-1110 ve 1178-1300 (kayıt yok); core/executor.py:552 ve 605-612; stock_bot.py:1802-1804, 1819-1824, 1853-1858`
- Ne oluyor: Kademeli satış state machine'i (`_handle_long_partial` → `_finish_partial_attempt`) emri gönderir, dolumu doğrular, `partial_sold=True` yazar ve `PARTIAL_STATE` telemetrisi basar — ama defter/istatistik yazan HİÇBİR çağrı yapmaz. Dosyada `trades_today`, `record_trade`, `record_outcome`, `update_loss_streak`, `wash_sale` geçmiyor (grep: 0 sonuç). Gerçekleşen PnL yalnız iki yerde yazılır ve ikisi de KALAN adede bakar: core/executor.py:552 `pnl_usd = (fill_price - float(entry)) * filled_qty` (filled_qty = kalan adet, satır 550 `qty = filled_qty`) ve stock_bot.py:1802 `pnl_usd = (fill_price - entry) * qty`. Böylece bir pozisyon döngüsünün defterdeki tek satırı = SON bacağın kârı; +%3'te bankalanan yarı hiç görünmez. Buna karşılık tools/olcum_raporu.py:235-291 `reconstruct_closed_trades` tüm bacakları toplar. Farkın tamamı budur — backfill dosyası PnL enjekte ETMİYOR (tools/olcum_backfill.json yalnız STOP_REGRESSION/UNIQUE_COLLISION/KORUMA olayları taşıyor), işaret hatası veya çift sayım yok.
- Neden onemli: Defter, botun kendi karar organlarını besliyor: `update_loss_streak` (core/streak.py:20-36) YALNIZ kaydedilen PnL işaretine bakar ve LOSS_STREAK_WARN/HALT kapısını (core/trade_gates.py:162-183) armlar — bu kapı LIVE hunisinde en sık blok sebebi (08-04: 144, 08-12: 85). Ölçülen PLTR 08-14 döngüsünde gerçek kâr +$62,75 iken defter yalnız son bacağı (+$4,91 = 11 × $0,4645) gördü; çıkış fiyatı $0,47 daha düşük olsaydı gerçekte +$57 kazandıran işlem deftere ZARAR yazılacak, ardışık zarar sayacı artacak ve giriş hunisi kilitlenecekti. Aynı bozukluk agent_performance (ajan ağırlıkları), wash_sale_tracker ve PerformanceTracker.get_stats (win-rate/profit-factor/Kelly girdisi) için de geçerli. Ölçülen dönemde kayıt dışı gerçekleşen kâr: 185,99 (SMCI) + 57,84 (PLTR) + 39,24 (AAPL) + 37,72 (PLTR 08-21, hâlâ açık pozisyonun yarı satışı: 7 × ($179,3671−$173,9779)) ≈ $320,79.
- Onerilen duzeltme: `_finish_partial_attempt` içinde `intent["status"] == "FILLED"` (veya kısmi terminal dolum) olduğunda, dolan adet ve gerçek fill fiyatıyla tam kapanışla aynı muhasebeyi çalıştır: `bot.trades_today.append({action:"SELL", ..., pnl:(fill-entry)*dolan_adet, reason:"PARTIAL_3PCT", exit_order_id:...})` + `bot.performance.record_trade(...)` + `bot.agent_perf.record_outcome(...)` + `update_loss_streak(bot, symbol, pnl)`. Short tarafında da aynısı (core/position_manager.py:614-651 `partial_covered` yolu hiç kayıt yazmıyor, üstelik dolum doğrulaması da yok). Bu düzeltilmeden defterle broker asla uzlaşmaz.

### METRIK4-EKSIK-KAYDI-GOREMEZ  (Olcum Sistemi)
**Metrik-4 "kayıt bütünlüğü" tek yönlü: fazla kaydı sayar, EKSİK kaydı yapısal olarak göremez**

- Yer: `tools/olcum_raporu.py:499-538 (phantom_count), 836-837 (metric4_ok)`
- Ne oluyor: `phantom_count` broker çıkış bacaklarından bir liste kurar (satır 523-526) ve YEREL defter satırlarını bu listeye eşler (satır 527-537). Eşleşmeyen YEREL satır `unmatched++` olur. Ama eşleşmeden kalan BROKER bacakları (`item[2] is False`) hiçbir yerde sayılmaz. Dönüş `max(duplicates, unmatched)` — yani defter broker'ın çıkışlarının yarısını hiç yazmasa bile phantom=0 çıkar. metric4_ok = `phantom == 0 and stop_rejections == 0` (satır 837). Eşleme ayrıca yalnız (sembol, yuvarlanmış adet) ile yapılıyor: zaman, fiyat, yön ve order_id karşılaştırması yok.
- Neden onemli: Metrik-4 kapının tek 'defter broker'la uyuşuyor mu' sorusu ve OLCUM-DEFTER bulgusunu yakalaması gereken tek yer. Ölçüm penceresinde broker'da 12 gerçek çıkış bacağı var (PLTR 1 + SMCI 5 + AMZN 1 + PLTR 2 + SMCI 1 + AAPL 2), defterde 6 satır var — çıkış bacaklarının %50'si kayıp. Metrik-4 yine de PASS veriyor. Yani canlıya kalibrasyon geçirmenin ön koşulu olan 'kayıt bütünlüğü' kapısı, sistemin gerçekten sahip olduğu kayıt bozukluğuna karşı KÖR.
- Onerilen duzeltme: Tek satırlık düzeltme: `return`'den önce `missing = sum(1 for item in broker_exits if not item[2])` ekleyip `return max(duplicates, unmatched, missing), duplicates, unmatched` yap (ve `missing`'i rapor detayına bas). Bu tek değişiklik mevcut dönemde metrik-4'ü doğru şekilde FAIL'e çevirir ve OLCUM-DEFTER bulgusunu otomatik yakalar.

### GENEL-PASS-N20-30GUN-KOSULUNU-UYGULAMIYOR  (Olcum Sistemi)
**GENEL PASS kendi n>=20 / 30-işlem-günü ön koşulunu hiç uygulamıyor — n=6, gün=8'de exit 0 dönüyor**

- Yer: `tools/olcum_raporu.py:863-869 ve 951; PLAN.md:483-487`
- Ne oluyor: PLAN.md:483 kapıyı şöyle tanımlıyor: "20 kapalı paper işlemi VEYA 30 işlem günü → (1) net PnL>0, (2) ... (4) ...". Kodda ise `overall = (metric1_ok and partial.passed and metric3_ok and metric4_ok)` — `len(period)` ve `elapsed_days` bu ifadeye HİÇ girmiyor. Bu iki değer sadece başlık satırında (satır 775-777) ve tempo uyarısında (satır 778-781) yazdırılıyor; hiçbir karar üretmiyorlar. `main()` satır 951'de `return 0 if passed else 1` yapıyor, yani exit kodunu okuyan her otomasyon 6 işlemde yeşil görüyor.
- Neden onemli: Bu, çelişkinin arkasındaki asıl yanlış-yeşil. Kapı, `LIVE_ENTRIES_ENABLED=true` (canlı alım kilidi, PLAN.md:679) açılışının ön koşulu. Rapor aynı çıktıda hem "gun=8/30, n=6/20", hem "TEMPO UYARISI: mevcut tempoyla hedef 20'nin altinda" hem de "GENEL: PASS" basıyor — birbiriyle çelişen üç satır, ve kararı veren en zayıfı. Örneklem yeterliliği ile metrik sonucu birbirinden ayrılmış durumda; 6 işlemin 1'i (SMCI +189,48) net PnL'in %87'si, o tek işlem çıkarılınca net +29,41'e, ilk iki kazanan çıkarılınca −$120'ye düşüyor.
- Onerilen duzeltme: Tek satır: `overall = (len(period) >= 20 or elapsed_days >= 30) and metric1_ok and partial.passed and metric3_ok and metric4_ok`. Örneklem yetmiyorsa GENEL 'PASS' değil 'YETERSIZ ORNEKLEM' basmalı ve exit kodu 1 olmalı — 'henüz karar verilemez' ile 'geçti' aynı sinyali vermemeli.

### STREAK-KILIDI-CIKISI-YOK  (Saglamlik/Altyapi)
**consecutive_losses=2 kalıcı diskte, zaman aşımı yok — canlı kendi kendini açamayan bir kilit içinde**

- Yer: `core/streak.py:26-38 + core/trade_gates.py:181-189 + stock_bot.py:1963,2084 + config.py:405-408`
- Ne oluyor: Sayaç YALNIZ gerçekleşen PnL işaretiyle güncelleniyor: `if pnl_usd < 0: bot._consecutive_losses += 1` / `elif pnl_usd > 0: bot._consecutive_losses = 0` (streak.py:26-36). Zaman aşımı, gün devri veya kısmi sönüm YOK. Değer her döngüde diske yazılıyor (stock_bot.py:1963 `"consecutive_losses": self._consecutive_losses`) ve açılışta geri okunuyor (2084). Kapı: `elif loss_count >= config.get("loss_streak_warn", 2): elevated_conf = config.get("loss_streak_elevated_conf", 70); if analysis["confidence"] < elevated_conf: return True, "LOSS_STREAK_WARN"` (trade_gates.py:184-189). Canlı config: loss_streak_warn=2, loss_streak_elevated_conf=70 (config.py:405,408). Sayacı sıfırlayan tek diğer yol trade_gates.py:171-175 — o da ancak seri 4'e (loss_streak_halt) ulaşırsa çalışır; 2'de takılı kalan seri oraya asla varamaz.
- Neden onemli: Canlı hesap 16 Temmuz'dan beri consecutive_losses=2 ile takılı. Bu kapı, güven<70 olan HER girişi reddediyor. Aşağıdaki AJAN-COGUNLUK bulgusuyla birleşince canlıda 70 güven ulaşılamaz → kapı kalıcı. Kilidi açmanın kod içindeki TEK yolu kârlı bir kapanış, kârlı kapanış için giriş, giriş için kilidin açık olması gerekiyor. Huni verisi bunu doğruluyor: gate_block_reasons'ta LOSS_STREAK_WARN 08-04'te 144, 08-12'de 85, 08-10'da 60. 5 haftalık sıfır girişin mekanik sebebi bu; strateji seçiciliği değil.
- Onerilen duzeltme: (a) Seriye zaman aşımı ver: son zararın üzerinden N işlem günü geçtiyse seriyi sıfırla (persist edilen `last_loss_date` ile). (b) `loss_streak_elevated_conf`'u koordinatörün GERÇEK güven dağılımına göre kalibre et — 70 ölçülen tavanın üstünde, yani kapı değil duvar. (c) Botun bu kapının kaç gündür armlı olduğunu ayrı bir kritik alarm olarak yayması (LOSS_STREAK_STUCK), NO_TRADE'in içinde kaybolmaması.

### AJAN-COGUNLUK-YAPISAL-ULASILMAZ  (Saglamlik/Altyapi)
**3/5 çoğunluk kuralı üretimde imkânsız: 3 ajan sessizce nötrleşiyor, RiskAgent tasarım gereği hiç BUY oyu vermiyor**

- Yer: `stock_bot.py:1198-1232 + core/agent_coordinator.py:336-343,436-476`
- Ne oluyor: `_get_agent_decision` üç sağlayıcıyı da çıplak `except Exception: pass` ile sarıyor: FundAgent için 1203-1204, SentAgent için 1225-1226, SocialAgent için 1231-1232. Hata olduğunda değişkenler başlangıç değerlerinde kalıyor (`fund_data = {"fundamental_score": 0, "metrics": {}}`, `sent_data = {"news_score": 0}`, `social_data = {"social_score": 0}`). Bu skorlarla üç ajan da HOLD döner ve güvenleri 0 olur (agent_coordinator.py FundAgent `confidence = min(abs(score)*2,100)`, SocialAgent aynısı). RiskAgent ise v4.8'den beri hiçbir koşulda BUY dönmüyor: `if risk_score <= -30: signal="SELL" elif risk_score <= -15: signal="HOLD" else: signal="HOLD"` (agent_coordinator.py:336-341). Yani BUY oyu verebilecek ajan sayısı 4, bunlardan 2'si ölü → buy_count en fazla 2 → `if buy_count >= 3` (436) hiç sağlanmaz → `majority=False` → `confidence *= 1.2` (475) hiç uygulanmaz.
- Neden onemli: Nihai güven `confidence = abs(weighted_score) * 2.0` (473). Fund/Social ölüyken |ws| tavanı = TechAgent(0.25×100) + SentAgent(0.20×100) = 45 değil, pratikte TechAgent 0.25 ağırlıklı tek başına ≈25 → güven ≈50. Canlı eşiği `min_confidence_score: 50` (config.py:383). Yani sinyaller tam eşiğin sınırında ölüyor: huni verisi bunu birebir gösteriyor (08-21 canlı: 232 sinyal, 168'i conf_below_min). Ve LOSS_STREAK kapısının istediği 70'e HİÇBİR koşulda ulaşılamıyor. Sistem 5 ajanlı gibi görünüyor, gerçekte 2 ajanlı ve ikisinden birinin ağırlığı 0.25.
- Onerilen duzeltme: (a) Nötr ile ARIZALI'yı ayır: her ajan verisi için `*_data_ok` bayrağı taşı (FundAgent için zaten var — stock_bot.py:1210-1213 `fundamental_data_ok`; aynısını sent/social için de üret). (b) Ölü ajanı oylamadan ÇIKAR ve ağırlıkları kalanlara yeniden normalize et; yoksa ×2.0 remap'i yanlış tabanla ölçekleniyor. (c) Çoğunluk eşiğini canlı ajan sayısına göre hesapla (`>= ceil(alive/2)+... `), 5 sabit sayısına değil. (d) 2+ ajan üst üste N tur ölü ise kritik alarm bas — şu anda hiçbir alarm yok.

### FUND-AV-KOTA-NEGATIF-CACHE-YOK-15SN-BLOK  (Saglamlik/Altyapi)
**Alpha Vantage kotası dolunca sonuç önbelleğe alınmıyor: her taramada sembol başına 15 saniye ana döngü blokajı + kalıcı FUND_NO_DATA**

- Yer: `core/fundamental_analyzer.py:47-97 (özellikle 64-71)`
- Ne oluyor: `get_company_overview` cache-miss'te HTTP çağrısı yapıyor ve HEMEN ARDINDAN `time.sleep(15)  # Rate limit (5 req/min)` (satır 65) çalıştırıyor. Bot TEK THREAD'li: bu uyku ana trading döngüsünü bloklar. Kota dolduğunda Alpha Vantage `{"Information": ...}` döner, kod `if "Symbol" not in data: return None` (69-70) ile çıkar — VE BU SONUCU ÖNBELLEĞE ALMAZ (`self.cache[cache_key] = overview` yalnız başarı yolunda, satır 89). Sonuç: başarısızlık kalıcı, 12 saatlik cache hiç devreye girmez, her taramada her sembol için tekrar 15 sn uyunur. `analyze_fundamentals` da `if not overview: return {... "metrics": {}}` (150-156) ile boş metrics döner.
- Neden onemli: İki ayrı hasar. (1) Karar hasarı: `metrics={}` → stock_bot.py:1210-1213 `fundamental_data_ok=False` → paper'da `fundamental_gate_enabled: True` (config.py:814) olduğu için FUND_NO_DATA kapısı (trade_gates.py:87-90) girişi bloklar — huni 08-17'de 26, 08-18'de 12 kez bunu gösteriyor. Ayrıca FundAgent ölür (üstteki bulgu). (2) Zaman hasarı: evren 10 sembol (`stock_bot.py:1418` `[:10]`), 10×15sn = her tam tarama turunda 150 SANİYE ölü blokaj. `scan_interval_seconds: 30` (config.py:439) bir kurgu; gerçek tur süresi dakikalarca. Pozisyon çıkış kontrolü (`_manage_positions`, stock_bot.py:645) tur başına BİR kez koştuğu için trailing stop / take-profit takibi bu süre boyunca hiç çalışmaz.
- Onerilen duzeltme: (a) `time.sleep(15)` → 0-2 sn (news_analyzer ile aynı doktrin), ya da tamamen kaldır. (b) NEGATİF ÖNBELLEK ekle: `return None` yolundan önce `self.cache[cache_key] = None; self.last_fetch[cache_key] = now` — kota dolduğunda 12 saat tekrar denenmesin. (c) Kota tükendiğini ayrı bir durum olarak tut ve günde 1 kez INFO/alarm bas; şu an kotanın dolduğu hiçbir yerde loglanmıyor. (d) Uzun süren sağlayıcı çağrılarını ana döngüden çıkar (arka plan yenileme + son iyi değer).

### BARS-BOS-DF-EMA200-GUN-BOYU-ZEHIRLENME  (Saglamlik/Altyapi)
**Bar verisi hatası sessizce boş DataFrame dönüyor; EMA200 sonucu None olarak GÜN BOYU önbelleğe alınıp kapı bozuk değere düşüyor**

- Yer: `stock_bot.py:1343-1362 + stock_bot.py:1368-1390 + stock_bot.py:1165-1167 + core/trade_gates.py:78-82`
- Ne oluyor: `get_stock_bars` HERHANGİ bir hatada (rate-limit, ağ, abonelik) `logger.debug(...)` + `return pd.DataFrame()` yapıyor (1360-1362). Bu boş df üç yere akıyor. (1) `_daily_above_ema200`: `if not df.empty and len(df) >= 100:` sağlanmazsa `result` None kalır — ve kritik satır 1389: `self._daily_ema200_cache[symbol] = (today, result)` — NEGATİF SONUÇ DA ET-GÜN BOYUNCA ÖNBELLEĞE ALINIYOR. (2) `_get_technical_analysis`:1165-1167 `if daily_above is not None: result["above_ema200"] = daily_above` → None ise GÜNLÜK EMA200 hiç yazılmaz, analyzer'ın SAATLİK EMA200 değeri (v4.8'in "yanlış" dediği ~8 işlem günlük filtre) kapıya girer. (3) `_get_technical_analysis` df boşsa `return None` (1137-1138) → `_analyze_and_trade` satır 903-905'te `if analysis is None: return` — bu `self._funnel_bump("scanned")` (908) satırından ÖNCE, yani sembol hunide HİÇ GÖRÜNMEZ.
- Neden onemli: EMA200 paper'ın 1 numaralı blokçusu: 08-21'de 1122 sinyalin 1122'si, 08-19'da 838'in 837'si EMA200. Bu kapının girdisi, tek bir geçici veri hatasıyla, sessizce ve GÜN BOYU yanlış kaynağa (saatlik) düşebiliyor — üstelik bunun tek izi INFO seviyesine hiç çıkmayan bir DEBUG satırı. Ayrıca (3) yüzünden toplu bir Alpaca veri kesintisi hunide `scanned=0` olarak görünür ve operatör "bot neden hiç taramadı?" sorusunun cevabını hiçbir sayaçta bulamaz — huni tam da bu teşhis için var.
- Onerilen duzeltme: (a) `_daily_above_ema200`'de yalnız BAŞARILI sonucu önbelleğe al; None'ı önbellekleme (en fazla birkaç dakikalık kısa negatif TTL). (b) None döndüğünde kapıyı fail-open bırakmak yerine `EMA200_VERI_YOK` sebebiyle huniye yaz ve INFO logla — şu an kapı sessizce farklı bir göstergeye geçiyor. (c) `_get_technical_analysis` None döndüğünde `_funnel_bump("scanned")` + yeni bir `data_error` aşaması yaz. (d) `get_stock_bars` hatalarını sembol bazında sayıp eşik aşılınca kritik alarm bas.

### KILLSWITCH-KOD-HATASINI-API-HATASI-SANIYOR  (Saglamlik/Altyapi)
**Ana döngüdeki HER istisna "API hatası" sayılıyor; 5 tanesi tüm pozisyonları piyasa emriyle likide ediyor — ve sayaç seans dışında hiç sıfırlanmıyor**

- Yer: `stock_bot.py:695-698 + stock_bot.py:485-497 + core/kill_switch.py:83-99,131-161 + stock_bot.py:2222-2232`
- Ne oluyor: Ana döngünün genel yakalayıcısı ne tür olursa olsun her istisnayı KillSwitch'e API hatası olarak besliyor: `except Exception as e: self.consecutive_errors += 1; logger.error(...); if self.kill_switch.check_api_error(e): continue` (695-698). `check_api_error` sayacı artırıp `max_consecutive_errors` (varsayılan 5, config'den okunuyor stock_bot.py:246) eşiğinde `_trigger_kill` çağırıyor (kill_switch.py:95-99). `_trigger_kill` → `on_kill_callback` → `_emergency_close_all` → `self.client.close_all_positions(cancel_orders=True)` (stock_bot.py:2229). Sayacı sıfırlayan tek çağrı `self.kill_switch.reset_error_count()` ve o SADECE PİYASA AÇIK kolunda (stock_bot.py:494) — CLOSED / PRE_MARKET / AFTER_HOURS kolları o satıra varmadan `continue` ediyor (452-484).
- Neden onemli: Gerçek para riski. Bir pandas/ta sürüm değişikliği, bir KeyError, bir None aritmetiği — API ile hiç ilgisi olmayan 5 kod hatası — canlı hesabın TÜM pozisyonlarını piyasa emriyle kapattırır. Dahası: seans dışında sayaç hiç sıfırlanmadığı için, pre-market taramasında tekrar eden bir hata gece boyunca birikip likidasyonu SABAH 04:00-09:30 arasında (likidite yok, spread geniş) tetikleyebilir. `requirements.txt`'te tek bir pin yok (hepsi `>=`) ve Dockerfile `--no-cache-dir` ile kuruyor; yani aynı commit'in iki deploy'u farklı kütüphane sürümü çekebilir — bu senaryo teorik değil.
- Onerilen duzeltme: (a) `check_api_error`'ı yalnız gerçek API istisnalarına ver (alpaca `APIError` / `requests` istisna sınıflarıyla tip kontrolü); diğerlerini ayrı `code_error` sayacına yaz ve o sayaç likidasyon YAPMASIN. (b) Her başarılı tam döngü turunda (piyasa durumundan bağımsız) `reset_error_count()` çağır. (c) `_emergency_close_all` için piyasa-açık koşulu şart koş; kapalı/pre-market'te likidasyon yerine kritik alarm + duraklat.

### POZISYON-METADATA-ATOMIK-DEGIL-VE-DEBUG-YUTULUYOR  (Saglamlik/Altyapi)
**bot_positions.json her turda atomik olmayan biçimde üzerine yazılıyor; bozulursa tüm çıkış durumu DEBUG seviyesinde sessizce kayboluyor**

- Yer: `stock_bot.py:1968-1973 + stock_bot.py:2087-2089 + karşılaştırma: core/funnel.py:150-155`
- Ne oluyor: Kayıt: `with open(self.POSITIONS_FILE, "w") as f: json.dump(data, f, indent=2, default=str)` — geçici dosya + `os.replace` YOK, doğrudan truncate-and-write. Bu satır ana döngünün her turunda çalışıyor (stock_bot.py:687 `self._save_position_metadata()`). Yükleme tarafı ise tüm gövdeyi tek `try` ile sarıp `except Exception as e: logger.debug(f"  Pozisyon metadata yüklenemedi: {e}")` (2088-2089) ile bitiriyor. Aynı repoda DOĞRU desen zaten var: core/funnel.py:150-155 `temp_path = f"{self.path}.tmp" ... os.replace(temp_path, self.path)`.
- Neden onemli: Bu dosyada `highest_price` (trailing stop referansı), `breakeven_set`, `partial_sold`, `stop_loss_price`, `stop_loss_pct`, `server_stop_verified`, `partial_intent` duruyor. Konteyner yazma anında öldürülürse (Coolify redeploy, OOM, host restart) dosya yarım kalır, JSON parse patlar, DEBUG'a yazılır ve konsola HİÇ çıkmaz (log_level INFO). Sonuç: broker'da pozisyonlar durur, bot onları Alpaca'dan varsayılan değerlerle geri kurar → trailing stop mevcut fiyattan yeniden başlar (kilitli kâr silinir), `partial_sold=False` olduğu için yarısı İKİNCİ kez satılabilir, `breakeven_set=False` olduğu için break-even stop geri alınır. Bunların hiçbiri operatöre bildirilmiyor.
- Onerilen duzeltme: (a) `_save_position_metadata`'yı funnel'daki tmp+`os.replace`+`fsync` desenine çevir. (b) Yükleme hatasını `logger.debug` değil `notifier.notify_critical("STATE_LOST", ...)` yap — açık pozisyon varken metadata kaybı sessiz olamaz. (c) Yükleme başarısızsa çıkış bayraklarını Alpaca emir geçmişinden yeniden türetmeyi dene (partial fill var mı, stop emri açık mı) ve türetilemiyorsa yeni girişleri durdur.

### STRAT-CIKIS-MERDIVENI-RR-YIKIMI  (Strateji/Alfa)
**Çıkış merdiveni R:R'yi tasarımdan 0.925'e indiriyor — başabaş kazanma oranı %51.9, gerçekleşen %28.6**

- Yer: `core/position_manager.py:356-435 + core/trade_gates.py:24-48 + config.py:765-774`
- Ne oluyor: Giriş planı `plan_exit_pcts` ile SL=clamp(ATR%×1.8, %5, %6), TP=min(max(%5, SL×1.25), %7.5) → tipik SL %5 / TP %6.25 (R:R 1.25). Ama pozisyon açıldıktan sonra üç mekanizma kazananı buduyor: (1) `position_manager.py:357-359` +%2.5'te stop'u giriş+%0.3'e çekiyor (`breakeven_trigger_pct`=0.025, `breakeven_offset_pct`=0.003), (2) `position_manager.py:424-434` +%3'te pozisyonun YARISINI satıyor (`_handle_long_partial` → `target_qty = round(snapshot_qty * 0.5, 4)`, satır 1199), (3) `position_manager.py:415` peak'ten %4 geri çekilmede çıkıyor. Kaybeden tarafta hiçbir budama yok: %2.5'e ulaşmayan işlem tam boyla −%5/−%6 stop'a gidiyor. Aritmetik: MAKSİMUM tam-pozisyon kazancı = 0.5×%3 + 0.5×%6.25 = %4.625; kayıp = %5. Trailing koşulu `pnl_pct > 0.01 AND trailing_drop >= 0.04` olduğu için ancak zirve ≈+%5.2'yi geçen işlemlerde tetiklenir — yani kazananların büyük çoğunluğu +%0.3 (break-even) veya +%1.65 (partial+break-even) bandında kapanır.
- Neden onemli: En iyi senaryoda bile R:R 4.625/5 = 0.925 → başabaş kazanma oranı 1/(1+0.925) = %51.9. Gerçekleşen kazanma oranı %28.6. Gerçekleşen kazananların medyanı $4.20, kaybedenlerin medyanı $47.12 → medyan R:R 0.089 → başabaş WR %91.8. AAPL +$2.72 / 4 adet = +$0.68/adet ≈ +%0.30 — tam olarak `breakeven_offset_pct`=0.003. Yani ölçülen mini-kazançlar strateji değil, break-even stop'un imzası. Üstüne `COMMISSION_CONFIG.estimated_slippage_pct`=0.001 (gidiş-dönüş %0.2) binince +%0.3'lük çıkış net ≈ +%0.1, yani sıfır. LIVE konfigürasyonu bu açıdan paper'dan DAHA İYİ (SL %4 / TP %8 / partial %5 → R:R_max 1.625, başabaş WR %38.1) — kalibrasyon hesabı, terfi ettireceği hesaptan daha kötü bir R:R ile çalışıyor.
- Onerilen duzeltme: partial_profit_pct'i TP'nin en az %70'ine taşı (paper'da %3 → %4.5) veya partial'ı tamamen kapat; break-even stop'u +%2.5'te giriş+%0.3'e değil, tetik kârının yarısına (giriş+%1.25) çek; trailing'i ATR-oranlı yap (ör. 2.5×ATR) ki %4 sabit eşik yüksek-ATR isimlerde erken kesmesin. Değişiklikten sonra R:R_max'ı TEKRAR hesapla ve `min_rr_ratio` yerine GERÇEKLEŞEN R:R'yi bir kapı olarak ölç.

### STRAT-EV-NEGATIF-KELLY-BYPASS  (Strateji/Alfa)
**Beklenen değer −$25.76/işlem, Kelly f* = −0.64; buna rağmen LONG boyutu Kelly'yi bypass ediyor**

- Yer: `core/position_sizer.py:88-140 (bant kısayolu) + 141-155 (Kelly yolu) + 205-250`
- Ne oluyor: 14 kapalı işlemin ölçüsü: WR=%28.57, avgW=$40.24, avgL=$52.17, b=0.7714 → EV = 0.2857×40.24 − 0.7143×52.17 = −$25.76/işlem (×14 = −$360.69, defterle birebir). Kelly f* = (b·p − q)/b = −0.6402. `_calculate_kelly()` bu durumu ZATEN tespit ediyor ve `kelly_full <= 0` dalında "Kelly NEGATİF — Strateji optimizasyona ihtiyaç duyuyor!" uyarısı basıyor. AMA `calculate_position_size` satır 91'de `if side == "LONG" and (bands or fixed_usd > 0):` ile ERKEN DÖNÜYOR — Kelly, ATR vol ölçeklemesi, kayıp dampingi ve rejim ayarının HİÇBİRİ LONG'a uygulanmıyor. Hem paper (`PAPER_AGGRESSIVE_CONFIG.conf_position_bands`, config.py:751-756) hem live (stock_bot.py:199 `config["conf_position_bands"] = config.get("live_conf_position_bands")`) bu daldan geçiyor. Kelly yolunun kendisi de kırık: satır 152 `base_pct = max(base_pct, self.MIN_POSITION_PCT)` Kelly negatif olsa bile boyutu equity'nin %5'inin ALTINA indirmiyor.
- Neden onemli: Kelly negatif = "bu bahiste sermayenin sıfırı optimaldir". Kod bunu ölçüyor, logluyor ve sonra yok sayıyor. Sonuç: paper'da bant tabanı $2,500 (AMZN 9 adet ≈ $2,295 tam olarak bu bant), tavanı $9,000 = equity'nin %14.2'si; açık 5 pozisyon $41,810 = equity'nin %66'sı. Negatif-EV bir stratejide boyut büyütmek zararın hızını artırır: ölçüm dönemindeki 6 işlemin ikisi (AMZN −$127.98, SMCI −$97.03) 4 kazananın toplamını (+$160.97) tek başlarına siliyor. Ayrıca bant yolu `max_position_pct` (%20) tavanını HİÇ uygulamıyor — yalnız `fixed_position_max_pct` (%62) ve `max_position_usd` uygulanıyor (satır 117-119).
- Onerilen duzeltme: Bant yolunu Kelly'nin ÜSTÜNE değil ALTINA koy: `position_usd = min(bant_hedefi, kelly_boyutu, tavanlar)`. Kelly ≤ 0 iken YENİ GİRİŞ AÇMA (fail-closed), $25'lik işlem üretme sorununu boyut tabanıyla değil işlem sayısıyla çöz. `MIN_POSITION_PCT` tabanını kaldır — taban, negatif edge'i zorla sahaya sürüyor. Bant yoluna `max_position_pct` tavanını da ekle.

### STRAT-BACKTEST-AYRI-UYGULAMA  (Strateji/Alfa)
**Backtest canlı kodu ÇALIŞTIRMIYOR — ayrı bir strateji test ediyor, dolayısıyla hiçbir şey kanıtlamıyor**

- Yer: `backtest.py:365-482 (_technical_analysis) + 500-536 (_execute_buy) + 561-646 (_manage_all_positions)`
- Ne oluyor: backtest.py `core.analyzer.TechnicalAnalyzer`'ı import ETMİYOR; kendi basitleştirilmiş skorlayıcısını yazıyor. Canlıda olup backtest'te OLMAYANLAR: (1) AgentCoordinator ve 5 ajanın tamamı — backtest'in `confidence`i `min(buy_score,100)`, canlınınki `abs(weighted_score)*2.0`; iki tamamen farklı büyüklük. (2) analyzer.py'nin Ichimoku, ADX, OBV divergence, Fibonacci, RSI divergence, S/R, VWAP terimleri (core/analyzer.py:175-292) — backtest'te yok. (3) TradeGates'in 10 kapısının HİÇBİRİ (EMA200, earnings, MTF, VOL, kayıp serisi, hisse filtresi, sektör, wash-sale, PDT) — yalnız opsiyonel bir `BT_LONG_TREND_GATE` env bayrağı var. (4) `conf_position_bands` boyutlandırması: backtest `max_usd = min(max_position_usd, capital*max_position_pct, capital*0.9)` kullanıyor (satır 502-506) — bantlar hiç çalışmıyor. (5) PositionSizer'ın tamamı. (6) SectorRotator, RelativeStrength, VolumeAnalyzer, SignalQueue, IndexParking, BearBrain. (7) KADEMELİ KÂR ALMA: `"partial_sold": False` satır 531'de yazılıyor ama `_manage_all_positions` içinde partial mantığı YOK — canlının +%3'te yarı satışı simüle EDİLMİYOR. (8) Break-even SABİT KODLU ve config'den farklı: satır 585-586 `if pnl_pct >= 0.015 ... entry * (1 + 0.001)` — config 0.025 / 0.003 diyor.
- Neden onemli: Backtest'in ölçtüğü şey ile canlıda çalışan şey aynı strateji değil. Kanıt: 44 işlemin tamamında `confidence` yalnız İKİ değer alıyor — LONG'ların 21/21'i 65, SHORT'ların 23/23'ü 45. Yani bant merdiveni (30/45/60/75 → $2500/$4000/$6000/$9000) hiç test edilmedi ve backtest'te "güven" sıfır varyanslı, sıfır bilgi taşıyan bir sabit. `BT_MIN_CONF` sweep hook'u da anlamsız: backtest zaten `buy_score >= 45` şartıyla giriyor, 45'in altındaki eşikler hiçbir şeyi değiştirmez. Sonuç: backtest'in PF 1.71 / Sharpe 1.78 rakamları canlı strateji hakkında hiçbir iddia taşımıyor.
- Onerilen duzeltme: Backtest'i bir bar-replay harness'ına çevir: `TechnicalAnalyzer.analyze`, `AgentCoordinator.decide`, `TradeGates.check_all_gates`, `PositionSizer.calculate_position_size` ve `PositionManager` çıkış zincirini GERÇEK sınıflarla çağır; broker/haber/sosyal çağrılarını sahte adaptörlerle besle. Aynısını yapamıyorsan backtest_results.json'ı ve türevlerini (walk_forward, regime_experiment) karar dayanağı olarak KULLANMA — dosyaları "bu canlı stratejiyi ölçmez" başlığıyla işaretle.

### STRAT-BACKTEST-GUNLUK-KAPANIS-KURGUSU  (Strateji/Alfa)
**Backtest çıkışları yalnız günlük kapanışta ölçüyor — kazananların %90'ı TP tavanının üstünde 'kapanmış', kârın %75'i kurgu**

- Yer: `backtest.py:561-605 (_manage_all_positions) + 596-600 (take profit) + 606-608 (trailing)`
- Ne oluyor: `current_price = float(current_bars["close"].iloc[-1])` — pozisyon yönetimi gün başına BİR kez, kapanış fiyatıyla çalışıyor; bar-içi high/low kullanılmıyor. Canlı bot ise `scan_interval_seconds` 15 (paper) / 30 (live) saniyede bir gün-içi fiyatla aynı kararları veriyor ve TP bir BRACKET LİMİT emri olarak sunucuda duruyor (core/executor.py:265 `take_profit={"limit_price": tp_price}`). Limit emri tanım gereği limit fiyatının ÜSTÜNDE dolmaz. Backtest ise `pnl_pct >= tp_pct` olduğu ilk günün KAPANIŞINDA çıkıp aradaki tüm taşmayı kâr yazıyor.
- Neden onemli: backtest_results.json'daki 20 kazananın 18'i config TP tavanını aşıyor: LONG'ların 9/10'u %7.5'in üstünde (%7.65–%12.45), SHORT'ların 9/10'u %6'nın üstünde (%6.31–%22.95; SMCI +%22.95, SOFI +%18.21, SHOP +%17.33 — `short_take_profit_pct`=0.06 iken). Kazananları config tavanına kırptığımda toplam P&L $2,692.30 → $666.53 (+%0.67) düşüyor; SPY aynı dönemde +%8.49. SHORT kolu +$391.53'ten −$685.92'ye dönüyor. En cömert iki-taraflı düzeltmede bile (kazananlar tavana, kaybedenler −%6 tabanına kırpılı) $1,102–$1,299 (+%1.1–1.3) çıkıyor — yani düzeltilmiş backtest de SPY'ın ~7 puan gerisinde, canlıda ölçülen −5.9 puanla ve walk-forward'ın −%11.48'iyle aynı yöne bakıyor. Ayrıca canlının kazanan-budayıcı mekanizmaları (break-even, partial, gün-içi trailing) backtest'te hiç yok; bu yüzden backtest'in avgWin'i (%3.6 notional) canlınınkinden (medyan +%0.3) bir büyüklük mertebesi yüksek.
- Onerilen duzeltme: Çıkışları bar-içi (en azından saatlik, tercihen dakikalık) high/low ile değerlendir: LONG için stop `low <= stop_price`, TP `high >= tp_price` ve DOLUM fiyatı tam olarak stop/limit seviyesi olsun (taşma kâr yazılmasın). Aynı döngüye break-even ratchet'ini, +%3 partial'ı ve trailing'i canlı config değerleriyle ekle. Düzeltilmeden önce mevcut backtest/walk-forward çıktılarını terfi kapısı olarak kullanma.

### STRAT-KENDI-KANITI-SPY-ALTI  (Strateji/Alfa)
**Projenin KENDİ walk-forward'ı 'SPY'ı 0/2 pencerede yendi, ortalama alfa −%11.48' diyor; deney sonuçları Haziran'dan kalma**

- Yer: `walk_forward_results.json:1-40 + regime_experiment_results.json:1-30 + backtest_results.json (spy_buyhold_pct)`
- Ne oluyor: walk_forward_results.json: `"beat_spy": 0, "frac_beat_spy": 0.0, "mean_alpha_pct": -11.48, "worst_alpha_pct": -13.01`. İki pencere: 2025-12-18→2026-06-16 bot +%2.09 / SPY +%12.05; 2025-06-21→2025-12-18 bot −%0.07 / SPY +%12.94. regime_experiment_results.json beş rejim modunun HEPSİNİ ölçüyor ve hiçbiri SPY'ı geçmiyor: off −%12.15, base/flat/scale −%11.48, overlay −%2.84. Seçilen mod `"best_deployable_mode": "base"` = −%11.48. backtest_results.json'ın kendisi de `"total_return_pct": 2.69` vs `"spy_buyhold_pct": 8.49` yazıyor. Bu üç dosya .gitignore'da (satır 30-32), yani sürüm kontrolünde izlenmiyor; dosya zaman damgaları walk_forward/regime = 16 Haziran, backtest = 5 Temmuz iken config.py 10 Ağustos'ta değişmiş; backtest_results'taki LONG notional'ı $5,000 (v4.12 öncesi `max_position_usd`), bugünkü değer $9,000.
- Neden onemli: "Backtest ne vaat ediyor?" sorusunun cevabı: HİÇBİR ŞEY — kendi araçları zaten stratejinin SPY'ı yenemediğini söylüyor ve bot buna rağmen canlıya alındı. En keskin rakam `overlay` modu: boştaki nakit SPY'de tutulduğunda ortalama getiri %9.665 (base'de %1.01), yani potansiyel getirinin 8.65 puanı SPY betasından geliyor; ama overlay bile SPY'ın 2.84 puan gerisinde — aktif alım-satımın ÖLÇÜLMÜŞ katkısı −%2.84/6 ay. Üstelik bu rakamlar yukarıdaki günlük-kapanış kurgusuyla ŞİŞİRİLMİŞ hâlleri; düzeltilmiş gerçek daha kötü. Sonuçların Haziran tarihli olması, Temmuz-Ağustos'taki agresif config değişikliklerinin (bantlar $9,000'e, partial %3'e, TP tavanı %7.5'e, min_rr 1.25'e) HİÇ backtest edilmediği anlamına geliyor.
- Onerilen duzeltme: Terfi kapısını değiştir: "4/4 PASS" değil, "düzeltilmiş walk-forward'da ≥4 bağımsız pencerenin ≥3'ünde SPY'ı net (maliyet sonrası) geç" olsun. Her config değişikliğinden sonra walk-forward'ı yeniden koştur ve sonucu tarih+config-SHA ile birlikte sakla; sonuç dosyaları .gitignore'dan çıkarılıp (veya ayrı bir results/ dizininde) sürümlensin ki bayat kanıt sessizce kullanılamasın.

### STRAT-SINYAL-KAPI-CELISKISI  (Strateji/Alfa)
**Sinyal üretici ortalamaya-dönüş, ana kapı trend-takip — ikisi anti-korele, huni 1122/1122 EMA200 bloğu gösteriyor**

- Yer: `core/analyzer.py:139-160, 349-355 + core/trade_gates.py:80-85 + stock_bot.py:1165-1167`
- Ne oluyor: analyzer.py'nin BUY skorlayıcısı bir ortalamaya-dönüş motoru: `rsi < rsi_oversold(30)` +25, `current_price < bb_lower*(1+%1)` +20, Fib %61.8 geri çekilme +12, S/R desteğe yakınlık +15, düşen fiyatta yükselen OBV (+15). Karar eşiği `buy_score >= 45`. Yani YALNIZCA "RSI<30 + BB dibi" = 25+20 = 45 → tam olarak eşik: aşırı satılmış olmak tek başına BUY üretiyor. Hemen ardından TradeGates'in 2. kapısı trend-takip mantığı uyguluyor: `if not analysis.get("above_ema200", True): return False, "EMA200"` ve bu değer stock_bot.py:1165'te GÜNLÜK bar EMA200'üyle hesaplanıyor. Ayrıca `_get_symbols_to_analyze` (stock_bot.py:1410-1421) evreni screener skoruna göre ilk 10'a indiriyor; screener hacim sıçraması/gap/momentum ile sıralıyor — yani en çok DÜŞEN isimleri öne çıkarıyor.
- Neden onemli: İki mekanizma yapısal olarak birbirini yiyor: sinyal ancak hisse dibe vurunca doğuyor, kapı ancak hisse 200 günlük ortalamasının üstündeyken geçiriyor. Ölçülen huni bunu birebir gösteriyor: 08-21'de 1122 sinyalin 1122'si, 08-19'da 838'in 837'si, 08-07'de 380'in 375'i EMA200'den blok. Yani bot günlerce sıfır giriş üretiyor — 'durmasının' birinci sebebi bu. Geçebilen nadir işlemler ise tanım gereği 'güçlü uptrend'de sert düşüş' profilinde; Ağustos'ta bunlar AMZN −$127.98 (9 adet, −$14.22/adet ≈ −%5.6) ve SMCI −$97.03 olarak gerçekleşti — yani filtrenin geçirdiği tek şey, düşüşün başlangıcı. Kapı gevşetilirse (EMA200 kapatılırsa) düşen bıçak yakalanır; kapı sıkı kalırsa hiç işlem olmaz. Bu bir ayar sorunu değil, tez çelişkisi.
- Onerilen duzeltme: Tek bir tez seç ve tüm zinciri ona göre kur. (A) Trend-takip: RSI<30 / BB_dip / Fib / S/R-destek terimlerini BUY skorundan çıkar, girişi kırılma+hacim onayına bağla, EMA200 kapısını koru. (B) Ortalamaya dönüş: EMA200 kapısını 'fiyat EMA200'ün üstünde' yerine 'EMA200 EĞİMİ pozitif' şartına çevir (dip alımı sağlıklı trendde serbest kalsın) ve stop'u ATR-tabanlı, TP'yi ortalamaya (VWAP/EMA21) dönüş hedefi yap. Karma hâli — dipten al, trendde ol, %4 trailing ile çık — üç tezin de en kötü yanını topluyor.


## YUKSEK

### AGENT-COGUNLUK-KURALI-OLU  (Ajanlar)
**Dokümante edilen '≥3 ajan aynı yönde' çoğunluk kuralı üretimde ulaşılamaz; ayrıca çoğunluk yönü ağırlıklı skorun işaretiyle çelişebiliyor**

- Yer: `core/agent_coordinator.py:14-16, 336-341, 414-416, 436-447, 474-475`
- Ne oluyor: Sınıf docstring'i iki kural vaat ediyor: 'Çoğunluk: ≥3 ajan aynı yönde' ve 'Risk vetosu: RiskAgent SELL → BUY yapılamaz'. Veto kodda GERÇEKTEN var (satır 430-431, 451-456) ve çalışıyor. Çoğunluk ise üretimde erişilemez:
- RiskAgent v4.8'den beri BUY oyu VEREMEZ — her iki dal da HOLD döndürüyor (satır 336-341).
- SocialAgent kalıcı HOLD (Reddit 403).
- FundAgent kota bittikten sonra kalıcı HOLD.
Geriye Tech + Sent kalıyor → buy_count en fazla 2 → `if buy_count >= 3` (satır 436) hiç tetiklenmiyor. Her BUY, `elif weighted_score > 15` yedek yolundan (satır 442) doğuyor; yani her giriş 1-2 ajanlık bir karar, 'konsensüs' değil. `majority` bayrağı da False kaldığı için ×1.2 çarpanı (satır 474-475) hiç uygulanmıyor.

Ayrıca çoğunluk yolu ile güven formülü birbiriyle çelişebiliyor: çoğunluk sinyali OY SAYISIYLA, güven ise AĞIRLIKLI SKORLA belirleniyor. 3 ajan SELL derken 1 ajan yüksek güvenle BUY derse ws ≈ 0 çıkar → sinyal SELL, güven ~0 → hiçbir eşiği geçemez. Yani çoğunluk kuralı hiçbir işlemi kurtaramaz, sadece işaret çelişkisi üretir.
- Neden onemli: Mimarinin satış argümanı (5 uzmanın oy birliği) üretimde yok. Karar fiilen TechAgent + işareti bozuk SentAgent'tan geliyor, ama eşikler ve pozisyon boyutu bantları 5 ajanlı bir mutabakat varsayımıyla kalibre edilmiş. Bu, canlıya terfi kararında yanlış güvence yaratıyor: 'çoğunluk sağlandı' logu üretimde hiç basılmıyor ama kimse fark etmiyor çünkü ölçülmüyor.
- Onerilen duzeltme: Ya çoğunluk kuralını gerçekten uygulanabilir hale getir (sessiz ajanları oy sayısından çıkar: 'oy veren ajanların ≥%60'ı' gibi), ya da docstring'i gerçeğe uydurup kaldır. Her iki durumda da çoğunluk yönü ile ağırlıklı skorun işareti çeliştiğinde kararı HOLD'a düşür (şu an SELL çıkıp güveni 1 oluyor — sessiz çöp sinyal). Koordinatör çıktısına `agents_voting` (HOLD olmayan ajan sayısı) alanı ekle ve funnel'a yaz; bu metrik 2'nin altına düştüğünde alarm ver.

### AGENT-RISK-VETO-SHORTU-KIRPIYOR  (Ajanlar)
**RiskAgent SHORT ile HEMFİKİRken bile kendi vetosu SHORT güvenini yarıya indiriyor**

- Yer: `core/agent_coordinator.py:428-431, 449-456, 474-485`
- Ne oluyor: `risk_veto`, RiskAgent SELL dediği ANDA True yapılıyor (satır 430-431), nihai sinyalin yönüne bakılmaksızın. Veto'nun asıl işi satır 451-456'da yapılıyor (BUY → HOLD) ve orada doğru. Ama güven hesabında koşul yön-farkında değil:

```python
# core/agent_coordinator.py:476-483
if risk_veto:
    confidence *= 0.5   # <- final_signal SELL/SHORT olsa da uygulaniyor
short_boost = getattr(risk_vote, "short_boost", 0)
if final_signal == "SELL" and short_boost > 0:
    confidence += short_boost
```

RiskAgent SELL demesi için risk_score ≤ -30 gerekiyor; bunun tipik tetikleyicileri VIX>35 (-25, short_boost +20), jeopolitik HIGH (-20, +10), günlük kayıp <-%2 (-30). Yani panik anında RiskAgent SELL diyor → risk_veto=True → SHORT güveni önce ÖNCE yarıya iniyor, sonra short_boost ekleniyor. RiskAgent SELL oyu ayrıca ws'e -0.20×conf katkı yapıyor, yani sinyali güçlendiriyor — ama sonra çarpan onu kırpıyor.
- Neden onemli: Short motoru tam olarak short'un en değerli olduğu anda (VIX panik, jeopolitik kriz) kendi kendini sakatlıyor. Paper BOT_MODE=both olmasına rağmen tüm işlem defterinde tek bir gerçek short yok (07-07 SPY COVER dışında) — 14 kapalı işlemin 13'ü long. İki aylık -%1.55'lik sonucun tamamı 5 mega-cap long'un betası; düşüş tarafından hiç gelir yok. Bu, kaybeden bir long-only sistemin neden 'both' modda çalışıyormuş gibi göründüğünü açıklıyor.
- Onerilen duzeltme: `confidence *= 0.5`'i yalnız vetonun GERÇEKTEN karar değiştirdiği durumda uygula: `if risk_veto and preliminary_signal == "BUY"`. RiskAgent SELL ile final SHORT hemfikirse çarpan uygulanmamalı (hatta mutabakat bonusu almalı). Ayrıca `risk_veto` bayrağını sonuç sözlüğünde 'veto_applied' (karar değişti) ve 'risk_bearish' (sadece oy) olarak ikiye ayır ki telemetri yanıltmasın.

### AGENT-RISK-HESAP-RISKINI-YON-SINYALINE-CEVIRIYOR  (Ajanlar)
**RiskAgent hesap-seviyesi riski her sembol için yönlü SELL oyuna çeviriyor; bot zarardayken topluca short'a geçiyor**

- Yer: `core/agent_coordinator.py:275-350, 421-424; stock_bot.py:1281-1315, 925-926`
- Ne oluyor: RiskAgent'ın skorunu belirleyen girdilerin çoğu SEMBOLDEN BAĞIMSIZ hesap/piyasa durumu: `daily_pnl_pct` (-30), `open_positions >= max_positions` (-25), `equity_floor_hit` (-50), `vix` (-25), `geopolitical_risk` (-20), `oil_signal` (-15). Sembole özgü tek girdi `atr_pct`. Bu skor ≤ -30 olunca ajan SELL oyu veriyor ve koordinatör bunu YÖNLÜ bir oy olarak ağırlıklı skora yazıyor:

```python
signal_value = {"BUY": 1, "SELL": -1, "HOLD": 0}[vote.signal]
weighted_score += signal_value * weight * vote.confidence   # -0.20 * (|risk_score|+30)
```

Yani 'hesabım bugün %2 zararda' veya 'VIX 36' bilgisi, taranan HER sembole -20'ye varan bir bearish ws itmesi olarak dağılıyor. Ardından stock_bot.py:925-926 bunu short'a çeviriyor:

```python
if decision["signal"] == "SELL" and symbol not in self.positions:
    decision["signal"] = "SHORT"
```
- Neden onemli: Risk yöneticisi bir FREN olmalıyken GAZ görevi görüyor — v4.8'de BUY tarafında düzeltilmiş ama SELL tarafında aynı hata duruyor. Sonuç: bot zarardayken veya piyasa panikteyken tüm evrende eşzamanlı short sinyali üretiyor; bu, bir risk kontrolünün tam tersi davranış (kayıp anında pozisyon artırma). Ayrıca RiskAgent'ın `confidence = min(|risk_score| + 30, 100)` formülü (satır 343) tabanı 30'dan başlattığı için SELL oyu her zaman ≥60 güvenle geliyor — yani en gürültülü oy.
- Onerilen duzeltme: RiskAgent'ı ağırlıklı skordan tamamen çıkar (weight=0) ve yalnızca veto/boyut-kısıcı olarak kullan; hesap-seviyesi risk yönlü sinyal üretmemeli. Sembol-bağımsız risklerin cezası pozisyon boyutuna ve max_open_positions'a yansımalı, ws'e değil. Ayrılan %20 ağırlığı Tech/Sent arasında yeniden dağıt ve eşikleri kalibre et. Ölü `elif` dalını kaldır veya gerçekten farklı bir davranışa bağla.

### AGENT-RISK-VERI-YOKSA-FAIL-OPEN  (Ajanlar)
**Hesap sorgusu hata verirse RiskAgent sessizce 'risk normal' diyor ve equity-floor vetosu kapanıyor**

- Yer: `stock_bot.py:1281-1315 (özellikle 1313-1315); core/agent_coordinator.py:275-347`
- Ne oluyor: `_build_risk_data` tüm gövdeyi tek bir try içinde tutuyor ve HERHANGİ bir hatada boş sözlük döndürüyor:

```python
# stock_bot.py:1313-1315
        except Exception:
            return {}
```

Boş sözlük RiskAgent'a girince tüm `.get()` varsayılanları devreye giriyor: daily_pnl_pct=0, open_positions=0, atr_pct=0, equity_floor_hit=False, vix=0, geo=NORMAL, oil=STABLE → risk_score=0 → signal=HOLD, reasoning='Risk seviyeleri normal' (satır 347). Yani Alpaca `get_account()` çağrısı zaman aşımına uğradığında bot, hesap %85 tabanın altında olsa bile 'risk normal' diyor ve equity floor vetosu SESSİZCE devre dışı kalıyor.
- Neden onemli: Bu bir fail-open güvenlik açığı ve doğrudan gerçek para riski: koruma katmanının kapandığını hiçbir log söylemiyor — tam tersine 'Risk seviyeleri normal' yazıyor. Aynı anda ağ sorunu yaşayan bir bot hem fiyat verisini eksik alıp hem de risk frenini kaybediyor. Botun genel deseniyle uyumlu: exception'lar `except Exception: pass` / `logger.debug` ile yutuluyor (stock_bot.py:1205-1206, 1213-1214, 1225-1226, 1233-1234), yani beş ajanın DÖRDÜ veri kaynağı çöktüğünde sessizce nötrleşiyor ve karar tek ajana kalıyor — ama karar yine de 'coordinator kararı' olarak loglanıyor.
- Onerilen duzeltme: `_build_risk_data` hata durumunda boş sözlük değil, `{"data_ok": False}` döndürsün; RiskAgent `data_ok` False ise fail-CLOSED davranıp SELL/veto üretsin (veya koordinatör kararı HOLD'a düşürsün). Genel olarak her ajanın girdisine `*_data_ok` bayrağı ekle (FundAgent için stock_bot.py:1210-1212'de zaten var ama koordinatör onu OKUMUYOR) ve koordinatör kaç ajanın gerçek veriyle çalıştığını sonuca yazsın; 2'nin altındaysa işlem açma.

### AGENT-OGRENME-DONGUSU-HIC-TETIKLENMIYOR  (Ajanlar)
**Dinamik ağırlık öğrenmesi kapalı devre değil: eşik hiç dolmuyor, ağırlıklar her zaman varsayılan dönüyor**

- Yer: `core/agent_performance.py:26, 152-224; stock_bot.py:1238-1243`
- Ne oluyor: Döngünün yazma ucu çalışıyor (giriş anında `record_prediction`, çıkışta `record_outcome`), ama okuma ucu hiç aktive olmuyor:

```python
# core/agent_performance.py:171-180
recent = [p for p in preds
          if p.get("timestamp", "") >= cutoff       # son 30 gun
          and p.get("correct") is not None]         # HOLD oylari haric (satir 138-139)
if len(recent) < self.MIN_TRADES_FOR_EVAL:          # 5
    raw_weights[agent_name] = self.DEFAULT_WEIGHTS[agent_name]
    continue
```

İki bağımsız neden eşiği doldurmayı imkânsız kılıyor:
1) Sayı: paper'da son 30 günde 6 kapalı işlem var (07-30 sonrası: PLTR, SMCI, AMZN, PLTR, SMCI, AAPL). Ajan başına en fazla 6 kayıt.
2) HOLD elemesi: `if not directional or predicted == "HOLD": pred["correct"] = None` (satır 138-139). FundAgent, SocialAgent ve RiskAgent üretimde HOLD verdiği için bu üç ajanın `correct` sayacı SIFIR kalıyor — MIN_TRADES_FOR_EVAL 5'e asla ulaşamıyorlar.
Sonuç: `get_dynamic_weights()` her çağrıda DEFAULT_WEIGHTS'i normalize edip döndürüyor; stock_bot.py:1240-1241'deki `self.coordinator.WEIGHTS = dynamic_weights` her seferinde aynı sabit sözlüğü yazıyor.

Ayrıca eşik dolsa bile n=5-6 ile 'öğrenme' istatistiksel gürültü: `boost = (accuracy - 0.5) * 0.5` formülü tek bir işlemin sonucuna ±0.05 ağırlık oynatır.
- Neden onemli: Soru 4'ün cevabı: sistem 'öz-değerlendiren ajanlar' olarak pazarlanıyor ama geri besleme halkası kapalı DEĞİL — yazılıyor, hiç okunmuyor (daha doğrusu okunuyor ama hep varsayılana düşüyor). LIVE'daki agent_performance.json'ın 16 Temmuz'dan beri hiç değişmemesi bunun kanıtı: dosya yalnız `record_prediction`/`record_outcome`/`prune` yazınca değişir; 16 Temmuz'dan beri hiç GİRİŞ olmadığı için hiç tahmin kaydedilmemiş, `prune` de silecek bir şey bulamadığı için (satır 312-313: `if pruned > 0: self._save()`) yazmamış. Yani dosyanın donmuş olması bozukluk değil, sıfır-işlem durumunun imzası. Paper'daki 12.6KB ise ~14 işlem × 5 ajan × birkaç alan kadar — ağırlık öğrenmesi için gereken hacmin çok altında.
- Onerilen duzeltme: Ya öğrenmeyi gerçekten çalışır hale getir (MIN_TRADES_FOR_EVAL'i işlem sayısına değil, tahmin sayısına bağla; HOLD'ları 'katkısız' değil 'kaçırılan fırsat' olarak etiketleyip ölçmeye kat; lookback'i 30 günden 90'a çıkar), ya da devre dışı bırakıp sabit ağırlıkları açıkça yaz — şu haliyle 'öğreniyor' iddiası ölçüme dayanmıyor. Her durumda `get_agent_stats()` çıktısını (notifier.py:308-309 zaten çağırıyor) günlük rapora ZORUNLU alan yap ki 'VERİ YOK' durumu görünür olsun.

### EXIT-STOP-LIMIT-GAP-DOLMAMA  (Cikislar/Defter)
**Sunucu koruması stop-MARKET değil, %0,5 bantlı stop-LIMIT; gap'te tetiklenip dolmuyor ve mutabakatçı yalnız ALARM basıyor**

- Yer: `core/executor.py:263-274; core/position_manager.py:1701-1703, 1982-1990; core/protection.py:361-372; core/position_manager.py:796-798`
- Ne oluyor: Hem bracket bacağı hem de sonraki her stop güncellemesi limit fiyatını `stop × 0.995` yapıyor — tetiğin sadece %0,5 altı. Fiyat bu bandın altına açılırsa (gap) veya hızla geçerse emir elect olur ama dolmaz. protection.classify_covering_order bu durumu tespit ediyor (ELECTED_UNFILLED), fakat ensure_protective_stops sadece alarm basıp `continue` ediyor — düzeltici piyasa emri YOK (position_manager.py:796-798). Tek gerçek kurtarıcı yerel döngüdeki `should_exit_locally` → execute_sell (position_manager.py:394-403), o da yalnız piyasa AÇIKken ve döngü sırası geldiğinde çalışır; PRE_MARKET dalında `_manage_positions` hiç çağrılmıyor (stock_bot.py:462-466).
- Neden onemli: Tasarlanan tek-işlem riski paper'da %5-6. Gap senaryosunda sunucu stopu dolmayınca gerçekleşen kayıp bu tavanı aşar — AMZN 9 adette -$14,22/adet (~%6) tam olarak bu sınırın ucundadır ve daha büyük bir gap'te sınır yoktur. Ayrıca fractional pozisyonlarda bracket TIF zorunlu DAY (executor.py:257-261) → gece koruma emri hiç yok; koruma yalnız açılışta yeniden kuruluyor.
- Onerilen duzeltme: Sunucu korumasını stop-MARKET'e çevir (Alpaca StopOrderRequest); likidite endişesi varsa limit bandını ATR'ye bağla (ör. 1×ATR), sabit %0,5 bırakma. ELECTED_UNFILLED tespitinde alarm yetmez: emri iptal edip anında close_position ile piyasa çıkışı yap. PRE_MARKET dalında da _manage_positions çağrılsın.

### EXIT-GAP-TARAYICI-ACILIS-SONRASI  (Cikislar/Defter)
**"Pre-Market Gap Scanner" fiilen piyasa AÇILDIKTAN sonra çalışıyor — gap zaten realize olmuş oluyor**

- Yer: `stock_bot.py:462-466 vs 486, 511-521`
- Ne oluyor: PRE_MARKET dalı `self._do_morning_scan(); time.sleep(30); continue` ile döngüyü kesiyor (stock_bot.py:463-466). Gap tarayıcı bloğu ise `=== PİYASA AÇIK ===` işaretinden (satır 486) SONRA, satır 511-521'de. Yani `scan_overnight_gaps` + `execute_gap_actions` yalnız market_status == OPEN iken çalışabilir.
- Neden onemli: SELL_AT_OPEN aksiyonunun tüm değeri açılıştan ÖNCE karar verip açılışta hemen çıkmaktır. Kod, gap zararı zaten fiyata girdikten sonra tarama yapıyor — koruma değil, gecikmiş teyit. Gap kaynaklı büyük kayıpların (AMZN sınıfı) önlenmesi için tasarlanan mekanizma fiilen devre dışı; yorum satırı ("piyasa acilmadan") koddan sapmış durumda ve bu sapma denetimde "koruma var" izlenimi veriyor.
- Onerilen duzeltme: Gap tarama/aksiyon bloğunu PRE_MARKET dalının içine (continue'dan önce) taşı; SELL_AT_OPEN için açılışta hemen çalışacak bir niyet kuyruğu bırak. Ayrıca gap eşiği aşıldığında koruma stopunu piyasa-öncesi daraltmak yerine doğrudan açılışta market çıkış planla.

### EXIT-PARTIAL-SONRASI-TP-BACAGI-KAYBOLUYOR  (Cikislar/Defter)
**Kademeli satış tüm SELL bacaklarını iptal ediyor ama yalnız STOP geri kuruluyor — kalan yarının sunucu TP'si kalıcı olarak yok oluyor**

- Yer: `core/position_manager.py:957-976 (_cancel_partial_conflicts), 1112-1177 (_restore_partial_protection)`
- Ne oluyor: _cancel_partial_conflicts sembolün AKTİF tüm SELL emirlerini iptal ediyor — bracket'ın take-profit limit bacağı dahil. _restore_partial_protection ise sadece `_update_server_stop_loss` çağırıp stopu geri kuruyor; TP limit emri yeniden verilmiyor. Ek olarak kalan aktif stop dışındaki her emri (`extras`) iptal ediyor.
- Neden onemli: Kademeli satıştan sonra kalan yarının sunucuda tek otomatik çıkışı break-even stopudur (+%0,3). Kâr hedefi tamamen yerel döngüye (`elif pnl_pct >= pos_tp_pct`, position_manager.py:405-412) bağımlı hale gelir; bot durur/deploy olur/konteyner yeniden başlarsa kalan yarı yalnızca aşağı yönde emirle korunur, yukarı yönde hiçbir realize mekanizması kalmaz. Bu, EXIT-BE-TAVANI bulgusuyla birleşince "yarısını +%3'te sat, kalan yarıyı +%0,3'e geri ver" davranışını yapısal hale getirir.
- Onerilen duzeltme: _restore_partial_protection'a TP bacağını da ekle: kalan qty için `take_profit_pct` ile LimitOrderRequest (GTC, tam pay ise) gönder ve stop ile birlikte OCO/doğrulanmış çift olarak yönet. Alternatif: kademeli satışta bracket'ı iptal etmek yerine ReplaceOrder ile bacak adetlerini küçült.

### EXIT-ZAMAN-STOPU-YOK  (Cikislar/Defter)
**Hisse long'larında maksimum tutma süresi / bayat pozisyon çıkışı YOK — sermaye süresiz kilitli**

- Yer: `core/position_manager.py:386-434 (tüm çıkış zinciri); core/bear_brain.py:377-390 (tek zaman-stopu, yalnız ters-ETF)`
- Ne oluyor: manage_positions'ın çıkış zinciri tam olarak 4 daldan ibaret: STOP_LOSS, TAKE_PROFIT, TRAILING_STOP, KADEMELİ. Hiçbirinde süre bileşeni yok. Repo genelinde `time_stop` / `held_days` yalnız bear_brain.exit_reason içinde ve yalnız kaldıraçlı ters-ETF sleeve'i için (`time_stop_days_3x`/`_1x`). Normal hisse pozisyonları için karşılığı yok.
- Neden onemli: Paper'da 5 açık long (~$41.810 = equity'nin %66'sı) süresiz duruyor. Bu sermaye ne yeni sinyale ne de max_open_positions slotuna dönebiliyor; bot fiilen bir mega-cap buy-and-hold sepeti taşıyıp üstüne +%0,3'lük bir break-even stopu koyuyor. Ölçülen "başarılı" pencerede kazancın tamamının bu 5 long'un betası olması ve kapalı işlemlerin negatif olması tam olarak bu yapının sonucu: strateji alfa üretmiyor, pozisyonlar sadece piyasayla sürükleniyor. Ayrıca slot doluluğu, giriş hunisinin ölçülemez hale gelmesine katkı veriyor.
- Onerilen duzeltme: Swing ufkuna uygun bir zaman stopu ekle (ör. `max_hold_days` 10-15 iş günü; süre dolduğunda MFE < %1 ise tam çıkış, kârdaysa stopu piyasa fiyatının hemen altına çek). Ayrıca "fırsat maliyeti çıkışı": pozisyon N gündür ±%1 bandındaysa ve daha yüksek güvenli bir sinyal slot bekliyorsa kapat. Zaman stopunu backtest ile kalibre et.

### CONFIG-CANLI-CIKIS-GEOMETRISI-OLCULMEMIS  (Cikislar/Defter)
**TP bandı %5-7,5 ve min_rr 1.25 YALNIZ paper'da; canlı hâlâ TP %8-12 / min_rr 2.0 — ölçüm canlıyı doğrulamıyor**

- Yer: `config.py:365-377 (STOCK_CONFIG) vs config.py:766-774 (PAPER_AGGRESSIVE_CONFIG); core/trade_gates.py:24-47`
- Ne oluyor: STOCK_CONFIG (canlının kullandığı taban): stop_loss_pct 0.04, stop_loss_max_pct 0.06, take_profit_pct 0.08, take_profit_max_pct 0.12, min_rr_ratio 2.0, partial_profit_pct 0.05. PAPER_AGGRESSIVE_CONFIG bunları ezip paper'da: stop 0.05, TP tabanı 0.05, TP tavanı 0.075, min_rr 1.25, partial 0.03 yapıyor. trailing_stop_pct (0.04) ve breakeven (0.025/0.003) ikisinde de AYNI. plan_exit_pcts bu değerlerden TP'yi türetiyor: paper'da TP ≈ %6,25, canlıda TP ≈ %8-%12.
- Neden onemli: Ölçüm penceresinin tüm verisi paper geometrisiyle üretiliyor; 4/4 PASS kapısı geçse bile canlıda çıkış geometrisi TAMAMEN farklı (TP hedefi ~2 kat uzak, min_rr 2.0 ek giriş bloklaması yapıyor). Yani paper kanıtı canlı davranışını doğrulamıyor — ölçüm kapısının temel varsayımı geçersiz. Dahası canlıda partial %5'te, break-even %2,5'te: kazananın etkin tavanı yine +%0,3 dead-zone'unda (bkz. EXIT-BE-TAVANI) ama TP %8-12'ye uzadığı için oraya ulaşma olasılığı paper'dan daha da düşük. Canlı huniyi kilitleyen min_rr 2.0 + TP tavanı %12 kombinasyonu, yüksek-ATR isimlerde R:R kapısını tekrar bir volatilite filtresine dönüştürür.
- Onerilen duzeltme: Ölçüm kapısını geçirmeden önce paper ile canlının ÇIKIŞ parametrelerini eşitle (paper yalnız giriş kapılarında/boyutta agresif olsun). Aksi halde canlıya terfi kararı ölçülmemiş bir geometriye yapılır. Ek olarak min_rr ve TP tavanının birlikte oluşturduğu örtük ATR filtresini walk_forward ile yeniden ölç.

### CFG-PAPER-LIVE-AYRI-SISTEM  (Config/Risk)
**PAPER_AGGRESSIVE canlıdan 20 ayarda ayrışıyor: paper'da ölçülen PnL canlı strateji için geçersiz**

- Yer: `config.py:741-827, stock_bot.py:158-171, 191-201`
- Ne oluyor: stock_bot.__init__ paper modda PAPER_AGGRESSIVE_CONFIG'i STOCK_CONFIG dict'inin ÜSTÜNE yazıyor (stock_bot.py:158-165). Ortaya çıkan iki profil arasındaki farklar (LIVE efektif → PAPER efektif):
- min_confidence_score: 50 → 30
- min_rr_ratio: 2.0 → 1.25
- take_profit_pct / take_profit_max_pct: %8 / %12 → %5 / %7.5
- stop_loss_pct: %4 → %5
- partial_profit_pct: %5 → %3
- max_position_usd: $300 (stock_bot.py:191 live_max_position_usd) → $9000
- conf_position_bands: [[50,100],[60,150],[70,200],[80,300]] → [[30,2500],[45,4000],[60,6000],[75,9000]]
- max_open_positions: 3 → 10
- multi_tf_enabled: True → False (canlının en çok giriş yiyen kapısı paper'da KAPALI)
- max_atr_pct: %5 → %8
- max_positions_per_sector: 2 → 3
- coin_max_consecutive_losses: 3 → 5
- cash_reserve_pct: %10 → %5
- equity_floor_pct: %85 → %75
- max_daily_loss_pct (kill): %5 → %8
- loss_streak_warn / halt / halt_hours: 2 / 4 / 24s → 999 / 8 / 4s
- pullback_queue_enabled: False → True
- fundamental_gate_enabled: False → True
- sell_cooldown_seconds: 300 → 120
- scan_interval_seconds: 30 → 15
SADECE paper'da test edilip canlıya HİÇ geçmemiş mekanizmalar: pullback signal queue, fundamental gate, $2500-9000 bant ölçeği, MTF kapısız giriş, %8 ATR toleransı.
- Neden onemli: Ölçüm penceresindeki 6 kapalı işlem $2500-9000 boyutlu, conf>=30 eşikli, MTF kapısı KAPALI, R:R 1.25 hedefli bir sistemin ürünü. Canlı ise conf>=50 (kayıp serisiyle 70), R:R 2.0, MTF açık, ATR<=%5 ve $98 bütçeli. Bu iki dağılım aynı strateji değil: paper'ın giriş evreni canlınınkinin kat kat üstünde ve çıkış geometrisi (TP %5-7.5 vs %8-12) tamamen farklı. Dolayısıyla "paper 4/4 PASS oldu, canlıya geçelim" akıl yürütmesi yapısal olarak geçersiz — ölçtüğümüz sistem terfi ettirilecek sistem değil. Ayrıca `enable_options` / `prefer_options_over_stock` merge tarafından ATLANIYOR (stock_bot.py:162-163 `elif key.startswith("enable_") or key.startswith("prefer_"): pass`) yani bu iki paper ayarı hiçbir yere yazılmıyor — ölü.
- Onerilen duzeltme: Terfi kapısının ön koşulu değiştirilsin: ölçüm, CANLI parametre setiyle çalışan ayrı bir paper konteynerinde (TRADING_MODE=paper + PAPER_AGGRESSIVE devre dışı + live bantlar/kapılar) yapılsın. Mevcut agresif paper 'öğrenme/örnek üretme' hattı olarak kalsın ama 4/4 PASS kapısına VERİ SAĞLAMASIN. Kısa vadede: olcum_raporu.py çalışırken hangi profil altında toplanmış veriyi okuduğunu rapor başlığına yazsın.

### OLCUM-PARK-PNL-SIZINTISI  (Config/Risk)
**Index parking işlemleri deftere yazılmıyor ama terfi kapısı broker fill'lerinden okuyor: SPY park kârı 4/4 PASS metriğine karışıyor**

- Yer: `core/index_parking.py:205-249, tools/olcum_raporu.py:176-232, 236-292, 783-788`
- Ne oluyor: index_parking._buy ve _sell doğrudan `self.bot.client.submit_order(...)` / `self.bot.client.close_position(...)` çağırıyor; executor'ın kayıt yolundan (trade_history.json / trades_today / performance.record_trade) GEÇMİYOR — modülde tek bir defter yazımı yok. Diğer yandan tools/olcum_raporu.py, PnL metriğini defterden değil doğrudan broker'ın kapalı emirlerinden kuruyor: `fetch_closed_orders(client, since)` (satır 198-231, `cursor = since - timedelta(days=90)`), sonra `broker_fills()` TÜM sembollerin fill'lerini alıyor (satır 176-194, hiçbir sembol filtresi yok) ve `reconstruct_closed_trades()` sembol bazında net pozisyon döngüsü kuruyor (satır 236-292). Metrik-1 doğrudan bunların toplamı: `net_pnl = sum(trade.pnl for trade in period)` (satır 783).
- Neden onemli: Paper'da index_parking_enabled=True (config.py:807) ve rezerv %5 nakit + %30 park kombinasyonu yüzünden SPY sleeve'i on binlerce dolarlık. Bu sleeve kapandığında (bear-unwind veya rezerv tamamlama) broker tarafında BÜYÜK bir kapalı SPY döngüsü oluşuyor ve olcum_raporu bunu 'kapalı strateji işlemi' sayıp PnL'e ekliyor; oysa bot defterinde bu işlemin izi bile yok. Ölçülen çelişki tam bu şekli gösteriyor: aynı 6 işlem defterde −$64.04 iken araç "net $+218.89 / PASS" diyor. Yani terfi kapısı, stratejinin değil SPY beta'sının kârını ölçüyor — ve bu kapı gerçek paraya geçişin ön koşulu. Ek olarak reconstruct_closed_trades, pencere başında zaten AÇIK olan pozisyonun ilk gördüğü SELL fill'ini yeni bir SHORT girişi sayıyor (satır 240-251: state yoksa `direction = -1` ile state kuruluyor), bu da hayali döngüler üretir.
- Onerilen duzeltme: olcum_raporu.broker_fills() içine sembol dışlaması ekle: index parking sembolü (config.index_parking_symbol) ve bear_brain.bear_symbols() listesindekiler strateji PnL'inden çıkarılsın, ayrı satırda raporlansın. İkinci olarak `reconstruct_closed_trades` pencere başında açık olan pozisyonları tanısın (başlangıç envanterini Alpaca positions'tan al) ve eşleşmeyen SELL'i SHORT girişi saymasın. Üçüncüsü: parking emirleri de deftere yazılsın (kaynak etiketiyle) ki iki muhasebe birbirini denetleyebilsin.

### RISK-KILL-SWITCH-ACIL-TASFIYE  (Config/Risk)
**Kill switch küçük hesapta anlamsız eşikte ama tetiklendiğinde her şeyi piyasa emriyle satıp canlı botu KALICI kilitliyor; 3 ardışık hata da aynı sonucu veriyor**

- Yer: `config.py:448-449, core/kill_switch.py:46-74,84-101,108-126, stock_bot.py:245-249,489-497,694-698,2222-2233`
- Ne oluyor: Canlı eşikler: `max_daily_loss_pct: 0.05` ve `max_consecutive_errors: 3` (config.py:448-449). Tetiklendiğinde callback `_emergency_close_all` çalışıyor: `self.client.close_all_positions(cancel_orders=True)` (stock_bot.py:2228). Hata sayacı sadece API hatasıyla değil, ANA DÖNGÜNÜN HERHANGİ bir istisnasıyla artıyor: stock_bot.py:694-698 `except Exception as e: self.consecutive_errors += 1; ... if self.kill_switch.check_api_error(e): continue`. Kill dosyası yeni günde OTOMATİK sıfırlanma yolu YALNIZCA paper için var: kill_switch.py:53-56 `if TRADING_MODE != "live" and "Günlük" in str(data.get("reason", "")): ... stale_daily = ts.date() < datetime.now().date()`.
- Neden onemli: $491.72'lik canlı hesapta -%5 = -$24.59. Strateji tarafı bu zararı üretemez (maks $98 pozisyon × %4-6 stop = $4-6). Eşiği tetikleyebilecek TEK varlık $345.90'lık SPY parkı, yani kill switch fiilen "SPY -%7 günü" dedektörüne dönüşmüş; tetiklendiğinde yaptığı şey çöküş gününün dibinde SPY'yi piyasa emriyle satmak ve ardından canlı botu insan müdahalesine kadar kilitlemek. Diğer yandan 3 ardışık get_account hatası (Alpaca kesintisi, 15-30sn'lik döngüde ~45-90 saniye) aynı tasfiyeyi tetikler: bir altyapı arızası gerçek pozisyonların piyasa emriyle kapanmasına yol açar. Bu, koruma değil, riskin kendisidir.
- Onerilen duzeltme: (a) API/altyapı hatasını tasfiye sebebinden AYIR: ardışık hata kill'i pozisyon kapatmasın, yalnızca yeni girişleri dursun ve alarm üretsin (`auto_close_positions` kararı sebebe göre verilsin). (b) Günlük kayıp kill'i dolar tabanlı bir taban da alsın (örn. max(%5 × equity, $50)) ki küçük hesapta piyasa gürültüsü tasfiye tetiklemesin. (c) Tasfiye piyasa emri yerine kademeli/limitli olsun ya da yalnız strateji pozisyonlarını kapatsın; index parking sleeve'i kill kapsamı dışında tutulsun. (d) Canlı kill dosyası için de zaman aşımlı otomatik sıfırlama + zorunlu bildirim eklensin (şu an kalıcı sessiz kilit).

### RISK-BEAR-BRAIN-TEK-YONLU-ASIMETRI  (Config/Risk)
**Düşüşte canlıda SPY parkı SATILIYOR ama ters-ETF girişi R5 kilidiyle kapalı: bot koruma ayağını uygular, kazanç ayağını uygulayamaz**

- Yer: `config.py:558-559, core/bear_brain.py:526-535,632, core/index_parking.py:154-174, core/executor.py:147-157`
- Ne oluyor: BEAR_BRAIN_CONFIG `enabled: True, allow_live: True` (config.py:558-559). BearBrain'in giriş yolu executor.execute_buy'dan geçiyor (bear_brain.py:632 `bought = bot.executor.execute_buy(symbol, analysis, call_cfg)`) ve orada canlı kilidi var: `if is_live and not config.get("live_entries_enabled", False): ... return False` (executor.py:147-157). Ama BearBrain'in park direktifi HİÇBİR kilitten geçmiyor: `parking_directive()` DEFENSE/ATTACK'ta "unwind" döndürüyor (bear_brain.py:530-535) ve index_parking bunu doğrudan uyguluyor: `self.bot.client.close_position(self.symbol)` (index_parking.py:161).
- Neden onemli: Canlı hesabın TEK varlığı SPY parkı ($345.90 = equity'nin %70'i) ve 3 aylık +%0.49'un tamamı bu parktan geliyor. Skor 55'i (DEFENSE) geçtiği anda bot bu tek getiri kaynağını satacak, sonra SH/SQQQ girişini R5 kilidi yüzünden yapamayacak → hesap %100 nakde geçip getirisi tam olarak SIFIR olacak. Yani "iki taraftan kazanma" tasarımının canlıdaki net etkisi tek yönlü: yalnız satış tarafı çalışıyor. Ayrıca `defense_parking_unwind: True` (config.py:583) bu davranışı 55-71 bandında da açıyor, yani nadir bir kriz değil sıradan bir düzeltme yeterli.
- Onerilen duzeltme: Park unwind'i bear girişinin gerçekten yapılabilir olmasına bağla: `parking_directive()` "unwind" döndürmeden önce canlı giriş kilidini (live_entries_enabled) ve bear enstrümanının açılabilirliğini kontrol etsin; giriş yapılamıyorsa en fazla "pause" dönsün. Alternatif olarak unwind'i kısmi yap (sleeve'in %50'si) ki hedge yapılamıyorken tüm beta kaybedilmesin.

### PDT-OLU-CONFIG-VE-BRACKET-BACAKLARI  (Config/Risk)
**PDT ayarları config'de var ama hiç okunmuyor (sabit kodlu); PDT kapısı blok ETMİYOR ve server-side bracket bacakları gün-içi işlem yakabiliyor**

- Yer: `config.py:435-436, core/pdt_tracker.py:24-25,121-126, core/trade_gates.py:145-153, core/executor.py:427-435, 259-268`
- Ne oluyor: config.py:435-436'daki `max_day_trades_per_week: 2` ve `pdt_equity_threshold: 25000` repo genelinde HİÇ okunmuyor (tarama: bu iki anahtar config.py dışında hiçbir dosyada geçmiyor). Gerçek değerler pdt_tracker.py:24-25'te sınıf sabiti: `MAX_DAY_TRADES_PER_WEEK = 2`, `PDT_EQUITY_THRESHOLD = 25_000`. PDT kapısı alım tarafında hiçbir şeyi bloklamıyor — trade_gates.py:145-153 sadece log basıyor ve yorumu bunu itiraf ediyor: `# PDT gate blok ETMİYOR, sadece uyarı veriyor`. Satış tarafında koruma var ama STOP_LOSS bypass'lı: executor.py:430-435 `if "STOP_LOSS" not in reason: ... return False` else `logger.warning("PDT: STOP_LOSS override")`. Ayrıca her giriş bracket olarak gönderiliyor (executor.py:262-274) — TP/SL bacakları Alpaca'da sunucu tarafında duruyor ve aynı gün dolarsa bot'un PDT sayacından bağımsız olarak gün-içi işlem yaratıyor. Haftalık pencere de FINRA'nın 5 iş günü yerine 7 takvim günü: pdt_tracker.py:124 `cutoff = date.today() - timedelta(days=7)  # yaklaşık 5 iş günü`.
- Neden onemli: Canlı hesap $491.72, yani $25k eşiğinin çok altında ve FINRA 4210 kapsamında: 5 iş gününde 4. gün-içi işlemde hesap Pattern Day Trader olarak işaretlenir ve $25k'ya tamamlanana kadar 90 gün boyunca sadece kapanış işlemi yapabilir. Bot'un kendi 2 işlemlik güvenlik marjı bunu ENGELLEYEMEZ çünkü (a) alım tarafında kapı blok etmiyor, (b) bracket TP/SL bacakları bot'un iznini sormuyor, (c) STOP_LOSS bilerek PDT'yi eziyor. $20'lik bir stop'u kurtarmak için $492'lik hesabı 90 gün kullanılamaz hâle getirme riski alınıyor. Ayrıca config'deki iki ayarın ölü olması, ayarı değiştiren birine sahte bir kontrol hissi veriyor.
- Onerilen duzeltme: (a) PDTTracker sınıf sabitlerini config'ten beslesin (`PDTTracker(equity, max_dt=config["max_day_trades_per_week"], threshold=config["pdt_equity_threshold"])`) — ölü ayar kalmasın. (b) $25k altındaki hesapta haftalık kota tükendiğinde ALIM da bloklansın (aynı gün içinde TP/SL'in dolma ihtimali var), en azından bracket yerine sadece GTC stop kullanılsın. (c) Hesabın cash mı margin mi olduğu başlangıçta Alpaca'dan okunup log'lansın; cash hesapta PDT yerine T+1 takas/free-riding kuralı geçerli ve kodda takas edilmemiş nakit takibi HİÇ yok. (d) 7 takvim günü yerine gerçek 5 iş günü penceresi kullanılsın.

### GATE-EMA200-TARAYICIYLA-TERS-YON  (Kapilar/Huni)
**EMA200 kapısı doğru yönde ama tarayıcı+TechAgent ters yönde: huni kendi kendini iptal ediyor**

- Yer: `core/trade_gates.py:79-82 + core/stock_screener.py:189-229 + core/agent_coordinator.py:55-116 + stock_bot.py:1410-1421`
- Ne oluyor: Kapının mantığı DOĞRU: `if not analysis.get("above_ema200", True): return False, "EMA200"` — fiyatın günlük EMA200 üstünde olmasını istiyor, tolerans/eşik yok, veri yolu da sağlam (400 günlük günlük bar, 275 bar döndü, hata yok, hesap doğru). Sorun kapının kendisi değil, önündeki seçici. Bot her turda yalnız tarayıcının top-10'unu analiz ediyor (stock_bot.py:1414-1418 `sorted(...)[:10]`). Tarayıcı puanlaması düşen hisseyi ödüllendiriyor: RSI<30 → +25, RSI<40 → +10, 5 günlük düşüş → +10 'dip fırsatı', RSI>70 → −15. TechAgent de aynı yönde: RSI<25 → +25, BB alt bant altı → +8. Yani sinyal üreteci bir dip-alıcı, kapı ise bir trend-takipçisi; ikisi yapısal olarak zıt.
- Neden onemli: 08-21'de paper'da 1122 sinyalin 1122'sinin EMA200'e takılması bir veri hatası değil, bu ters yönlülüğün doğal sonucu. Bugün ölçtüğüm tarayıcı top-10'u: MARA(25), META(20), RIVN(15), SMCI(10), QQQ(10), NVDA(10), LCID(10), COIN(10), AAPL(10), SOFI(5). Bunlardan META, LCID, COIN, SOFI, MARA EMA200 ALTINDA (yani kesin blok); QQQ endeks (asla işlenmez); NVDA zaten pozisyonda (atlanır). Geriye alınabilir 3 isim kalıyor. Oysa aynı 20 sembollük evrende 12 isim EMA200 ÜSTÜNDE — bot onları taramıyor bile. Sermaye 'sinyal yok'tan değil, 'yanlış listeye bakmaktan' boşta duruyor.
- Onerilen duzeltme: Tarayıcıya EMA200 ön-filtresi koy: `above_ema200 == False` olan sembol top-10'a hiç girmesin (veya puanından −40 alsın). Böylece 10 tarama slotu alınabilir isimlere ayrılır ve huni gerçek fırsatı ölçmeye başlar. Alternatif (daha köklü): stratejik kimliğe karar ver — ya dip-alıcı olup EMA200 kapısını 'fiyat > EMA200 × 0.97' gibi toleranslı bir rejim filtresine çevir, ya trend-takipçisi olup tarayıcıdaki RSI-düşük ödülünü kaldır. İkisini aynı anda çalıştırmak yapısal sıfır işlem üretiyor.

### GATE-WASH-SALE-SAYACSIZ-30GUN-YASAK  (Kapilar/Huni)
**Wash-sale 30 günlük sembol yasağı paper'da da uygulanıyor, hiçbir huni sayacında görünmüyor**

- Yer: `stock_bot.py:665-670 + core/executor.py:601-605 + core/compliance.py:57-100`
- Ne oluyor: Her zararlı çıkışta `record_loss_sale` çağrılıyor ve o sembol 30 gün 'wash window'a giriyor. Ana döngüde bu kontrol `_analyze_and_trade`'den ÖNCE yapılıyor: `is_wash, wash_reason = self.wash_sale_tracker.check_wash_sale(symbol); if is_wash: continue`. Yani sembol taranmıyor bile — `scanned`, `signal_buy`, `gate_block` sayaçlarının HİÇBİRİ artmıyor. Blok tamamen görünmez. Ve kontrol paper/live ayrımı yapmıyor (compliance.py:81 sadece kriptoyu muaf tutuyor).
- Neden onemli: Paper hesap sahte para; wash-sale bir VERGİ kuralı ve paper'da hiçbir anlamı yok — ama orada da her zararlı çıkış evreni daraltıyor. Paper'ın kapalı işlem defterinde 8 farklı sembol zararla kapandı (SPY, AMD, NVDA, RIVN, GOOGL, META, AMZN, SMCI). 20 sembollük evrenin %40'ı dönüşümlü olarak yasaklı demek; üstelik bot yalnız top-10'a baktığı için etkin daralma daha da sert. 14 Ağustos AMZN (−$127.98) ve 17 Ağustos SMCI (−$97.03) çıkışları bu iki ismi Eylül ortasına kadar sildi. Bu, 'neden giriş yok' sorusunun cevabının bir parçası ve hiçbir raporda görünmüyor — yanlış-yeşil üreten sessiz bir kapı.
- Onerilen duzeltme: (1) Paper modda wash-sale kontrolünü tamamen devre dışı bırak (`if not bot.is_paper`) — paper'ın işi örnek üretmek. (2) Canlıda kalsın ama `_funnel_bump("gate_block", reason="WASH_SALE")` ile sayaca yaz ve günlük raporda kaç sembolün yasaklı olduğunu göster. (3) Canlıda bile bu bir 'uyar' kararı olmalı, 'blokla' değil — zarar vergiden düşülemiyor diye kârlı bir işlemi kaçırmak net negatif olabilir; kararı İhsan'a bırak.

### GATE-HUNI-SAYACLARI-KARSILASTIRILAMAZ  (Kapilar/Huni)
**conf_below_min BUY ve SHORT redlerini karıştırıyor, index sinyalleri sayaçsız kaçıyor, sembol başına soğuma yok — huni teşhis için kullanılamaz**

- Yer: `stock_bot.py:906-915, 940-954, 974-985 + config.py:439-442`
- Ne oluyor: Üç ayrı bozukluk aynı dosyada: (1) `conf_below_min` hem BUY-altı hem SHORT-altı reddi tek kovaya yazıyor ve BOT_MODE kontrolü yok — canlı bot `long_only` olduğu halde asla işlenmeyecek SHORT redleri de sayılıyor, bu yüzden bazı günlerde conf_below_min > signal_buy oluyor (08-06: signal_buy=42, conf_below_min=204). Çift sayım DEĞİL (if/elif), etiket hatası. (2) `scanned` ve `signal_buy/signal_sell` bump'ları satır 908-915'te, index/ters-ETF erken çıkışı ise satır 946-954'te — yani SPY/QQQ ve SQQQ/SH/SPXS sinyalleri sayacı şişirip HİÇBİR blok sayacına düşmeden return ediyor (bugün tarayıcı top-10'unda QQQ var, yani bu her turda oluyor). (3) Sembol başına soğuma yok: `min_interval_high_conf/med/low` config'de tanımlı ama repoda hiçbir yerde OKUNMUYOR (ölü ayar), `last_trade_time` yazılıyor ama hiç okunmuyor. 10 sembol × ~120 tur/gün = aynı sembol günde ~120 kez sayılıyor.
- Neden onemli: '08-21'de 1122 sinyalin 1122'si EMA200' cümlesi 1122 fırsat değil, ~9 sembolün 120 kez tekrar sayılması. Kapı atfı ve 'kaç işlem kaçırdık' hesabı bu sayaçlarla yapılamaz. Bu doğrudan yanlış-yeşil riski: ölçüm kapısı ve watchdog aynı sayaçlara bakıyor, karar bu sayılara dayanıyor.
- Onerilen duzeltme: (1) `conf_below_min`'i `conf_below_min_buy` / `conf_below_min_short` olarak ayır ve BOT_MODE'un kapattığı yönü hiç sayma. (2) index/ters-ETF erken çıkışını funnel bump'larından ÖNCEYE al (satır 908'in üstüne). (3) Sembol+gün başına tekilleştirme ekle: aynı sembol için aynı blok sebebini günde bir kez say (ör. `day.setdefault("gate_block_symbols", {})[reason] = set(...)`), ham tekrar sayısını ayrı bir alanda tut. Tekilleştirilmiş sayı olmadan hiçbir kapı kararı verilmemeli.

### GATE-LIVE-LOCK-ZINCIRIN-SONUNDA  (Kapilar/Huni)
**LIVE_LOCK_R5 kapı zincirinin en sonunda: 17 günde yalnız 7 kez tetiklendi, teşhis gücü sıfır**

- Yer: `core/executor.py:134-158 + core/trade_gates.py:56-154 + stock_bot.py:1030`
- Ne oluyor: Kapıların gerçek çalışma sırası şu (ilk blok akışı bitirir): [ana döngü] pozisyonda mı → sektör tavanı → wash-sale (üçü de SAYAÇSIZ) → [_analyze_and_trade] güven eşiği (conf_below_min) → sektör rotasyonu (sector_block) → [check_all_gates] 1.MARKET_CLOSED, 2.EMA200, 3.FUND (canlıda kapalı), 4.EARNINGS, 5.LOSS_STREAK, 6.STOCK_FILTER, 7.RR_GATE, 8.MTF, 9.VOLATILITY, 10.PDT (blok etmiyor, sadece log) → [execute_buy] LIVE_LOCK_R5 → equity floor → market saati → nakit rezerv → boyutlandırma → bracket. LIVE_LOCK_R5 execute_buy'ın İLK satırı ama execute_buy zincirin en sonunda çağrılıyor (stock_bot.py:1056). Bu yüzden 17 günde toplam 7 kez sayılmış: ona ulaşan sinyal sayısı bu.
- Neden onemli: Kilit bilinçli bir güvenlik kararı (I-13/R5), kusur değil. Kusur, kilidin konumunun teşhisi bozması: 'canlıda 0 giriş' tablosuna bakan biri LIVE_LOCK_R5'in 7 olduğunu görüp 'demek ki kilit sebep değil, sinyal yok' diyebilir; gerçek şu ki kilit AÇILSA bile 17 günde en fazla 7 giriş DENEMESİ olurdu ve onların çoğu kesirli-bracket reddine (bkz. GATE-LIVE-FRACTIONAL-BRACKET-OLU-KAPI) takılırdı. Yani ölçüm kapısı 'kilidi aç' dediğinde canlı hesap yine hareketsiz kalacak ve sebep anlaşılamayacak.
- Onerilen duzeltme: Kilit kontrolünü zincirin BAŞINA da bir 'kuru koşu' olarak ekle: canlıda kilit kapalıyken sinyal tüm kapılardan geçerse `gate_block reason="LIVE_LOCK_R5_WOULD_ENTER"` yerine ayrı bir `would_enter` sayacı artır ve emri gönderme. Böylece 'kilit açılsa kaç giriş olurdu' ölçülebilir hale gelir — R5 kapısının açılıp açılmayacağına bu sayı ile karar verilir. Ayrıca index_parking'in canlı emirleri kilitten muaf olması bilinçli mi, kayıt altına alınmalı: canlı hesabın TEK pozisyonu (SPY 0.4517) bu muafiyetin ürünü.

### METRIK3-TAUTOLOJI  (Olcum Sistemi)
**Metrik-3 (never-green) yapısal olarak reddedilemez; AMZN −$127.98 ile 'yeşile geçmiş' sayılıyor**

- Yer: `tools/olcum_raporu.py:818-834 (özellikle 819, 823-825)`
- Ne oluyor: `never_green = [t for t in known if t.peak_pct <= 0]`; `peak_pct` `attach_peaks` (satır 293-333) ile dakika barlarının EN YÜKSEK high'ından hesaplanıyor. Yani bir işlemin 'asla yeşil olmaması' için giriş fiyatının 1 sent bile üstüne çıkmaması gerekiyor. Gün içi market emriyle girilen bir pozisyonda bu pratik olarak imkânsız. İkinci koşul `loss_share = never_losses / total_losses` da otomatik olarak 0.0 oluyor çünkü never_green listesi boş (satır 822-823).
- Neden onemli: Kapının 4 metriğinden biri hiçbir veri kombinasyonunda FAIL veremiyor; PASS sayısı 4/4 değil fiilen 3/4 üzerinden okunuyor. Somut: AMZN 04.08→14.08 arasında ölçülen tepe +%1,92 (yaklaşık +$48 kâğıt kâr) ve sonra −$127,98 kapandı; metrik-3 bunu 'yeşile geçmiş, sorun yok' sayıyor. SMCI 08-17 de tepe +%2,17 → −$96,72. Yani dönemin iki büyük kaybı da metriğe hiç dokunmuyor. Metrik-3'ün asıl sorması gereken 'işlem kademeli satış eşiğine (+%3) ulaşabildi mi / MFE-MAE oranı ne' sorusu hiç sorulmuyor.
- Onerilen duzeltme: Eşiği anlamlı bir yere taşı: `never_green = [t for t in known if t.peak_pct < config_partial_profit_pct]` (yani '+%3 kademeli satış eşiğine hiç ulaşamayan işlem'). Böylece AMZN ve SMCI 08-17 sayılır, oran 2/6 = %33 > %20 → doğru şekilde FAIL. Ek olarak `loss_share`'i sadece never_green kaybıyla değil tüm kayıpların MAE dağılımıyla hesapla.

### METRIK2-PAYDA-KAPALI-ISLEM-KUMESI-DEGIL  (Olcum Sistemi)
**Metrik-2'nin 4/5 paydası 6 işlemden gelmiyor: hâlâ AÇIK olan PLTR pozisyonunun yarı satışı payı şişiriyor**

- Yer: `tools/olcum_raporu.py:616-664 (631-633, 641-645, 647-663), 583, 37`
- Ne oluyor: `evaluate_partial_metric` paydayı üç ayrı kaynaktan kuruyor: (a) kapalı işleme eşleşen telemetri epizotları (satır 620-624), (b) telemetrisi olmayan ama bar/log +%3 kanıtı olan ve kanıt zamanı `telemetry_start`'tan ÖNCE olan işlemler (satır 641-643, 'legacy miss'), (c) hiçbir kapalı işleme eşleşMEYEN telemetri epizotları (satır 647-663) — bunlar hâlâ açık pozisyonların yarı satışları olabilir ve her biri payda+1, dolum eşleşirse pay+1 yazıyor. Tepe +%3'ün altında kalan işlemler (satır 631-633) paydadan tamamen düşüyor.
- Neden onemli: Kapı 'kapalı işlemlerin ≥%60'ında +%3 bacağı doldu mu' diye okunuyor ama ölçülen küme bu değil. Broker fill'lerinden saydım: 6 kapalı işlemin yalnız 3'ünde gerçek bir +%3 yarı satışı var (SMCI 08-12, PLTR 08-14, AAPL 08-21). Rapordaki 4. isabet zorunlu olarak (c) yolundan geliyor — 19.08'de alınan 14 PLTR'nin 21.08'de 7'sinin $179,3671'e (+%3,10) satılması; o pozisyon hâlâ AÇIK, kapalı işlem kümesinde değil. Paydadaki 5. kalem ise PLTR 08-07: tepe +%6,38 olmasına rağmen HİÇ yarı satış yapılmamış gerçek bir kaçırma, ama satır 37'deki sabit `TELEMETRY_START = 2026-08-09 18:03:27` afı sayesinde 'legacy' etiketiyle sert FAIL'e (event_completeness) dönüşmüyor. Bu haliyle metrik-2, açık pozisyon epizotları eklenerek istenen yöne kaydırılabiliyor.
- Onerilen duzeltme: Paydayı tanıma sadık tut: yalnız ölçüm penceresinde KAPALI olan ve tepesi eşiği geçmiş işlemler sayılsın; `unmatched` epizot bloğunu (satır 647-663) paydadan çıkar veya ayrı bir 'açık pozisyon epizotları' satırı olarak bilgi amaçlı bas. Satır 583'teki `target <= 0 or` kısa devresini kaldır (target yoksa isabet değil, VERİ EKSİK sayılmalı). Satır 37'deki sabit af tarihini kaldırıp legacy kaçırmaları da normal kaçırma olarak say.

### RAPOR-BENCHMARK-VE-EQUITY-OKUMUYOR  (Olcum Sistemi)
**Rapor SPY/benchmark, equity ve drawdown okumuyor — hesap iki ayda SPY'ın 5,9 puan gerisindeyken 4/4 PASS verebiliyor**

- Yer: `tools/olcum_raporu.py:783-784 (metric1_ok) ve dosyanın tamamı`
- Ne oluyor: Dosyada `SPY`, `benchmark`, `equity`, `drawdown`, `get_portfolio_history` geçen tek bir satır yok (grep: 0 sonuç). Metrik-1'in tamamı `net_pnl = sum(trade.pnl for trade in period)` ve `metric1_ok = broker_available and net_pnl > 0` — 6 sayının toplamının işaret testi. Ne beklenen değer (expectancy), ne işlem başına R, ne kazanan/kaybeden asimetrisi, ne drawdown, ne de piyasaya göreli getiri ölçülüyor. Rapor Alpaca'ya bağlanıyor ama sadece emirleri ve barları çekiyor; hesap equity'sine hiç bakmıyor.
- Neden onemli: Bu, kapının en büyük yapısal kör noktası. Ölçülen: portföy 2026-06-23 $64.247,28 → 2026-08-22 $63.252,58 = −%1,55; aynı dönem SPY +%4,36. Ölçüm penceresinde bot +%2,89, SPY +%3,24. Kapı 'net PnL>0' dediği için, piyasa +%3 giderken hesabın gerisinde kalması PASS'i hiç etkilemiyor. Dahası metrik-1 yalnız 6 strateji döngüsünü ölçüyor; aynı hesapta SPY park kolu ölçüm penceresinde 20 kez alınıp satıldı (ölçüldü) ve 5 açık mega-cap long ~$41.810 tutuyor — yani raporun 'PnL' dediği şey hesabın gerçek performansını tarif etmiyor. İstatistiksel olarak da n=6'da işaret testi anlamsız: 4 kazanan/2 kaybeden için binom p≈0,34; net PnL'in %87'si tek işlemden (SMCI +189,48) geliyor, o tek işlem çıkarılınca +29,41 kalıyor.
- Onerilen duzeltme: Metrik-1'i mutlak işaret testinden çıkar: (a) aynı pencerede `client.get_portfolio_history` ile hesap getirisini ve SPY bar getirisini çek, PASS koşulunu 'strateji getirisi ≥ SPY getirisi' (veya en az alfa ≥ 0) yap; (b) net PnL yanında işlem başına beklenen değer, kazanan/kaybeden ortalaması ve en büyük kazananın net PnL'deki payını bas — tek işlemin metriği taşıdığı durumda uyar; (c) n=6 gibi örneklemlerde 'PASS' yerine 'KARARSIZ' kategorisi tanımla.

### SAGLIK-KONTROLU-KOSULSUZ-YESIL  (Saglamlik/Altyapi)
**health_check heartbeat tazeyse koşulsuz "BOT SAGLIKLI" diyor — 5 haftadır sıfır giriş yapan bot da yeşil**

- Yer: `health_check.py:172-181`
- Ne oluyor: Sonuç bloğu: `if loop_alive:` içinde üç dalın hepsi "✅ BOT SAGLIKLI" basıyor ve blok `bot_alive = True` ile kapanıyor (181). Yani `loop_alive` (heartbeat.json 30 dakikadan taze mi) True olduğu sürece işlemsizlik ASLA kırmızıya dönüşmüyor; `--alert` eşiği (varsayılan 12 saat) bu dalda tamamen devre dışı. Script exit kodu da bu değerden geliyor (`sys.exit(0 if healthy else 1)`, satır 208).
- Neden onemli: Operatörün ve dış izleme/cron'un gördüğü tek özet sinyal bu. Canlı 5 haftadır (25+ işlem günü) hiç giriş yapmadı; health_check bu süre boyunca exit 0 ve "✅ BOT SAGLIKLI (döngü canlı) — ℹ️ son 7 günde hiç işlem yok" döndürdü. Sağlamlık açısından bu tam bir yanlış-yeşil: alt sistem kilitlenmiş, üst sistem yeşil. v4.10 yorumu "seçicilik canlılık değildir" derken haklı, ama düzeltme aşırıya kaçmış — artık HİÇBİR işlemsizlik süresi kırmızı üretmiyor.
- Onerilen duzeltme: İki boyutlu sonuç ver: DÖNGÜ (canlı/durmuş) ve GİRİŞ ÜRETİMİ (normal/kilitli). İkinci boyut için üst eşik koy — ör. `no_trade_alert_business_days`ın 3 katı (9 iş günü) işlemsizlik → 🔴 ve exit 1, mesajda huniden baskın gate sebebi. Şu anki üç dal da aynı emoji ve aynı exit kodunu üretiyor, bu yüzden hiçbir cron bunu yakalayamaz.

### HUNI-CONF-SAYACI-KIRLI-NOTRADE-TESHISI-YANLIS  (Saglamlik/Altyapi)
**conf_below_min sayacı long_only konteynerde bile SHORT retlerini sayıyor; NO_TRADE alarmı bu yüzden yanlış baskın aşamayı bildiriyor**

- Yer: `stock_bot.py:911-916, 932-933, 975-985 + core/funnel.py:305-322,357-364`
- Ne oluyor: Akış sırası kritik: (1) 911-916'da sinyal aşaması yazılıyor — SELL için `signal_sell`. (2) 932-933'te BOT_MODE'a BAKMADAN sinyal dönüştürülüyor: `if decision["signal"] == "SELL" and symbol not in self.positions: decision["signal"] = "SHORT"`. (3) 982-985'te yine BOT_MODE'a BAKMADAN `elif decision["signal"] == "SHORT" and decision["confidence"] < effective_short_conf: self._funnel_bump("conf_below_min")`. Yani aynı sembol hem `signal_sell` hem `conf_below_min` sayılıyor. Canlı konteyner BOT_MODE=long_only olduğu için bu SHORT'lar zaten hiçbir zaman işleme dönüşemez (1093-1096'daki SHORT kolu `BOT_MODE in ("short_only","both")` istiyor) — ama sayaçta duruyorlar. NO_TRADE alarmının teşhis satırı `dominant_stage` ile en yüksek sayılı aşamayı seçiyor (funnel.py:305-322 `max(..., key=lambda item: item[1])`).
- Neden onemli: Görevde işaret edilen tutarsızlığın kaynağı tam olarak budur: 08-06'da signal_buy=42 iken conf_below_min=204; 08-14'te signal_buy=294, conf_below_min=346. Aradaki fark SELL→SHORT'a çevrilip long_only botta anlamsız olan retler. Sonuç ikili: (a) huni okunamaz hale geliyor, (b) günlük NO_TRADE alarmı "Baskin funnel asamasi: conf_below_min (168)" diyor — oysa long tarafındaki gerçek engeller EMA200 (63) ve LOSS_STREAK_WARN. Yani 5 hafta boyunca her gün ateşlenen alarm operatörü YANLIŞ yöne bakmaya itti.
- Onerilen duzeltme: (a) `conf_below_min`'i yönlere ayır: `conf_below_min_long` / `conf_below_min_short`, ve SHORT sayacını `BOT_MODE in ("short_only","both") and SHORT_CONFIG["short_enabled"]` koşuluna bağla. (b) SELL→SHORT dönüşümünü de aynı koşula bağla — long_only botta SHORT sinyali üretmenin hiçbir karşılığı yok. (c) `dominant_stage`'i sadece o konteynerin gerçekten işleyebileceği aşamalar üzerinden hesapla.

### NOTRADE-ALARM-YORGUNLUGU-VE-TESLIM-ONCESI-DEDUPE  (Saglamlik/Altyapi)
**NO_TRADE alarmı 5 haftadır her gün aynı metinle ateşleniyor; dedupe işareti teslimden ÖNCE yazıldığı için teslim hatasında o günün alarmı kalıcı kayboluyor**

- Yer: `core/funnel.py:340-383 (özellikle 371-379) + stock_bot.py:2143-2144,2178-2186 + core/ntfy_notifier.py:100-106`
- Ne oluyor: Alarm gerçekten ateşleniyor: `_daily_reset` içinde `funnel.maybe_notify_no_trade(...)` (stock_bot.py:2178) çağrılıyor, eşik `no_trade_alert_business_days: 3` (config.py:303). Ama üç kusur var. (1) Dedupe işareti teslimden ÖNCE kalıcılaştırılıyor: `self.last_no_trade_alarm_date = today_str; self._persist(force=True)` (funnel.py:373-374) satırlarından SONRA `notifier.notify_critical(...)` çağrılıyor (376) ve o çağrının istisnası `logger.debug` ile yutuluyor (377-378). Teslim başarısızsa o günün alarmı bir daha ASLA denenmez. (2) Alarm `if self._daily_reset_date is not None:` bloğunun içinde (stock_bot.py:2144) — yani her konteyner restart'ından sonraki İLK gün devri alarm üretmez. (3) Mesaj gövdesi 5 hafta boyunca sabit; ne şiddet artışı ne de "kaç gündür" temelli bir tırmanma var — 25+ özdeş bildirim.
- Neden onemli: Alarm teknik olarak çalışıyor ama operasyonel olarak ölü. 25 gün üst üste gelen, gövdesi değişmeyen ve (üstteki bulgu yüzünden) yanlış baskın aşamayı gösteren bir bildirim, tanımı gereği görmezden gelinir. Nitekim 5 hafta boyunca kimse müdahale etmedi. Ayrıca (1) yüzünden alarm "en fazla bir kez" değil "en fazla bir DENEME" garantisi veriyor — ntfy/Telegram bir gün düşerse o gün tamamen sessiz.
- Onerilen duzeltme: (a) Dedupe işaretini teslim SONUCUNA bağla: `result.direct_delivered` veya `persisted` doğruysa işaretle; ikisi de başarısızsa bir sonraki turda tekrar dene. (b) Tırmanma ekle: 3 iş günü INFO, 5 gün WARN, 10+ gün ayrı bir kritik tür ("NO_TRADE_KRONIK") + mesajda "N gündür, baskın engel X, N gün önce Y idi" karşılaştırması. (c) Restart sonrası ilk devri atlamayı düzelt — `_daily_reset_date is None` durumunda funnel'daki `last_entry_date`'ten yaşı hesaplayıp alarmı yine de üret.

### KOPRU-ALARM-COOLDOWNUNU-BYPASS-EDIYOR  (Saglamlik/Altyapi)
**Cooldown ile bastırılan alarmlar yine de alarms.jsonl'e yazılıyor ve DELIVERY işareti almadığı için VPS köprüsü hepsini gönderiyor — cooldown fiilen yok**

- Yer: `core/ntfy_notifier.py:180-206,214-221 + tools/vps_bridge_patch.md:16-46 + core/protection.py:442-456`
- Ne oluyor: `publish()` önce kaydı yazıyor (`persisted = self._append_record(record)`, satır 180) SONRA cooldown'a bakıyor (`if self._in_cooldown(key, now):` 187) ve bastırılan alarmda `direct_delivered=False` ile erken dönüyor (194-206) — DELIVERY işareti YAZILMIYOR (o yalnız `if ntfy_delivered:` dalında, 216-217). VPS köprüsünün filtresi ise (tools/vps_bridge_patch.md içindeki `ntfy_undelivered.py`) "DELIVERY referansı olmayan her kaydı geçir" mantığında. Yani bot'un bastırdığı her alarm köprü tarafından yine de telefona itiliyor.
- Neden onemli: `protection_alarm` kendi içinde 900 saniyelik dedupe uyguluyor (protection.py:442, `dedupe_seconds: int = 900`) ve publisher 4 saatlik cooldown vaat ediyor (`DEFAULT_COOLDOWN = timedelta(hours=4)`, ntfy_notifier.py:20). Kalıcı bir durum (ör. korumasız pozisyon) için beklenen gün başına ~6 bildirim; gerçekte köprü 15 dakikada bir yeni kayıt gördüğü için günde ~96 bildirim gider. Bu, kanalı boğarak GERÇEK bir korumasız pozisyon alarmını görünmez kılar — protection.py:477-482'deki yorum bu riski zaten tarif ediyor ama çözüm cooldown'un köprü tarafından delindiğini hesaba katmıyor. Ayrıca alarms.jsonl'i budayan hiçbir kod yok; köprü her koşusunda dosyanın TAMAMINI tarıyor.
- Onerilen duzeltme: (a) Cooldown ile bastırılan kayda `"suppressed": true` alanı ekle ve köprü filtresine bu alanı atlatacak kuralı yaz — ya da bastırılan alarmı hiç yazma, bunun yerine mevcut kaydın sayaç alanını güncelle. (b) `_delivered_at` sözlüğünü diske persist et; şu an in-memory (ntfy_notifier.py:45) olduğu için her restart cooldown'u sıfırlıyor. (c) alarms.jsonl için boyut/gün bazlı rotasyon ekle.

### CONSECUTIVE-ERRORS-BASARIDA-SIFIRLANMIYOR  (Saglamlik/Altyapi)
**StockBot.consecutive_errors başarılı turda hiç sıfırlanmıyor ve emir retleriyle de besleniyor: rastgele bir anda 300 saniyelik ticaret durması + sahte "5 ardışık hata" alarmı**

- Yer: `stock_bot.py:211,696,700-705 + core/executor.py:411`
- Ne oluyor: Sayaç `__init__`'te 0 (211), ana döngü istisnasında artıyor (696) ve YALNIZCA eşiği aştıktan sonra sıfırlanıyor (705). Başarılı bir tur onu sıfırlamıyor — kodda `self.consecutive_errors = 0` sadece 211 ve 705'te var. Üstelik executor başarısız her BUY'da aynı sayacı artırıyor: `bot.consecutive_errors += 1` (executor.py:411), ki bu "ana döngü hatası" değil, sıradan bir emir reddi (fractional reddi, PDT, yetersiz bakiye).
- Neden onemli: Sayaç "ardışık" değil KÜMÜLATİF. Günler içinde birikmiş 5 alakasız olay, tamamen sağlıklı bir anda `MAIN_LOOP_ERROR` kritik alarmını ateşliyor ve `time.sleep(300)` ile botu 5 DAKİKA piyasa saatinde durduruyor (702-704) — bu sürede pozisyon yönetimi de çalışmıyor. Yani hem sahte alarm (alarm yorgunluğuna katkı) hem gerçek işlem kaybı. Alarm metni de yanlış bilgi veriyor: "Ana dongude {error_count} ardisik hata" (stock_bot.py:382-384) — hiçbiri ardışık değil.
- Onerilen duzeltme: (a) Döngü sonunda (`time.sleep(interval)` öncesi) `self.consecutive_errors = 0` — tur hatasız bittiyse seri kırılmıştır. (b) Emir reddini bu sayaçtan ayır; executor.py:411'i kaldır veya ayrı bir `order_reject_count`'a yaz. (c) 300 saniyelik uyku yerine bir sonraki turu normal aralıkla dene, alarmı yine bas — piyasa saatinde 5 dakika durmanın gerekçesi yok.

### SESSIZ-YUTMALAR-YALNIZ-DEBUGA-GIDIYOR-LOG-ROTASYONU-YOK  (Saglamlik/Altyapi)
**118 sessiz yutmanın tamamı DEBUG'a yazıyor ama konsol handler INFO'da; dosya handler ise konteyner başlangıç tarihinde donmuş ve hiç dönmüyor**

- Yer: `utils/logger.py:44-60 + config.py:628 + Dockerfile CMD + docker-compose.yml (logs mount)`
- Ne oluyor: Konsol handler seviyesi config'den geliyor: `console_handler.setLevel(getattr(logging, LOG_CONFIG.get("log_level", "INFO")))` (utils/logger.py:59) ve `config.py:628` `"log_level": "INFO"`. Dosya handler DEBUG yazıyor ama adı import anında sabitleniyor: `today = datetime.now().strftime("%Y-%m-%d")` → `FileHandler(os.path.join(log_dir, f"bot_{today}.log"))` (utils/logger.py:47-50). Rotasyon (TimedRotatingFileHandler vb.) yok. Konteyner haftalarca ayakta kaldığı için tüm günlerin logu tek ve yanlış tarihli dosyaya yazılıyor, sınırsız büyüyor. Coolify'ın gösterdiği stdout ise yalnız INFO.
- Neden onemli: Bu bulgu diğer tüm sessiz-yutma bulgularının ÇARPANI. Repoda `except ...: pass/continue` deseni 118 yerde (`grep -rn "except.*:$" --include=*.py . -A1 | grep -E "pass$|continue$" | wc -l` → 118), bunların 21'i stock_bot.py'de. Kritik yollardaki teşhis satırlarının hepsi `logger.debug`: bar verisi hatası (1361), günlük EMA200 hatası (1386), sembol analiz hatası (1127), ajan karar hatası (1257), pozisyon metadata yüklenemedi (2088), NO_TRADE teslim hatası (funnel.py:377). Operatör Coolify loglarına baktığında bunların HİÇBİRİNİ göremez; görebileceği tek yer, tarihi yanlış ve rotasyonsuz bir dosya. Yani "sessiz başarısızlık" burada bir tasarım sonucu değil, iki ayarın çarpışması.
- Onerilen duzeltme: (a) Dosya handler'ı `TimedRotatingFileHandler(when="midnight", backupCount=14)` yap. (b) Karar yolundaki yutmaları DEBUG'dan WARNING'e çıkar (bar verisi, EMA200, metadata yükleme, ajan karar hatası) — bunlar "gürültü" değil, karar kalitesini değiştiren olaylar. (c) Sembol/sağlayıcı bazlı hata sayaçları tut ve gün sonunda huni raporuna ekle; tek tek satır basmak yerine agregeyi INFO'ya çıkar.

### DIS-VERI-SAGLAYICILARI-UCU-OLU-VE-DONGUYU-BLOKLUYOR  (Saglamlik/Altyapi)
**Sosyal (Reddit/X) ve makro (FRED) sağlayıcıları üretimde veri üretmiyor; Reddit yolu sembol başına 12 saniye ana döngüyü blokluyor, makro yanlış seriyi okuyor**

- Yer: `core/social_sentiment.py:148-190 + core/macro_data.py:62-140,271-296 + .env (FRED_API_KEY yok)`
- Ne oluyor: SOSYAL: `_analyze_reddit` 6 sub × 2 terim = 12 iterasyon dönüyor, her birinde `requests.get(..., timeout=10)` + `time.sleep(1)  # Reddit rate limit` (satır 188). Kimlik doğrulaması yok, User-Agent `"StockBot/1.0"` (satır 161) — Reddit'in kimliksiz .json uçları veri merkezi IP'lerinden 403/429 döndürüyor; hata `logger.debug(f"Reddit {sub}/{term} hatasi: {e}")` (190) ile yutuluyor. X yolu ntscraper/Nitter'e bağlı ve kod "empty sequence" hatasını BEKLENEN davranış olarak hardcode etmiş (satır 227-229). MAKRO: `.env`'de FRED_API_KEY YOK → `get_fred_series` hemen `_get_fallback_data`'ya düşüyor (macro_data.py:81-82); fallback ise SADECE DGS10 için bir dal içeriyor (satır 122), FEDFUNDS/CPIAUCSL/UNRATE için boş liste dönüyor ve önbelleklenmiyor. DGS10 dalı da yanlış seriyi okuyor: `avg_interest_rate_amt` (satır 133) = ABD federal borcunun ORTALAMA faiz oranı, 10 yıllık tahvil getirisi değil.
- Neden onemli: (1) SocialAgent kalıcı olarak `social_score=0` → HOLD + güven 0 → çoğunluk kuralının ölmesine doğrudan katkı (bkz. AJAN-COGUNLUK bulgusu). (2) Zaman: 10 sembol × 12 sn = her 15 dakikada 120 SANİYE ölü blokaj, sıfır veri karşılığında. Fundamental'ın 150 sn'siyle birlikte tur süresi dakikalara çıkıyor; `_manage_positions` tur başına bir kez koştuğu için çıkış tepkisi bu kadar gecikiyor. (3) Makro: `get_macro_score` ağırlıkları faiz %30 + enflasyon %20 + dolar %20 + VIX %30 (macro_data.py:283-288); üçü 0 olduğu için makro skoru fiilen `0.30 × VIX skoru`. Başlangıç logu "MacroData baslatildi - FRED key yok, alternatif kaynaklar" (satır 68) diyor — 4 serinin 3'ü için alternatif kaynak YOK, log yanıltıyor.
- Onerilen duzeltme: (a) Reddit'i ya OAuth ile düzgün kur ya da tamamen kapat — 12 saniye blokaj + 0 veri kabul edilemez; kapatılacaksa SocialAgent'ı oylamadan çıkar ve ağırlığı yeniden dağıt. (b) Tüm sağlayıcı çağrılarını ana döngüden ayır (arka plan yenileyici + son bilinen değer + tazelik damgası). (c) FRED anahtarını al (ücretsiz) ya da FEDFUNDS/CPI/UNRATE bileşenlerini ağırlıklandırmadan ÇIKAR — şu an 0 skorları "nötr veri" gibi ağırlığa giriyor, oysa "veri yok". (d) `analyze_dollar_strength`'in okuduğu seriyi düzelt veya fonksiyonu devre dışı bırak.

### OLCUM-ARACI-SEMBOL-FILTRESIZ-YANLIS-YESIL  (Saglamlik/Altyapi)
**Ölçüm raporu tüm broker fill'lerinden PnL kuruyor — index-parking SPY döngüleri dahil; bot defteriyle hiçbir mutabakat ve sapma alarmı yok**

- Yer: `tools/olcum_raporu.py:236-292,772-788 + config.py:358-361 + core/index_parking.py:212`
- Ne oluyor: `reconstruct_closed_trades(fills)` gelen TÜM fill'leri sembol bazında eşleştiriyor; hiçbir sembol dışlaması yok (`grep -n "parking\|exclude\|filter" tools/olcum_raporu.py` → yalnız `period = [trade for trade in trades if trade.closed_at >= since]` tarzı TARİH filtreleri). Metrik 1 doğrudan bu toplamı kullanıyor: `net_pnl = sum(trade.pnl for trade in period)` / `metric1_ok = broker_available and net_pnl > 0` (782-784). Oysa bot'un kendi defteri `state_*/trade_history.json` yalnız strateji çıkışlarını kaydediyor ve `_analyze_and_trade` parking sembolünü stratejiden açıkça DIŞLIYOR (stock_bot.py:900-902 `if self.index_parking.is_parking_symbol(symbol): return`).
- Neden onemli: Rapor "GENEL: PASS — n=6, net $+218.89" derken defter aynı 6 işlem için -$64.04 diyor; $283 fark. index_parking paper'da ve canlıda AÇIK (`index_parking_enabled: True` config.py:358, `index_parking_allow_live: True` config.py:361) ve kendi `submit_order`'ını kullanıyor (index_parking.py:212) — yani nakit sleeve'inin SPY betası strateji PnL'i olarak sayılıyor. Bu, 4/4 PASS kapısını yanlış-yeşile çeviriyor ve o kapı canlı kalibrasyonun ön koşulu: kaybeden bir strateji, beta hasadı sayesinde gerçek paraya terfi edecek. Sağlamlık açısından asıl kusur şu: iki PnL kaynağı var, hiçbiri diğerini doğrulamıyor ve sapma için hiçbir alarm yok.
- Onerilen duzeltme: (a) `reconstruct_closed_trades` çağrısına dışlama listesi ver: `index_parking_symbol` + `BEAR_BRAIN_CONFIG` ters-ETF sembolleri + DCA sembolleri. (b) Rapora ZORUNLU bir mutabakat metriği ekle: broker-türetilmiş PnL ile `trade_history.json` toplamı arasındaki fark eşiği aşarsa metrik FAIL olsun ve kritik alarm bassın — şu an fark sessizce yutuluyor. (c) Botun kendi kaydettiği her çıkışa `strategy_source` alanı ekle (agent / parking / bear / dca) ki iki taraf aynı evren üzerinde konuşsun.

### STRAT-RR-GATE-TOTOLOJI  (Strateji/Alfa)
**R:R kapısı matematiksel olarak hiçbir zaman blokleyemez — TP zaten SL×min_rr olarak hesaplanıyor**

- Yer: `core/trade_gates.py:36-48 (plan_exit_pcts) + 186-201 (_check_rr_gate)`
- Ne oluyor: `plan_exit_pcts` TP'yi `tp = min(max(tp_floor, sl * min_rr), max(tp_cap, tp_floor))` ile üretiyor. `_check_rr_gate` sonra AYNI fonksiyonu çağırıp `rr_ratio = planned_tp / planned_sl` hesaplıyor ve `rr_ratio + 1e-9 < min_rr` ise bloklıyor. LIVE: SL ∈ [%4, %6] → SL×2.0 ∈ [%8, %12] → max(%8, ·) = SL×2 → min(·, %12) = SL×2 → TP/SL = 2.0 = min_rr, HER ZAMAN. PAPER: SL ∈ [%5, %6] → SL×1.25 ∈ [%6.25, %7.5] → max(%5, ·) = SL×1.25 → min(·, %7.5) = SL×1.25 → TP/SL = 1.25 = min_rr, HER ZAMAN. Yani oran her iki konfigürasyonda da tam olarak eşiğe eşit ve kapı hiçbir ATR değerinde tetiklenemez.
- Neden onemli: Sistemin "risk/ödül disiplini" diye sunduğu tek kapı bir totoloji. Gerçek risk/ödül, çıkış merdiveni yüzünden 0.925 (paper) iken kapı 1.25 raporluyor — yani kapı, işlemin gerçekte veremeyeceği bir oranı onaylıyor. Bu doğrudan yanlış-yeşil: hiçbir işlem "kötü R:R" diye reddedilmiyor (huninin gate_block_reasons'ında RR_GATE hiç görünmüyor; sadece EMA200, LOSS_STREAK_WARN, LIVE_LOCK_R5 var). Config.py:772'deki yorum bunun bilinçli olduğunu itiraf ediyor.
- Onerilen duzeltme: Kapıyı PLANLANAN değil GERÇEKLEŞEBİLİR orana bağla: `beklenen_kazanc = partial_pct*partial_oran + tp_pct*(1-partial_oran)`, `rr = beklenen_kazanc / sl`; bu oran min_rr'nin altındaysa gerçekten blokla. Ya da kapıyı kaldır ve yerine 'son N işlemin gerçekleşen R:R'si' telemetrisini koy — sahte bir kapıdan iyidir.

### STRAT-GUVEN-OLCULMEMIS-BOYUT-SURUCUSU  (Strateji/Alfa)
**Pozisyon boyutunun TEK sürücüsü olan 'confidence'ın sonucu öngördüğü hiç ölçülmedi — meta_labeler bağlı değil, model dosyası yok**

- Yer: `core/agent_coordinator.py:473-483 + core/position_sizer.py:97-113 + meta_labeler.py:16-19`
- Ne oluyor: `confidence = abs(weighted_score) * 2.0` (×1.2 çoğunlukta, ×0.5 vetoda) ve bu sayı doğrudan `conf_position_bands` merdivenine giriyor: paper'da 30→$2,500, 45→$4,000, 60→$6,000, 75→$9,000. Yani tek bir sayı hem "al mı?" hem "ne kadar?" kararını veriyor. Bu sayının kazanç/kayıpla ilişkisini test edecek araç repoda VAR (meta_labeler.py) ama karar yoluna BAĞLI DEĞİL: `grep -rn MetaLabeler` yalnız meta_labeler.py'yi buluyor, stock_bot.py hiç import etmiyor; dosyanın kendi docstring'i "WIRE-ETME KAPISI: OOF AUC > ~0.55 ... olduğunda bağlarız" diyor ve üretmesi gereken `meta_model.json` diskte YOK.
- Neden onemli: Boyut = güven olduğuna göre, güven ayırt edici değilse boyut RASTGELE RİSK demektir. Ayırt ediciliğe dair tek nicel kanıt tam tersini söylüyor: backtest'in 44 işleminde confidence yalnız iki değer alıyor (LONG'ların 21/21'i 65, SHORT'ların 23/23'ü 45) — sıfır varyans, sıfır bilgi. Canlı tarafta ölçüm ise şu: AMZN bant tabanında ($2,500 hedef, 9 adet ≈ $2,295) açıldı ve −$127.98 verdi; AAPL nakit sınırına takılıp ~$900 açıldı ve +$2.72 verdi. Yani bir işlemin dolar riski, tezinin kalitesinden çok o an bantta nereye düştüğüne ve kasada ne kadar nakit kaldığına bağlı (`core/executor.py:220` `max_invest = min(max_invest, available_cash)`).
- Onerilen duzeltme: Bantları AUC kanıtı gelene kadar DÜZLEŞTİR: tek sabit boyut (ör. equity'nin %2-3'ü) kullan — ayırt edici olmayan bir sinyale göre 3.6× boyut farkı uygulamak saf varyans ekler. Paralelde meta_labeler'ı kapalı işlemlerle koştur; OOF AUC ≤0.55 çıkarsa güveni boyutlandırmadan tamamen çıkar, >0.55 çıkarsa boyutu confidence'a değil `predict_proba`ya bağla.

### STRAT-FUNDAGENT-SABIT-OY  (Strateji/Alfa)
**FundAgent mega-cap'lerde yapısal olarak SABİT bir BUY oyu üretiyor — 5 'bağımsız uzman'dan biri sabit ofset**

- Yer: `core/fundamental_analyzer.py:160-230 + core/agent_coordinator.py:140-180 + 244-260 (cache 12 saat)`
- Ne oluyor: `analyze_fundamentals` skoru: EPS>0 → +5, kâr marjı >%15 → +5, analist hedefi >%15 yukarıda → +5, P/E 15-40 arası → 0, temettü <%2 → 0, 52-hafta konumu → 0/−3. Tipik bir kârlı mega-cap (AAPL/MSFT/GOOGL/AMZN/META/NVDA) bu tabloda 12-15 puan alır; `if score >= 10: signal = "BULLISH"` (satır 225) → FundAgent BUY oyu verir, confidence = `min(abs(score)*2, 100)` ≈ 24-30. Veri 12 saat cache'li (`FUNDAMENTAL_CONFIG.cache_hours`=12) ve çeyreklik bilançodan önce değişmez. Koordinatörde bu, her taramada her mega-cap için `0.20 × ~26 = +5.2` sabit weighted_score katkısı demek. Aynı anda RiskAgent hiçbir dalda BUY oyu VEREMİYOR (satır 336-341: yalnız SELL veya HOLD) — yani BUY tarafında toplam ağırlık 0.80 ve bunun 0.20'si sabit.
- Neden onemli: İki sonuç: (1) YÖN — evrende hangi teknik kurulum olursa olsun mega-cap'ler sürekli bir artı ofsetle başlıyor; bu, paper'daki 5 açık pozisyonun (GOOGL, MSFT, NVDA, PLTR, SHOP) neden tamamen mega-cap olduğunu açıklıyor. (2) BOYUT — bant merdiveni bu sabit ofsetle yukarı kayıyor: yalnız TechAgent'ın BUY dediği bir kurulum ws≈13.6 → conf 27 ile paper'ın 30 eşiğini GEÇEMEZKEN, aynı kurulum bir mega-cap'te FundAgent ofsetiyle ws≈18.8 → conf ≈37.6 ile $2,500 bandına giriyor. Yani "güven" büyük ölçüde "bu hisse kârlı bir mega-cap mi?" sorusunun cevabını ölçüyor, işlemin kalitesini değil. Ayrıca `fundamental_gate_enabled: True` (paper) ile aynı statik skor bir de KAPI olarak kullanılıyor — aynı bilgi iki kez sayılıyor.
- Onerilen duzeltme: FundAgent'ı seviye değil DEĞİŞİM sinyaline çevir (EPS revizyonu, sürpriz, marj trendi) veya oyunu evren içinde göreceleştir (z-skor: 20 sembolün fundamental skorları içindeki sırası). Aynı statik skoru hem oy hem kapı olarak kullanmayı bırak. Kısa vadede: FundAgent oyunu weighted_score'dan çıkarıp yalnız kapı olarak bırak, bantları buna göre yeniden kalibre et.

### STRAT-BULL-SHORT-KILIDI-PAHALI-BETA  (Strateji/Alfa)
**SPY>EMA200 iken short tamamen kapalı; kalan portföy %66 mega-cap long + SPY parking = pahalı ve gürültülü beta**

- Yer: `core/short_executor.py:47-58 + core/index_parking.py:205-225 + config.py:355-362`
- Ne oluyor: `short_executor.execute_short` en başta rejim kilidini uyguluyor: `if market_regime == 'BULL' or 'BULL' in enhanced_regime_name: return False`. `_market_regime` yalnız SPY>EMA200 ikilisinden geliyor (stock_bot.py:825-830). SPY ölçüm döneminde sürekli EMA200'ün üstünde olduğu için SHORT kolu TAMAMEN kapalı; LIVE zaten `BOT_MODE=long_only`. Aynı anda IndexParking boştaki nakdi SPY'ye basıyor (`index_parking_enabled: True` hem STOCK_CONFIG hem PAPER_AGGRESSIVE'de). Sonuç portföy: paper'da 5 mega-cap long $41,810 = equity'nin %66'sı + SPY parking; live'da tek pozisyon SPY 0.4517 adet.
- Neden onemli: Bu, "bot pahalı bir mega-cap al-tut mu yapıyor?" sorusunun evet cevabı ve rakamı projenin kendi deneyinde var: regime_experiment'ta `overlay` (boş nakit SPY'de) modunun ortalama getirisi %9.665, `base` modunun %1.01 — potansiyel getirinin 8.65 puanı beta sleeve'inden geliyor. Ama overlay bile SPY'ın 2.84 puan gerisinde, yani aktif alım-satım ölçülmüş olarak −%2.84/6 ay değer İMHA ediyor. Canlı ölçüm aynı yönde: iki ayda bot −%1.55, SPY +%4.36 (−5.91 puan); ölçüm penceresinde bot +%2.89, SPY +%3.24 ve kapalı işlemler −$64.04 — yani pozitif görünen getirinin tamamı 5 long'un betası. SPY'ı yenmek için gereken somut kenar: yıllıklandırılmış olarak ~%6-12 alfa, tek bir mega-cap-tech faktörüne %66 konsantre bir portföyle. Şu anki tasarımın böyle bir kenarı yok; sahip olduğu tek ölçülebilir kenar (beta) zaten SPY'ın kendisi.
- Onerilen duzeltme: Dürüst bir karar noktası kur: (1) Beta istiyorsan aktif motoru kapat, sermayeyi SPY'de tut — ölçülen fark +2.84 puan/6 ay lehine. (2) Alfa istiyorsan stratejiyi beta-nötr yap: her long'un SPY-beta'sını hesapla ve portföy net beta'sını ~0'a getir (ters-ETF veya SPY short sleeve'iyle), böylece P&L artık piyasa yönünü değil seçimi ölçer. Bugünkü hâlde 'strateji kazandı mı?' sorusu ölçülemiyor çünkü beta ile alfa aynı hesapta karışıyor.

### STRAT-OLCUM-SPY-PARKING-KARISIMI  (Strateji/Alfa)
**Ölçüm aracı SPY parking sleeve'ini strateji işlemi sayıyor — 4/4 PASS kapısı yanlış yeşil veriyor**

- Yer: `tools/olcum_raporu.py:235-292 (reconstruct_closed_trades) + 783-788 (metrik 1) + core/index_parking.py:205-225`
- Ne oluyor: `reconstruct_closed_trades` broker fill'lerini sembol bazında eşleştirip kapalı işlem üretiyor ve `print_report` metrik 1'i `net_pnl = sum(trade.pnl for trade in period)` ile hesaplıyor. Hiçbir aşamada parking sembolü dışlanmıyor: dosyada `SPY`, `parking` veya `is_parking_symbol` geçmiyor. Oysa IndexParkingManager gerçek SPY BUY/SELL emirleri gönderiyor (`index_parking.py:205-225`) ve bot'un kendi kodu bu sleeve'i strateji dışı sayıyor (`position_manager.py:286` "Parking sleeve (SPY) trade DEĞİL — stop/TP/partial uygulanmaz", `position_manager.py:474` mutabakattan muaf). Ek olarak `reconstruct_closed_trades` satır 246-252'de bir sembolün İLK fill'ini her zaman YENİ giriş sayıyor; pencere başlangıcından önce açılmış bir pozisyonun satışı sahte bir SHORT girişi olarak yorumlanır.
- Neden onemli: Rapor 6 kapalı işlem için net $+218.89 derken defter aynı 6 işlem için −$64.04 diyor; $283 fark. Ölçüm penceresinde (07-30→08-22) SPY +%3.24 yaptı; paper'da parking rezervi equity'nin %30'u (≈$19k) mertebesinde ve günde bir rebalance ediliyor — birkaç bin dolarlık bir SPY tur işlemi %3'lük bir hareket üzerinde tam olarak bu büyüklükte bir kâr üretir. Sonuç: kaybeden bir stratejiyi, kendi kodunun 'strateji değil' dediği beta sleeve'inin kârıyla PASS gösteren bir kapı. Bu kapı canlıya kalibrasyon terfisinin ön koşulu olduğu için, doğrudan gerçek para riski.
- Onerilen duzeltme: `reconstruct_closed_trades`in girdisini filtrele: `INDEX_PARKING_SYMBOLS` ve `MARKET_REGIME_CONFIG.index_symbols` (SPY, QQQ) ile BearBrain'in ters-ETF'lerini (SQQQ, SH, SPXS) dışla — ya da daha sağlamı, yalnız bot'un kendi `trade_history` kayıtlarıyla eşleşen fill'leri kabul et ve eşleşmeyenleri `unmatched_rows` olarak RAPORLA. Ayrıca pencere başında açık pozisyon varsa ilk fill'i 'kapanış' olarak tanı (broker pozisyon anlık görüntüsünden başlangıç durumu kur), yoksa sahte short döngüleri üretilir.


## ORTA

### AGENT-OUTCOME-LIFO-YANLIS-ATAMA  (Ajanlar)
**record_outcome sembole göre LIFO eşleştirme yapıyor; aynı sembolde ikinci işlem açıldığında kredi yanlış tahmine yazılıyor**

- Yer: `core/agent_performance.py:130-150; core/options_executor.py:381-389`
- Ozet: `record_outcome` yalnız sembole bakıp `reversed(preds)` ile EN SON çözümsüz kaydı buluyor ve `break` ediyor:

```python
# core/agent_performance.py:130-148
for agent_name, preds in self.predictions.items():
    for pred in reversed(preds):
        if pred["symbol"] == symbol and pred["actual_outcome"] is None:
            ...
            break
```

Aynı sembolde iki pozisyon üst üste açılırsa (pap

### SENT-FEAR-GREED-OLU-DAL  (Ajanlar)
**SentAgent'ın Fear&Greed mantığı ölü kod: üretimde girdi sabit 50/NEUTRAL yazılıyor, testler ise gerçek değer besliyor**

- Yer: `stock_bot.py:1219-1224; core/agent_coordinator.py:189-190, 196-199, 207-210; core/news_analyzer.py:551-552, 568-587`
- Ozet: SentAgent üç Fear&Greed dalı içeriyor: contrarian BUY (fg<25), contrarian SELL (fg>75) ve `combined ± 10` ayarlaması (fg_signal'e göre). Ama üretimde bu girdiler SABİT yazılıyor:

```python
# stock_bot.py:1219-1223
sent_data = {
    "news_score": news.get("news_score", 0),
    "sentiment_label": news.get("signal", "NEUTRAL"),
    "fear_greed_value": 50,        # HARDCODED
    "fear_greed_signal": 

### TECH-ADX-ISARET-VE-SIRA-BAGIMLILIGI  (Ajanlar)
**TechAgent'ta ADX kuvvetlendirmesi nötr kurulumda bearish itme yapıyor ve sonucu değerlendirme sırasına bağlı**

- Yer: `core/agent_coordinator.py:91-113`
- Ozet: ADX 'mevcut yönü güçlendir' amacıyla yazılmış ama yön belirsizken (indie_score == 0) else dalına düşüyor ve NEGATİF ekliyor:

```python
# core/agent_coordinator.py:92-97
adx = tech_data.get("adx", 0)
if adx > 30:
    indie_score += 8 if indie_score > 0 else -8   # indie_score==0 -> -8
    reasons.append(f"ADX={adx:.0f} guclu trend")
elif adx > 25:
    indie_score += 5 if indie_score > 0 else -5   

### FUNNEL-CONF-BELOW-MIN-KARISIYOR  (Ajanlar)
**conf_below_min sayacı BUY ve SHORT redlerini aynı kovaya yazıyor; canlıda hiç alınamayacak SHORT'lar 'güven yetersiz BUY' gibi görünüyor**

- Yer: `stock_bot.py:909-915, 925-926, 976-985`
- Ozet: Funnel'da sinyal etiketi remap'ten ÖNCE yazılıyor (satır 909-915: SELL → `signal_sell`), sonra satır 925-926 SELL'i SHORT'a çeviriyor, sonra satır 976-985 hem BUY-altı hem SHORT-altı redleri AYNI `conf_below_min` kovasına bump ediyor:

```python
# stock_bot.py:976-985
if (decision["signal"] == "BUY" and decision["confidence"] < effective_buy_conf):
    self._funnel_bump("conf_below_min")
elif (dec

### EXIT-DEFTER-GIRIS-FIYATI-SINYAL-FIYATI  (Cikislar/Defter)
**Gerçekleşen PnL, broker ortalama giriş fiyatıyla değil SİNYAL fiyatıyla hesaplanıyor**

- Yer: `core/executor.py:243, 306-308, 437-438, 540; stock_bot.py:1755, 1801-1802`
- Ozet: execute_buy pozisyonu `price = analysis["price"]` (sinyal anındaki fiyat) ile kaydediyor (executor.py:243, 306). execute_sell çıkışta `entry = pos.get("entry_price", 0)` okuyup `pnl_usd = (fill_price - float(entry)) * filled_qty` yapıyor (executor.py:540). _reconcile_external_exit de aynı alanı kullanıyor (stock_bot.py:1755, 1801). Oysa manage_positions pnl_pct'i broker'ın `avg_entry_price`'ıyla h

### EXIT-PDT-KAZANC-CIKISINI-BLOKLUYOR  (Cikislar/Defter)
**Canlıda PDT koruması kâr çıkışlarını blokluyor ama zarar çıkışını geçiriyor — asimetriyi kodla pekiştiriyor**

- Yer: `core/executor.py:426-436; core/pdt_tracker.py:57-59, 100-114`
- Ozet: execute_sell, aynı gün açılmış pozisyonda `should_hold_overnight` True dönerse satışı reddediyor; tek istisna gerekçe metninde "STOP_LOSS" geçmesi. TAKE_PROFIT, TRAILING_STOP ve GAP_DOWN gerekçeli çıkışlar bloklanıyor. PDT muafiyeti equity >= $25.000 ile geliyor (pdt_tracker.py:57-59); canlı hesap equity $491,72 → muafiyet YOK, haftalık limit 2.

### EXIT-COOLDOWN-STOP-KONTROLUNU-DE-ATLIYOR  (Cikislar/Defter)
**sell_cooldown, pozisyonun TÜM yönetimini (stop-loss kontrolü dahil) atlıyor**

- Yer: `core/position_manager.py:292-295; core/position_manager.py:1073-1076`
- Ozet: manage_positions döngüsünün başında `cooldown_until = bot.sell_cooldown.get(symbol); if cooldown_until and datetime.now() < cooldown_until: continue` var — bu `continue`, trailing güncellemesini, break-even armlamasını ve yerel STOP-LOSS tetiğini de atlar. Cooldown, kademeli satış doğrulandığında da kuruluyor (_finish_partial_attempt, position_manager.py:1073-1076), paper'da 120 sn, canlıda 300 sn

### CFG-BACKTEST-BASKA-SISTEMI-OLCUYOR  (Config/Risk)
**backtest.py / walk_forward.py canlının boyutlandırma ve sermaye kısıtlarını hiç modellemiyor — 'doğrulanmış' strateji canlıda çalışan strateji değil**

- Yer: `backtest.py:56-70, 500-515`
- Ozet: backtest.py `self.config = dict(STOCK_CONFIG)` alıyor, live modda yalnız `self.config["max_position_usd"] = self.config.get("live_max_position_usd", 200)` (satır 69) yazıyor. `conf_position_bands`, `fixed_position_usd`, `cash_reserve_pct`, `index_parking`, `live_entries_enabled` ve PositionSizer kelimelerinin HİÇBİRİ backtest.py / walk_forward.py / regime_experiment.py içinde geçmiyor (tarama: 0 e

### RISK-EQUITY-FLOOR-DONMUS-BAZ  (Config/Risk)
**Equity floor yalnız __init__'te hesaplanıyor: restart'ta yeniden bazlanıyor, tepe değerini hiç takip etmiyor**

- Yer: `stock_bot.py:203, 2137-2206, 489-505`
- Ozet: `self.equity_floor = equity * config.get("equity_floor_pct", 0.85)` (stock_bot.py:203) yalnızca __init__ içinde, konteynerin başladığı andaki equity ile hesaplanıyor. `_daily_reset` (stock_bot.py:2137-2206) `initial_equity`'yi günlük yeniliyor ama `equity_floor`'a DOKUNMUYOR; kodda başka hiçbir yerde equity_floor'a atama yok.

### CFG-OLU-AYAR-BLOKLARI  (Config/Risk)
**Hiç okunmayan config blokları sahte kontrol hissi veriyor: ORDER_CONFIG (limit emir) ve short squeeze/EMA200/RSI kapıları tamamen ölü**

- Yer: `config.py:650-655, 499-522, 660-664, 233-241`
- Ozet: Kod taraması sonucu config.py dışında hiçbir dosyada geçmeyen anahtarlar:
- ORDER_CONFIG'in TAMAMI: prefer_limit_orders, limit_order_slippage_pct, min_volume_for_market_order, limit_order_timeout_minutes (config.py:650-655) → bot her emri MarketOrderRequest ile gönderiyor, limit emir yolu yok.
- DATA_CONFIG'in TAMAMI: require_realtime_data, max_acceptable_delay_seconds, warn_on_delayed_data (confi

### FUNNEL-CONF-BELOW-MIN-SHORT-SAYIMI  (Config/Risk)
**conf_below_min hem BUY hem SHORT sinyallerini sayıyor: long_only canlıda asla alınamayacak SHORT redleri huniyi kirletiyor (signal_buy < conf_below_min çelişkisinin kaynağı)**

- Yer: `stock_bot.py:908-915, 926-927, 974-986`
- Ozet: Huni sinyal sayacı `signal_buy`'ı yalnız BUY'a, SHORT/SELL'i `signal_sell`'e yazıyor (stock_bot.py:908-915). Ancak hemen ardından koordinatörün SELL kararı pozisyon yoksa SHORT'a çevriliyor (stock_bot.py:926-927 `if decision["signal"] == "SELL" and symbol not in self.positions: decision["signal"] = "SHORT"`) ve `conf_below_min` sayacı HEM BUY hem SHORT için artırılıyor (stock_bot.py:974-986).

### GATE-KUYRUK-VE-BEARBRAIN-KAPI-BYPASS  (Kapilar/Huni)
**Pullback kuyruğu ve BearBrain girişleri check_all_gates'i atlıyor; BearBrain girişi entries sayacını da artırmıyor**

- Yer: `stock_bot.py:560-590 + core/bear_brain.py:595-645`
- Ozet: (1) Pullback kuyruğu: uzamış giriş kuyruğa alınırken kapılardan geçmiş oluyor, ama tetiklendiğinde (2 saat içinde %1.5 düşüş) main loop yalnız pozisyon/sektör/wash-sale'i yeniden kontrol edip doğrudan `self.executor.execute_buy(sym, sig_analysis, config)` çağırıyor — `check_all_gates` YENİDEN çalıştırılmıyor. Yani 2 saat önceki EMA200/kayıp-serisi/volatilite kararıyla emir gidiyor; bu sürede fiyat

### GATE-OPTIONS-YOLU-TUM-KAPILARIN-ONUNDE  (Kapilar/Huni)
**Opsiyon yolu güven eşiğinin ve tüm kapıların ÖNÜNDE; canlı R5 kilidi opsiyonlarda hiç kontrol edilmiyor**

- Yer: `stock_bot.py:987-1005 + core/options_executor.py:28-60`
- Ozet: `_analyze_and_trade` içinde opsiyon değerlendirmesi, güven eşiği kontrolünden (satır 1006 `decision["confidence"] >= effective_buy_conf`) ve `check_all_gates`'ten (satır 1030) ÖNCE geliyor ve başarılıysa `return` ile fonksiyonu bitiriyor. Yani opsiyon yolu min_confidence_score, EMA200, earnings, kayıp serisi, volatilite, R:R, MTF — hiçbirini görmüyor. Ayrıca `options_executor.execute_call/execute_

### GATE-MTF-SESSIZ-FAIL-OPEN  (Kapilar/Huni)
**MTF kapısı hatada sessizce açılıyor ve veri şartı sık sık sağlanmıyor — canlıda var sanılan koruma çoğu zaman yok**

- Yer: `core/trade_gates.py:209-227`
- Ozet: `_check_mtf` tüm gövdeyi `try/except Exception: pass` içine almış ve sonda `return False, ""` (yani GEÇTİ) dönüyor. Ayrıca üç ayrı sessiz koşul var: `df_1h.empty or len(df_1h) < 50` → kapı yok; `len(df_4h) < 20` → kapı yok. `get_stock_bars(symbol, days=14)` saatlik veri çekiyor; 14 takvim günü ≈ 10 işlem günü, 4 saatlik resample'da ~20-25 bar üretiyor — yani 20 bar şartı sınırda. Hiçbir dalda log 

### BACKFILL-KORUMA-IHLALI-BILGIYE-INDIRILMIS  (Olcum Sistemi)
**Backfill PnL enjekte etmiyor ama kanıtlanmış koruma ihlallerini 'bilgi'ye indiriyor; metrik-4'ün stop-reddi sayacı 40010001'i göremiyor**

- Yer: `tools/olcum_backfill.json:12-38; tools/olcum_raporu.py:696-716 (count_broker_stop_rejections), 718-740 (invariant_counts), 856-862`
- Ozet: tools/olcum_backfill.json'da ölçüm penceresine ait 3 olay var: STOP_REGRESSION (doğrulanmış BE stop 159,50 iken trail 156,56'ya geriledi), UNIQUE_COLLISION count=5 (40010001 ile stop retry 2..6 reddedildi), KORUMA (replace sonrası aktif replacement bulunmayan çıplak pencere). Bunlar PnL taşımıyor — yani $283 farkın kaynağı DEĞİL. Ama `invariant_counts` bunları yalnız satır 859'daki "Sistem invaria

### REKONSTRUKSIYON-STRATEJI-DISI-ISLEMLERI-AYIRMIYOR  (Olcum Sistemi)
**Metrik-1 sembol bazında körü körüne netleme yapıyor: park kolu/DCA/opsiyon ayrımı yok, pencere dışı PnL pencereye düşüyor, eski pozisyon SHORT sanılıyor**

- Yer: `tools/olcum_raporu.py:196-231 (fetch_closed_orders), 235-291 (reconstruct_closed_trades), 772; core/position_manager.py:286`
- Ozet: `reconstruct_closed_trades` bir sembolün TÜM fill'lerini kronolojik netliyor; emrin stratejiden mi, SPY park kolundan mı, DCA'dan mı geldiğine bakmıyor (bot tarafında `position_manager.py:286` park kolunu açıkça 'trade DEĞİL' sayıyor). Pencere filtresi yalnız `closed_at >= since` (satır 772) — girişi pencereden çok önce olan bir döngünün TÜM PnL'i pencereye yazılıyor. Ayrıca `fetch_closed_orders` 

### URETIMDE-RUN-BOT-CALISMIYOR-KORUMALAR-OLU-KOD  (Saglamlik/Altyapi)
**Dockerfile doğrudan stock_bot.py'yi çalıştırıyor; run_bot.py'deki tek-instance kilidi ve kill-switch açılış guard'ı üretimde hiç çalışmıyor**

- Yer: `Dockerfile (son satır CMD) + run_bot.py:44-66,185-193 + stock_bot.py:407-421`
- Ozet: Dockerfile `CMD ["python", "-u", "stock_bot.py"]` ile bitiyor — run_bot.py hiç devreye girmiyor. Dolayısıyla: (a) `acquire_single_instance_lock()` (run_bot.py:44-66) çağrılmıyor, `instance.lock` üretimde hiç alınmıyor; (b) `if kill_switch_active(): log("🚨 KILL SWITCH AKTIF (onceki oturum). Bot baslatilmiyor."); return` (run_bot.py:187-190) guard'ı çalışmıyor. Bunun yerine stock_bot.run() kill akti

### R5-CANLI-GIRIS-KILIDI-KISMI  (Saglamlik/Altyapi)
**"Canlıda yeni giriş yok" kilidi yalnız executor.execute_buy'da; short_executor, options_executor ve index_parking bu kontrolü hiç yapmıyor**

- Yer: `core/executor.py:144-158 + config.py:462-468 + core/short_executor.py:128 + core/options_executor.py:190 + core/index_parking.py:212`
- Ozet: `live_entries_enabled` kontrolü tek bir yerde: `if is_live and not config.get("live_entries_enabled", False):` (executor.py:148). `grep -rln "live_entries_enabled" --include=*.py .` yalnız config.py, core/executor.py ve iki test dosyası döndürüyor. Oysa canlı hesapta yeni risk açabilen dört `submit_order` yolu var: core/short_executor.py:128 (SHORT girişi), core/options_executor.py:190 (CALL/PUT),

### RESTART-KAYBOLAN-DURUM-HALT-KUYRUK-CACHE  (Saglamlik/Altyapi)
**Kalıcı olmayan durumlar: kayıp-serisi durdurma süresi her restart'ta baştan armlanıyor, kuyruktaki girişler kayboluyor, tüm sağlayıcı önbellekleri soğuk başlıyor**

- Yer: `core/trade_gates.py:164-175 + core/signal_queue.py:26-28 + core/ntfy_notifier.py:45 + stock_bot.py:1963-1966 (persist edilen liste)`
- Ozet: `_save_position_metadata` yalnız şunları yazıyor: positions, short_positions, options_positions, last_trade_time, consecutive_losses, symbol_consecutive_losses, daily_buys_count, trades_today (stock_bot.py:1956-1966). Yazılmayanlar: (a) `bot._loss_halt_until` — trade_gates.py:167-170'te `if halt_until is None: bot._loss_halt_until = datetime.now() + timedelta(hours=halt_hours)` ile kuruluyor; rest

### MTF-KAPISI-FAIL-OPEN-SESSIZ  (Saglamlik/Altyapi)
**Multi-timeframe kapısı hata durumunda sessizce GEÇİRİYOR — güvenlik filtresi yanlış yöne başarısız oluyor**

- Yer: `core/trade_gates.py:207-227`
- Ozet: `_check_mtf` tüm gövdeyi tek `try` ile sarıyor ve `except Exception:\n            pass` ile bitirip `return False, ""` (yani BLOKLAMA YOK) dönüyor. `self.bot.get_stock_bars(symbol, days=14)` boş DataFrame döndüğünde (üstteki BARS bulgusu) `if not df_1h.empty and len(df_1h) >= 50:` sağlanmaz ve fonksiyon yine bloklamadan çıkar. Ayrıca bloklama durumunda bile log `logger.debug` (satır 222) — INFO'ya

### STRAT-REJIM-OLU-KOD  (Strateji/Alfa)
**4-rejimli MarketRegimeDetector fiilen ölü kod — get_confidence_modifier hiç çağrılmıyor, position_sizer'ın rejim ayarı LONG'da bypass**

- Yer: `core/market_regime.py:181-231 + core/position_sizer.py:91, 148 + stock_bot.py:851-853`
- Ozet: `MarketRegimeDetector` ADX + Bollinger genişliği + EMA dizilimi + VIX ile BULL_TREND/BEAR_TREND/RANGE_BOUND/CHOPPY üretiyor ve her rejim için `get_confidence_modifier` (buy_conf_adj, position_size_mult, max_positions_adj) tanımlıyor. Repo genelinde `get_confidence_modifier` için TEK arama sonucu tanımın kendisi — hiçbir çağrı yeri yok. `_regime_trading_mode` (AGGRESSIVE/CAUTIOUS/MINIMAL) stock_bot

### STRAT-RS-VOLUME-OLU  (Strateji/Alfa)
**RelativeStrength ve VolumeAnalyzer güven katkıları karar yolunda EZİLİYOR — iki modül de fiilen ölü**

- Yer: `stock_bot.py:1147-1183 + 1020 + core/relative_strength.py:141-168`
- Ozet: `_get_technical_analysis` iki yerde `result["confidence"]`i değiştiriyor: VolumeAnalyzer'ın `confidence_boost`u (satır 1152-1158) ve RelativeStrength'in `get_rs_signal_boost`u (satır 1178-1180, −10..+15 aralığında). Fakat karar akışında eşik karşılaştırması `decision["confidence"]` (koordinatör) ile yapılıyor ve hemen ardından stock_bot.py:1020 `analysis["confidence"] = decision["confidence"]` ile

### STRAT-KORELASYON-YOK  (Strateji/Alfa)
**'Sektör çeşitlendirmesi' kozmetik — portföyde korelasyon/beta hesabı hiç yok, 5 pozisyon tek faktör**

- Yer: `config.py:208-227 (SECTOR_MAP) + stock_bot.py:1448-1466 + core/agent_coordinator.py:295-300`
- Ozet: Konsantrasyon koruması yalnız `SECTOR_MAP` etiketlerini sayıyor: `max_positions_per_sector` (paper 3, live 2). Etiketler GOOGL/MSFT/META='Technology', NVDA/AMD/SMCI='Semiconductors', AMZN/SHOP='E-Commerce', PLTR='Data_AI' şeklinde. Repoda korelasyon, kovaryans veya portföy beta'sı hesaplayan hiçbir kod yok: `grep -rn 'corr|covarian|beta'` yalnız FundamentalAnalyzer'ın veri alanı olarak çektiği `be

### STRAT-FUNNEL-CONF-KARISIMI  (Strateji/Alfa)
**conf_below_min sayacı hem BUY hem SHORT redlerini topluyor — huni teşhisi yanıltıyor**

- Yer: `stock_bot.py:906-983 + core/funnel.py:23-34`
- Ozet: stock_bot.py:907-915'te huni aşaması `decision["signal"]`e göre yazılıyor (BUY→signal_buy, SELL/SHORT→signal_sell). HEMEN SONRA satır 930'da `if decision["signal"] == "SELL" and symbol not in self.positions: decision["signal"] = "SHORT"` remap'i yapılıyor. Ardından satır 971-983'te TEK bir sayaç, hem `signal=="BUY" and confidence < effective_buy_conf` hem `signal=="SHORT" and confidence < effectiv


## DUSUK

### CFG-CELISKILI-VARSAYILANLAR-VE-YORUMLAR  (Config/Risk)
**Kod içi varsayılanlar config değerleriyle çelişiyor ve config yorumları yanlış bilgi veriyor (kill eşiği, rezerv, boyut tavanı)**

- Yer: `config.py:633-645, core/executor.py:183, core/position_sizer.py:117, stock_bot.py:246-247`
- Ozet: Çelişkiler:
1) config.py:635-638 yorumu: "değerler STOCK_CONFIG ile AYNI tutuldu (3 hata / %3 günlük kayıp)" ve KILL_SWITCH_CONFIG:642 `max_daily_loss_pct: 0.03`. Oysa gerçek eşik STOCK_CONFIG:448'de %5. Aynı yorum eşiklerin "~satır 370"te olduğunu söylüyor; gerçekte 448-449.
2) stock_bot.py:246-247 varsayılanları config'le ters yönde: `max_consecutive_errors` default 5 (config 3), `max_daily_loss

### OLU-KOD-VE-NAIVE-ZAMAN-DAMGASI  (Olcum Sistemi)
**Ölü kod (load_local_exit_rows) ve naive timestamp: telemetri zaman damgaları UTC varsayılıyor, konteyner TZ'si UTC değilse metrik-2 sessizce çöker**

- Yer: `tools/olcum_raporu.py:469-495 (hiç çağrılmıyor), 133-146 (_as_datetime), 547-553, 452-456; core/telemetry.py:17-21`
- Ozet: (1) `load_local_exit_rows` tanımlı ama repoda tek bir çağrısı yok (grep: yalnız tanım satırı); metrik-4 aslında `load_authoritative_state`'in `trade_rows`'unu kullanıyor. Okuyucuyu 'trades_today önceliklidir' sanısına düşüren ölü kod. (2) `core/telemetry.py:18` `"ts": datetime.now().isoformat()` — zaman dilimi bilgisi OLMAYAN yerel saat yazıyor; raporun `_as_datetime`'ı (satır 141-142) naive değer

### DOCKERFILE-VE-BAGIMLILIK-KIRILGANLIGI  (Saglamlik/Altyapi)
**FinBERT indirmesi başarısız olsa bile build yeşil; requirements'ta tek pin yok — aynı commit farklı sürümlerle deploy oluyor**

- Yer: `Dockerfile:16-45 + requirements.txt (tamamı)`
- Ozet: Dockerfile'daki model indirme bloğu hem iç döngüde her dosyayı `try/except` ile sarıyor hem de tüm RUN adımını `|| echo "Model pre-download failed, will download at runtime"` ile kapatıyor. Blok içinde `if not model_ok or not tok_ok: print('UYARI: Kritik dosyalar eksik! FinBERT VADER fallback ile calisacak.')` yazıyor ama exit kodu değiştirmiyor. requirements.txt'in tamamı `>=` ile yazılmış (`alpa
