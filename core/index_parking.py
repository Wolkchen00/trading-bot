"""
Index Parking — botun boştaki nakdini index ETF'ine (SPY) park eder.

NEDEN: regime_experiment overlay testi gösterdi ki SPY açığının ÇOĞU "nakitte
oturma fırsat maliyeti" (alpha −11.5% → −2.8%). Boştaki nakit, cash yerine SPY'de
dururken piyasa beta'sını yakalar → açığın çoğu kapanır.

GÜVENLİK (canlı parayla çalışan sisteme additive, düşük-risk):
  - config ile KAPALI varsayılan; yalnız PAPER'da açık (paper-first).
  - LIVE'da ekstra opt-in şart (index_parking_allow_live).
  - Parking pozisyonu agent/stop-loss/max-pozisyon mantığından DIŞLANIR
    (stock_bot._sync_positions_from_alpaca + _analyze_and_trade guard'ları).
  - GÜNDE 1 rebalance → SPY aynı gün AL-SAT yok → PDT day-trade riski yok.
  - equity-floor ihlalinde park yapılmaz (drawdown'da yeni beta ekleme).
  - Robust: tüm hatalar yutulur, ana trading döngüsünü ASLA bozmaz.

Rebalance mantığı (günde 1): hedef = boştaki nakit index'te, rezerv likit kalsın.
    delta = cash - reserve   (reserve = reserve_pct × equity)
    delta > 0 → fazla nakdi SPY'ye park et (BUY notional)
    delta < 0 → rezerv ihlali, SPY'den çöz (SELL) → trade buying-power tamamlanır
"""
from datetime import date

from alpaca.trading.requests import ClosePositionRequest, MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

from config import TRADING_MODE
from utils.logger import logger


