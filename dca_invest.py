"""
Index DCA (Dollar-Cost Averaging) — Basit, güvenli, düzenli index alımı.

Trading bot'un TAM TERSİ: sinyal yok, agent yok, market-timing yok, stop-loss yok.
Sadece düzenli olarak sabit $ tutarında SPY alır ve TUTAR. Çoklu-pencere backtest'i
gösterdi ki bot SPY'ı geçmiyor; o yüzden parayı büyütmenin rasyonel yolu budur.

Neden işe yarar: S&P 500 uzun vadede yıllık ~%7-10 (reel ~%6.5) getirmiştir. Maliyet
yok denecek kadar az (Alpaca komisyon $0, SPY gider oranı %0.09 — VOO %0.03 daha ucuz).
Market-timing yapmaya çalışmamak, çoğu yatırımcıyı (ve bu botu) geçer.

Kullanım:
  py dca_invest.py             # DRY-RUN — emir GÖNDERMEZ, ne yapacağını gösterir
  py dca_invest.py --execute   # Gerçekten alır (TRADING_MODE'a göre paper/live)

Gerçek "düzenli DCA" için (otomatik her ay/hafta al):
  Windows Task Scheduler → haftalık/aylık, hafta içi piyasa saatinde (TR ~17:00 sonrası):
    program:   py
    argüman:   C:\\Users\\ihsan\\Desktop\\Antigravity\\trading\\dca_invest.py --execute

VETERAN NOTU — Lump-sum vs DCA:
  Hesabında ZATEN duran nakit için (ör. mevcut ~$1.000), istatistiksel olarak tek
  seferde almak (lump-sum) ortalamada DCA'yı geçer. DCA asıl GELECEKTEKİ düzenli
  katkılar (ör. her ay eklediğin yeni para) içindir. Yani: mevcut bakiyeyi bir kerede
  SPY yap; bu scripti gelecekteki aylık katkıları otomatikleştirmek için kullan.
"""
import sys
from datetime import datetime

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

from config import ALPACA_API_KEY, ALPACA_SECRET_KEY, TRADING_MODE

# ============================================================
# AYARLAR — buradan düzenle
# ============================================================
DCA_SYMBOL = "SPY"        # "SPY" (en likit) veya "VOO" (Vanguard, daha düşük gider %0.03)
DCA_AMOUNT_USD = 50.0     # Her çalıştırmada alınacak $ (DCA dilimi). Lump-sum için bakiyene eşitle.
LOG_FILE = "dca_log.txt"
# ============================================================


def main():
    execute = "--execute" in sys.argv
    is_paper = TRADING_MODE != "live"
    mode = "PAPER (sanal)" if is_paper else "🔴 LIVE (GERÇEK PARA)"

    client = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=is_paper)

    account = client.get_account()
    cash = float(account.cash)
    equity = float(account.equity)

    print("=" * 56)
    print(f"  INDEX DCA — {DCA_SYMBOL}")
    print(f"  Mod: {mode}")
    print(f"  Equity: ${equity:,.2f} | Kullanılabilir nakit: ${cash:,.2f}")
    print(f"  DCA dilimi: ${DCA_AMOUNT_USD:,.2f}")
    print("=" * 56)

    if DCA_AMOUNT_USD < 1:
        print("❌ DCA_AMOUNT_USD en az $1 olmalı.")
        return

    if cash < DCA_AMOUNT_USD:
        print(f"❌ Yetersiz nakit: ${cash:,.2f} < ${DCA_AMOUNT_USD:,.2f}. Hesaba para yatır veya dilimi düşür.")
        return

    # Piyasa açık mı? (notional/fractional emirler piyasa açıkken çalışır)
    try:
        clock = client.get_clock()
        if not clock.is_open:
            print(f"⚠️  Piyasa KAPALI. Sonraki açılış: {clock.next_open}")
            print("    Fractional/notional emir yalnızca piyasa açıkken (US 09:30–16:00 ET) çalışır.")
            if execute:
                print("    Emir GÖNDERİLMEDİ — piyasa açıkken tekrar dene.")
                return
    except Exception as e:
        print(f"  (Piyasa saati kontrol edilemedi: {e})")

    if not execute:
        print(f"\n[DRY-RUN] ${DCA_AMOUNT_USD:.2f} değerinde {DCA_SYMBOL} alınacaktı (emir GÖNDERİLMEDİ).")
        print("Gerçekten almak için:  py dca_invest.py --execute")
        return

    # Notional (dolar-bazlı) market emri — Alpaca fractional shares ile $ tutarı alır
    req = MarketOrderRequest(
        symbol=DCA_SYMBOL,
        notional=round(DCA_AMOUNT_USD, 2),
        side=OrderSide.BUY,
        time_in_force=TimeInForce.DAY,
    )
    order = client.submit_order(req)
    ts = datetime.now().isoformat()
    print(f"\n✅ ALINDI: ${DCA_AMOUNT_USD:.2f} {DCA_SYMBOL}  | order id: {order.id}")

    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{ts} | {mode} | BUY ${DCA_AMOUNT_USD:.2f} {DCA_SYMBOL} | {order.id}\n")
        print(f"   Kayıt: {LOG_FILE}")
    except Exception as e:
        print(f"   (Log yazılamadı: {e})")


if __name__ == "__main__":
    main()
