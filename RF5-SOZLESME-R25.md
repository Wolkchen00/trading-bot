# R25 INSA SOZLESMESI , Orta agresif paket (canli alim hunisini ac, korumalar aynen)

Sahip karari (Ihsan, 2026-09-16): "3 gundur bot al-sat yapmiyor, biraz daha agresif
risk alsin." Secilen paket: ORTA AGRESIF. Kill, taban, stop, zarar serisi AYNEN kalir.

## OLCULMUS TESHIS (Claude, canli log 14-16 Eyl, 15.880 karar)

- Canli 4 ajan oy veriyor (SocialAgent politika ile kapali). RiskAgent kodda HIC BUY
  donmuyor (v4.8 "fren"), SentAgent 3 gunde 0 BUY. Cogunluk `buy_count >= 3` istiyor,
  yani BUY tarafinda cogunluk YAPISAL OLARAK imkansiz; x1.2 bonusu olu.
- 16 Eyl gercek vaka: GOOGL Tech BUY 59 + Fund BUY 30 + Sent HOLD 10 + Risk HOLD 30
  ("Risk seviyeleri normal") -> ws +20.8 -> guven 42 < esik 45.
- Tekrar oynatma: 2-oy cogunluk + esik 40 ile 3 gunde GOOGL 49.9, AAPL 40.3, SOFI 43.9
  aday olurdu (SOFI EMA200 kapisinda elenir).
- Tarama `_get_symbols_to_analyze` icinde `[:10]` ile 10 sembole kirpiliyor; evren 20.
- Nakit: equity ~$490, SPY parki %70, islem nakdi ~$145 -> ayni anda tek ~$96 giris.

## GOAL

Canli (ve ayni kod yolunu kullanan paper-livecfg) botun, itirazsiz iki ajanli net BUY
sinyalinde gercekten giris yapabilmesi; daha genis tarama; iki pozisyon icin nakit.
Butun koruma kilitleri ve SELL tarafi davranisi birebir korunur.

## SPEC

### 1. Koordinator: itirazsiz 2-oy cogunlugu (`core/agent_coordinator.py`)

1a. `RiskAgent.analyze`: olusan oya `vote.risk_score = risk_score` ekle (`short_boost`
    ile ayni kalip). Baska davranis degismez.

1b. `AgentCoordinator.decide` icinde, oylar toplandiktan sonra:

- `buy_capable` = oy kumesinde `RiskAgent` DISINDAKI ajanlar (RiskAgent tasarim geregi
  BUY veremez). Canli bugun: Tech, Fund, Sent -> 3.
- `buy_needed = min(3, len(buy_capable) // 2 + 1)` -> 3 ajanda 2, 4 ajanda 3.
- `risk_normal`: `risk_vote.risk_score` gercek bir sayi (bool DEGIL, NaN/inf DEGIL) VE
  `> -15`. Eksik, None, NaN, inf, bool, string -> `False` (fail-closed).
- `itirazsiz_buy_cogunlugu = buy_count >= buy_needed and sell_count == 0 and not risk_veto and risk_normal`

1c. Sinyal sirasi DEGISMEZ, yalniz `ws > 15` dalinda cogunluk bayragi hesaplanir:

```
if buy_count >= 3:            BUY,  majority=True,  majority_rule="3_oy"
elif sell_count >= 3:         SELL, majority=True,  majority_rule="3_oy"
elif weighted_score > 15:     BUY,  majority=itirazsiz_buy_cogunlugu,
                                    majority_rule="itirazsiz_2_oy" if o bayrak True else None
elif weighted_score < -15:    SELL, majority=False, majority_rule=None
else:                         HOLD, majority=False, majority_rule=None
```

- ZAYIF 2'li oy (ws <= 15) BUY'a TERFI ETMEZ; HOLD kalir.
- Guven formulu AYNEN: `abs(ws) * 2.0`, cogunlukta x1.2, vetoda x0.5, short_boost, tavan 100.
- SELL tarafi ve SocialAgent mantigi DEGISMEZ.
- `result` sozlugune `"majority_rule"` anahtari eklenir. Mevcut anahtarlar degismez.
- Log satirinda `COGUNLUK` / `tekil` kelimesi AYNEN kalir (log ayristiricilar buna
  bakiyor). Itirazsiz 2-oyda `COGUNLUK(2oy)` yazilabilir, `COGUNLUK` ile BASLAMALI.