class IndexParkingManager:
    """Boştaki nakdi SPY'de tutan nakit-sleeve yöneticisi (paper-first)."""

    def __init__(self, bot, config):
        self.bot = bot
        self.symbol = config.get("index_parking_symbol", "SPY")
        self.reserve_pct = float(config.get("index_parking_reserve_pct", 0.30))
        self.min_trade = float(config.get("index_parking_min_trade_usd", 50))
        self._last_rebalance = None

        enabled = bool(config.get("index_parking_enabled", False))
        # paper-first: LIVE'da çalışması için ekstra açık onay şart
        if TRADING_MODE == "live" and not config.get("index_parking_allow_live", False):
            enabled = False
        self.enabled = enabled

        if self.enabled:
            logger.info(
                f"  🅿️ INDEX PARKING aktif: boştaki nakit {self.symbol}'de park "
                f"edilecek (rezerv %{self.reserve_pct*100:.0f} likit, mod={TRADING_MODE})"
            )

    def is_parking_symbol(self, symbol: str) -> bool:
        """Bu sembol parking sleeve'i mi? (enabled değilse her zaman False)"""
        return self.enabled and symbol == self.symbol

    def _get_park_position(self):
        """Mevcut parking pozisyonu → (qty, current_price, market_value)."""
        try:
            pos = self.bot.client.get_open_position(self.symbol)
            return float(pos.qty), float(pos.current_price), float(pos.market_value)
        except Exception:
            return 0.0, None, 0.0  # pozisyon yok

    def maybe_rebalance(self):
        """Günde 1 kez boştaki nakdi SPY'de tut, rezervi koru.
        Hata ana döngüyü ASLA bozmaz (sessizce yutulur)."""
        if not self.enabled:
            return
        # v4.9 SELF-HEAL: park sembolü asla SHORT olamaz. 07 Tem'de Alpaca paper
        # tarafı geceleyin pozisyonları kayıtsız sildi; API bir uçta hayalet
        # pozisyon gösterirken satış emri gerçek hesabı 5.3 SPY short'a düşürdü
        # ve 42 dk öyle kaldı. Negatif park pozisyonu görülürse ANINDA kapatılır.
        # (Günlük-rebalance kapısından ÖNCE — her döngüde ucuz bir kontrol.)
        try:
            qty, _price, _mval = self._get_park_position()
            if qty < 0:
                # Zaten bekleyen bir kapatma emri varsa yenisini yığma
                # (halt/kapalı piyasada emir dinlenirken her döngüde yeni
                # buy-to-close basmak açılışta pozisyonu ters yöne şişirir)
                from alpaca.trading.requests import GetOrdersRequest
                from alpaca.trading.enums import QueryOrderStatus
                open_orders = self.bot.client.get_orders(
                    GetOrdersRequest(status=QueryOrderStatus.OPEN)
                )
                if any(getattr(o, "symbol", "") == self.symbol for o in open_orders):
                    return
                logger.error(
                    f"  🅿️🚨 PARK SELF-HEAL: {self.symbol} pozisyonu NEGATİF "
                    f"({qty}) — short derhal kapatılıyor (veri tutarsızlığı?)"
                )
                self.bot.client.close_position(self.symbol)
                return
        except Exception as e:
            logger.debug(f"  Park self-heal kontrol hatası: {e}")
        today = date.today()
        if self._last_rebalance == today:
            return
        # Drawdown koruması: equity-floor ihlalinde yeni beta ekleme
        if getattr(self.bot, "_floor_block", False):
            return
        try:
            account = self.bot.client.get_account()
            cash = float(account.cash)
            equity = float(account.equity)
            reserve = self.reserve_pct * equity
            delta = cash - reserve  # +: park et, -: çöz
            self._last_rebalance = today  # günde tek deneme (hata olsa da yarın tekrar)

            if abs(delta) < self.min_trade:
                return
            if delta > 0:
                self._buy(round(delta, 2))
            else:
                self._sell(round(-delta, 2))
        except Exception as e:
            logger.debug(f"  Index parking rebalance hatası: {e}")

    def _buy(self, notional: float):
        """Notional (dolar) market BUY — fazla nakdi beta'ya."""
        try:
            req = MarketOrderRequest(
                symbol=self.symbol, notional=notional,
                side=OrderSide.BUY, time_in_force=TimeInForce.DAY,
            )
            self.bot.client.submit_order(req)
            logger.info(f"  🅿️ PARK BUY {self.symbol}: ${notional:,.2f} (boş nakit → beta)")
        except Exception as e:
            logger.debug(f"  Park buy hatası: {e}")

    def _sell(self, notional: float):
        """SPY'den notional kadar çöz (gerekirse tümünü) — rezervi tamamla.

        v4.9: SELL emri yerine Alpaca close_position endpoint'i kullanılır.
        DELETE /v2/positions yalnız MEVCUT pozisyonu küçültebilir — pozisyon
        yoksa/yetmiyorsa emir reddedilir, kısa pozisyon AÇAMAZ. 07 Tem'de
        notional SELL, hayalet pozisyon verisi yüzünden hesabı short'a
        düşürmüştü; bu yol o sınıf hatayı yapısal olarak imkânsız kılar.
        """
        qty, price, mval = self._get_park_position()
        if qty <= 0 or mval <= 0 or price is None:
            return  # park yok, satılacak bir şey yok
        try:
            if notional >= mval * 0.99:
                # rezerv açığı ≥ park değeri → tümünü çöz (kalan fraksiyon kalmasın)
                self.bot.client.close_position(self.symbol)
                desc = f"tümü ({qty} pay)"
            else:
                # qty-sınırlı kısmi kapama: istenen notional'ı paya çevir,
                # eldeki miktarı asla aşma
                sell_qty = min(round(notional / price, 6), qty)
                if sell_qty <= 0:
                    return
                self.bot.client.close_position(
                    self.symbol,
                    close_options=ClosePositionRequest(qty=str(sell_qty)),
                )
                desc = f"${notional:,.2f} (~{sell_qty} pay)"
            logger.info(f"  🅿️ PARK SELL {self.symbol}: {desc} (rezerv tamamla)")
        except Exception as e:
            logger.debug(f"  Park sell hatası: {e}")
