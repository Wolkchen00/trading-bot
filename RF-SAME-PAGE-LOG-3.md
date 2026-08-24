# RF-SAME-PAGE-LOG-3.md , v4.16 Clarity Break

**Plan:** RF-PLAN-3.md | **Envanter:** RF-ISSUES-3.md
**Codex:** codex-cli 0.145.0, model `gpt-5.6-sol`, reasoning effort `xhigh` (config.toml; -m pinlenmedi)
**Thread:** `01a034f7-a4c7-7381-bac0-6cac5adc4609`

> NOT: Tur 1 fresh cagrisi 10 dk timeout'ina takildi ama Codex tum analizi bitirmisti
> (4/4 todo completed, 126 komut calistirilmis). Ayni thread `resume` ile devam
> ettirildi ve nihai rapor `out-r1b.txt` olarak alindi. Snapshot: calisma agaci
> her iki cagriden sonra da TEMIZ (read-only ihlali yok).

## Round 1

### Integrator findings (Codex, verbatim)

## GOREV 1 - 12 iddianin dogrulamasi

`AGENT-SENT-TERS-ISARET` = DOGRU - FinBERT negatif sonucu zaten eksi işaretliyor; tüketici ikinci kez eksileyerek NLP katkısını pozitife çeviriyor ve yeterli toplamda SentAgent BUY üretiyor: `score = -raw_score` (core/finbert_analyzer.py:370-371), ardından `nlp_score = -result["score"] * 30` (core/news_analyzer.py:308-309) ve `if combined >= 12: signal = "BUY"` (core/agent_coordinator.py:212-213).

`KILLSWITCH-KOD-HATASINI-API-HATASI-SANIYOR` = KISMEN - Dış ana döngü catch’ine ulaşan her `Exception` gerçekten API hatası diye sayılıyor: `except Exception as e: ... self.kill_switch.check_api_error(e)` (stock_bot.py:695-699) ve eşikte callback tasfiye yapıyor: `self.client.close_all_positions(cancel_orders=True)` (stock_bot.py:2227); fakat mevcut eşik 5 değil 3’tür (`"max_consecutive_errors": 3`, config.py:449), iç catch’lerde yutulan hatalar buraya ulaşmaz ve başarılı açık-piyasa `get_account` çağrısı sayacı sıfırlar (`reset_error_count()`, stock_bot.py:494).

`GATE-LOSS-STREAK-WARN-KILITLI-KISIR-DONGU` = DOGRU - HALT dalında `_loss_halt_until` oluşturulup süre dolunca sayaç sıfırlanıyor (core/trade_gates.py:164-174), WARN dalıysa yalnız güveni kontrol edip `return True, "LOSS_STREAK_WARN"` yapıyor ve hiçbir zaman aşımı taşımıyor (core/trade_gates.py:176-183); streak yalnız `pnl_usd > 0` ile sıfırlanıyor (core/streak.py:34-37).

`GATE-LIVE-FRACTIONAL-BRACKET-OLU-KAPI` = KISMEN - Kod kesirli adet görünce doğrudan dönmüyor; `qty = round(max_invest / price, 4)` sonrasında DAY bracket göndermeyi deniyor (core/executor.py:228-274). Broker bracket’i reddederse canlıda hata loglayıp `return False` yapıyor (core/executor.py:276-288) ve funnel sayacı yok; dolayısıyla “kesirli ⇒ kesin return” kod içi bir koşul değil, broker reddine bağlıdır ve tamamen sessiz de değildir.

`EXIT-PARTIAL-DEFTERE-YAZILMIYOR` = DOGRU - Dolum sonucu yalnız intent state’ine yazılıp persist/telemetry çağrılıyor: `intent["filled_qty"] = total_filled` ve `_persist_partial_position(...)` / `_partial_event(...)` (core/position_manager.py:1061,1102-1108). `_handle_long_partial` boyunca `trades_today`, `performance.record_trade`, `update_loss_streak` veya `agent_perf.record_outcome` çağrısı yok; tam çıkış yolu bunları açıkça çağırıyor (core/executor.py:585-621).