### 2. Esik 45 -> 40 (`config.py`, `stock_bot.py`, `tools/parity_harness.py`)

- `STOCK_CONFIG["min_confidence_score"] = 40`.
- `STOCK_CONFIG["live_conf_position_bands"] = [[40,100],[60,150],[70,200],[80,300]]`.
  Taban bant esikle AYNI olmak zorunda (R21 kurali).
- Yedek degerler: `stock_bot.py` `_analyze_and_trade` icindeki
  `config.get("min_confidence_score", 45)` -> 40; `tools/parity_harness.py` BUY yedegi
  45 -> 40. SHORT yedekleri (`short_min_confidence`) DEGISMEZ.
- Yorumlar R25 gerekcesiyle guncellenir (kisa).
- `PAPER_AGGRESSIVE_CONFIG` DEGISMEZ (min_conf 30, kendi bantlari).
- BEAR rejim +10, `loss_streak_elevated_conf` 70 DEGISMEZ.

### 3. Tarama 10 -> 20 (`config.py`, `stock_bot.py`)

- `STOCK_CONFIG["scan_max_symbols"] = 20` (yeni anahtar).
- `_get_symbols_to_analyze` iki dalda da (scan_cache var / yok) bu degeri kullanir.
- Gecersiz deger (eksik, bool, float olmayan tam sayi disi, NaN, <1) -> ESKI davranis 10
  ve bir kez WARNING. Ust sinir 30'a kirpilir.
- Ters-ETF filtresi AYNEN.

### 4. Park rezervi %30 -> %60 (`config.py`)

- `STOCK_CONFIG["index_parking_reserve_pct"] = 0.60` (equity'nin %60'i likit kalir,
  parkin payi ~%40).
- `PAPER_AGGRESSIVE_CONFIG` icine acikca `"index_parking_reserve_pct": 0.30` eklenir ki
  o profilin davranisi birebir korunsun.
- `core/index_parking.py` DEGISMEZ (yapici yedegi 0.30 kalir).

### 5. Kozmetik (`core/run_profile.py`)

- `profil_ozeti()` LIVE aciklamasi sabit ve YANLIS: "R5 kilidi kapaliyken yeni giris yok".
  Yerine durum iddia ETMEYEN metin: "gercek para; yeni giris LIVE_ENTRIES_ENABLED
  kilidine bagli (durum: tools/saglik.py)".

### 6. Testler

Yeni `tests/test_r25_orta_agresif.py`, en az su vakalar:

a. GERCEK 16 Eyl GOOGL: Tech BUY 59, Fund BUY 30, Sent HOLD 10, Risk HOLD risk_score 0
   -> signal BUY, ws 20.75, majority True, majority_rule "itirazsiz_2_oy",
   guven 49.8 (+-0.1), >= 40, canli bant boyutu $100.
b. Ayni vaka ama SentAgent SELL -> majority False, guven x1.2 ALMAZ.
c. RiskAgent risk_score -15 (HOLD ama yukselmis risk) -> majority False; -14 -> True.
d. Risk vetosu (risk_score <= -30) -> final HOLD.
e. 2 BUY ama ws <= 15 (ornegin Tech 20, Fund 20) -> HOLD, majority False.
f. SocialAgent ETKIN (4 BUY-yetenekli ajan) -> buy_needed 3; 2 BUY + ws > 15 -> majority False.
g. risk_score eksik / None / NaN / inf / True / "0" -> majority False (fail-closed).
h. SELL tarafi regresyonu: 2 SELL + ws < -15 -> SELL, majority False, guven degismedi.
i. 3 BUY yolu (Social etkin) -> majority True, majority_rule "3_oy".
j. Esik: 39.9 BUY dalina girmez, 40.0 girer; bantlar 40->$100, 59.9->$100, 60->$150,
   70->$200, 80->$300; live VE paper_live_config ayni boyutu verir.
k. `scan_max_symbols`: 20 sembollu scan_cache -> 20 doner, skora gore sirali; cache bos
   -> varsayilan havuzdan 20; gecersiz degerler -> 10; 99 -> 30; ters-ETF'ler elenir.
l. Park matematigi: equity 490.15, cash 145.82, reserve 0.60 -> `_sell` ~148.27 notional
   ile cagrilir, `_buy` cagrilmaz; PAPER_AGGRESSIVE_CONFIG reserve 0.30.
