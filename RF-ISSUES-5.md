# RF-ISSUES-5.md , Canlandırma döngüsünün ertelenen işleri

> Döngü: RF-PLAN-5.md (2026-09-11). Önceki liste: RF-ISSUES-4.md (hâlâ geçerli).

## YÜKSEK

### KALIBRASYON-45-SONUC-VERISI
Eşik 45 sahip kararıyla kondu, sonuç verisi yok. SentAgent BUY oranı R12 düzeltmesinden sonra
%38.5 -> %11.5 düştü; v4.9 ×2.0 remap hatalı dağılımda kalibre edilmişti. İlk 20 canlı + paper
kapanıştan sonra bant bazında (45-49, 50-59, 60+) kazanma oranı / ortalama R / SPY farkı raporlanmalı.

### KILIT-KAPISI-ROLU-DEGISTI
RF-ISSUES-4::KILIT-KAPISI-ARACI kapıyı "kilidi açan" olarak tanımlıyordu. Kilit sahip kararıyla
açıldı; kapının rolü artık "açık kilidi izleyen ve gerekirse kapatmayı öneren". Sözleşme yeniden
yazılmalı. Sağlık aracı "kilit açık, ölçüm kapısı geçmedi (sahip kararı)" diye etiketlemeli.

### BAGIMLILIK-SURUMLERI-SABIT-DEGIL (Codex R3, ertelendi)
`requirements.txt` tamamen `>=` kullanıyor (alpaca-py, pandas, numpy, ta, scikit-learn...). Her Coolify
rebuild'i gerçek parayla çalışan bota sessizce yeni kütüphane sürümü getirebilir; dosya sha256'ları
aynı olsa bile çalışma davranışı değişebilir. Base image de sabit değil. Sürümleri kilitle (lock
dosyası) ve deploy'da image digest'ini kanıt olarak kullan. Bu döngüde risk yalnız "kilit açılışında
rebuild yok" kuralıyla çevrelendi.

### MERKEZI-TOPLAM-POZISYON-TAVANI (Codex R1 #17, ertelendi)
`max_open_positions` yalnız normal long akışında (`stock_bot.py:685-723`); BearBrain yalnız kendi
sayısına bakıyor (`core/bear_brain.py:537-545`). Canlıda bear/opsiyon/short açılmadan ÖNCE tavan
`can_open_new_risk` içinde broker-uzlaştırmalı uygulanmalı.

### STOP-DOGRULANAMAYINCA-OTOMATIK-KAPATMA (Codex R1 #18/#21, kısmen ertelendi)
R24 bu durumda yeni girişleri kalıcı kilitliyor ama pozisyonu kapatmıyor. Otomatik kapatma, oversize ve
yanlış provenance watchdog'u kendi hata modları testleriyle ayrı bir rock olmalı.

## ORTA

### NO-TRADE-SAYACI-GERIYE-SAYIYOR
"İşlemsiz iş günü" sayacı 22 -> 21 -> 20 -> 3 diye geriye gidiyor (canlı alarms.jsonl 09-05..09-11).
R22 hafta sonu alarmını ve bloker adını düzeltiyor; sayacın kendisi ayrıca incelenmeli.

### PAPER-MSFT-CLOSE-IN-PROGRESS-YAPISKAN
`core/executor.py:211/613/709` `close_in_progress=True` yazıyor, yalnız başarıda (759) temizleniyor.
Restart kapanışı yarıda keserse bayrak kalıcı; `core/position_manager.py:815-820` her koruma
turunda KORUMA alarmı atıyor (paper'da 305 alarm). Canlıda pozisyonlu bir deploy aynı durumu
üretebilir.

### PAPER-DEVRALINAN-POZISYON-PROVENANCE
Paper'da 7 günde 19 dolumun 16'sı provenance `UNKNOWN` (agresif dönemden devralınan pozisyonların
çıkışları). R20 bunları seriden ayırıyor; ölçüm katmanında da ayrı etiket gerekli.

## DÜŞÜK

### TEST-HERMETIKLIGI-AV-ANAHTARI
`tests/test_r16_review_fixes2.py::test_prefetch_gun_boyunca_yine_tum_evreni_kapsiyor` ve
`tests/test_r16_review_fixes3.py::test_basarisiz_sembol_her_turu_tekellestirmiyor`
`ALPHA_VANTAGE_KEY` yoksa Yahoo yedeğine düşüp GERÇEK ağa istek atıyor. Ana ağaçta `.env`
yüklendiği için geçiyordu. Testler anahtarı kendileri sabitlemeli.

### KILIT-YAZIM-HATASI-SESSION-SENTINEL (Codex R4 #5, REDDEDILDI, kayit icin)
Auto-lock yazimi gecici olarak basarisiz olup restart'ta disk yazilabilir hale gelirse kilit kaybolur.
Reddedildi cunku ciplak pozisyon durdugu surece sonraki koruma turu auto-lock'u yeniden yaziyor; ek bir
"unclean session" sentineli her deploy'u elle onaya baglardi. Kilit yazimi ile korumanin ikisi birden
bozulursa bu bosluk acilir; daha ucuz bir ikinci dayanikli kayit bulunursa yeniden degerlendirilmeli.