`METRIK4-EKSIK-KAYDI-GOREMEZ` = DOGRU - Fonksiyon broker çıkışlarını hazırlıyor, sonra yalnız yerel satırların broker’da eşini arayıp eşleşmeyeni artırıyor (tools/olcum_raporu.py:523-537); eşleşmeden kalan broker çıkışları için ikinci yönlü kontrol bulunmadığından eksik yerel kayıt görünmez.

`GENEL-PASS-N20-30GUN-KOSULUNU-UYGULAMIYOR` = DOGRU - `overall` yalnız dört metriğin birleşimidir (tools/olcum_raporu.py:863-865); `len(period)` ve `elapsed_days` yalnız metne basılıyor (tools/olcum_raporu.py:866-869), sonra `return 0 if passed else 1` geliyor (tools/olcum_raporu.py:951). Bu nedenle n<20 ve gün<30 iken diğer metrikler yeşilse exit 0 mümkündür.

`OLCUM-SPY-PARKING-TRADE-SAYILIYOR` = DOGRU - `broker_fills` bütün sembolleri filtresiz `Fill` nesnesine çeviriyor (tools/olcum_raporu.py:176-195), `reconstruct_closed_trades` bunları sembol bazında trade yapıyor (tools/olcum_raporu.py:235-293). Parking ise doğrudan broker’a BUY/close gönderiyor (core/index_parking.py:205-247) ve emirlerde strateji/provenance etiketi bulunmadığından kapanmış SPY döngüsü Metrik-1’e karışır.

`STRAT-BACKTEST-AYRI-UYGULAMA` = DOGRU - `backtest.py` canlı koddan yalnız `plan_exit_pcts` ve `should_exit_locally` import ediyor (backtest.py:33-34), kendi `def _technical_analysis(...)` uygulamasını çalıştırıyor (backtest.py:365) ve dosyada `AgentCoordinator` veya `check_all_gates` çağrısı yoktur.

`BARS-BOS-DF-EMA200-GUN-BOYU-ZEHIRLENME` = DOGRU - Boş/veri-yetersiz durumda `result` None kalıyor fakat yine de gün anahtarıyla cache’e yazılıyor: `self._daily_ema200_cache[symbol] = (today, result)` (stock_bot.py:1378-1390). Sonraki çağrı aynı gün cache’deki None’ı döndürüyor (stock_bot.py:1373-1376); çağıran da yalnız değer None değilse günlük EMA’yı uygular (stock_bot.py:1165-1167), yani kapı günlük veri yerine saatlik/fail-open değerde kalır.

`EXIT-BE-TAVANI-0.3PCT` = KISMEN - +%2,5’te stop gerçekten entry×1,003’e çekiliyor (core/position_manager.py:355-376; config.py:430-432), fakat bu mutlak bir kâr tavanı değildir: fiyat yeterince yükselirse trailing stop daha yukarı ratchet edilir (core/position_manager.py:436-455). Risk paper’da %5-6, canlıda %4-6’dır (`stop_loss_pct` config.py:365, paper override config.py:766; tavan config.py:367); “bütün kazananlar +%0,3’e kilitli” ifadesi fazla güçlüdür.

`STRAT-SINYAL-KAPI-CELISKISI` = KISMEN - Üreticide ortalamaya dönüş bileşenleri gerçektir (`RSI oversold +25`, `BB_dip +20`, core/analyzer.py:139-153) ve gate fiyatı EMA200 altında engeller (core/trade_gates.py:78-82); ancak üretici saf mean-reversion değildir, aynı blok `EMA+`, `Trend+`, momentum ve Ichimoku gibi trend bileşenleri de içerir (core/analyzer.py:143-160,191-215,330-337). Saatlik dip ile günlük EMA200 üstü aynı anda mümkün olduğundan “anti-korelasyon” koddan kanıtlanamaz; ölçülmesi gerekir.

## GOREV 1b - Envanterde gozden kacan / yanlis siddetlendirilmis bulgular

