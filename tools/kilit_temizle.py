"""R24a , OTOMATIK KILIT TEMIZLEME: once KANIT, sonra kilit kalkar.

Kilit, botun kendi durumundan emin olmadigi icin kondu. Onu korlemesine
kaldirmak, kilidi hic koymamakla ayni sey olurdu. Bu yuzden komut her acik
canli pozisyon icin BROKER'DAN koruma dogrulamasi kosar ve biri flat degil VE
tam stop kapsami dogrulanmamissa kilidi KALDIRMAZ.

Kullanim (konteyner icinden):
    python tools/kilit_temizle.py                 # durumu goster
    python tools/kilit_temizle.py --temizle       # kanit gecerse kaldir
    python tools/kilit_temizle.py --zorla "sebep" # SAHIP gecersiz kilmasi
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.safety_state import auto_lock_oku, auto_lock_temizle   # noqa: E402
from utils.logger import logger                                   # noqa: E402


def _state_dir() -> str:
    from config import STATE_DIR
    return STATE_DIR


def koruma_kaniti() -> tuple[bool, list[str]]:
    """Her acik pozisyon icin broker'dan koruma dogrula.

    Doner: (hepsi_guvenli, satirlar). Broker'a ulasilamazsa GUVENLI DEGIL
    kabul edilir , belirsizlik kilidi kaldirmaz.
    """
    satirlar: list[str] = []
    try:
        from alpaca.trading.client import TradingClient
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import OrderStatus, OrderSide
        from config import ALPACA_API_KEY, ALPACA_SECRET_KEY, TRADING_MODE

        client = TradingClient(
            ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=(TRADING_MODE != "live"),
        )
        pozisyonlar = client.get_all_positions() or []
        if not pozisyonlar:
            satirlar.append("  Acik pozisyon YOK , broker flat.")
            return True, satirlar

        acik_emirler = client.get_orders(
            filter=GetOrdersRequest(status=OrderStatus.OPEN)
        ) or []
        hepsi = True
        for poz in pozisyonlar:
            sym = str(poz.symbol).upper()
            poz_qty = abs(float(poz.qty))
            uzun = float(poz.qty) > 0
            koruyan = 0.0
            for emir in acik_emirler:
                if str(getattr(emir, "symbol", "")).upper() != sym:
                    continue
                tur = str(getattr(emir, "order_type", "") or getattr(emir, "type", "")).lower()
                if "stop" not in tur:
                    continue
                yon = getattr(emir, "side", None)
                dogru_yon = (
                    yon == OrderSide.SELL if uzun else yon == OrderSide.BUY
                )
                if not dogru_yon:
                    continue
                try:
                    koruyan += abs(float(emir.qty))
                except (TypeError, ValueError):
                    continue
            kapsam = koruyan >= poz_qty - 1e-9
            satirlar.append(
                f"  {sym}: pozisyon={poz_qty:g} korunan={koruyan:g} "
                f"-> {'TAM KAPSAM' if kapsam else 'EKSIK KAPSAM'}"
            )
            if not kapsam:
                hepsi = False
        return hepsi, satirlar
    except Exception as exc:
        satirlar.append(f"  BROKER DOGRULAMASI BASARISIZ ({exc}) , guvenli SAYILMAZ")
        return False, satirlar


def main(argv: list[str]) -> int:
    state_dir = _state_dir()
    kayit, hata = auto_lock_oku(state_dir)

    print("=" * 66)
    print("  OTOMATIK KILIT DURUMU")
    print("=" * 66)
    if hata:
        print(f"  Kilit kaydi OKUNAMADI: {hata}")
        print("  Fail-closed: bu durumda da yeni canli risk acilmaz.")
    elif kayit is None:
        print("  Otomatik kilit YOK.")
        return 0
    else:
        print(f"  sebep  : {kayit.get('sebep')}")
        print(f"  symbol : {kayit.get('symbol') or '-'}")
        print(f"  zaman  : {kayit.get('zaman')}")
        print(f"  ayrinti: {kayit.get('ayrinti') or '-'}")

    zorla_sebep = None
    if "--zorla" in argv:
        i = argv.index("--zorla")
        if i + 1 >= len(argv) or not argv[i + 1].strip():
            print("\n  --zorla bir SEBEP ister: --zorla \"sebep metni\"")
            return 2
        zorla_sebep = argv[i + 1].strip()

    if not ("--temizle" in argv or zorla_sebep):
        print("\n  Kaldirmak icin: --temizle (kanit gerekir) ya da --zorla \"sebep\"")
        return 1

    print("\n  BROKER KORUMA DOGRULAMASI")
    guvenli, satirlar = koruma_kaniti()
    for satir in satirlar:
        print(satir)

    if zorla_sebep:
        logger.error(
            f"OTOMATIK KILIT ZORLA KALDIRILDI , sahip gerekcesi: {zorla_sebep} "
            f"(broker kaniti guvenli={guvenli})"
        )
        auto_lock_temizle(state_dir)
        print(f"\n  KILIT ZORLA KALDIRILDI. Gerekce loglandi: {zorla_sebep}")
        return 0

    if not guvenli:
        print(
            "\n  KILIT KALDIRILMADI: pozisyonlardan biri flat degil ve tam stop "
            "kapsami dogrulanamadi.\n"
            "  Once korumayi duzeltin, ya da bilerek gecersiz kilin: "
            "--zorla \"sebep\""
        )
        return 3

    auto_lock_temizle(state_dir)
    logger.warning("Otomatik kilit KALDIRILDI , broker koruma kaniti gecti")
    print("\n  KILIT KALDIRILDI (broker kaniti gecti).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
