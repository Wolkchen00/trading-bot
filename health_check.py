"""
Bot Saglik Kontrolu — Alpaca API'den bot durumunu kontrol eder.
Coolify'a girmeye gerek kalmadan botun calisip calismadigini gosterir.

Kullanim:
    python health_check.py              # Normal kontrol
    python health_check.py --alert 6    # 6 saat islem yoksa UYARI
"""
import os
import sys
import argparse
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv()

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOrdersRequest
from alpaca.trading.enums import QueryOrderStatus


def trading_hours_between(start: datetime, end: datetime) -> float:
    """İki an arasındaki saatlerden Cts/Paz'a düşenleri çıkarır (v4.9).

    Alarm "X saattir işlem yok" duvar-saatiyle sayınca her Pzt raporu
    hafta sonunu da sayıp yalancı 🔴 üretiyordu (05 Tem: "73.7h işlem yok").
    Tatiller sayılmaya devam eder (nadir; kabul edilen yaklaşıklık).
    """
    if end <= start:
        return 0.0
    total = 0.0
    cur = start
    while cur < end:
        # İçinde bulunulan günün sonu (ertesi gün 00:00)
        day_end = (cur + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        seg_end = min(day_end, end)
        if cur.weekday() < 5:  # Pzt-Cum
            total += (seg_end - cur).total_seconds() / 3600.0
        cur = seg_end
    return total


def check_health(alert_hours: float = 12.0):
    """Bot saglik kontrolu yapar."""
    # v4.8.2: anahtarlari config'den al — konteynerlerde anahtarlar prefix'li
    # (ALPACA_LIVE_API_KEY / ALPACA_PAPER_API_KEY + ALPACA_KEY_PREFIX); ciplak
    # ALPACA_API_KEY genelde BOS olur ve kontrol sahte "HATA" uretirdi.
    from config import ALPACA_API_KEY as api_key, ALPACA_SECRET_KEY as secret_key, TRADING_MODE as mode

    is_paper = (mode != "live")
    client = TradingClient(api_key, secret_key, paper=is_paper)

    results = []
    def p(msg=""):
        results.append(msg)
        print(msg, flush=True)

    p("=" * 60)
    p(f"  BOT SAGLIK KONTROLU")
    p(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    p(f"  Mod: {'PAPER' if is_paper else 'LIVE'}")
    p("=" * 60)

    # === HESAP ===
    try:
        account = client.get_account()
        equity = float(account.equity)
        cash = float(account.cash)
        p(f"\n[HESAP]")
        p(f"  Equity:  ${equity:,.2f}")
        p(f"  Cash:    ${cash:,.2f}")
        p(f"  Status:  {account.status}")
        p(f"  Blocked: {account.account_blocked or account.trading_blocked}")
    except Exception as e:
        p(f"\n[HESAP] HATA: {e}")
        return False

    # === POZISYONLAR ===
    try:
        positions = client.get_all_positions()
        total_unrealized = sum(float(p.unrealized_pl) for p in positions)
        p(f"\n[POZISYONLAR] ({len(positions)} adet)")
        for pos in positions:
            pnl = float(pos.unrealized_pl)
            pnl_pct = float(pos.unrealized_plpc) * 100
            val = float(pos.market_value)
            p(f"  {pos.symbol:12s} ${val:>8.2f} P/L:${pnl:+.2f} ({pnl_pct:+.1f}%)")
        p(f"  Toplam Unrealized: ${total_unrealized:+.2f}")
    except Exception as e:
        p(f"\n[POZISYONLAR] HATA: {e}")

    # === SON EMIRLER ===
    bot_alive = False
    hours_since_last = None
    try:
        orders_request = GetOrdersRequest(
            status=QueryOrderStatus.ALL,
            after=datetime.now() - timedelta(days=7),
            limit=20,
        )
        orders = client.get_orders(orders_request)
        filled = [o for o in orders if o.status.value == "filled"]

        p(f"\n[SON 7 GUN] ({len(filled)} filled emir)")
        for o in filled[:10]:
            ts = (o.filled_at or o.created_at).strftime("%m/%d %H:%M")
            side = o.side.value
            sym = o.symbol
            price = float(o.filled_avg_price) if o.filled_avg_price else 0
            qty = float(o.qty) if o.qty else 0
            val = qty * price
            p(f"  {ts} {side:4s} {sym:12s} ${val:>8.2f}")

        # Son islem ne zaman? (v4.9: hafta sonu saatleri sayılmaz)
        if filled:
            last_ts = max(o.filled_at or o.created_at for o in filled)
            now = datetime.now(last_ts.tzinfo)
            wall_hours = (now - last_ts).total_seconds() / 3600
            hours_since_last = trading_hours_between(last_ts, now)
            if wall_hours - hours_since_last > 1:
                p(f"\n  Son islem: {hours_since_last:.1f} islem-günü saati once "
                  f"(duvar saati: {wall_hours:.1f}h, hafta sonu düşüldü)")
            else:
                p(f"\n  Son islem: {hours_since_last:.1f} saat once")
            bot_alive = hours_since_last < alert_hours
        else:
            p(f"\n  SON 7 GUNDE HICBIR ISLEM YOK!")
            bot_alive = False
    except Exception as e:
        p(f"\n[EMIRLER] HATA: {e}")

    # === SONUC ===
    p(f"\n{'=' * 60}")
    if bot_alive:
        p(f"  ✅ BOT SAGLIKLI — son islem {hours_since_last:.1f}h once")
    elif hours_since_last is not None:
        p(f"  🔴 BOT SORUNLU — {hours_since_last:.1f}h'dir islem yok! (limit: {alert_hours}h)")
        p(f"     >> Coolify kontrol edin veya botu yeniden deploy edin")
    else:
        p(f"  🔴 BOT CALISMIYOR — hicbir islem bulunamadi!")
    p(f"{'=' * 60}")

    # Dosyaya kaydet
    try:
        log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
        os.makedirs(log_dir, exist_ok=True)
        out_file = os.path.join(log_dir, "health_check.txt")
        with open(out_file, "w", encoding="utf-8") as f:
            f.write("\n".join(results))
    except Exception:
        pass

    return bot_alive


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bot Saglik Kontrolu")
    parser.add_argument("--alert", type=float, default=12.0,
                       help="Kac saat islem yoksa alarm (varsayilan: 12)")
    args = parser.parse_args()

    healthy = check_health(alert_hours=args.alert)
    sys.exit(0 if healthy else 1)