- `R5-CANLI-GIRIS-KILIDI-KISMI` ORTA değil en az YÜKSEK güvenlik invariantıdır: kilit yalnız `execute_buy` içindedir; short/options/parking yollarını merkezi olarak kesmez. Mevcut canlı `BOT_MODE=long_only`, options kapalı ve short paper-only olduğu için R8’in streak reset’i bugün hisse girişi açmaz, fakat planın “R5 bütün canlı girişlerden bağımsız korur” iddiası sistem düzeyinde doğru değildir.
- `CFG-PAPER-LIVE-AYRI-SISTEM` YÜKSEK değil CORE-FOCUS açısından KRİTİK’tir: agresif paper’ın eşikleri, boyutları, MTF ve çıkış geometrisi canlıdan farklıyken onun 4/4 PASS’i canlı R5 açma kanıtı olamaz.
- `REKONSTRUKSIYON-STRATEJI-DISI-ISLEMLERI-AYIRMIYOR` ORTA değil KRİTİK’tir; Metrik-1’in evrenini doğrudan bozuyor ve geçmiş/başlangıç envanteri olmayan SELL’i SHORT girişi sanabiliyor.
- R11’deki `dominant_stage` “her zaman signal_hold döner” iddiası YANLIŞTIR: `max(..., key=lambda item: item[1])` en yüksek sayıyı seçer ve priority sırası eşitliği çözer (core/funnel.py:303-321); mevcut `test_dominant_stage_priority_breaks_ties` bunu zaten doğruluyor.
- `CONSECUTIVE-ERRORS-BASARIDA-SIFIRLANMIYOR` fazla genellenmiştir: başarılı tam BUY ve SELL `bot.consecutive_errors = 0` yapıyor (core/executor.py:389,629), fakat başarılı ana döngü turu sıfırlamadığı için sayaç yine gerçek anlamda “ardışık tur hatası” değildir.
- Fractional-bracket, BE tavanı ve sinyal/EMA çelişkisi KRİTİK etiketiyle fazla kesin anlatılmıştır; sırasıyla broker davranışına bağlı, ratchet nedeniyle koşullu ve istatistiksel anti-korelasyonu kanıtlanmamış iddialardır.
- Envanterin kaçırdığı en önemli tasarım kusuru R8’in partial fill’i “trade sonucu” saymasıdır: execution leg’i `update_loss_streak` ve `record_outcome` ile sonuçlandırmak, sonradan toplamda zarar eden trade’i önce WIN olarak öğretir.
- R8 yalnız gelecek dolumları düzeltirse geçmişte zaten FILLED olmuş görünmez bacakları onaramaz; broker-tabanlı migration/backfill olmadan yeni çift yönlü Metrik-4 kalıcı FAIL verir.
- 94 sayı bağımsız 94 kök neden değildir; partial, streak, fractional ve ölçüm kusurları farklı başlıklarda tekrar sayılmıştır, fakat plan bunları yalnız “kapatılan bulgular” listesine koyarak doğrulanmış sayamaz.

## GOREV 2 - Plan incelemesi

