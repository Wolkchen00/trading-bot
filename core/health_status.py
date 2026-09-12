"""R17+R22 , bot saglik durumu: TEK SKALER DEGIL, DORT BAGIMSIZ BOYUT.

NEDEN VAR
---------
`health_check.py` 2026-09-03'e kadar tek bir kovaya bakiyordu ve canli hesap icin
"BOT CALISMIYOR" diyordu. Bot CALISIYORDU; yalnizca R5 giris kilidi kapaliydi.
Ihsan bir hafta boyunca botu bozuk sandi. Olcum aleti yanlis okuyorsa butun
dongu kor ucar.

Ama tersi de tuzak: tek bir skaler durum kullanilirsa `KILITLI`, OLU bir karar
hattini MASKELER. Kilitli VE bayat bir bot yalnizca "kilitli" diye raporlanamaz;
bu, ilk hatanin aynadaki goruntusudur.

DORT BOYUT
----------
1. `runtime`              , surec/konteyner ayakta mi, heartbeat taze mi
2. `decision_pipeline`    , tarama ve karar URETILIYOR mu
3. `entry_authorization`  , canli giris kilidi acik mi
4. `entry_flow`           , esigi gecen aday GIRISE donuyor mu (R22)

DORDUNCU BOYUT NEDEN GEREKTI
----------------------------
Uc boyut "bot calisiyor mu" sorusunu cevapliyordu ve TIKALI bir hatta uc
boyut da yesil yanabiliyordu: tarama var, karar var, kilit acik. 09 Eylul
canli funnel'inde 285 aday (paper'da 527) zarar-serisi kilidinde durdu ve
rapor "SAGLIKLI" dedi. `entry_flow` tam olarak bunu olcer.

Her boyut KENDI durumunu tasir. Ozet bir skaler DEGILDIR: saglikli olmayan HER
boyut ozette adiyla gorunur.

DOLUMLAR SAGLIK KANITI DEGILDIR
-------------------------------
Taze bir dolum park islemi, manuel islem ya da cikis olabilir. Canli hesapta
2026-09-03 itibariyle tek hareket SPY PARKI; strateji olu oldugu halde "son islem
taze" diye yesil yanardi. Dolumlar AYRI bir boyut olarak, provenance ile raporlanir.

FAIL-CLOSED
-----------
Her boyutta belirsizlik `UNKNOWN`'a duser, `SAGLIKLI`'ya DEGIL.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Dict, Optional


class Durum(str, Enum):
    """Bir boyutun durumu. Siddet sirasi SIDDET sozlugunde."""

    SAGLIKLI = "SAGLIKLI"      # boyut calisiyor
    SESSIZ = "SESSIZ"          # calisiyor ama uretim yok (secici mod olabilir)
    KILITLI = "KILITLI"        # kasitli olarak kapali (R5 giris kilidi)
    UNKNOWN = "UNKNOWN"        # OLCULEMEDI , asla SAGLIKLI sayilmaz
    DEGRADED = "DEGRADED"      # bozuk/tutarsiz sinyal
    KAPALI = "KAPALI"          # surec yok


# Siddet: buyuk = daha kotu. Cikis kodu ve "en kotu boyut" bundan turer.
SIDDET: Dict[Durum, int] = {
    Durum.SAGLIKLI: 0,
    Durum.SESSIZ: 1,
    Durum.KILITLI: 2,
    Durum.UNKNOWN: 3,
    Durum.DEGRADED: 4,
    Durum.KAPALI: 5,
}

# Sozlesme: proof'ta gecen HER durum burada tanimli olmali. Sema/test uyumu
# `test_r17_honest_health.py` tarafindan ayrica dogrulanir.
TUM_DURUMLAR = tuple(Durum)

BOYUTLAR = (
    "runtime", "decision_pipeline", "entry_authorization", "entry_flow",
)


class EntryFlow(str, Enum):
    """R22 , GIRIS AKISI: esigi gecen aday ne oldu?

    Uc boyut "bot calisiyor mu" sorusunu cevapliyordu. Ama TIKALI bir hat
    calisiyor gibi gorunur: tarama var, karar var, kilit acik, ve yine de
    aylardir tek giris yok. Dorduncu boyut tam olarak bunu olcer.
    """

    AKIYOR = "AKIYOR"        # esigi gecen aday GIRISE donusuyor
    SESSIZ = "SESSIZ"        # esigi gecen aday YOK (ariza degil)
    FILTRELI = "FILTRELI"    # aday var, TASARIM kapisi durduruyor
    TIKALI = "TIKALI"        # aday var, DURUM kapisi durduruyor , ARIZA
    KILITLI = "KILITLI"      # aday var, R5 giris kilidi durduruyor , KASITLI
    UNKNOWN = "UNKNOWN"      # olculemedi ya da denklik tutmadi


# EntryFlow -> Durum: siddet ve cikis kodu MEVCUT makineden turer, ikinci bir
# cikis-kodu tablosu yazilmaz (iki tablo kacinilmaz olarak birbirinden sapar).
ENTRY_FLOW_DURUM: Dict["EntryFlow", Durum] = {
    EntryFlow.AKIYOR: Durum.SAGLIKLI,
    EntryFlow.SESSIZ: Durum.SESSIZ,        # secici mod , cikis 0
    EntryFlow.FILTRELI: Durum.SESSIZ,      # tasarim kapisi , cikis 0
    EntryFlow.KILITLI: Durum.KILITLI,      # kasitli , cikis 0
    EntryFlow.TIKALI: Durum.DEGRADED,      # ARIZA , cikis 3
    EntryFlow.UNKNOWN: Durum.UNKNOWN,      # olculemedi , cikis 2
}

# TASARIM kapilari: bilincli secicilik. Bunlar bir ariza DEGILDIR.
TASARIM_BLOKERLERI = frozenset({
    "EMA200", "EARNINGS", "VOLATILITY", "RR_GATE", "MTF", "MARKET_CLOSED",
    "SECTOR", "FUND_NEGATIVE", "FUND_NO_DATA",
    "QUEUED_PULLBACK", "QUEUE_DUP",
})

# DURUM kapilari: botun kendi durumu girisi durduruyor. Bunlar ARIZADIR.
DURUM_BLOKERLERI = frozenset({
    "LOSS_STREAK_WARN", "LOSS_STREAK_HALT", "STOCK_FILTER",
    "GUARD_ERROR", "KILL_SWITCH", "RISK_HALT", "EQUITY_FLOOR",
    "SIZER_ZERO", "CASH_RESERVE", "MIN_TRADE_VALUE", "TOO_SMALL",
    "STOP_UNVERIFIED", "ALREADY_FLAT", "FRACTIONAL_NO_BRACKET",
    "BUY_EXCEPTION", "PDT_REJECT", "UNCLASSIFIED",
    # R24a/R24b ile gelecek olanlar , simdiden siniflandirildi ki yeni bir
    # bloker sessizce "bilinmeyen" kovasina dusmesin.
    "LIVE_AUTO_LOCK", "KILL_CLOSE_PENDING", "LIVE_BEAR_LOCK",
})

# R5 giris kilidi , KASITLI, ariza degil ama SESSIZ de degil.
KILIT_BLOKERI = "LIVE_LOCK_R5"

CIKIS_KODLARI = {
    Durum.SAGLIKLI: 0,
    Durum.SESSIZ: 0,       # secici mod bir ariza DEGILDIR
    Durum.KILITLI: 0,      # kasitli kilit bir ariza DEGILDIR
    Durum.UNKNOWN: 2,
    Durum.DEGRADED: 3,
    Durum.KAPALI: 4,
}


@dataclass(frozen=True)
class BoyutDurumu:
    """Tek bir boyutun durumu + neden."""

    durum: Durum
    sebep: str
    ayrinti: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "durum": self.durum.value,
            "sebep": self.sebep,
            "ayrinti": dict(self.ayrinti),
        }


@dataclass(frozen=True)
class ProfilSagligi:
    """Tek bir profilin (live ya da paper) DORT boyutlu sagligi."""

    profil: str
    runtime: BoyutDurumu
    decision_pipeline: BoyutDurumu
    entry_authorization: BoyutDurumu
    # R22 , DORDUNCU BOYUT. Varsayilan UNKNOWN: olculmediyse yesil sayilmaz.
    entry_flow: BoyutDurumu = field(
        default_factory=lambda: BoyutDurumu(
            Durum.UNKNOWN, "giris akisi OLCULMEDI",
            {"entry_flow": EntryFlow.UNKNOWN.value},
        )
    )
    dolumlar: dict = field(default_factory=dict)   # SAGLIK KANITI DEGIL

    def boyutlar(self) -> Dict[str, BoyutDurumu]:
        return {
            "runtime": self.runtime,
            "decision_pipeline": self.decision_pipeline,
            "entry_authorization": self.entry_authorization,
            "entry_flow": self.entry_flow,
        }

    def en_kotu(self) -> Durum:
        return max(
            (b.durum for b in self.boyutlar().values()),
            key=lambda d: SIDDET[d],
        )

    def sorunlu_boyutlar(self) -> Dict[str, BoyutDurumu]:
        """SAGLIKLI olmayan HER boyut.

        Ozet bunlarin TAMAMINI gosterir. Yalniz "en kotu"yu gostermek, tam olarak
        R17'nin onlemek icin var oldugu maskelemeyi geri getirirdi: kilitli
        (siddet 2) bir bot, sessiz (siddet 1) bir karar hattini gizlerdi.
        """
        return {
            ad: b for ad, b in self.boyutlar().items()
            if b.durum is not Durum.SAGLIKLI
        }

    def ozet_metni(self) -> str:
        sorunlu = self.sorunlu_boyutlar()
        if not sorunlu:
            return f"{self.profil}: SAGLIKLI (dort boyut da temiz)"
        parcalar = [f"{ad}={b.durum.value}" for ad, b in sorunlu.items()]
        return f"{self.profil}: " + " | ".join(parcalar)

    def to_dict(self) -> dict:
        return {
            "profil": self.profil,
            "boyutlar": {ad: b.to_dict() for ad, b in self.boyutlar().items()},
            "en_kotu": self.en_kotu().value,
            "dolumlar": dict(self.dolumlar),
            "ozet": self.ozet_metni(),
        }


@dataclass(frozen=True)
class SistemSagligi:
    """Birden cok profilin toplulastirilmis sagligi.

    Toplulastirma ACIKCA cok kaynaklidir: tek surec iki konteyneri gozleyemez
    (ayri TRADING_MODE, ayri anahtar, ayri state_path). Okunamayan profil
    `UNKNOWN` olur, SESSIZCE ATLANMAZ.
    """

    profiller: Dict[str, ProfilSagligi]
    okunamayan: Dict[str, str] = field(default_factory=dict)

    def en_kotu(self) -> Durum:
        durumlar = [p.en_kotu() for p in self.profiller.values()]
        if self.okunamayan:
            durumlar.append(Durum.UNKNOWN)   # eksik kaynak = belirsizlik
        if not durumlar:
            return Durum.UNKNOWN
        return max(durumlar, key=lambda d: SIDDET[d])

    def cikis_kodu(self) -> int:
        return CIKIS_KODLARI[self.en_kotu()]

    def to_dict(self) -> dict:
        return {
            "profiller": {ad: p.to_dict() for ad, p in self.profiller.items()},
            "okunamayan": dict(self.okunamayan),
            "en_kotu": self.en_kotu().value,
            "cikis_kodu": self.cikis_kodu(),
        }


# ======================================================================
# BOYUT DEGERLENDIRICILERI , saf fonksiyonlar, IO YOK
# ======================================================================

def runtime_durumu(
    heartbeat_ts: Optional[datetime],
    simdi: datetime,
    *,
    bayat_dakika: float = 30.0,
    okuma_hatasi: Optional[str] = None,
) -> BoyutDurumu:
    """Surec ayakta mi.

    GELECEK tarihli heartbeat DEGRADED'dir: saat kaymasi ya da bozuk kayit
    demektir ve "cok taze" diye okunup yesil yanmamalidir.
    """
    if okuma_hatasi:
        return BoyutDurumu(
            Durum.DEGRADED, f"heartbeat okunamadi: {okuma_hatasi}"
        )
    if heartbeat_ts is None:
        return BoyutDurumu(
            Durum.UNKNOWN, "heartbeat dosyasi yok , canlilik OLCULEMEDI"
        )

    yas_dk = (simdi - heartbeat_ts).total_seconds() / 60.0
    if yas_dk < -1.0:
        return BoyutDurumu(
            Durum.DEGRADED,
            f"heartbeat GELECEK tarihli ({-yas_dk:.0f} dk ileri) , saat kaymasi",
            {"yas_dakika": round(yas_dk, 1)},
        )
    if yas_dk > bayat_dakika:
        return BoyutDurumu(
            Durum.KAPALI,
            f"heartbeat {yas_dk:.0f} dk eski (esik {bayat_dakika:.0f} dk)",
            {"yas_dakika": round(yas_dk, 1)},
        )
    return BoyutDurumu(
        Durum.SAGLIKLI,
        f"heartbeat {max(0.0, yas_dk):.0f} dk once",
        {"yas_dakika": round(max(0.0, yas_dk), 1)},
    )


def karar_hatti_durumu(
    son_karar_ts: Optional[datetime],
    simdi: datetime,
    *,
    bayat_saat: float = 6.0,
    ardisik_hata: int = 0,
    hata_esigi: int = 3,
    kill_switch_aktif: bool = False,
    risk_halt: bool = False,
    invaryant_ihlali: bool = False,
    okuma_hatasi: Optional[str] = None,
) -> BoyutDurumu:
    """Karar URETILIYOR mu.

    SAGLIK BURADAN OLCULUR, DOLUMLARDAN DEGIL. Bot tasarim geregi gunlerce islem
    yapmayabilir (secici mod); ama karar URETMIYORSA hat oludur.
    """
    if okuma_hatasi:
        return BoyutDurumu(
            Durum.DEGRADED, f"karar telemetrisi okunamadi: {okuma_hatasi}"
        )

    # Invaryant ihlali (or. RiskAgent oyu kayip) hatti OLU sayar , siradan bir
    # HOLD gibi gorunmesi tam olarak R15 incelemesinde yakalanan hataydi.
    if invaryant_ihlali:
        return BoyutDurumu(
            Durum.DEGRADED,
            "ajan invaryanti ihlal edildi , karar yolu guvenilmez",
        )
    if kill_switch_aktif:
        return BoyutDurumu(Durum.DEGRADED, "kill switch aktif")
    if risk_halt:
        return BoyutDurumu(Durum.DEGRADED, "risk halt aktif")
    if ardisik_hata >= hata_esigi:
        return BoyutDurumu(
            Durum.DEGRADED,
            f"{ardisik_hata} ardisik tarama hatasi (esik {hata_esigi})",
        )

    if son_karar_ts is None:
        return BoyutDurumu(
            Durum.UNKNOWN, "karar telemetrisi yok , hat OLCULEMEDI"
        )

    yas_saat = (simdi - son_karar_ts).total_seconds() / 3600.0
    if yas_saat < -0.05:
        return BoyutDurumu(
            Durum.DEGRADED,
            f"son karar GELECEK tarihli ({-yas_saat:.1f} saat ileri)",
        )
    if yas_saat > bayat_saat:
        return BoyutDurumu(
            Durum.SESSIZ,
            f"son karar {yas_saat:.1f} saat once (esik {bayat_saat:.0f}h)",
            {"yas_saat": round(yas_saat, 1)},
        )
    return BoyutDurumu(
        Durum.SAGLIKLI,
        f"son karar {max(0.0, yas_saat):.1f} saat once",
        {"yas_saat": round(max(0.0, yas_saat), 1)},
    )


def giris_yetkisi_durumu(
    live_entries_enabled: Optional[bool],
    *,
    is_paper: bool,
) -> BoyutDurumu:
    """Canli giris kilidi , KASITLI bir durum, ariza DEGIL.

    Kilidi "bozuk" diye raporlamak Ihsan'a bir hafta kaybettirdi. Ama kilidin
    OZETTE GIZLENMESI de yasak: kilitli + olu hat, "sadece kilitli" olarak
    ozetlenemez (bkz. ProfilSagligi.sorunlu_boyutlar).
    """
    if is_paper:
        return BoyutDurumu(Durum.SAGLIKLI, "paper , giris kilidi uygulanmaz")
    if live_entries_enabled is None:
        return BoyutDurumu(
            Durum.UNKNOWN, "giris kilidi durumu OKUNAMADI"
        )
    if not live_entries_enabled:
        return BoyutDurumu(
            Durum.KILITLI,
            "canli giris kilidi KAPALI (R5) , kasitli; olcum kapisi gecmedi. "
            "Acmak: Coolify env LIVE_ENTRIES_ENABLED=true + restart",
        )
    return BoyutDurumu(Durum.SAGLIKLI, "canli giris ACIK")


def _bloker_dagilimi(gun: dict) -> Dict[str, int]:
    """Bir gunun terminal redlerini SEBEP bazinda topla.

    Asama degil SEBEP sayilir: "gate_block=7" hicbir sey soylemez, ama
    "LOSS_STREAK_WARN=7" tam olarak neyin durdurdugunu soyler.
    """
    dagilim: Dict[str, int] = {}

    def ekle(ad: str, adet: int) -> None:
        adet = int(adet or 0)
        if adet > 0:
            dagilim[ad] = dagilim.get(ad, 0) + adet

    ekle("SECTOR", gun.get("sector_block", 0))
    ekle("QUEUED_PULLBACK", gun.get("queued_pullback", 0))
    ekle("QUEUE_DUP", gun.get("queue_dup", 0))
    for alan in ("gate_block_reasons", "executor_block_reasons"):
        sebepler = gun.get(alan) or {}
        if isinstance(sebepler, dict):
            for sebep, adet in sebepler.items():
                ekle(str(sebep).strip().upper() or "UNCLASSIFIED", adet)
    return dagilim


def _belirleyici_bloker(dagilim: Dict[str, int]):
    """Adaylari EN COK durduran sebep. Esitlikte alfabetik , karar deterministik."""
    if not dagilim:
        return None, 0
    return max(sorted(dagilim.items()), key=lambda kv: kv[1])


def giris_akisi_durumu(
    gunler: Optional[dict],
    *,
    pencere: int = 3,
    okuma_hatasi: Optional[str] = None,
) -> BoyutDurumu:
    """R22 , esigi gecen aday ne oldu?

    Pencere: TARAMA YAPILMIS son `pencere` islem gunu (`scanned == 0` atlanir;
    hafta sonu bir ariza degildir). Her gun AYRI siniflanir ve durum, eligible
    adayi olan EN YENI gune gore verilir , eski bir gunun girisi daha yeni bir
    gunun blokerini MASKELEYEMEZ.
    """
    def _sonuc(akis: EntryFlow, sebep: str, ayrinti: Optional[dict] = None):
        detay = dict(ayrinti or {})
        detay["entry_flow"] = akis.value
        return BoyutDurumu(ENTRY_FLOW_DURUM[akis], sebep, detay)

    if okuma_hatasi:
        return _sonuc(EntryFlow.UNKNOWN, f"funnel okunamadi: {okuma_hatasi}")
    if not isinstance(gunler, dict) or not gunler:
        return _sonuc(EntryFlow.UNKNOWN, "funnel verisi yok , giris akisi OLCULEMEDI")

    try:
        sirali = sorted(
            ((str(g), v) for g, v in gunler.items() if isinstance(v, dict)),
            key=lambda kv: kv[0], reverse=True,
        )
    except Exception as exc:
        return _sonuc(EntryFlow.UNKNOWN, f"funnel verisi bozuk: {exc}")

    taranan = [(g, v) for g, v in sirali if int(v.get("scanned", 0) or 0) > 0]
    if not taranan:
        return _sonuc(
            EntryFlow.UNKNOWN,
            "son gunlerde TARAMA YOK , giris akisi OLCULEMEDI",
        )
    pencere_gunleri = taranan[:max(1, int(pencere))]

    from core.funnel import DailyFunnel

    en_iyi_marj = None
    for gun_str, gun in pencere_gunleri:
        # R22 ONCESI GUNLER: `eligible_buy` alani YOK. Sifir saymak bu gunleri
        # sessizce "aday yok" gosterirdi , tam olarak gizlemek istedigimiz sey.
        # Kapilar YALNIZ esik gecildikten sonra kosar, dolayisiyla her kapi
        # reddi bir adayi ima eder: ALT SINIR olarak turetilir.
        # `wash_sale_block` DAHIL EDILMEZ , o esikten ONCE kosar.
        turetilmis = "eligible_buy" not in gun
        if turetilmis:
            eligible = sum(
                int(gun.get(alan, 0) or 0)
                for alan in ("sector_block", "gate_block", "queued_pullback",
                             "queue_dup", "executor_block", "entries")
            )
        else:
            eligible = int(gun.get("eligible_buy", 0) or 0)
        marj = gun.get("max_margin")
        if en_iyi_marj is None and isinstance(marj, dict):
            en_iyi_marj = dict(marj, gun=gun_str)
        if eligible <= 0:
            continue

        if turetilmis:
            # Turetilmis gunde denklik TANIM GEREGI tutar; sahte kesinlik
            # uretmemek icin kontrol atlanir ama rapor bunu ACIKCA soyler.
            girisler = int(gun.get("entries", 0) or 0)
            dagilim = _bloker_dagilimi(gun)
            bloker, adet = _belirleyici_bloker(dagilim)
            ayrinti = {
                "gun": gun_str, "eligible_buy": eligible, "turetilmis": True,
                "bloker": bloker, "bloker_adedi": adet, "dagilim": dagilim,
            }
            if girisler > 0:
                return _sonuc(
                    EntryFlow.AKIYOR,
                    f"{gun_str}: {girisler} giris (aday sayisi TURETILMIS)",
                    ayrinti,
                )
            if bloker is None:
                return _sonuc(
                    EntryFlow.UNKNOWN,
                    f"{gun_str}: turetilmis aday var ama sebep YOK", ayrinti,
                )
            if bloker == KILIT_BLOKERI:
                akis_turu, metin = EntryFlow.KILITLI, "R5 giris kilidinde durdu (kasitli)"
            elif bloker in TASARIM_BLOKERLERI:
                akis_turu, metin = EntryFlow.FILTRELI, f"TASARIM kapisi ({bloker}={adet})"
            else:
                etiket = bloker if bloker in DURUM_BLOKERLERI else f"{bloker} (siniflandirilmamis)"
                akis_turu, metin = EntryFlow.TIKALI, f"bloker {etiket}={adet}"
            return _sonuc(
                akis_turu,
                f"{gun_str} (TURETILMIS): {eligible} aday , {metin}",
                ayrinti,
            )

        # Denklik: aciklanamayan fark = olculmemis bir `return False` yolu.
        denklik = DailyFunnel.terminal_denklik(gun)
        if not denklik["denk"]:
            return _sonuc(
                EntryFlow.UNKNOWN,
                (
                    f"{gun_str}: yasam dongusu denkligi TUTMADI , "
                    f"eligible={denklik['eligible_buy']} "
                    f"terminal={denklik['terminal_toplam']} "
                    f"aciklanamayan={denklik['aciklanamayan']}"
                ),
                {"gun": gun_str, "denklik": denklik},
            )

        girisler = int(gun.get("entries", 0) or 0)
        if girisler > 0:
            return _sonuc(
                EntryFlow.AKIYOR,
                f"{gun_str}: {eligible} aday, {girisler} giris",
                {"gun": gun_str, "eligible_buy": eligible, "entries": girisler},
            )

        dagilim = _bloker_dagilimi(gun)
        bloker, adet = _belirleyici_bloker(dagilim)
        ayrinti = {
            "gun": gun_str, "eligible_buy": eligible,
            "bloker": bloker, "bloker_adedi": adet, "dagilim": dagilim,
        }
        if bloker is None:
            return _sonuc(
                EntryFlow.UNKNOWN,
                f"{gun_str}: {eligible} aday var ama terminal sebep YOK",
                ayrinti,
            )
        if bloker == KILIT_BLOKERI:
            return _sonuc(
                EntryFlow.KILITLI,
                f"{gun_str}: {eligible} aday R5 giris kilidinde durdu (kasitli)",
                ayrinti,
            )
        if bloker in TASARIM_BLOKERLERI:
            return _sonuc(
                EntryFlow.FILTRELI,
                f"{gun_str}: {eligible} adayi TASARIM kapisi durdurdu ({bloker}={adet})",
                ayrinti,
            )
        # Bilinmeyen sebep de DURUM blokeri sayilir , fail-closed.
        etiket = bloker if bloker in DURUM_BLOKERLERI else f"{bloker} (siniflandirilmamis)"
        return _sonuc(
            EntryFlow.TIKALI,
            f"{gun_str}: {eligible} aday GIRISE DONMEDI , bloker {etiket}={adet}",
            ayrinti,
        )

    ayrinti = {"pencere": [g for g, _ in pencere_gunleri]}
    if en_iyi_marj is not None:
        ayrinti["max_margin"] = en_iyi_marj
        fark = en_iyi_marj.get("margin")
        return _sonuc(
            EntryFlow.SESSIZ,
            (
                f"esigi gecen aday yok , en yakin "
                f"{en_iyi_marj.get('symbol', '?')} "
                f"guven {en_iyi_marj.get('confidence')} vs esik "
                f"{en_iyi_marj.get('threshold')} (marj {fark})"
            ),
            ayrinti,
        )
    return _sonuc(
        EntryFlow.SESSIZ, "esigi gecen aday yok (marj verisi yok)", ayrinti
    )


def dolum_boyutu(
    dolumlar: list,
    simdi: datetime,
) -> dict:
    """Dolumlar , AYRI boyut, SAGLIK KANITI DEGIL.

    Her dolum provenance ile raporlanir. `index_parking` bir strateji islemi
    DEGILDIR; onu "bot calisiyor" kaniti saymak canli hesapta birebir yasandi.
    """
    strateji = [d for d in dolumlar if str(d.get("provenance")) == "strategy"]
    diger = [d for d in dolumlar if str(d.get("provenance")) != "strategy"]

    def _en_taze(kume):
        zamanlar = [d.get("ts") for d in kume if d.get("ts")]
        return max(zamanlar) if zamanlar else None

    strateji_son = _en_taze(strateji)
    return {
        "toplam": len(dolumlar),
        "strateji": len(strateji),
        "strateji_disi": len(diger),
        "provenance_dagilimi": {
            p: sum(1 for d in dolumlar if str(d.get("provenance")) == p)
            for p in sorted({str(d.get("provenance")) for d in dolumlar})
        },
        "son_strateji_dolumu": strateji_son.isoformat() if strateji_son else None,
        "not": (
            "Dolumlar SAGLIK KANITI DEGILDIR: park/manuel/cikis olabilir. "
            "Saglik karar hattinin tazeliginden olculur."
        ),
    }
