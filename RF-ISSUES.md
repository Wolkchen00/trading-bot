# RF-ISSUES , 2026-08-09 Clarity Break denetimi

> Kaynaklar: VPS canli loglar (trading-live / trading-short, 72-240h), `state_paper/alarms.jsonl`,
> `tools/olcum_raporu.py --since 2026-07-30` (konteyner icinde kosuldu), yerel kod okumasi
> (`core/position_manager.py`, `core/protection.py`, `config.py`, `tools/olcum_raporu.py`).
> Olcum donemi durumu: **n=1 kapali islem, gun=7/30, PnL +$149.40 (PASS), metrik-2 FAIL (0/1),
> metrik-3 PASS, metrik-4 PASS.**

## Basarilar (bozuk olmayanlar , dokunma)

- **Sistem tarihinin ILK TAKE_PROFIT cikisi**: PLTR 2026-08-07, 15 adet @ $168.98, +$149.85 (+6.3%).
  v4.13 TP bandi (%5-7.5) hedefe erisilebilir cikti , eski %8-12 bandinin "yapisal erisilemez" tezi dogrulandi.
- Koruma PATCH mekanizmasi (R0-B) calisiyor: PLTR omru boyunca 9+ "SL DOGRULANDI" basarili guncelleme.
- Kayit butunlugu (v4.13) calisiyor: phantom=0, cift kayit yok (eski AMD/META cift kaydi tekrarlanmadi).
- E1/E2 giris kapilari + huni gozlemlenebilirligi (v4.14) uretimde veri uretiyor; NO_TRADE alarmi tasarime uygun.
- alarms.jsonl -> VPS koprusu -> ntfy zinciri calisiyor; AMZN acik pozisyonu dogrulanmis stopla korunuyor ($263.24).
- Paper equity 7 gunde ~+$1.6k (08-03 $61.834 -> 08-09 $63.470).

## YUKSEK etki

- **I-1 Kademeli satis ACLIK (metrik-2 yapisal FAIL).** `position_manager.py:333` 3b dali
  (`elif pnl>0.02 and breakeven_set`) if/elif zincirini tuketiyor; kademeli dal (`:347`, esik +%3)
  break-even (+%2.5) kurulduktan sonra **matematiksel olarak erisilemez** (BE tetigi %2.5 < partial %3
  ve 3b, pnl>%2'de her donguyu yutuyor). Kanit: PLTR +3.01..3.62% arasi bircok dongu, sifir kademeli satis;
  olcum metrigi 2 = 0/1. Olcum kapisi bu bug kapanmadan ASLA 4/4 PASS olamaz.
- **I-2 Stop GERI kaymasi (risk bugi).** 3b `trailing_sl_price = highest*(1-trail%)` degerini yalniz
  `last_server_sl` (ilk deger 0) ile kiyasliyor; BE fiyati veya kanonik stopla kiyaslamiyor, klamp yok.
  Kanit: PLTR 13:32:00 BE $159.50 -> 13:32:01 sunucu stop $156.56'ya INDIRILDI (long'da stop asla geri gitmemeli;
  kilitlenen kar geri acildi).
- **I-3 Kanonik/sunucu ayrismasi.** 3b sunucu stopunu guncelliyor ama `stop_loss_price` (kanonik) BE'de kaliyor
  ($159.50 vs sunucu $157.63). Mutabakatci her turda "kapsama eksigi" gorup hedefi geri cekmeye calisiyor ,
  iki alt sistem birbirinin emrini eziyor.
- **I-4 deterministic client_order_id TARIHSEL cakisma + kor retry.** `protection.py:235`
  id = f(symbol, side, price, qty) , zaman bileseni yok. BE 13:32'de $159.50 icin id'yi kullandi;
  mutabakatci 13:35'te ayni hedefe donmek isteyince Alpaca 40010000/40010001 "unique" reddi verdi ve
  `_update_server_stop_loss` AYNI id ile 5 kez daha denedi (deneme 2..6 birebir ayni hata).
  Cagri ici korelasyon dogru amac, cagrilar-arasi ebedi sabitlik yanlis.
- **I-5 Yanlis CRITICAL.** PLTR tum olay boyunca aktif, tam-qty kapsayan bir stopa ($157.63) sahipti ,
  "koruma kurulamadi" CRITICAL'i ciplaklik ima ediyor ama pozisyon ciplak degildi; deadline-sonu eski-stop
  fallback'i (`position_manager.py:1070-1097`) bu vakada VERIFIED donmedi (kok neden Rock 2'de testle bulunacak).
  Ayrica 2026-08-05 tek bir gecici nginx 500'u dogrudan KORUMA CRITICAL'i uretti (retry yok).

## ORTA etki

- **I-6 olcum_raporu --since tuzagi.** Varsayilan `date.today()` , bayraksiz kosum "n=0, gun=0" gosterip
  olcumu bozuk sandiriyor (bugun tam bunu yasadik). Olcum baslangici (2026-07-30) tek yerde sabitlenmeli,
  rapor basligi since'i yazmali, tempo projeksiyonu eklenmelil (bkz. I-8).
- **I-7 Alarm teslim gurultusu + gecikme.** Telegram kimligi yok (Ihsan karari rafta) -> her kritik alarmda
  "KRITIK ALARM TESLIM EDILEMEDI" ERROR'u + gunluk funnel WARNING'i. Teslim yalniz 20dk'lik VPS koprusune bagli.
  Bot icinde dogrudan ntfy HTTP yayini yok (topic zaten kurulu ve Ihsan abone; token gerektirmez).
- **I-8 Olcum temposu (KOD DEGIL, Ihsan karar maddesi).** 7 iste-gununde 1 kapali islem; bu tempoyla 30 gunde
  n~4-5 olur, 20-islem hedefi dolamaz. Huni: 08-04 1382 BUY sinyali -> 1 giris; 08-07 380 BUY -> 0 giris
  (EMA200=375 blok). Secenekler donem sonunda Ihsan'a sunulacak.

## DUSUK etki

- **I-9** Funnel ozeti teslim WARNING'i , I-7 ile ayni kok (notifier kapali), ayri is degil.
- **I-10** Short tarafinda ayni if/elif kalibi var (`position_manager.py:465-585`) , Rock 1 icinde kontrol
  edilip ayni onarim uygulanmali (canli long-only; maruziyet yalniz paper).
- **I-11** 3b guncellemesi `_stash_exit_flags` cagirmiyor gorunumde , restart'ta `last_server_sl` kaybi
  tekrar-ayrisma uretebilir; Rock 1 kapsaminda dogrula/duzelt.

## Bu donguye ALINMAYAN acik kalemler

- R5 kilit acma (ayri Ihsan kapisi; olcum 4/4 PASS on kosul , I-1 duzelmeden imkansizdi).
- Telegram token karari (rafta; I-7'nin ntfy yolu bundan bagimsiz).
- Strateji/doktrin isleri (TP bandi ayari, EMA200 gate gevsetme, giris temposu) , olcum donemi sonunda.