- [FIX] R8 execution fill, tamamlanmış trade ve öğrenme outcome’unu aynı olay sayıyor -> Her fill’i immutable execution ledger’a yaz, streak ve agent outcome’u yalnız pozisyon tamamen kapandığında toplam trade PnL’iyle bir kez güncelle.
- [FIX] `PerformanceTracker.record_trade` mevcut şemada `order_id`, `strategy`, `entry_price`, `trade_id` ve dedupe alanı taşımıyor -> Append-only fill şeması ve açıkça versiyonlanmış migration ekle, performans istatistiğini fill sayısından değil kapalı episode’lardan türet.
- [FIX] R8’in `client_order_id` dedupe anahtarı retry sırasında yenileniyor (`_partial_client_id`, core/position_manager.py:885-886,1291) -> Tekillik için broker `order_id`/fill kimliği kullan ve her retry order’ını aynı position/trade episode’una bağla.
- [FIX] R8 geçmişte dolmuş fakat yazılmamış partial’ları ele almıyor -> Broker closed fills’ten idempotent, dry-run çıktılı ve provenance işaretli tek seferlik backfill/migration tanımla.
- [FIX] R8’in “kuruşu kuruşuna eşit” proof’u aynı hesaplama helper’ını iki tarafta kullanarak yanlış yeşil verebilir -> Bağımsız sabit oracle ile farklı fiyatlı çoklu partial, restart, retry, duplicate replay ve final-exit senaryolarını doğrula.
- [FIX] R9 strateji ayrımını mevcut `Fill` modelinden çıkaramaz çünkü model yalnız symbol/side/qty/price/order_id taşır -> Broker emri üretilirken kalıcı provenance journal yaz ve raporda yalnız bu journal ile doğrulanmış strateji fill’lerini kullan.
- [FIX] Eksik yerel kayıt strateji etiketi de eksik olacağından yerel etikete dayalı broker filtresi daireseldir -> Broker-order kimliği/prefix’i veya submit-öncesi intent journal’ı ile sınıflandırmayı yerel sonuç defterinden bağımsızlaştır.
- [FIX] Metrik-4 “set karşılaştırması” qty/symbol eşlemesiyle duplicate ve kısmi fill’leri karıştırabilir -> Order-id tabanlı canonical multiset karşılaştırması yap ve legacy kimliksiz satırı PASS değil UNKNOWN say.
- [FIX] R9 PASS/FAIL ile örnek yetersizliğini aynı exit kodunda topluyor -> PASS, FAIL, NOT_READY ve UNKNOWN için ayrı durum/exit sözleşmesi tanımla; n≥20 ve ≥30 işlem günü sağlanmadan PASS yasak olsun.
- [FIX] R9 `RAPOR-BENCHMARK-VE-EQUITY-OKUMUYOR` bulgusunu kapattığını söylüyor fakat kapsam ve proof’ta benchmark/equity testi yok -> Hesap getirisi, strateji getirisi, SPY getirisi ve config/profile fingerprint’i için fixture ve PASS kuralı ekle.
- [FIX] Agresif paper verisi canlı parametre setini temsil etmiyor -> R5 açma kanıtını exact live-profile çalışan ayrı paper mirror’dan iste ve rapora config hash yaz.
- [DEFER] R10’daki sentiment, EMA fail-closed ve split düzeltmeleri etkin karar dağılımını değiştiriyor -> Ölçüm/ledger baseline’ı kurulup soak alınana kadar strateji-davranışı değiştiren bu maddeleri sonraki cycle’a taşı.
- [FIX] Kill switch sınıf ayrımı çözümü gereksiz derecede karmaşık ve API kesintisini hâlâ tasfiye sebebi yapabilir -> Kod/API hatasında yalnız yeni risk açmayı durdurup alarm ver; otomatik `close_all_positions` yetkisini günlük kayıp ve manuel kill ile sınırla.
- [DEFER] Partial sonrası TP restorasyonu gerçek canlı çıkış topolojisini değiştiriyor ve “açık pozisyonlara dokunma” non-goal’ıyla çelişiyor -> Ayrı safety rock’ında broker OCO semantiği ve açık pozisyon migration’ıyla ele al.
- [FIX] “Hiçbir config değeri değişmesin” literal olarak R11’in timeout/policy ihtiyacıyla çelişiyor -> Mevcut strateji tuning değerlerini dondur, fakat yeni safety anahtarları ve state schema version eklenmesine açıkça izin ver.
- [KILL] R11 `dominant_stage` düzeltmesi yanlış bir koda-okuma iddiasına dayanıyor -> Bu alt maddeyi tamamen çıkar ve yalnız modla ilgisiz sayaç karışımını düzelt.
- [DEFER] WARN streak’i zaman geçince eritmek yeni kanıt olmadan riski yeniden açar ve Core Focus’a hizmet etmez -> Önce episode-bazlı doğru streak üret; risk politikasını ölçüm sonrasında ayrıca kararlaştır.
- [FIX] R11 fractional yolu iki zıt davranışı “VEYA” diye açık bırakıyor -> Bu cycle’da yalnız fail-closed iptal + `FRACTIONAL_NO_BRACKET` telemetrisi seç; iki-adımlı canlı giriş fallback’ini açma.
- [FIX] `would_enter` yalnız gate sonrasında artırılırsa cash, sizing ve broker uyumluluğunu görmeden yanlış isimlendirilir -> Side-effect-free `plan_entry` çıkar veya sayacı dürüstçe `reached_executor` olarak adlandır.
- [FIX] Aynı sembolü günde bir saymak blok sıklığını gizler -> Event sayısı ile unique-symbol sayısını ayrı tut ve dominant teşhisi ikisini birlikte raporlasın.
- [FIX] R5 yalnız long executor testiyle “bağımsız güvenlik” kabul ediliyor -> Bütün risk-açan stock/short/options/BearBrain yollarının paylaştığı merkezi `can_open_new_risk` guard’ı ve entegrasyon testi ekle.
- [DEFER] R12 tek başına bir cycle büyüklüğünde ve tarihsel haber/temel veri, replay clock, broker state ve fill modeli olmadan dürüst olamaz -> R12’yi ayrı cycle’a çıkar.
- [FIX] R12 `--selftest` yalnız fonksiyon çağrı sayacı/spying ile mock tarafından kandırılabilir -> Canlı ve backtest’in aynı saf DecisionEngine’i aynı golden event tape üzerinde çalıştırıp aynı karar/exit trace’ini üretmesini zorunlu kıl.
- [FIX] AgentCoordinator’u çağırmak aynı sistemi ölçtüğünü kanıtlamaz çünkü geçmiş-zamanlı sent/fund/risk girdileri tanımlanmamış -> Point-in-time veri sözleşmesi, enjekte edilmiş clock ve look-ahead yasakları tanımlanmadan walk-forward sonucu yayınlama.
- [FIX] `pytest tests/` R10 ve R12 için güçlü kanıt değildir çünkü `tests/test_full_system.py` conftest tarafından collection’dan çıkarılıyor -> Sentiment, kill switch, EMA cache ve backtest parity için gerçekten toplanan pytest testleri ekle.
- [KILL] Beş rock ve 94 bulguyu tek cycle’da kapatma iddiası review ve migration riskini kontrol edilemez yapıyor -> Cycle’ı güvenlik guard’ı + ledger truth + measurement truth ile sınırla, kalan doğrulanmış kökleri issues listesine taşı.

