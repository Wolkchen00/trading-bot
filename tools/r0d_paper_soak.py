"""R0-D ,  koruma degismezinin GERCEK Alpaca paper API'sine karsi soak testi.

Mock'lar mantigi kanitlar, brokerin davranisini kanitlamaz. Bu betik yeni koruma
kodunu gercek emir yasam dongusune sokar ve her adimda sunucudan YENIDEN OKUYARAK
dogrular:

  1. Kucuk bir LONG pozisyon acilir (tam lot -> GTC stop mumkun).
  2. Koruyucu stop konur, classify_covering_order VERIFIED demeli.
  3. Stop yukari cekilir (_update_server_stop_loss) -> REPLACED_VERIFIED,
     ve sunucuda TEK aktif stop, YENI fiyatta kalmali (eski iz birakmamali).
  4. Stop disaridan iptal edilir (bot cokmus gibi) -> ensure_protective_stops
     bunu gormeli, onarmali ve DOGRULAMALI.
  5. Temizlik: emirler iptal, pozisyon kapatilir.

YALNIZ PAPER. Canli anahtarlarla calistirilmaz (basta kontrol edilir).
"""
from __future__ import annotations

import os
import sys
import time

from dotenv import load_dotenv

load_dotenv()

from alpaca.trading.client import TradingClient  # noqa: E402
from alpaca.trading.enums import (  # noqa: E402
    OrderSide,
    QueryOrderStatus,
    TimeInForce,
)
from alpaca.trading.requests import (  # noqa: E402
    GetOrdersRequest,
    MarketOrderRequest,
    StopLimitOrderRequest,
)

from core.position_manager import PositionManager  # noqa: E402
from core.protection import (  # noqa: E402
    ProtectionOutcome,
    classify_covering_order,
    flatten_orders,
    is_active_order,
    is_stop_order,
)

SYMBOL = os.getenv("SOAK_SYMBOL", "F")
QTY = 1  # tam lot -> GTC
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    mark = "GECTI" if ok else "KALDI"
    print(f"  [{mark}] {name}" + (f" ,  {detail}" if detail else ""))


class Parking:
    def is_parking_symbol(self, symbol: str) -> bool:
        return symbol == "SPY"


class Notifier:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def notify_critical(self, kind: str, message: str) -> bool:
        self.messages.append(f"{kind}: {message}")
        return True

    def notify_error(self, message: str) -> bool:
        self.messages.append(message)
        return True


class SoakBot:
    def __init__(self, client: TradingClient) -> None:
        self.client = client
        self.positions: dict = {}
        self.short_positions: dict = {}
        self.index_parking = Parking()
        self.notifier = Notifier()
        self._exit_flag_cache: dict = {}
        self._protection_poll_seconds = 1

    def _stash_exit_flags(self, symbol: str, data: dict) -> None:
        self._exit_flag_cache[symbol] = dict(data)


def open_orders(client: TradingClient, symbol: str) -> list:
    orders = client.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN))
    return [
        o for o in flatten_orders(orders)
        if str(getattr(o, "symbol", "")) == symbol
    ]


def active_stops(client: TradingClient, symbol: str) -> list:
    return [
        o for o in open_orders(client, symbol)
        if is_stop_order(o) and is_active_order(o)
    ]


def get_position(client: TradingClient, symbol: str):
    for p in client.get_all_positions():
        if p.symbol == symbol:
            return p
    return None


def wait_for(fn, timeout=45, interval=2):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(interval)
    return None


