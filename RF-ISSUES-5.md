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

## ORTA

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