## Kapsam onerisi

Mevcut R8→R9 bağımlılığı doğrudur, fakat önüne küçük bir R0 güvenlik adımı gelmelidir: API/kod hatasının otomatik tasfiye yetkisini kaldır ve R5’i bütün risk-açan yollar için merkezi invariant yap. Ardından R8 yalnız immutable fill ledger, episode aggregation, idempotency ve geçmiş migration olsun; partial fill streak’i veya agent outcome’u güncellemesin. R9 bundan sonra çift yönlü reconciliation, strateji provenance’ı, live-profile fingerprint’i, benchmark ve sert NOT_READY koşullarını kursun.

Bu cycle’dan R10’un sentiment/EMA/split davranış değişikliklerini, R11’in streak unlock ve iki-adımlı fractional girişini, R12’nin tamamını keserdim. R11’den en fazla yön-bazlı funnel sayaçları ve dürüst `reached_executor` telemetrisi kalabilir. R12 ayrı cycle olmalıdır.

Mevcut test etkisi statik olarak şöyledir:

- R11 iki-adımlı canlı fallback seçerse `tests/test_protection_invariant.py::test_live_bracket_rejection_does_not_submit_market_fallback` bilinçli olarak kırılır.
- Partial sonrası yeni TP emri eklenirse `test_paper_301_percent_reaches_fill_verified_partial_and_restores_one_stop`, `test_live_301_percent_does_not_partial_but_live_501_does`, `test_nofill_paths_leave_partial_false_and_restore_real_qty_stop`, `test_single_share_partial_fill_is_not_counted_as_half_sale_and_retries_remainder`, `test_restart_reconciles_same_cid_and_never_submits_second_half_sale` ve `test_terminal_nofill_budget_stops_fourth_cancel_submit_churn` fixture’ları TP emrini modellemediğinden kırılır veya genişletilmelidir.
- `phantom_count` dönüş sözleşmesi değiştirilirse `tests/test_r6_entry_quality.py::test_measurement_reconstructs_partial_and_flags_local_broker_mismatch` güncellenmelidir.
- `print_report` imzası değiştirilirse `test_header_projection_and_tempo_warning`, `test_evidence_before_first_telemetry_is_legacy_miss_and_fails`, `test_mandatory_post_telemetry_bar_without_event_is_integrity_fail`, `test_authoritative_state_missing_source_makes_metric4_unknown` ve `test_empty_telemetry_has_no_completeness_miss_and_metric4_is_unknown` etkilenir; geriye uyumlu keyword/default parametrelerle kırılma önlenebilir.
- `tests/test_r4_adversarial.py::test_dominant_stage_priority_breaks_ties` kırılacak test değildir; tam tersine R11’deki dominant-stage iddiasını şimdiden çürütür.
- Toplanan 138 pytest içinde sentiment işareti, dış ana-döngü kill sınıflandırması, günlük EMA None cache’i veya gerçek backtest/canlı parity’si için koruma yoktur; suite’in yeşil kalması bu rock’ların doğru çalıştığını kanıtlamaz.

