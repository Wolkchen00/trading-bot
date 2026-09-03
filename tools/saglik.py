"""R17 , DURUST saglik raporu: uc boyut, profil basina, fail-closed.

`health_check.py` tek bir kovaya bakiyordu ve canli hesap icin "BOT CALISMIYOR"
diyordu. Bot CALISIYORDU; yalnizca R5 giris kilidi kapaliydi. Bu rapor o hatayi
ve onun aynadaki goruntusunu (kilidin olu bir hatti maskelemesi) birden onler.

Kullanim:
    py tools/saglik.py                 # mevcut profil
    py tools/saglik.py --profil live   # belirli profil
    py tools/saglik.py --json          # makine okunur

CIKIS KODLARI (core/health_status.CIKIS_KODLARI):
    0 = SAGLIKLI / SESSIZ / KILITLI  (kasitli durumlar ariza DEGILDIR)
    2 = UNKNOWN    3 = DEGRADED    4 = KAPALI
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Windows konsolu cp1252 acilinca emoji satirlari UnicodeEncodeError uretiyor.
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from core.health_status import (
    BOYUTLAR,
    Durum,
    ProfilSagligi,
    SistemSagligi,
    dolum_boyutu,
    giris_yetkisi_durumu,
    karar_hatti_durumu,
    runtime_durumu,
)


def _oku_json(yol):
    """(veri, hata) doner. Hata varsa veri None , SESSIZCE atlanmaz."""
    try:
        if not os.path.exists(yol):
            return None, None
        with open(yol, "r", encoding="utf-8") as f:
            return json.load(f), None
    except Exception as exc:
        return None, str(exc)


def _ts(deger):
    try:
        d = datetime.fromisoformat(str(deger))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def profil_sagligi(profil: str, state_dir: str, simdi: datetime) -> ProfilSagligi:
    """Bir profilin uc boyutunu DISKTEN olcer.

    Broker cagrisi YAPMAZ: bu rapor konteynerin ICINDE kosar ve kendi profilini
    olcer. Tek surec iki konteyneri gozleyemez (ayri anahtar, ayri state_path);
    okunamayan profil UNKNOWN olur.
    """
    # --- runtime
    hb, hb_hata = _oku_json(os.path.join(state_dir, "heartbeat.json"))
    runtime = runtime_durumu(
        _ts(hb.get("ts")) if isinstance(hb, dict) else None,
        simdi,
        okuma_hatasi=hb_hata,
    )

    # --- decision_pipeline: karar telemetrisi (R13 agent_stats) tazeligi
    stats, stats_hata = _oku_json(os.path.join(state_dir, "agent_stats.json"))
    son_karar = None
    if isinstance(stats, dict):
        gunler = stats.get("days") or {}
        if isinstance(gunler, dict) and gunler:
            try:
                en_son_gun = max(gunler)
                # Gun bazli toplam; gun sonu 23:59 varsayilir (muhafazakar:
                # gercek karar daha erken olabilir, yani BAYATLIK KUCUK GORUNMEZ)
                son_karar = _ts(f"{en_son_gun}T00:00:00+00:00")
                if (gunler.get(en_son_gun) or {}).get("coordinator", {}).get("decisions"):
                    pass
                else:
                    son_karar = None    # gun var ama karar yok
            except Exception:
                son_karar = None

    kill, kill_hata = _oku_json(os.path.join(state_dir, "kill_switch.json"))
    kill_aktif = bool(kill.get("killed")) if isinstance(kill, dict) else False

    # R17: AJAN INVARYANTI ihlali , karar yolu guvenilmez demektir.
    # stock_bot her istisnayi yutup HOLD donduruyor, bu yuzden kalici
    # isaret olmadan saglik katmani hatti NORMAL sanirdi.
    ihlal, _ihlal_hata = _oku_json(
        os.path.join(state_dir, "invariant_violations.json")
    )
    ihlal_var = False
    if isinstance(ihlal, dict):
        son = _ts(ihlal.get("son_ihlal_ts"))
        # Son 24 saat icindeki ihlal hatti bozuk sayar.
        ihlal_var = bool(son and (simdi - son).total_seconds() < 86400)

    karar = karar_hatti_durumu(
        son_karar,
        simdi,
        kill_switch_aktif=kill_aktif,
        invaryant_ihlali=ihlal_var,
        okuma_hatasi=stats_hata,
    )

    # --- entry_authorization
    is_paper = profil != "live"
    try:
        from config import STOCK_CONFIG
        kilit_acik = bool(STOCK_CONFIG.get("live_entries_enabled", False))
    except Exception:
        kilit_acik = None
    yetki = giris_yetkisi_durumu(kilit_acik, is_paper=is_paper)

    # --- dolumlar (SAGLIK KANITI DEGIL)
    dolumlar = []
    try:
        from core.fill_ledger import read_fills
        for satir in read_fills() or []:
            ts = _ts(satir.get("ts_utc"))
            if ts and (simdi - ts).days <= 7:
                dolumlar.append(
                    {"provenance": satir.get("provenance"), "ts": ts}
                )
    except Exception:
        pass

    return ProfilSagligi(
        profil=profil,
        runtime=runtime,
        decision_pipeline=karar,
        entry_authorization=yetki,
        dolumlar=dolum_boyutu(dolumlar, simdi),
    )


def sistem_sagligi(profiller=None, simdi=None) -> SistemSagligi:
    simdi = simdi or datetime.now(timezone.utc)
    try:
        from config import TRADING_MODE, STATE_DIR
        mevcut = "live" if TRADING_MODE == "live" else "paper"
    except Exception:
        return SistemSagligi({}, {"?": "config okunamadi"})

    istenen = profiller or [mevcut]
    sonuc, okunamayan = {}, {}
    for p in istenen:
        if p == mevcut:
            sonuc[p] = profil_sagligi(p, STATE_DIR, simdi)
        else:
            # DURUSTLUK: tek surec digerinin state'ini ve anahtarini goremez.
            okunamayan[p] = (
                "bu konteynerden okunamaz , ayri TRADING_MODE/anahtar/state_path; "
                "o profilin kendi konteynerinde kosturulmali"
            )
    return SistemSagligi(sonuc, okunamayan)


def rapor_satirlari(saglik: SistemSagligi) -> list:
    L = []
    L.append("=" * 66)
    L.append("  BOT SAGLIK RAPORU (R17 , uc boyut)")
    L.append(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    L.append("=" * 66)

    for ad, p in saglik.profiller.items():
        L.append("")
        L.append(f"[PROFIL: {ad.upper()}]")
        for boyut in BOYUTLAR:
            b = p.boyutlar()[boyut]
            isaret = "OK " if b.durum is Durum.SAGLIKLI else "!! "
            L.append(f"  {isaret}{boyut:20s} {b.durum.value:10s} {b.sebep}")

        d = p.dolumlar
        L.append(f"  .. {'dolumlar (7g)':20s} {'BILGI':10s} "
                 f"toplam={d.get('toplam')} strateji={d.get('strateji')} "
                 f"diger={d.get('strateji_disi')}")
        if d.get("provenance_dagilimi"):
            L.append(f"     provenance: {d['provenance_dagilimi']}")
        L.append(f"     NOT: {d.get('not')}")

        L.append("")
        L.append(f"  OZET , {p.ozet_metni()}")

    if saglik.okunamayan:
        L.append("")
        L.append("[OKUNAMAYAN PROFILLER] , UNKNOWN sayilir, atlanmaz")
        for ad, sebep in saglik.okunamayan.items():
            L.append(f"  ?? {ad}: {sebep}")

    L.append("")
    L.append("=" * 66)
    L.append(f"  EN KOTU: {saglik.en_kotu().value}   "
             f"(cikis kodu {saglik.cikis_kodu()})")
    L.append("=" * 66)
    return L


def main() -> int:
    ap = argparse.ArgumentParser(description="Durust bot saglik raporu (R17)")
    ap.add_argument("--profil", action="append",
                    help="olculecek profil (tekrarlanabilir)")
    ap.add_argument("--json", action="store_true", help="makine okunur cikti")
    args = ap.parse_args()

    saglik = sistem_sagligi(args.profil)

    if args.json:
        print(json.dumps(saglik.to_dict(), indent=2, ensure_ascii=False))
    else:
        metin = "\n".join(rapor_satirlari(saglik))
        print(metin)
        try:
            log_dir = ROOT / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            (log_dir / "saglik.txt").write_text(metin, encoding="utf-8")
        except Exception:
            pass

    return saglik.cikis_kodu()


if __name__ == "__main__":
    raise SystemExit(main())