m. Korumalar DEGISMEDI assert'i: `max_daily_loss_pct` 0.05, `equity_floor_pct` 0.85,
   `stop_loss_pct` 0.04, `stop_loss_max_pct` 0.06, `loss_streak_warn` 2,
   `loss_streak_halt` 4, `loss_streak_elevated_conf` 70, `max_open_positions` 3,
   `live_max_position_usd` 300, `options_enabled` canli False.

Mevcut testlerde 45'i "tek dogru canli deger" diye kilitleyen assert'ler 40'a guncellenir
(ozellikle `tests/test_r21_threshold45.py`, `tests/test_r19_live_config_paper.py`).
Testi SILME, gevsetme; ayni kilidi yeni degerle koru ve R25 notu dus. Literal taramasi
izinli kumesinden 45 cikarilir (canli esik artik 40).

`tests/test_full_system.py` (pytest'e toplanmiyor, standalone): YALNIZ esik/bant
assert'lerini 40 ve [40,100] yap. Diger bayat testlere DOKUNMA.

## KEY PATHS

Once oku: `core/agent_coordinator.py`, `stock_bot.py` (`_analyze_and_trade`,
`_get_symbols_to_analyze`, `canli_karar_boyutlandirmasi_uygula`), `config.py`
(STOCK_CONFIG, PAPER_AGGRESSIVE_CONFIG), `core/index_parking.py` (`maybe_rebalance`),
`core/run_profile.py`, `tests/test_r21_threshold45.py`, `tests/test_r15_agent_weights.py`,
`tests/test_r19_live_config_paper.py`, `tools/parity_harness.py`.

Degisecek: yukaridaki uretim dosyalari + yeni test dosyasi + guncellenen testler.

## CONSTRAINTS

- DOKUNMA: kill switch (`max_daily_loss_pct`), `equity_floor_pct`, stop/TP yuzdeleri,
  zarar serisi, `LIVE_ENTRIES_ENABLED` / BEAR kilidi / `risk_guard`, opsiyonlar,
  `max_open_positions`, `BOT_MODE`, kapilar (`core/trade_gates.py`), `core/executor.py`,
  `core/index_parking.py`, `docker-compose.yml`, `requirements.txt`, `Dockerfile`.
- Ag YOK, yeni bagimlilik YOK, git commit YOK.
- Turkce yorumlarda uzun tire (U+2014) kullanma; virgul kullan.
- Fail-closed kalip: belirsiz girdide GEVSEME degil, eski/sert davranis.

## NON-GOALS

SentAgent BUY mantigi, FundAgent / Alpha Vantage kotasi, MTF 4h kapisi, EMA200 kapisi,
`tests/conftest.py` collect_ignore, bayat 4 testin geri kalani, deploy, RF5-DEVAM.md.

## PROOF

1. `ALPHA_VANTAGE_KEY=dummy-test-key python -m pytest tests/ -q -p no:cacheprovider`
   -> hepsi gecer, sayi 760'tan BUYUK (baseline 760 passed).
2. `ALPHA_VANTAGE_KEY=dummy-test-key python tests/test_full_system.py`
   -> basarisizlar baseline'in ALT KUMESI olmali. Baseline 7 basarisiz:
   ".env dosyasi mevcut", "config.py tum export'lar", "API key uzunlugu" (worktree'de
   .env yok), "Paper ogrenme + canli esik konfigurasyonu", "v4.11.2 maruziyet tavanina
   sigdirma", "v4.12 paper agresif+ config + LIVE kilit korumasi",
   "v4.12 merge sirasi (kill/floor override'i isler)".
3. `ALPHA_VANTAGE_KEY=dummy-test-key python tools/parity_harness.py` -> ciktiyi aynen
   raporla (cikis 1 bilincli). Baseline'da GOOGL ve NVDA `min_confidence_score` ile
   bloklu; her sembol bazinda farki acikla.

## OUTPUT

Rapor: degisen her dosya tek satir (yol + ne/neden), uc proof'un tam ciktisi (pytest
ozet satiri yeterli ama basarisizlik varsa tam), spesifikasyondan her sapma gerekcesiyle.
Ayrica, salt okuma analizi olarak: tarama 20 sembole cikinca tur basina dis cagri
(Alpaca bar, haber, temel veri, Alpha Vantage kota butcesi 13/gun) nasil degisir,
kota tukenmesi veya dongu suresi riski var mi, kod satiri kanitiyla.