VERDICT: NOT YET
### Visionary response (Claude) , Round 1

**Once: Codex'in kod iddialarindan uctu BAGIMSIZ olarak dogrulandi (Claude kendi kostu).**

- `core/funnel.py:303-321` okundu: `max(...)` generator'u `priority` sirasinda
  tuketiyor, Python `max` esitlikte ILK maksimumu dondurur -> oncelik demeti
  esitlikte ZATEN uygulaniyor. Mevcut `test_dominant_stage_priority_breaks_ties`
  bunu kilitlemis. **Codex hakli, onceki iddiam yanlisti.**
- `tests/conftest.py` okundu: `collect_ignore = ["test_full_system.py"]`.
  **Codex hakli** , 138 pytest yesil olmasi 115-testlik tam sistem harness'ini
  KAPSAMIYOR. Tum PROOF komutlari duzeltildi.
- `config.py:449` okundu: `max_consecutive_errors: 3` (5 degil) ve
  `stock_bot.py:494` `reset_error_count()` cagiriyor. **Codex hakli**,
  envanterin "esik 5 / hic sifirlanmaz" ifadesi fazla genellemeydi.
- `core/position_manager.py:885-886` + `:1290` okundu: `_partial_client_id`
  her cagride `uuid4()` uretiyor VE retry'de yeniden yaziliyor.
  **Codex hakli** , dedupe anahtari olamaz.

**Dogrulama sirasinda Claude'un ENVANTERDE OLMAYAN yeni bulusu:**
`news_analyzer.py:473` , `result["label"] == "negative" and result["score"] > 0.6`.
Negatif etikette `score` negatiftir, dolayisiyla kosul HICBIR ZAMAN dogru olamaz;
ELEVATED risk dali olu koddur. Yeni ID: `SENT-ELEVATED-DALI-OLU-KOD`, R12'ye eklendi.
Ayrica isaret hatasi sayisal olarak kostulup dogrulandi: iyi haber `+27.6`,
kotu haber `+26.4` (ikisi de POZITIF).

**Dispozisyonlar:**

- ACCEPTED: R8 fill/trade/outcome'u ayni olay sayiyor -> Fill ledger ile episode
  toplama ayrildi; `update_loss_streak` ve `record_outcome` YALNIZ episode
  kapanisinda, toplam PnL ile bir kez calisir. (Yeni R9)
- ACCEPTED: `record_trade` semasi yetersiz -> append-only fill semasi +
  `ledger_schema_version` + surumlenmis migration eklendi.
- ACCEPTED: `_partial_client_id` dedupe anahtari degil -> anahtar broker `order_id`.
- ACCEPTED: Gecmis dolmus-yazilmamis bacaklar -> `--dry-run` cikisli, idempotent,
  provenance isaretli tek seferlik backfill R9 kapsamina alindi.
- ACCEPTED: "kurusu kurusuna" proof sahte yesil verebilir -> BAGIMSIZ SABIT oracle
  + coklu partial/restart/retry/duplicate/final senaryolari zorunlu kilindi.
- ACCEPTED: `Fill` modelinden strateji ayrimi cikarilamaz -> provenance submit
  ANINDA yazilir; rapor yalniz journal ile dogrulanmis fill'leri sayar.
- ACCEPTED: Yerel etikete dayali filtre dairesel -> siniflandirma yerel sonuc
  defterinden bagimsiz (submit-oncesi intent journal).
- ACCEPTED: Metrik-4 qty/symbol eslemesi -> `order_id` tabanli kanonik multiset;
  kimliksiz legacy satir UNKNOWN.
- ACCEPTED: PASS/FAIL/NOT_READY/UNKNOWN ayri sozlesme; n>=20 + 30 gun olmadan
  PASS YASAK.
- ACCEPTED: Benchmark/equity iddiasinin proof'u yoktu -> rapor sozlesmesine
  hesap/strateji/SPY getirisi + config fingerprint eklendi.
