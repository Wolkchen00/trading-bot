"""
Options Position Manager — Açık Opsiyon Pozisyonlarını Yönet.

Kurallar:
  1. %80+ kar → KAPAT (take-profit)
  2. %50+ kar → yarısını sat (partial profit)
  3. %40+ zarar → KAPAT (stop-loss)
  4. Vadeye ≤1 gün kala → KAPAT (theta hızlanır)
  5. Tracking: en yüksek/düşük fiyat takibi
"""
import logging
from datetime import datetime, date
from typing import Dict

from utils.logger import logger


class OptionsPositionManager:
    """Açık opsiyon pozisyonlarını yönet."""

    def __init__(self, bot):
        self.bot = bot

    def manage_positions(self, config: Dict):
        """Tüm açık opsiyon pozisyonlarını kontrol et ve yönet."""
        if not self.bot.options_positions:
            return

        positions_to_close = []

        for symbol, pos in list(self.bot.options_positions.items()):
            try:
                action = self._evaluate_position(symbol, pos, config)
                if action:
                    positions_to_close.append((symbol, action))
            except Exception as e:
                logger.debug(f"  {pos.get('underlying', symbol)} OPT yönetim hatası: {e}")

        # Aksiyonları uygula
        for symbol, action in positions_to_close:
            try:
                if action["action"] == "CLOSE":
                    self.bot.options_executor.close_option(
                        symbol, action["reason"]
                    )
                elif action["action"] == "PARTIAL":
                    qty = action.get("qty", 1)
                    self.bot.options_executor.close_partial(
                        symbol, qty, action["reason"]
                    )
            except Exception as e:
                logger.debug(f"  {symbol} OPT aksiyon hatası: {e}")

    def _evaluate_position(
        self, symbol: str, pos: Dict, config: Dict
    ) -> Dict:
        """Tek bir opsiyon pozisyonunu değerlendir.

        Returns:
            {"action": "CLOSE"/"PARTIAL", "reason": "..."} veya None
        """
        underlying = pos.get("underlying", "???")
        direction = pos.get("type", "???")
        entry_price = pos.get("entry_price", 0)
        qty = pos.get("qty", 1)
        expiry_str = pos.get("expiry", "")
        cost_basis = pos.get("cost_basis", 0)

        # Güncel fiyat al
        current_price = self._get_current_price(symbol)
        if current_price is None:
            return None

        # Tracking güncelle
        pos["highest_price"] = max(
            pos.get("highest_price", current_price), current_price
        )
        pos["lowest_price"] = min(
            pos.get("lowest_price", current_price), current_price
        )

        # PnL hesapla
        if entry_price <= 0:
            return None
        pnl_pct = (current_price - entry_price) / entry_price

        # === KURAL 1: TAKE PROFIT ===
        tp_pct = config.get("options_take_profit_pct", 0.80)
        if pnl_pct >= tp_pct:
            return {
                "action": "CLOSE",
                "reason": f"OPT_TAKE_PROFIT ({pnl_pct:+.0%})",
            }

        # === KURAL 2: PARTIAL PROFIT ===
        partial_pct = config.get("options_partial_profit_pct", 0.50)
        if pnl_pct >= partial_pct and qty > 1 and not pos.get("partial_done"):
            half_qty = max(1, qty // 2)
            pos["partial_done"] = True
            return {
                "action": "PARTIAL",
                "qty": half_qty,
                "reason": f"OPT_PARTIAL_PROFIT ({pnl_pct:+.0%})",
            }

        # === KURAL 3: STOP LOSS ===
        sl_pct = config.get("options_stop_loss_pct", 0.40)
        if pnl_pct <= -sl_pct:
            return {
                "action": "CLOSE",
                "reason": f"OPT_STOP_LOSS ({pnl_pct:+.0%})",
            }

        # === KURAL 4: VADE YAKLAŞIYOR ===
        try:
            if isinstance(expiry_str, str) and expiry_str:
                expiry_date = datetime.strptime(expiry_str, "%Y-%m-%d").date()
            elif hasattr(expiry_str, "year"):
                expiry_date = expiry_str
            else:
                expiry_date = None

            if expiry_date:
                days_left = (expiry_date - date.today()).days
                close_before = config.get("options_close_before_expiry_days", 1)
                if days_left <= close_before:
                    return {
                        "action": "CLOSE",
                        "reason": f"OPT_EXPIRY_CLOSE ({days_left}d kaldı)",
                    }
        except Exception:
            pass

        # === KURAL 5: TRAILING STOP (tepeden düşüş) ===
        highest = pos.get("highest_price", current_price)
        if highest > 0 and entry_price > 0:
            from_peak_pct = (current_price - highest) / highest
            # Eğer tepeden %30+ düştüyse ve hala karda ise → kapat
            if from_peak_pct <= -0.30 and current_price > entry_price:
                return {
                    "action": "CLOSE",
                    "reason": f"OPT_TRAILING_STOP (tepeden {from_peak_pct:.0%})",
                }

        return None

    def _get_current_price(self, contract_symbol: str):
        """Kontratın güncel fiyatını al.

        v4.9: MID tercih edilir. Stop-loss bu fiyata bakar; tek taraflı bid
        okumak geniş-spread kontratlarda alımdan saniyeler sonra sahte
        "-%70 zarar" üretip anında stop tetikliyordu (06 Tem churn'ü).
        """
        try:
            if hasattr(self.bot, "options_analyzer"):
                snap = self.bot.options_analyzer.get_contract_snapshot(
                    contract_symbol
                )
                if snap:
                    bid, ask = snap.get("bid"), snap.get("ask")
                    if bid and ask and ask >= bid > 0:
                        return (bid + ask) / 2.0
                    price = snap.get("latest_trade_price") or snap.get("bid")
                    return price

            # Fallback: Alpaca position API
            try:
                alpaca_pos = self.bot.client.get_open_position(contract_symbol)
                return float(alpaca_pos.current_price) if alpaca_pos else None
            except Exception:
                pass

            return None
        except Exception:
            return None

    def get_options_summary(self) -> Dict:
        """Mevcut opsiyon pozisyonlarının özetini döndür."""
        if not self.bot.options_positions:
            return {"count": 0, "total_cost": 0, "positions": []}

        total_cost = 0
        positions = []

        for symbol, pos in self.bot.options_positions.items():
            cost = pos.get("cost_basis", 0)
            total_cost += cost
            positions.append({
                "symbol": symbol,
                "underlying": pos.get("underlying"),
                "type": pos.get("type"),
                "strike": pos.get("strike"),
                "expiry": pos.get("expiry"),
                "qty": pos.get("qty"),
                "cost": cost,
            })

        return {
            "count": len(positions),
            "total_cost": total_cost,
            "positions": positions,
        }
