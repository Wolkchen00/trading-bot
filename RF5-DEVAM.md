# RF5-DEVAM.md , Canlandırma döngüsü: kaldığımız yer

> Son güncelleme: 2026-09-11 15:05 PDT. Sürücü: Claude (Visionary) + Codex (Integrator).
> **Yarın buradan devam.** Önce bu dosyayı, sonra `RF-PLAN-5.md`'yi oku.

## Bugün ne oldu (özet)

- Teşhis canlı konteynerlerde yapıldı: bot bozuk değil, **tıkalı**. Asıl engel R5 kilidi DEĞİL,
  16 Temmuz'dan kalan `consecutive_losses=2` kilitlenmesi (eşiği 70'e çıkarıyor, işlem olmadan
  sıfırlanmıyor). Ayrıntı: `RF-PLAN-5.md` TEŞHİS.
- Plan yazıldı ve **4 tur Codex incelemesinden** geçti. ~50 bulgunun tamamı işlendi; yalnız 1 tanesi
  gerekçesiyle reddedildi (`RF-ISSUES-5.md::KILIT-YAZIM-HATASI-SESSION-SENTINEL`).
- 5. tur (son doğrulama) **İhsan kararıyla atlandı**; pencere R20 inşasına ayrıldı.
- Kod DEĞİŞMEDİ. Canlıya dokunulmadı. Deploy yok. Yalnız plan belgeleri commit'lendi.

## İhsan'ın kararları (planda işli)

1. Canlı kilit onarımla birlikte açılır, **tam bantlarla** (3 pozisyon, $100/$150/$200/$300).
2. **BearBrain canlıda kapalı** kalır (ayrı env anahtarı).
3. Zarar serisi son zarardan **24 saat** sonra söner; profil başına ayrı; sembol filtresi de 24 saat.
4. Güven eşiği **50 -> 45** (+ `[45,100]` bandı, yoksa değişiklik işe yaramaz).
5. Kill tasfiyesi riski kabul edilmedi, **bu döngüde düzeltilir** (R24b).
6. İnşa Codex ile, pencere pencere devam eder.

## Nerede duruyoruz

| Rock | Durum |
|---|---|
| R20 zarar serisi | SIRADA , sözleşme hazır (`RF5-SOZLESME-R20.md`) |
| R21 eşik 45 + bantlar | bekliyor |
| R24a emniyet kilitleri | bekliyor (insadan önce bir doğrulama turu alabilir) |
| R24b dürüst kill tasfiyesi | bekliyor (aynı) |
| R22 dürüst giriş akışı | bekliyor |
| R23 deploy + kilit açılışı | en son, iki adımlı, Claude yürütür |

## Yarın ilk iş , R20 inşası

Çalışma ağacı geçici klasördeydi ve silinmiş olabilir; commit'ler trading reposunda güvende.

```bash
# 1) Worktree'yi tazele (eskisi silindiyse)
cd /c/Users/ihsan/Desktop/Antigravity/trading
git worktree prune
git worktree add <YENI_YOL> codex-canlandirma     # dal zaten var, HEAD = RF5 commit'leri
cd <YENI_YOL>

# 2) Baseline kanıtı (beklenen: 592 passed)
ALPHA_VANTAGE_KEY=dummy-test-key py -m pytest tests/ -q

# 3) Codex build cagrisi (codex-cli 0.154: --full-auto YOK)
codex exec -s workspace-write -c approval_policy="never" --skip-git-repo-check --json \
  -o "$RUN/build-r20.txt" - <RF5-SOZLESME-R20.md > "$RUN/stream-r20.jsonl" 2>/dev/null
# thread id'yi stream'den al, fix round'lari AYNI id ile resume et (resume -C KABUL ETMEZ, once cd)
```

Sonra **Level 10 inceleme (Claude):** tam diff okunur (Codex'in raporu kanıt değil), proof KOMUTLARI
Claude koşar, Codex'in suite'inin kaçırdığı kenar durumlar için Claude kendi saldırgan testini yazar.
En fazla 2 fix round, sonra direksiyon Claude'a geçer.

## Faydalı gerçekler

- Codex thread (4 tur inceleme belleği): `01a0915b-339c-76d3-932d-6410b9024ca9`
- Codex modeli `gpt-5.6-sol`, çaba `high`. Kota: bir pencereye ~1 yüksek-çaba tur sığdı.
  Bugünkü limitler: 14:35 PDT ve 19:37 PDT.
- Testler `.env` olmadan koşar ama `ALPHA_VANTAGE_KEY=dummy-test-key` ŞART; yoksa 2 R16 testi
  gerçek ağa gidip düşer (RF-ISSUES-5, üretim hatası değil).
- Canlı durum fixture'ları: `tests/fixtures/canlandirma_2026_09_11/`.
- Canlı hesap: equity ~$491.65, nakit ~$145.82 (gerisi SPY parkında), 0 strateji pozisyonu,
  zarar serisi 2, kilit kapalı. Paper-livecfg: MSFT açık (devralınmış), seri 2.
- Bot durumu: `docker exec <konteyner> python tools/saglik.py`.
  live: `trading-live-dlyojlxudkezk2bze3f3ypp2-060315052850`
  paper: `trading-paper-livecfg-dlyojlxudkezk2bze3f3ypp2-060315077491`
- VPS: `ssh -i ~/.ssh/coolify_vps2 root@91.99.9.121`. Deploy otomatik DEĞİL (Coolify API).
- Dağıtılan image'da `pytest 9.1.1` ve `tests/` VAR , R23'ün image-içi test kapısı bu yüzden mümkün.

## Açık kalan riskler (İhsan biliyor)

- Gerçek para, ölçülmemiş alfa; 45 eşiği kanıtsız.
- Taban "en kötü ~$75" DEĞİL: gap, stop-limit dolmaması ve fractional DAY stopun gece penceresi
  kaybı büyütebilir (RİSKLER 2).
- SPY parkı nedeniyle canlıda pratikte günde ~1 giriş, ~$96 (tam bantlar pratikte oluşmuyor).
- `requirements.txt` sürümleri sabit değil; bu yüzden kilit açılışında YENİDEN DERLEME YOK kuralı var.