- ACCEPTED: Agresif paper canli kaniti olamaz -> `CFG-PAPER-LIVE-AYRI-SISTEM`
  KRITIK'e cikarildi; R5 kaniti yalniz canli-profil mirror'dan.
- ACCEPTED: R5 yalniz `execute_buy` icinde -> merkezi `can_open_new_risk` guard'i
  yeni R8 (ilk rock) olarak plana girdi; `R5-CANLI-GIRIS-KILIDI-KISMI` YUKSEK'e cikti.
- ACCEPTED: Kill switch sinif ayrimi karmasik -> basitlestirildi: API/kod hatasinda
  otomatik tasfiye YOK, yalniz "yeni risk acma + alarm"; tasfiye gunluk-zarar ve
  manuel kill'e birakildi.
- ACCEPTED: `dominant_stage` iddiasi yanlis -> alt madde KILL edildi; yerine gercek
  sorun yazildi (`signal_hold` sayica baskin oldugu icin dominant teshis degeri yok;
  downstream asamalar varken `signal_*` dominant secilmez).
- ACCEPTED: "hicbir config degismesin" kisiti celiskili -> yeniden yazildi:
  strateji TUNING degerleri donduruldu, yeni GUVENLIK anahtarlari + sema surumu serbest.
- ACCEPTED: R11 fractional "VEYA" belirsizdi -> tek secim: fail-closed iptal +
  `FRACTIONAL_NO_BRACKET` telemetrisi. Iki-adimli canli fallback ACILMIYOR;
  `test_live_bracket_rejection_does_not_submit_market_fallback` bilincli korunuyor.
- ACCEPTED: `would_enter` yaniltici -> `reached_executor` olarak yeniden adlandirildi.
- ACCEPTED: Sembol basina tek sayim blok sikligini gizler -> olay sayisi ve
  benzersiz-sembol sayisi AYRI raporlanir.
- ACCEPTED: `pytest tests/` guclu kanit degil -> her PROOF'a `py tests/test_full_system.py`
  eklendi.
- ACCEPTED (DEFER): Backtest/canli parity (eski R12) -> ayri cycle. `--selftest`
  spy'i mock'la kandirilabilir; dogru cozum saf `DecisionEngine` + golden event
  tape + point-in-time veri sozlesmesi. NON-GOAL'a tasindi.
- ACCEPTED (DEFER): EMA cache zehirlenmesi, split-duzeltmeli bar, partial sonrasi
  TP restorasyonu -> uçu de karar/cikis topolojisini degistirir; NON-GOAL'a tasindi,
  RF-ISSUES-3'te acik kaldi.
- ACCEPTED (DEFER): WARN streak zaman asimi -> NON-GOAL. Gerekce: R5 kapaliyken
  canliya marjinal etkisi SIFIR, ve R9 dogru episode-bazli streak uretince sayac
  zaten dogru degerine oturur.
- ACCEPTED: `phantom_count` / `print_report` imza degisiklikleri mevcut testleri
  kirar -> geriye uyumlu keyword/default parametre kisiti riskler tablosuna eklendi.

- REJECTED: "[KILL] Cycle'i guvenlik + ledger + measurement ile SINIRLA, digerlerini
  issues'a tasi" , kismen reddedildi. Guvenlik/ledger/olcum onceligi KABUL edildi
  ve cycle bunlarla basliyor; ancak SentAgent isaret hatasi cycle'da KALIYOR (R12).
  Gerekce: bu bir strateji ayari degil, tek satirlik bir ISARET hatasi, ve olcum
  baseline'ini bilerek bozuk bir haber girdisiyle kurmak baseline'i degersiz kilar.
  Codex'in atfedilebilirlik itirazi kabul edilerek EN SONA ve AYRI COMMIT'e alindi
  , boylece etkisi baseline'dan ayrilabilir olur.
- REJECTED: "94 sayi bagimsiz 94 kok neden degildir" tespiti DOGRU ama plan
  degisikligi gerektirmiyor; plan zaten "kapatilan bulgular" listesini kanit degil
  IZLENEBILIRLIK haritasi olarak kullaniyor. Kanit yalnizca PROOF komutlaridir.
