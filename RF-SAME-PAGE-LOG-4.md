# RF-SAME-PAGE-LOG-4.md , Clarity Break #4 mutabakat kaydı

> Plan dosyası: `RF-PLAN-4.md`. Integrator: Codex CLI `gpt-5.6-sol`, effort `high`.
> Baseline: `git HEAD = aa70225`, `py -m pytest tests/ -q` -> 250 passed (Claude koştu).

## Round 1

### Integrator findings (Codex, verbatim)

```
- [KILL] R15's `/0.85` renormalization converts missing data into stronger evidence: a lone TechAgent BUY at 60 confidence changes from `ws=15`/HOLD to `ws=17.65`/BUY with confidence 35.3, passing paper's threshold 30 -> Disable SocialAgent without renormalizing scores until post-change outcome data supports recalibrating the score and confidence scales.
- [FIX] R15's majority diagnosis is wrong because disabling SocialAgent leaves four voters and `(4//2)+1=3`, exactly the current threshold; with Fund unavailable and Risk normally HOLD, only Tech and Sent can BUY -> State that R16 is required to restore majority reachability and prove the combined R15+R16 behavior.
- [KILL] The generic living-agent majority formula has no benefit for this change and silently loosens the gate if another agent is disabled, while RiskAgent SELL can count toward a majority and receive the 1.2 confidence boost -> Keep the explicit three-alpha-agent quorum this cycle and treat RiskAgent solely as a veto unless role-aware quorum behavior is separately calibrated.
- [FIX] `WEIGHTS` is not a single source because `AgentPerformanceTracker.DEFAULT_WEIGHTS` duplicates it and `stock_bot.py` overwrites `coordinator.WEIGHTS` every symbol, so coordinator-only R15 tests can pass while production ignores the intended resolver -> Add one enable-aware weight resolver used by both components and an integration test through the real `stock_bot` assignment path.
- [FIX] Current `agent_stats` schema records only boolean `data_ok` for votes that exist, so omitting SocialAgent cannot truthfully encode `kapali` and the reporting tool may merely omit it -> Version and migrate the schema with explicit enabled/disabled state and test the actual `ajan_raporu` output.
- [FIX] "Permanent blindness" is not established by the code or one Reddit 403 because SocialAgent also has a conditional X/Nitter source -> Keep the policy default-off decision but distinguish `DISABLED_BY_POLICY` from observed `SOURCE_UNAVAILABLE`.
- [FIX] R16's per-mode FundamentalAnalyzer counter cannot enforce a key-wide quota because the same Alpha Vantage key is also called by `news_analyzer.py`, and live/paper run in separate containers with separate state volumes -> Centralize quota reservation by API key across both analyzers and services, disable AV News, or provision separate keys; test total calls across consumers.
- [FIX] R16's required "corrupt file starts clean" behavior can erase the daily counter and permit another 25 real requests -> Persist quota reservations atomically before each request and fail closed as exhausted on unreadable counter state while recovering payload cache entries independently.
- [CLARIFY] A counter keyed by unspecified local date can reset at different moments on Los Angeles development and UTC containers and may not match Alpha Vantage's quota boundary -> Which documented provider reset timezone will govern quota accounting, with America/New_York retained separately for trading-day telemetry?
- [FIX] R16 does not distinguish quota payloads from timeouts, HTTP failures, malformed JSON, or unsupported symbols, so negative caching can suppress a transiently recoverable source all day -> Use typed outcomes and test HTTP-200 quota messages separately from retryable failures and genuine no-data responses.
- [FIX] `LIVE_LOCK_R5` does guarantee zero new stock-long, stock-short, option, and bear-ETF entries, but it exempts index parking and does not prove the strategy would execute after unlocking -> Describe it as the sufficient blocker of strategy entries and require shadow funnel/executor evidence before opening it.
- [FIX] R17's four states omit `UNKNOWN/DEGRADED`, allowing corrupt or future heartbeats, broker failures, kill/risk-halt state, or a repeatedly failing scanner to fall into a misleading healthy classification -> Add fail-closed precedence and structured per-profile status where uncertainty can never become `SAGLIKLI`.
- [FIX] A recent broker fill cannot establish `SAGLIKLI` because it may be parking, a manual trade, or an exit while the signal pipeline is dead -> Base pipeline health on fresh scan/decision telemetry with provenance and report recent strategy fills as a separate dimension.
- [FIX] Current `health_check.py` imports one `TRADING_MODE`, one resolved key pair, and one `state_path`, while Docker mounts live and paper state separately, so a fake dual-profile test can pass although one deployed container cannot observe the other -> Run the check inside each container and aggregate results, or provide an explicitly mounted two-profile observer with distinct credentials and state roots.
- [FIX] R17's fixed 72-hour startup sweep still permanently misses fills after a 73-hour outage, while the proposed proof would pass by checking only the configured 72/24 values -> Persist a last-successful high-water mark and sweep from it with pagination and retention bounds; test outages beyond 72 hours and production wiring.
- [FIX] R18 cannot recompute new weights from `agent_stats.json` because it stores marginal histograms rather than joint per-decision votes, confidences, weights, and availability, so its synthetic proof can pass while production input is unusable -> Add bounded, versioned, replayable joint samples with config/commit identity before claiming production calibration.
- [KILL] R18's signal-count table contains no realized outcomes, PnL, drawdown, or benchmark comparison, so it cannot justify an alpha-maximizing threshold recommendation -> Retain it only as a descriptively named threshold-sensitivity report and remove automatic recommendations.
- [DEFER] Valid R18 calibration needs post-R15/R16 live-profile shadow data that cannot exist at the end of the implementation cycle -> The honest cycle-end result is `NOT_READY` with no threshold recommendation, followed by a defined collection period.
- [FIX] R19 cannot open the live gate from the existing measurement tool because its CLI accepts only paper mode and `measured_profile()` hardcodes `PAPER_AGGRESSIVE`, which the report itself says is not live evidence -> Require a live-config shadow profile and reject `AC` for every paper-aggressive artifact.
- [FIX] R19's proof says four metric PASS values imply `AC` but omits the existing `n>=20` and `>=30` trading-day prerequisites, allowing the exact false green previously repaired in `overall_status()` -> Test both prerequisites independently and require all metrics, sample size, duration, profile, and freshness before `AC`.
- [FIX] R15/R16 change decision behavior without starting a new measurement epoch, so R19 could combine old-strategy trades with the new commit and report a stale green -> Bind every observation to config/weight/commit identity and reset the epoch at deployment.
- [FIX] R19 proposes a second decision engine although `tools/olcum_raporu.py::overall_status()` already implements fail-closed four-state aggregation and sample prerequisites -> Add a thin read-only presentation wrapper around that function instead of duplicating gate logic.
- [CLARIFY] The stated 250-pass baseline could not be rerun in the mandated read-only environment because `tests/conftest.py` requires a writable temporary directory, although the only diff from baseline commit `5aea694` to current HEAD is `RF-PLAN-4.md` -> Re-run the exact suite in writable CI and attach its commit-scoped result before implementation.
VERDICT: NOT YET
```

### Visionary response (Claude)

Her bulgu koda karşı bağımsız doğrulandı. Sonuçlar:

**ACCEPTED , R15 renormalizasyon KILL'i. Bu bulgu planın en tehlikeli parçasını yakaladı.**
Doğrulama: `core/agent_coordinator.py:419-423` `weighted_score` HAM bir toplamdır,
hiçbir yerde toplam ağırlığa bölünmez. Eşikler mutlaktır: `ws > 15` (satır 442) ve
`confidence = abs(ws) * 2.0` (satır 473). SocialAgent'ın 0.15'ini kalanlara dağıtmak
her ws'yi 1.176 kat büyütür, yani AYNI kanıt daha yüksek skor üretir. Codex'in verdiği
sayı birebir doğru: tek başına TechAgent BUY conf 60 -> ws 15 (HOLD, çünkü `> 15`
değil) iken renormalizasyondan sonra ws 17.65 (BUY) ve confidence 35.3, paper eşiği
30'u geçiyor. **Planım, kör bir ajanı kaldırma kılığında her giriş kapısını
gizlice gevşetiyordu.** Gerçek parada tam olarak kaçınmamız gereken hata.
-> RF-PLAN-4.md R15: renormalizasyon TAMAMEN çıkarıldı. Kapalı ajanın payı
   dağıtılmaz; ağırlık toplamı 1.0'ın altına düşer ve bütün eşikler kalibre
   edildikleri ölçekte kalır.

**ACCEPTED ve daha da ileri götürüldü , çoğunluk teşhisi yanlıştı.**
Doğrulama: çoğunluk bir KAPI DEĞİL. `buy_count >= 3` (satır 436) bir `elif`
zincirinin ilk dalı; `weighted_score > 15` (satır 442) çoğunluk olmadan da işlem
açar. Çoğunluğun tek etkisi `confidence *= 1.2` (satır 475). Yani benim kullanıcıya
da söylediğim "çoğunluk imkansız, o yüzden işlem yok" iddiası YANLIŞTI.
-> RF-PLAN-4.md'den çoğunluk kapısı değişikliği tamamen çıkarıldı; teşhis metni
   düzeltildi ve İhsan'a da düzeltme bildirildi.

**ACCEPTED , genel çoğunluk formülü KILL'i.** Doğrulama: `votes` listesi RiskAgent'i
de içeriyor (satır 405-411), dolayısıyla RiskAgent SELL'i `sell_count`'a sayılıyor
ve `majority` üzerinden 1.2 güven çarpanı alıyor. Formül hem faydasız hem riskli.
-> Çıkarıldı.

**ACCEPTED ve bulgu olduğundan ağır , WEIGHTS tek kaynak değil.**
Doğrulama: `stock_bot.py:1298` her sembolde `self.coordinator.WEIGHTS = dynamic_weights`
yapıyor ve `core/agent_performance.py:219-221` bu ağırlıkları ZATEN toplamı 1.0
olacak şekilde normalize ediyor. Yani sınıf sabiti `WEIGHTS = {0.25, 0.20, ...}`
üretimde ÖLÜ; gerçek ağırlıklar performans bazlı ve zaten normalize. Coordinator
üstünde test yeşil yanarken üretim başka değer kullanırdı.
-> R15 artık `AgentPerformanceTracker` yolundan geçiyor ve testi `stock_bot`
   atama yolunun üstünden kanıtlıyor.

**ACCEPTED , agent_stats şeması `kapali` kodlayamıyor.** Doğrulama:
`core/agent_stats.py:128` `"data_ok": {"true": 0, "false": 0}` , sadece ikili.
-> R15'e şema sürümü + üçüncü durum + `ajan_raporu` çıktı testi eklendi.

**ACCEPTED , DISABLED_BY_POLICY ve SOURCE_UNAVAILABLE ayrımı.** Haklı: 403 bir
gözlem, kapatma bir politika. İkisi karışırsa kaynak geri geldiğinde fark edilmez.

**ACCEPTED , AV kotası anahtar genelinde.** Doğrulama: `core/news_analyzer.py:92`
aynı `ALPHA_VANTAGE_KEY` değişkenini okuyor. Üstelik live ve paper ayrı
konteynerlerde ayrı state hacimleriyle koşuyor, yani state dosyası üzerinden
koordinasyon imkansız. -> R16 yeniden yazıldı; ayrıca İhsan'a tek soru soruldu
(ikinci ücretsiz AV anahtarı mı, bütçe bölüşümü mü).

**ACCEPTED , bozuk sayaç fail-closed olmalı.** Planımın (d) maddesi tam tersini
istiyordu ("bozuk dosyada temiz başla"), bu 25 çağrılık ikinci bir tur açardı.
-> Yük cache'i temiz kurtarılır, KOTA SAYACI okunamıyorsa tükenmiş sayılır.

**ACCEPTED , kota saat dilimi (CLARIFY'a Visionary cevabı).** Karar: kota muhasebesi
**UTC** (Alpha Vantage ücretsiz katman günlük sınırı UTC gün sınırında sıfırlanır),
işlem-günü telemetrisi ayrıca **America/New_York**. İki saat dilimi ayrı alanlarda
tutulur, birbirine karışmaz. Kullanıcı kapısı değil, mimari karar.

**ACCEPTED , tipli sonuçlar.** Kota tükenmesi, timeout, HTTP hatası, bozuk JSON ve
"sembol yok" ayrı sonuç tipleri olacak; sadece kota tükenmesi gün boyu negatif
cache'lenir, geçici hatalar tekrar denenir.

**ACCEPTED ve bu döngünün en değerli fikri , LIVE_LOCK_R5 girişleri kesiyor ama
stratejinin açılınca işlem yapacağını KANITLAMIYOR.** Bu, kilidin nasıl açılacağı
sorusunun cevabını değiştiriyor: para riske atmadan kanıt üretmek gerekiyor.
-> Yeni R18 GÖLGE DEFTER rock'u bu bulgudan doğdu. Eski R18 (kalibrasyon) düştü.

**ACCEPTED , R17'nin dört durumu yetersiz.** UNKNOWN/DEGRADED yok, belirsizlik
SAGLIKLI'ya düşebiliyor. -> Fail-closed öncelik sırası eklendi: belirsizlik asla
SAGLIKLI olamaz.

**ACCEPTED ve canlı hesapta birebir yaşanan durum , taze dolum sağlık kanıtı değil.**
Canlı hesabın tek hareketi SPY PARKI; strateji ölüyken "son işlem taze" diye yeşil
yanardı. -> Sağlık, karar telemetrisinin tazeliğine bağlandı; dolumlar ayrı boyut
olarak raporlanıyor, provenance ile.

**ACCEPTED , tek süreç iki konteyneri gözleyemez.** -> health_check her konteynerde
kendi profilini raporlar; birleştirme ayrı ve açıkça iki kaynaklı.

**ACCEPTED , sabit 72 saat penceresi 73 saatlik kesintide yine delik bırakır ve
benim PROOF'um sadece 72/24 sayılarını kontrol ederek yeşil yanardı.** Bu tam
olarak "kanıt geçerken özellik bozuk" örneği. -> Sabit pencere yerine **son başarılı
süpürge yüksek-su işareti** (high-water mark) kalıcı tutulur, süpürge oradan başlar,
sayfalama ve saklama sınırı ile. Test kesinti > pencere senaryosunu koşar.

**ACCEPTED , agent_stats marjinal histogram tutuyor, ortak (joint) örnek değil.**
Doğrulama: şema ajan başına sayaç; karar başına oy/güven/ağırlık/erişilebilirlik
birlikte saklanmıyor. Kalibrasyon bu girdiden yapılamaz.
-> Yeni R18 tam olarak bu ortak örneği kaydediyor.

**ACCEPTED , sinyal sayısı tablosu eşik ÖNEREMEZ (KILL).** İçinde gerçekleşmiş
sonuç, PnL, drawdown, benchmark yok. -> Otomatik öneri kaldırıldı.

**ACCEPTED , kalibrasyon bu döngüde bitemez (DEFER).** Dürüst döngü sonu NOT_READY.
-> Kalibrasyon RF-ISSUES-4.md'ye taşındı, toplama süresi tanımlandı.

**ACCEPTED , R19 paper profiline kilitli.** Doğrulama: `tools/olcum_raporu.py:1179`
`measured_profile()` `PAPER_AGGRESSIVE` döndürüyor (satır 1203).
-> Kapı canlı-config gölge profili şart koşuyor; paper-aggressive eseri gördüğünde
   `AC` diyemiyor.

**ACCEPTED , n>=20 ve >=30 işlem günü ön şartları atlanmıştı.** -> Kapı sözleşmesine
eklendi, her biri bağımsız test ediliyor.

**ACCEPTED , yeni ölçüm epoch'u.** R15/R16 davranışı değiştiriyor; eski strateji
işlemleriyle yeni commit'i karıştırmak bayat yeşil üretir. -> Her gözlem
config+ağırlık+commit kimliğine bağlanıyor, deploy'da epoch sıfırlanıyor.

**REJECTED (isim) / ACCEPTED (öz) , `overall_status()` diye bir fonksiyon YOK.**
Doğrulama: `grep -rn "overall_status"` sıfır sonuç. Ama Codex'in ÖZÜ doğru:
`tools/olcum_raporu.py:77-88` `Status` enum'u (UNKNOWN/NOT_READY dahil) ve bir
şiddet sıralaması zaten var. -> İkinci karar motoru yazılmıyor; R19 mevcut `Status`
toplayıcısının ince, salt okunur sarmalayıcısı oluyor. Fonksiyon adı yanlıştı,
tavsiye doğruydu.

**ANSWERED , 250 baseline.** Codex salt okunur kumda koşamadı (`tests/conftest.py`
yazılabilir tmp istiyor). Ben koştum: `HEAD=aa70225`, `py -m pytest tests/ -q` ->
**250 passed, 9.39s**. Plan kusuru değil, kum kısıtı.

### Sonuç: plan yeniden yazıldı

R15 zararsızlaştırıldı (renormalizasyon ve çoğunluk değişikliği çıktı), R16
anahtar-geneli ve fail-closed yapıldı, R17 high-water mark'a geçti, eski R18
(kalibrasyon) DÜŞTÜ ve yerine GÖLGE DEFTER geldi, R19 mevcut Status toplayıcısını
sarmalıyor. Round 2'ye revize planla gidiliyor.

## Round 2

### Integrator findings (Codex, verbatim)

```
- [FIX] R15’s fixed `0.85` invariant is wrong for performance-adjusted weights because SocialAgent’s normalized share is not always `0.15` -> Compute the existing five-agent normalized vector unchanged, then mask SocialAgent without renormalizing; require `active_sum == 1 - pre-mask SocialAgent weight`.
- [FIX] R15’s 20-scenario ON/OFF comparison could pass after both paths have been rewritten identically while both differ from current production behavior -> Compare against frozen pre-R15 golden outputs and dynamic-performance histories, including `ws=±15` boundaries, majority/non-majority, veto, short boost, and confidence saturation.
- [FIX] R16 promises key-wide enforcement but stores state in separate live/paper volumes, so two local counters can each authorize 25 calls while its two-consumer single-process proof still passes -> Assign explicit persistent per-container budgets totaling 25, such as live 13 and paper 12, or provide an actually shared locked quota store; test two independent processes.
- [FIX] `AV-ANAHTARI-IKI-KONTEYNER` cannot remain deferred while R16 claims a hard key-wide maximum of 25 -> Move deterministic cross-container budget partitioning into R16; a second key may remain deferred.
- [FIX] A 24-hour on-demand cache plus deterministic symbol order can repeatedly spend each container’s allowance on the same early symbols, so the claimed two-day coverage is not guaranteed -> Persist a round-robin or oldest-first refresh cursor and prove every eligible symbol is refreshed within the stated horizon across restarts.
- [FIX] R16 says stale fundamentals remain usable but defines only a 24-hour TTL, leaving it unspecified whether expired data is returned, discarded, or trusted indefinitely -> Define stale-while-revalidate behavior, record source timestamp and age on every decision, impose a maximum stale age, and become `SOURCE_UNAVAILABLE` beyond it.
- [FIX] R16’s proposed `fund_source_quota` funnel event is currently silently discarded because it is absent from `DailyFunnel.STAGES` -> Add and migrate the stage plus reporter coverage, or record it as a `gate_block` reason, and test persisted production output.
- [FIX] Fresh/stale/verisiz “symbol counts” can become inflated scan counts because the same symbol is evaluated repeatedly -> Report unique symbols plus age distribution per profile and test repeated evaluations do not increase coverage.
- [FIX] R17 defines five states but its dual-profile proof expects `UNKNOWN`, which is absent from that state contract, while current `check_health()` returns only a boolean -> Define a structured per-profile and aggregate status schema with documented exit semantics, explicitly placing `UNKNOWN` in the fail-closed ordering.
- [FIX] R17’s high-water mark is unsafe unless it advances only after every broker page is complete and every required ledger write succeeds -> Use an overlap-safe committed watermark and test a mid-page failure and a ledger-write failure are retried without gaps before advancing it.
- [FIX] R17 does not define first-start behavior when no high-water mark exists or the outage predates broker retention -> Bootstrap from the measurement epoch or verified backfill boundary and report `DEGRADED/UNKNOWN` when complete recovery is impossible.
- [FIX] R18 records coordinator decisions rather than proving which orders would reach the broker, while LIVE_LOCK_R5 protects stock, queue, short, option, and bear-ETF paths after different downstream checks -> Capture intents at every centralized lock rejection after running the same deterministic order-planning logic used by real execution.
- [FIX] A stateless shadow ledger will repeatedly count correlated scans as independent trades and assumes permanently flat cash, positions, PDT, sector exposure, and loss-streak state -> Maintain a stateful shadow portfolio with deduplication, capital reservation, concurrent positions, and simulated closed episodes.
- [FIX] Hypothetical entry price and N-day return systematically omit spread, latency, rejection, non-fill, slippage, partial fills, fees, and market-path-dependent exits -> Record timestamped bid/ask and order intent, apply conservative fill/no-fill rules, replay the real stop/TP/trailing/partial state machine, and mark ambiguous outcomes `UNKNOWN`.
- [FIX] R18’s Done statement promises outcomes and SPY-relative results, but its proof contains no maturation, benchmark alignment, exit simulation, idempotency, or lookahead test and calls enrichment a future step -> Either include and prove the outcome labeler in R18 or make R18 collection-only and state that R19 must remain `NOT_READY`.
- [FIX] R18’s “preserve the current epoch” rule can violate its own file-size cap when the current epoch alone grows without bound -> Use bounded chunks or bounded sampling within the current epoch while preserving explicit dropped-sample counts.
- [FIX] Identity binding is not trustworthy if `commit_sha` is `UNKNOWN` or `config_hash` omits effective environment, agent, short, option, regime, and gate settings -> Canonically hash the complete effective live decision profile and make missing identity ineligible for R19.
- [FIX] R19 cites lines 77-88 as the existing collector, but those lines only define `Status` and exit codes; the actual aggregator is `gate_status()` at `tools/olcum_raporu.py:1160` -> Name and wrap `gate_status()` directly so no replacement precedence logic is introduced.
- [FIX] The existing four metrics are based on actual fills, partial executions, stop integrity, and broker-ledger reconciliation, none of which the proposed shadow records can honestly make PASS -> Define separate shadow-alpha metrics and require live-config paper execution evidence for execution metrics; never translate unavailable execution evidence into PASS.
- [FIX] RF-ISSUES-4 says calibration needs 20 “decisions” while R19 requires 20 “trades,” allowing thousands of repeated scans to satisfy `n` falsely -> Define `n` as matured, non-overlapping, stateful shadow closed episodes with known outcomes.
- [FIX] R19 proof can produce a synthetic `AC` although no valid post-deploy shadow outcomes can exist this cycle -> Require the real cycle-end command to return `NOT_READY` and defer any production `AC` claim until the shadow outcome and execution-evidence contracts are satisfied.
VERDICT: NOT YET```

### Visionary response (Claude)

Round 2'de 21 bulgu geldi, hepsi FIX. Üç kod iddiası bağımsız doğrulandı:
`gate_status()` GERÇEKTEN `tools/olcum_raporu.py:1160`'ta (Codex Round 1'deki
`overall_status()` adını kendisi düzeltti, ve benim "satır 77-88" atıfım da
yanlıştı , orası yalnız `Status` enum'u ve çıkış kodları);
`fund_source_quota` `core/funnel.py:23-38` `DailyFunnel.STAGES` içinde YOK
(eklenmeden kullanılsa sessizce yutulurdu); `check_health()` yapısal durum
döndürmüyor.

**ACCEPTED , sabit 0.85 invaryantı YANLIŞ.** Bu, Round 1 düzeltmemin içindeki
ikinci bir hatayı yakaladı. `agent_performance.py:219-221` performansa göre
ayarlanmış ham ağırlıkları normalize ettiği için SocialAgent'ın normalize payı
sabit 0.15 DEĞİL, zamanla değişir. Doğru invaryant:
`active_sum == 1.0 - maskeleme_oncesi_SocialAgent_agirligi`. Sıra da kesinleşti:
önce beş ajanlık normalize vektör değiştirilmeden hesaplanır, SONRA maskelenir.

**ACCEPTED , 20 senaryoluk ON/OFF karşılaştırması kendi kendini doğruluyordu.**
İki yeni yol birbiriyle karşılaştırılırsa ikisi birden kaymışken test yeşil yanar.
-> Karşılaştırma R15 ÖNCESİ üretim davranışından alınmış DONMUŞ ALTIN çıktılara
karşı yapılacak, ve senaryolar `ws = +-15` tam sınırlarını, çoğunluk var/yok,
RiskAgent veto, VIX short boost ve güven doygunluğunu kapsayacak.

**ACCEPTED , R16 "anahtar geneli 25" iddiası tutulamaz.** Live ve paper ayrı state
hacimlerinde; iki yerel sayaç her biri 25'e izin verirdi ve tek süreçlik kanıt
bunu göremezdi. -> İddia değiştirildi: konteyner-belirli kalıcı bütçe,
live 13 / paper 12, ve kanıt İKİ BAĞIMSIZ SÜREÇLE koşulur.
`AV-ANAHTARI-IKI-KONTEYNER` maddesi de ertelenmiş olmaktan çıkarılıp R16'ya alındı
(ikinci anahtar ertelenmiş kalıyor, bölüşüm kalmıyor).

**ACCEPTED , sabit sembol sırası + 24s cache = hep aynı ilk semboller tazelenir.**
İki günlük kapsama garantisi yoktu. -> Kalıcı yenileme imleci (round-robin /
en-eski-önce), restart'ı atlatan, ve her uygun sembolün belirtilen ufukta
tazelendiğini kanıtlayan test.

**ACCEPTED , bayat veri sözleşmesi tanımsızdı.** 24 saatlik TTL "sonra ne olacak"
sorusunu cevaplamıyordu. -> `stale-while-revalidate`, her karara kaynak zaman
damgası + yaş iliştirme, **maksimum bayatlık yaşı** ve aşılınca `SOURCE_UNAVAILABLE`.

**ACCEPTED , `fund_source_quota` sessizce yutulurdu.** -> STAGES'e eklenip göç
ettirilecek ya da `gate_block` sebebi olarak kaydedilecek; kanıt kalıcı üretim
çıktısını okuyacak.

**ACCEPTED , sembol sayıları tarama tekrarıyla şişerdi.** -> Benzersiz sembol
sayısı + profil başına yaş dağılımı.

**ACCEPTED , R17 durum kümesi tutarsızdı.** Beş durum tanımlayıp kanıtta `UNKNOWN`
bekliyordum. -> Tek tutarlı küme:
`KAPALI > DEGRADED > UNKNOWN > KILITLI > SESSIZ > SAGLIKLI`, ve şema/test uyumu
ayrıca doğrulanıyor.

**ACCEPTED , yüksek-su işareti tek başına güvenli değil.** -> İşaret yalnız her
broker sayfası eksiksiz VE gereken defter yazmaları başarılı olduktan sonra
ilerler; sayfa ortası hata ve defter yazma hatası ayrı ayrı test edilir.

**ACCEPTED , ilk açılış ve retansiyon sınırı tanımsızdı.** -> İşaret yoksa ölçüm
epoch'undan/doğrulanmış backfill sınırından başlar; kesinti broker retansiyonundan
eskiyse `DEGRADED`/`UNKNOWN`, sessiz başarı yok.

**ACCEPTED , R18 coordinator kararını kaydediyordu, broker'a ulaşacak emri değil.**
`LIVE_LOCK_R5` farklı yolları farklı aşağı-akış kontrollerinden sonra koruyor.
-> Niyet, gerçek çalıştırmanın kullandığı AYNI deterministik emir planlama
mantığı koşturulduktan sonra, merkezi kilit reddinde yakalanır.

**ACCEPTED , durumsuz gölge defter çöp üretir.** İlişkili taramaları bağımsız işlem
sayar, nakdi/pozisyonu/PDT'yi/sektörü/kayıp serisini sonsuz varsayar.
-> Durumlu gölge portföy: tekilleştirme, sermaye rezervasyonu, eşzamanlı pozisyon,
PDT, sektör maruziyeti, kayıp serisi. Episode ancak durumlu açılıp kapanır.

**ACCEPTED ve KAPSAM KARARINA dönüştü , varsayımsal fiyat sistematik olarak
iyimser.** Spread, gecikme, ret, dolmama, kayma, kısmi dolum, ücretler ve piyasa
yoluna bağlı çıkışlar yok. Codex'in sunduğu iki seçenekten ikincisini seçtim:
**R18 YALNIZ TOPLAMA olur**, sonuç etiketleme ayrı bir rock'a taşınır. Gerekçe:
dolum modeli + gerçek çıkış durum makinesi tekrarı + benchmark hizalama +
olgunlaşma + ileriye-bakış + idempotency, tek başına bir döngülük iştir; yarısını
yapmak sistematik iyimser bir kayıt üretir ve gerçek parayla kilit açma kararını
zehirler. Tam sözleşme RF-ISSUES-4.md `GOLGE-SONUC-ETIKETLEME` maddesine yazıldı.
Bu döngüde R18 sonuç, getiri ya da SPY karşılaştırması İDDİA ETMİYOR.

**ACCEPTED , boyut tavanı mevcut epoch'u aşabilirdi.** "Mevcut epoch'a dokunma"
kuralı kendi tavanını ihlal ediyordu. -> Mevcut epoch içinde sınırlı parça/örnekleme
ve **düşürülen örnek sayısının açıkça kaydı**. Sessiz veri kaybı yok.

**ACCEPTED , kimlik bağlama eksikti.** -> Etkin canlı karar profilinin kanonik
hash'i (env, ajan, short, option, rejim, kapı ayarları dahil); `commit_sha`
UNKNOWN ya da profil eksikse kayıt **R19-uygunsuz** işaretlenir.

**ACCEPTED , R19 atıfım yanlıştı.** -> `gate_status()` (satır 1160) doğrudan
sarılıyor, yeni öncelik mantığı yazılmıyor.

**ACCEPTED , bu round'un en derin bulgusu: mevcut dört metrik gölge verisiyle
dürüstçe PASS olamaz.** Metrikler gerçek dolumlara, kısmi çalıştırmalara, stop
bütünlüğüne ve broker-defter mutabakatına dayanıyor. -> Gölge-alfa metrikleri
AYRI eksen; çalıştırma metrikleri için canlı-config paper çalıştırma kanıtı şart;
**eksik çalıştırma kanıtı asla PASS'e çevrilmez, UNKNOWN kalır.**

**ACCEPTED , `n` tanımı iki dosyada farklıydı** ("karar" vs "işlem"), binlerce
tarama `n`'i şişirebilirdi. -> Tek tanım, iki dosyada AYNI kelimelerle:
*olgunlaşmış, örtüşmeyen, durumlu gölge KAPANMIŞ episode*.

**ACCEPTED , R19 sentetik `AC` üretebilirdi.** -> Döngü sonu gerçek koşunun
`NOT_READY` dönmesi sözleşmenin kendisi haline getirildi ve kanıt maddesi eklendi.

### Sonuç: plan revizyon 3

R15 doğru invaryanta ve donmuş altın çıktı regresyonuna geçti; R16 konteyner-belirli
bütçe + yenileme imleci + bayatlık sözleşmesi kazandı; R17 tutarlı durum kümesine ve
taahhüt edilmiş yüksek-su işaretine geçti; R18 durumlu ama YALNIZ TOPLAMA olarak
daraltıldı ve sonuç etiketleme RF-ISSUES-4.md'ye tam sözleşmesiyle taşındı; R19
`gate_status()`'ü sarıyor ve gölge metriklerini ayrı eksende tutuyor.
Round 3'e revizyon 3 ile gidiliyor.

## Round 3

### Integrator findings (Codex, verbatim)

```
- [FIX] Removing SocialAgent shortens `votes` to four entries, but `AgentCoordinator.decide()` reads `risk_vote = votes[4]`, causing an IndexError before any invariant is evaluated -> Locate RiskAgent by name and fail closed unless exactly one RiskAgent vote exists.
- [FIX] `active_sum == 1.0 - pre_mask_social_weight` is correct but insufficient because enabled weights can be redistributed while preserving that sum -> Also require every enabled agent’s post-mask weight to equal its pre-mask normalized weight within tolerance.
- [FIX] R15’s golden cases do not explicitly cover the `MIN_TRADES_FOR_EVAL` fallback transition that determines raw weights before normalization -> Include histories with 4 and 5 resolved samples for each agent and freeze the resulting per-agent weights and coordinator outputs.
- [FIX] Signal and confidence parity can miss a changed rounded `weighted_score`, which directly feeds BearBrain breadth in `stock_bot.py:972` -> Golden-test weighted score, majority, veto, and each enabled weight in addition to signal and confidence.
- [FIX] R16 overlooks a third Alpha Vantage consumer: `EarningsCalendar` calls `EARNINGS_CALENDAR`, so live 13 plus paper 12 fundamentals plus two calendar calls can exceed 25 -> Route every AV caller through the per-profile reservation budget and acknowledge that calendar calls reduce the fundamental allowance to at most 23.
- [FIX] The two-process quota proof does not cover concurrent same-profile processes during restart or deployment overlap, where read-modify-write reservations can race -> Add an interprocess lock and a same-profile concurrency test; atomic replacement alone is insufficient.
- [FIX] R17’s scalar precedence lets `KILITLI` mask a stale or dead decision pipeline, contradicting the claim that health depends on fresh decision telemetry -> Report runtime, decision-pipeline health, and entry authorization as separate dimensions, with locked-plus-stale never summarized as merely `KILITLI`.
- [FIX] R18 cannot maintain a stateful portfolio or produce closed episodes while exit replay and outcome labeling are explicitly deferred, because it cannot know when to release cash, update PDT, or advance loss streaks -> Make R18 an append-only intent/event collector and defer hypothetical portfolio transitions to `GOLGE-SONUC-ETIKETLEME`.
- [FIX] Entry-time bid/ask alone is insufficient for later stop, trailing, partial-exit, and fill replay if historical quote paths are unavailable or mutable -> Record the required lifecycle market observations now, or name and validate an immutable historical quote source before collection begins.
- [FIX] R18 lacks an explicit immutable intent identifier and append-only linkage contract for later labels and lifecycle events -> Store stable `intent_id`, event sequence, source timestamps, asset capabilities, order parameters, state snapshot, and schema-versioned label references now to avoid rewriting collected data.
- [FIX] Dropping or sampling current-epoch observations can bias later alpha measurement even when the dropped count is reported -> Specify deterministic stratified sampling and coverage thresholds, and force R19 to `UNKNOWN` when retained evidence is incomplete.
- [FIX] R19 never states that shadow-alpha and live-config-paper execution axes must both independently PASS, each with its own sample count, duration, epoch, and profile identity -> Invoke `gate_status()` separately per axis and permit `AC` only when both results are PASS and identities match.
- [CLARIFY] No rock or deferred contract defines how live-config paper execution evidence will be produced, while the existing paper bot uses `PAPER_AGGRESSIVE` and is explicitly ineligible -> Will a dedicated live-config paper epoch be added, or must R19 remain permanently `NOT_READY`?
- [FIX] RF-ISSUES-4 still defines the calibration minimum as 20 “decisions,” conflicting with R19’s matured, non-overlapping closed episodes -> Use the exact closed-episode definition in both files.
- [DEFER] R19 is guaranteed to return `NOT_READY` and depends on both the deferred outcome labeler and an undefined live-config paper evidence producer, so implementing it now locks in a premature interface without producing evidence -> Move R19 after those producers; the only loss is early CLI scaffolding, while R5 remains safely closed.
VERDICT: NOT YET```

### Visionary response (Claude)

Dört kod iddiası bağımsız doğrulandı, dördü de doğru çıktı:
`core/agent_coordinator.py:427` `risk_vote = votes[4]`;
`stock_bot.py:972` `decision["weighted_score"]`'u BearBrain genişliğine besliyor;
`core/earnings_calendar.py:40,107` ÜÇÜNCÜ bir AV tüketicisi;
`core/agent_performance.py:26,177-179` `MIN_TRADES_FOR_EVAL = 5` ve altında
`DEFAULT_WEIGHTS`'e düşüyor.

**ACCEPTED , `votes[4]` IndexError'ı. Bu, planımın koda sokacağı GERÇEK bir çökmeydi.**
SocialAgent oy kümesinden çıkınca liste 4 elemana düşüyor ve satır 427 her kararda
IndexError fırlatıyor. Hiçbir invaryant değerlendirilmeden bot çöküyor.
-> RiskAgent ada göre bulunacak, tam olarak bir RiskAgent oyu yoksa fail-closed.
Kanıt maddesi (a) olarak eklendi.

**ACCEPTED , `active_sum` invaryantı tek başına YETMİYOR.** Toplam korunurken
ağırlıklar kendi aralarında yeniden dağıtılabilir (Tech 0.25 -> 0.30, Fund 0.20 ->
0.15, toplam yine 0.85). -> İkinci invaryant eklendi: her etkin ajanın maskeleme
sonrası ağırlığı, maskeleme öncesi normalize ağırlığına EŞİT.

**ACCEPTED , altın çıktı kümesi `MIN_TRADES_FOR_EVAL` geçişini kapsamıyordu.**
-> Her ajan için 4 ve 5 çözümlenmiş örnekli geçmişler donduruluyor; hem varsayılan
ağırlık dalı hem hesaplanan dal.

**ACCEPTED , signal/confidence eşitliği `weighted_score` kaymasını kaçırır.**
`ws` ayrıca `stock_bot.py:972`'de BearBrain piyasa-genişliği bileşenini besliyor.
-> Donmuş alanlar genişletildi: `weighted_score` (yuvarlanmış hali dahil),
`majority`, `risk_veto` ve her etkin ajanın ağırlığı.

**ACCEPTED , ÜÇÜNCÜ AV tüketicisi atlanmıştı.** `EarningsCalendar` de aynı anahtarı
kullanıyor; 13 + 12 + takvim > 25. -> Her AV çağıranı aynı profil-bütçe
rezervasyonundan geçiyor; takvim payı temel analiz payını azaltıyor ve kalan
miktar config'de açık yazılıyor.

**ACCEPTED , atomik yazma eşzamanlı aynı-profil süreçlerini kurtarmıyor.**
Restart/deploy örtüşmesinde oku-değiştir-yaz yarışır. -> Interprocess lock +
aynı-profil eşzamanlılık testi (kanıt maddesi d).

**ACCEPTED , tek skaler durum `KILITLI` ile ölü hattı maskeliyordu.** Kendi
"sağlık karar telemetrisine bağlı" iddiamla çelişiyordu. -> Üç bağımsız boyut:
`runtime` / `decision_pipeline` / `entry_authorization`. Kilitli + bayat asla
yalnız `KILITLI` diye özetlenmiyor, ve bu tam olarak kanıt maddesi (a) ile
test ediliyor.

**ACCEPTED ve R18'in kapsamını yeniden belirledi , durumlu portföy, çıkış tekrarı
ertelenmişken İMKANSIZ.** Nakdin ne zaman serbest kalacağını, PDT'nin ne zaman
düşeceğini, kayıp serisinin ne zaman ilerleyeceğini bilmenin yolu yok; durum
geçişleri çıkışa bağlı. Round 2'de kabul ettiğim "durumlu ama yalnız toplama"
kendi içinde tutarsızmış. -> R18 **ekle-sadece niyet/olay toplayıcısı** oldu;
varsayımsal portföy geçişleri `GOLGE-SONUC-ETIKETLEME` sözleşmesine taşındı.

**ACCEPTED , giriş anı bid/ask'i sonraki tekrar için yetmez.** -> Ya yaşam
döngüsü gözlemleri şimdi kaydedilir, ya toplama BAŞLAMADAN ÖNCE değişmez bir
tarihsel kote kaynağı adlandırılıp erişilebilirliği doğrulanır. Toplamanın ön
koşulu yapıldı (kanıt maddesi h).

**ACCEPTED , yeniden yazmayı önleyecek alanlar , sorduğum sorunun doğrudan cevabı.**
-> ŞİMDİ kaydedilecekler listesi plana yazıldı: kalıcı `intent_id`, olay sırası,
kaynak zaman damgaları, varlık yetenekleri, emir parametreleri, durum anlık
görüntüsü, şema-sürümlü etiket referansı.

**ACCEPTED , düşürme/örnekleme sonraki alfa ölçümünü YANLI yapar.** Düşürülen
sayıyı raporlamak yetmiyor. -> Deterministik tabakalı örnekleme + kapsama eşiği;
eşik tutmuyorsa kayıt kümesi **eksik** işaretleniyor ve sonraki kapı bunu
`UNKNOWN` saymak zorunda.

**ACCEPTED , iki eksen bağımsız PASS olmalı.** -> Kapı sözleşmesine yazıldı:
her eksen kendi `gate_status()` çağrısıyla, kendi n/süre/epoch/profil kimliğiyle;
`AC` ancak ikisi de PASS ve kimlikler eşleşiyorsa.

**ACCEPTED , `n` tanımı hala iki dosyada farklıydı.** -> RF-ISSUES-4.md'deki
"20 karar" ifadesi kapı sözleşmesiyle birebir aynı kelimelere çevrildi:
*olgunlaşmış, örtüşmeyen, durumlu gölge KAPANMIŞ episode*.

**ACCEPTED , R19 DEFER'i. Kapı bu döngüde YAZILMIYOR.** Codex haklı: girdileri
var olmayan, sonucu garanti `NOT_READY` olan bir kapıyı yazmak, üreticiler
gelince değişecek bir arayüzü erkenden dondurur. Tam sözleşmesiyle
RF-ISSUES-4.md `KILIT-KAPISI-ARACI` maddesine taşındı.

**CLARIFY'a Visionary cevabı , canlı-config paper kanıtını KİM üretecek?**
Codex'in sorusu bu döngünün şeklini değiştirdi. Üretici yoksa kapı kalıcı olarak
`NOT_READY` kalır, kilit hiç açılmaz ve Core Focus çöker. Cevap: **tüketiciyi
değil ÜRETİCİYİ inşa et.** Yeni R19 = **canlı-config paper epoch'u**: paper
broker'ında ama canlı karar profiliyle (eşikler, boyutlandırma, kapılar) koşan
bir çalıştırma. Gerçek Alpaca paper dolumları üretir , gerçek kısmi dolumlar,
gerçek stop davranışı, gerçek broker-defter mutabakatı , **sıfır dolar riskle**.
Gölge defterin asla üretemeyeceği tek kanıt türü budur. `measured_profile()` de
sabit `PAPER_AGGRESSIVE` dönmek yerine yürürlükteki profili raporlayacak, ve
profil kimliği R18'in kanonik hash'iyle aynı algoritmadan üretilecek ki iki
eksen sonradan eşleştirilebilsin.

### Sonuç: plan revizyon 4

R15 çökme düzeltmesi + ikinci invaryant + genişletilmiş altın çıktı kazandı;
R16 üçüncü tüketiciyi ve interprocess lock'u kazandı; R17 tek skalerden üç boyuta
geçti; R18 ekle-sadece toplayıcıya daraldı ve "şimdi kaydet" alan listesi netleşti;
R19 kapı olmaktan çıkıp **kanıt üreticisi** oldu. Kapı ve etiketleyici
RF-ISSUES-4.md'de tam sözleşmeleriyle bekliyor.