def main() -> int:
    key = os.getenv("ALPACA_PAPER_API_KEY")
    sec = os.getenv("ALPACA_PAPER_SECRET_KEY")
    if not key or not sec:
        print("PAPER anahtarlari yok ,  iptal.")
        return 2
    if key.startswith("AK"):
        print("GUVENLIK: bu bir CANLI anahtar gibi gorunuyor ,  iptal.")
        return 2

    client = TradingClient(key, sec, paper=True)
    acct = client.get_account()
    print(f"Hesap: {acct.account_number} | equity ${float(acct.equity):,.2f} | PAPER")

    if get_position(client, SYMBOL) is not None:
        print(f"{SYMBOL} pozisyonu zaten var ,  soak icin temiz sembol gerekir. Iptal.")
        return 2

    bot = SoakBot(client)
    pm = PositionManager(bot)
    config = {
        "stop_loss_pct": 0.04,
        "breakeven_offset_pct": 0.003,
        "index_parking_symbol": "SPY",
    }

    try:
        # ---- 1) pozisyon ac -------------------------------------------------
        print(f"\n1) {QTY} adet {SYMBOL} aliniyor...")
        client.submit_order(MarketOrderRequest(
            symbol=SYMBOL, qty=QTY, side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
        ))
        pos = wait_for(lambda: get_position(client, SYMBOL))
        if pos is None:
            print("Pozisyon dolmadi (piyasa kapali olabilir) ,  iptal.")
            return 2
        entry = float(pos.avg_entry_price)
        print(f"   dolduruldu: {pos.qty} @ ${entry:.2f}")
        bot.positions[SYMBOL] = {
            "entry_price": entry, "qty": float(pos.qty),
            "stop_loss_pct": 0.04, "stop_loss_price": round(entry * 0.96, 2),
        }

        # ---- 2) koruyucu stop kur ve DOGRULA --------------------------------
        stop1 = round(entry * 0.96, 2)
        print(f"\n2) koruyucu stop ${stop1} kuruluyor...")
        client.submit_order(StopLimitOrderRequest(
            symbol=SYMBOL, qty=QTY, side=OrderSide.SELL,
            time_in_force=TimeInForce.GTC,
            stop_price=stop1, limit_price=round(stop1 * 0.995, 2),
        ))
        stops = wait_for(lambda: active_stops(client, SYMBOL))
        check("2a. stop sunucuda aktif", bool(stops),
              f"{len(stops or [])} aktif stop")
        if stops:
            pos = get_position(client, SYMBOL)
            res = classify_covering_order(stops[0], pos, "LONG")
            check("2b. classify VERIFIED diyor",
                  res.outcome is ProtectionOutcome.VERIFIED, res.detail)
            check("2c. GTC korundu (tam lot)",
                  str(getattr(stops[0], "time_in_force", "")).lower().endswith("gtc"),
                  str(getattr(stops[0], "time_in_force", "")))

        # ---- 3) stop'u yukari cek (PATCH yolu) ------------------------------
        stop2 = round(entry * 0.98, 2)
        print(f"\n3) stop ${stop1} -> ${stop2} (replace yolu)...")
        result = pm._update_server_stop_loss(SYMBOL, stop2, float(QTY), side="LONG")
        check("3a. yapisal sonuc dondu", result is not None,
              getattr(result, "outcome", "None"))
        check("3b. sonuc dogrulanmis",
              bool(getattr(result, "verified", False)),
              getattr(result, "detail", ""))

        time.sleep(3)
        stops = active_stops(client, SYMBOL)
        check("3c. sunucuda TEK aktif stop kaldi", len(stops) == 1,
              f"{len(stops)} adet")
        if len(stops) == 1:
            actual = float(getattr(stops[0], "stop_price", 0) or 0)
            check("3d. stop YENI fiyatta", abs(actual - stop2) < 0.02,
                  f"${actual} vs beklenen ${stop2}")

        # ---- 4) stop'u disaridan iptal et, mutabakat onarsin ----------------
        print("\n4) stop disaridan iptal ediliyor (bot cokmus senaryosu)...")
        for o in active_stops(client, SYMBOL):
            client.cancel_order_by_id(o.id)
        gone = wait_for(lambda: not active_stops(client, SYMBOL), timeout=30)
        check("4a. stop gercekten kalmadi", bool(gone),
              f"{len(active_stops(client, SYMBOL))} aktif stop")

        bot.notifier.messages.clear()
        summary = pm.ensure_protective_stops(config)
        check("4b. mutabakat korumayi geri kurdu", summary.placed >= 1,
              f"placed={summary.placed} verified={summary.verified} "
              f"failed={summary.failed}")

        time.sleep(3)
        stops = active_stops(client, SYMBOL)
        check("4c. sunucuda yeniden aktif stop var", len(stops) >= 1,
              f"{len(stops)} adet")
        if stops:
            pos = get_position(client, SYMBOL)
            res = classify_covering_order(stops[0], pos, "LONG")
            check("4d. onarilan stop dogrulaniyor", res.verified, res.detail)

    finally:
        # ---- 5) temizlik ----------------------------------------------------
        print("\n5) temizlik...")
        try:
            for o in open_orders(client, SYMBOL):
                try:
                    client.cancel_order_by_id(o.id)
                except Exception:
                    pass
            time.sleep(2)
            if get_position(client, SYMBOL) is not None:
                client.close_position(SYMBOL)
                print(f"   {SYMBOL} pozisyonu kapatildi")
            else:
                print("   pozisyon yok")
        except Exception as exc:
            print(f"   TEMIZLIK HATASI (elle bak): {exc}")

    print("\n" + "=" * 58)
    passed = sum(1 for _, ok, _ in results if ok)
    for name, ok, detail in results:
        if not ok:
            print(f"  KALDI: {name} ,  {detail}")
    print(f"  SOAK: {passed}/{len(results)} gecti")
    print("=" * 58)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
