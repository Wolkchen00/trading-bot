# RF5-DEVAM.md , Canlandırma döngüsü: kaldığımız yer

> Son güncelleme: 2026-09-12 09:45 PDT. Sürücü: Claude (Visionary) + Codex (Integrator).
> **Devam eden oturum buradan başlar.** Önce bu dosyayı, sonra `RF-PLAN-5.md`'yi oku.

## Durum özeti

| Rock | Durum |
|---|---|
| R20 zarar serisi | ✅ **İNŞA EDİLDİ + LEVEL 10 GEÇTİ** (commit'li, deploy YOK) |
| R21 eşik 45 + bantlar | ✅ **BİTTİ** (Claude yazdı, Codex kotası doldu) |
| R22 dürüst giriş akışı | ✅ **BİTTİ** (Claude) |
| R24a emniyet kilitleri | ✅ **BİTTİ** (Claude) |
| R24b dürüst kill tasfiyesi | SIRADA , kilit açılışının ÖN KOŞULU |
| R23 adım 1 (kilit KAPALI deploy) | Pazartesi hedefi, İhsan onayı bekliyor |
| R23 adım 2 (kilit açılışı) | R24b bitince |

**İhsan kararı 2026-09-12:** Pazartesi hedefi = yeni kod canlıda koşsun, **kilit
KAPALI**. Sıfır yeni para riski. Kilit, R24b kanıtlandıktan sonra ayrı bir
operasyonla açılır.

## 12 Eylül (öğleden sonra): R22 + R24a tamamlandı

Sıralama bilinçli olarak değiştirildi (R22 önce): Pazartesi kilit kapalıyken
bakacağımız tek şey huni ve sağlık raporu. R22 bitmeseydi rapor "SAĞLIKLI"
demeye devam eder, körlemesine bakardık.

**R22 , dürüst giriş akışı** (`f88d554`)
- Yeni sayaçlar: `eligible_buy`, `executor_block_reasons`, `max_margin`.
- Sağlığa DÖRDÜNCÜ boyut `entry_flow` (AKIYOR/SESSIZ/FILTRELI/TIKALI/KILITLI/UNKNOWN).
- Yaşam döngüsü denkliği tutmazsa gün UNKNOWN; bilinmeyen bloker DURUM blokeri sayılır.
- **Gerçek üretim verisiyle:** eski kod `live: SAGLIKLI` diyordu; yeni kod
  `entry_flow=TIKALI, 2026-09-09: 285 aday, bloker LOSS_STREAK_WARN=285`, çıkış 3.
  Paper'da 527 aday.
- İncelemede yakalanan kusur: BearBrain aynı `execute_buy`'ı kullanıyor ama
  `eligible_buy`'a girmiyor; terminal redleri aynı kovaya yazılsa denklik bozulurdu.
  Artık `fill_provenance == "strategy"` şartına bağlı, regresyon testi var.

**R24a , canlı emniyet kilitleri** (`54d9b36`)
- **Kalıcı yüksek-su tabanı:** zirve $491.65 kayıtlıyken equity $450 ve restart
  -> taban $417.90 (eski kod $382.50 verirdi, korumayı kaybederdik).
- **BearBrain ayrı canlı kilidi:** `LIVE_BEAR_ENTRIES_ENABLED` (varsayılan false).
  Tek başına R5'i açmak ters-ETF açmaz.
- **Kendi kendini kilitleme:** `live_auto_lock.json` + bellek-içi yedek, 4 tetikleyici
  (stop doğrulanmadı / koruma başarısız / zirve bozuk / gönderim sonrası istisna).
  BÜTÜN risk türlerini kapsar, parking dahil. Çıkışlar serbest.
- **Fail-closed okuma:** bozuk `kill_switch.json` canlıda AKTİF kill; okunamayan
  auto-lock KİLİTLİ; güvenlik dizini yazılamıyorsa canlı giriş baştan reddedilir.
- `tools/kilit_temizle.py`: kilidi körlemesine kaldırmaz, broker'dan koruma doğrular.
- `tools/saglik.py`: "KILITLI (OTOMATIK: <sebep>)" + çıkış 3.

Toplam: **731 test geçiyor** (baseline 592). parity_harness R21'den beri değişmedi.

## 11 Eylül: plan (kod yok)

- Teşhis: bot bozuk değil, **tıkalı**. Engel R5 kilidi DEĞİL, 16 Temmuz'dan kalan
  `consecutive_losses=2` kilitlenmesi. Ayrıntı `RF-PLAN-5.md` TEŞHİS.
- Plan 4 tur Codex incelemesinden geçti, ~50 bulgu işlendi, 1 tanesi gerekçeyle reddedildi.
- 5. tur İhsan kararıyla atlandı. Kod değişmedi.

## 12 Eylül: R20 inşa edildi

**Ne yapıldı**

- Codex `gpt-5.6-sol` / high ile R20 inşası (thread `01a09630-3e47-7922-9ae6-84f1bb46b25a`).
- Yeni `core/streak.py`: profil anahtarlı kalıcı yapı (`streaks_by_profile`), 24 saatlik
  sönme, UTC normalizasyonu, eski durum göçü, broker `filled_at` zaman kaynağı.
- `core/trade_gates.py`: bellek-içi `_loss_halt_until` TAMAMEN kalktı; WARN ve HALT aynı
  `last_loss_at` saatinden besleniyor, restart HALT'ı uzatamıyor.
- `entry_profile` tüm kalıcılık/geri-yükleme yollarına döşendi (giriş, stash, broker-sync,
  long/short/options restore).
- Kalıcılık sırası düzeltildi: seri güncellemesi ile metadata yazımı artık AYNI atomik adım
  (`atomic_write_json`, fsync + os.replace).
- Heartbeat artık `Zarar serisi: N (söner: <UTC>)` yazıyor.

**Level 10 inceleme (Claude, Codex'e güvenmeden)**

- Tam diff okundu (600 ekleme / 60 silme, 11 dosya).
- Üç proof da Claude tarafından koşuldu: `tests/` 639 passed, parity çıktısı baseline ile
  **bayt bayt aynı** (exit 1 bilinçli).
- Claude kendi saldırgan suite'ini yazdı: **`tests/test_r20_saldirgan.py`, 31 test.**
  Config zehirlenmesi (inf/nan/999/liste/sözlük/negatif/string/None), bozuk
  `streaks_by_profile` şekilleri, `halt_hours=999`, tam 24 saat sınırı, devralınmış
  pozisyonun KÂRI, profil izolasyonu, unicode sembol anahtarları, idempotency. Hepsi geçti.
- Bağımsız canlı doğrulama: GERÇEK Temmuz fixture'ı gerçek yükleme yolundan geçirildi.
  Göç `trade_history`'den 2026-07-16 zamanını buldu, seri söndü, META 53.9 kapıdan GEÇTİ.

**1 fix round (bulgu Claude'un, düzeltme Codex'in)**

`core/executor.py` üretim kodu `update_loss_streak` çağrısını `try/except TypeError` ile
sarmıştı; sebebi `tests/test_r9_ledger.py`'deki eski 3-parametreli monkeypatch'ti. O sessiz
yedek yol `filled_at` ve `entry_profile`'ı düşürüyordu , yani R20'nin öldürmek için var olduğu
iki hatayı (duvar saati, devralınmış pozisyonun seriyi hareket ettirmesi) sessizce geri
getirebilirdi. Yedek yol silindi, test yeni imzayı doğrulayacak şekilde GÜÇLENDİRİLDİ.

## 12 Eylül (devam): R21 tamamlandı , Claude yazdı

Codex kotası dolduğu için İhsan direktifiyle R21'i Claude yazdı (commit `0ebbe05`).

- `min_confidence_score` 50 -> 45; taban bant `[50,100]` -> `[45,100]`.
  **Eşik ile taban bant AYNI olmak zorunda**, yoksa eşiği geçen sinyal sizer'da
  "en düşük bandın altında" diye boyutsuz kalır ve giriş sessizce düşer.
- **Asıl yapısal düzeltme:** canlı karar boyutlandırması tek fonksiyona alındı
  (`canli_karar_profili` + `canli_karar_boyutlandirmasi_uygula`) ve artık `live` VE
  `paper_live_config` için aynı şekilde kurulur. Eskiden bu dal yalnız `is_paper=False`
  içindi, yani **paper-livecfg sessizce Kelly yoluna düşüyordu (~$25 işlemler)**:
  canlı kilidini açacak ÇALIŞTIRMA KANITINI canlının boyutlandırması olmadan üretiyordu.
  `paper_aggressive` davranışı birebir korundu.
- `stock_bot.py` ve `tools/parity_harness.py` yedek değerleri 50 -> 45.
- BEAR +10 aynen (efektif 55).

Proof (Claude koştu): **659 passed** (639 -> +20). `tests/test_r21_threshold45.py`:
eşik/taban-bant eşitliği, live 47 -> $100, paper-livecfg 47 -> $100 (bant yolu, Kelly
DEĞİL), 44.9 gerçek karar yolunda BUY dalına girmiyor, bant kademeleri, equity tavanı
kırpması, BEAR 50 blok / 55 geçer, paper_aggressive değişmedi, canlı eşik literali taraması.

parity_harness: tek fark AAPL'in blok sebebinden `min_confidence_score` düşmesi (hâlâ
multi_timeframe bloklu). Etkin aksiyon mutabakatı 8/8 AYNI. Beklenen ve doğru.

Gerçek env doğrulaması: `PAPER_PROFILE=live_config` -> 47 güven $100 bant yolundan;
`PAPER_PROFILE=aggressive` -> canlı yola girmiyor.

## Sıradaki iş , R24b (kilit açılışının ön koşulu)

`RF-PLAN-5.md`'deki R21 bölümü (eşik 50 -> 45 + `[45,100]` bandı, live VE paper-livecfg).
Sözleşme R20'nin `RF5-SOZLESME-R20.md` kalıbıyla yazılır, sonra:

```bash
cd <worktree>
codex exec -s workspace-write -c approval_policy="never" --skip-git-repo-check --json \
  -o "$RUN/build-r21.txt" - <RF5-SOZLESME-R21.md > "$RUN/stream-r21.jsonl" 2>/dev/null
```

Sonra Level 10: tam diff + proof'ları Claude koşar + Claude kendi saldırgan testini yazar.

## Faydalı gerçekler

- Worktree: `git worktree list` ile bul; silinmişse `git worktree prune` + yeniden `add`.
  **Commit'ler `codex-canlandirma` dalında güvende.**
- Baseline testler: `ALPHA_VANTAGE_KEY=dummy-test-key python -m pytest tests/ -q` (639 passed).
  Anahtar ŞART, yoksa 2 R16 testi gerçek ağa gider.
- codex-cli 0.154: `--full-auto` YOK. `-s workspace-write -c approval_policy="never"` kullan.
  `resume` `-C` kabul etmez, önce `cd`.
- Codex modeli `gpt-5.6-sol`, çaba `high`. R20 inşası ~15 dk sürdü.
- Canlı durum fixture'ları: `tests/fixtures/canlandirma_2026_09_11/`.
- Bot durumu: `docker exec <konteyner> python tools/saglik.py`.
  live: `trading-live-dlyojlxudkezk2bze3f3ypp2-060315052850`
  paper: `trading-paper-livecfg-dlyojlxudkezk2bze3f3ypp2-060315077491`
- VPS: `ssh -i ~/.ssh/coolify_vps2 root@91.99.9.121`. Deploy otomatik DEĞİL (Coolify API).

## Açık kalan riskler (İhsan biliyor)

- Gerçek para, ölçülmemiş alfa; 45 eşiği kanıtsız.
- Taban "en kötü ~$75" DEĞİL: gap, stop-limit dolmaması ve fractional DAY stopun gece
  penceresi kaybı büyütebilir (RİSKLER 2).
- SPY parkı nedeniyle canlıda pratikte günde ~1 giriş, ~$96.
- `requirements.txt` sürümleri sabit değil; kilit açılışında YENİDEN DERLEME YOK kuralı var.
- **YENİ (R20 incelemesinden, ertelendi):** agresif paper botu (`--profile aggressive`) bir gün
  tekrar açılırsa iki paper konteyneri aynı `state_paper/bot_positions.json` dosyasına yazar;
  her biri diğerinin profil kaydını boot anındaki bayat kopyayla ezebilir. Bugün risk YOK
  (agresif bot kapalı, tek paper botu koşuyor) ve zaten pozisyon çakışması nedeniyle iki botun
  aynı anda koşması bilinçli olarak engellenmiş. Agresif bot geri açılacaksa önce bu ele alınmalı.
