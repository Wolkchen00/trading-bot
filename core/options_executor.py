"""
Options Executor — Opsiyon Emir Yönetimi.

Görevler:
  1. CALL opsiyon al (BUY CALL)
  2. PUT opsiyon al (BUY PUT)
  3. Opsiyon pozisyonunu kapat (SELL to close)
  4. Pozisyon boyutlandırma (max $1000/trade, max %20 exposure)
  5. PDT kontrolü + Telegram bildirim
"""
import logging
from datetime import datetime
from typing import Dict, Optional

from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

from utils.logger import logger


class OptionsExecutor:
    """Opsiyon emir yönetimi — CALL ve PUT alım/satım."""

    def __init__(self, bot):
        self.bot = bot

    def execute_call(
        self,
        option_info: Dict,
        analysis: Dict,
        config: Dict,
    ) -> bool:
        """CALL opsiyon al.

        Args:
            option_info: OptionsAnalyzer'dan gelen kontrat bilgisi
            analysis: Teknik analiz sonuçları
            config: OPTIONS_CONFIG

        Returns:
            True = emir başarılı
        """
        return self._execute_option("CALL", option_info, analysis, config)

    def execute_put(
        self,
        option_info: Dict,
        analysis: Dict,
        config: Dict,
    ) -> bool:
        """PUT opsiyon al."""
        return self._execute_option("PUT", option_info, analysis, config)

    def _execute_option(
        self,
        direction: str,
        option_info: Dict,
        analysis: Dict,
        config: Dict,
    ) -> bool:
        """Opsiyon alım emri gönder."""
        try:
            contract_symbol = option_info["symbol"]
            underlying = option_info["underlying"]
            strike = option_info["strike"]
            expiry = option_info["expiry"]

            # === KONTROLLER ===

            # 0. Bekleyen emir kontrolü — MÜKERRER EMİR KORUMASI
            # Limit emri dolmadıkça Alpaca'da pozisyon oluşmaz; sync hafızadaki
            # kaydı düşürünce sinyal her turda YENİDEN emir basıyordu (2026-07-02:
            # 28 adet açık AMD PUT emri birikti). Aynı underlying için açık
            # opsiyon emri varken yenisi gönderilmez.
            try:
                from alpaca.trading.requests import GetOrdersRequest
                from alpaca.trading.enums import QueryOrderStatus
                open_orders = self.bot.client.get_orders(
                    GetOrdersRequest(status=QueryOrderStatus.OPEN)
                )
                for o in open_orders:
                    osym = getattr(o, "symbol", "") or ""
                    # Opsiyon sembolü: AMD260710P00435000 (underlying + YYMMDD + C/P + strike)
                    if osym.startswith(underlying) and len(osym) > len(underlying) + 8:
                        logger.debug(
                            f"  {underlying} OPT: Bekleyen opsiyon emri var ({osym}), yeni emir atlanıyor"
                        )
                        return False
            except Exception as e:
                logger.debug(f"  {underlying} OPT: Açık emir kontrolü başarısız: {e}")

            # 1. Max pozisyon kontrolü
            max_positions = config.get("options_max_positions", 5)
            current_options = len(self.bot.options_positions)
            if current_options >= max_positions:
                logger.debug(
                    f"  {underlying} OPT: Max opsiyon pozisyon ({max_positions})"
                )
                return False

            # 2. Aynı sembolde max kontrolü
            max_per_symbol = config.get("options_max_per_symbol", 2)
            symbol_count = sum(
                1
                for pos in self.bot.options_positions.values()
                if pos.get("underlying") == underlying
            )
            if symbol_count >= max_per_symbol:
                logger.debug(
                    f"  {underlying} OPT: Max per symbol ({max_per_symbol})"
                )
                return False

            # 3. Max exposure kontrolü
            equity = self.bot.equity
            max_exposure_pct = config.get("options_max_exposure_pct", 0.20)
            current_exposure = sum(
                pos.get("cost_basis", 0)
                for pos in self.bot.options_positions.values()
            )
            remaining_budget = (equity * max_exposure_pct) - current_exposure
            if remaining_budget <= 0:
                logger.debug(f"  {underlying} OPT: Max exposure aşıldı")
                return False

            # 4. Pozisyon boyutu hesapla
            max_usd = min(
                config.get("options_max_position_usd", 1000),
                remaining_budget,
            )

            # Kontrat fiyatı tahmini (close_price veya snapshot)
            contract_price = self._get_contract_price(option_info)
            if contract_price is None or contract_price <= 0:
                logger.debug(f"  {underlying} OPT: Fiyat alınamadı")
                return False

            # Kaç kontrat alabiliriz? (1 kontrat = 100 hisse)
            cost_per_contract = contract_price * 100
            qty = max(1, int(max_usd / cost_per_contract))

            # Bütçe kontrolü
            total_cost = qty * cost_per_contract
            if total_cost > max_usd:
                qty = max(1, int(max_usd / cost_per_contract))
                total_cost = qty * cost_per_contract

            if total_cost > max_usd * 1.1:  # %10 tolerans
                logger.debug(
                    f"  {underlying} OPT: Çok pahalı (${total_cost:.0f} > ${max_usd:.0f})"
                )
                return False

            # === EMİR GÖNDER ===
            try:
                order_data = LimitOrderRequest(
                    symbol=contract_symbol,
                    qty=qty,
                    side=OrderSide.BUY,
                    time_in_force=TimeInForce.DAY,
                    limit_price=round(contract_price * 1.02, 2),  # %2 tolerans
                )

                order = self.bot.client.submit_order(order_data=order_data)

                # Pozisyon kaydet
                self.bot.options_positions[contract_symbol] = {
                    "underlying": underlying,
                    "type": direction,
                    "strike": strike,
                    "expiry": expiry,
                    "qty": qty,
                    "entry_price": contract_price,
                    "cost_basis": total_cost,
                    "entry_time": datetime.now().isoformat(),
                    "order_id": str(order.id) if order else None,
                    "confidence": analysis.get("confidence", 0),
                    "highest_price": contract_price,
                    "lowest_price": contract_price,
                }

                emoji = "📞" if direction == "CALL" else "📉"
                logger.info(
                    f"  {emoji} {underlying} {direction} ALINDI | "
                    f"Strike: ${strike} | Vade: {expiry} | "
                    f"Fiyat: ${contract_price:.2f} | Adet: {qty} | "
                    f"Maliyet: ${total_cost:.0f} | "
                    f"Güven: {analysis.get('confidence', 0):.0f}"
                )

                # Telegram bildirim
                try:
                    self.bot.notifier.send_message(
                        f"{emoji} {direction} ALINDI\n"
                        f"Hisse: {underlying}\n"
                        f"Kontrat: {contract_symbol}\n"
                        f"Strike: ${strike} | Vade: {expiry}\n"
                        f"Fiyat: ${contract_price:.2f} × {qty}\n"
                        f"Toplam: ${total_cost:.0f}"
                    )
                except Exception:
                    pass

                # Metadata kaydet
                try:
                    self.bot._save_position_metadata()
                except Exception:
                    pass

                return True

            except Exception as e:
                logger.error(f"  {underlying} OPT emir hatası: {e}")
                return False

        except Exception as e:
            logger.error(f"  Opsiyon execute hatası: {e}")
            return False

    def close_option(
        self, contract_symbol: str, reason: str = "MANUAL"
    ) -> bool:
        """Opsiyon pozisyonunu kapat (SELL to close)."""
        try:
            pos = self.bot.options_positions.get(contract_symbol)
            if not pos:
                return False

            qty = pos.get("qty", 1)
            underlying = pos.get("underlying", "???")
            direction = pos.get("type", "???")
            entry_price = pos.get("entry_price", 0)

            # Alpaca close_position
            try:
                self.bot.client.close_position(contract_symbol)
            except Exception as e:
                # Fallback: market sell
                try:
                    order_data = MarketOrderRequest(
                        symbol=contract_symbol,
                        qty=qty,
                        side=OrderSide.SELL,
                        time_in_force=TimeInForce.DAY,
                    )
                    self.bot.client.submit_order(order_data=order_data)
                except Exception as e2:
                    logger.error(
                        f"  {underlying} OPT kapatma hatası: {e2}"
                    )
                    return False

            # PnL hesapla (tahmini)
            current_price = self._get_current_price(contract_symbol)
            pnl = 0
            if current_price and entry_price:
                pnl = (current_price - entry_price) * 100 * qty

            emoji = "✅" if pnl >= 0 else "❌"
            logger.info(
                f"  {emoji} {underlying} {direction} KAPATILDI | "
                f"Sebep: {reason} | PnL: ${pnl:+.2f}"
            )

            # Pozisyondan çıkar
            if contract_symbol in self.bot.options_positions:
                del self.bot.options_positions[contract_symbol]

            # Telegram bildirim
            try:
                self.bot.notifier.send_message(
                    f"{emoji} {direction} KAPATILDI\n"
                    f"Hisse: {underlying}\n"
                    f"Sebep: {reason}\n"
                    f"PnL: ${pnl:+.2f}"
                )
            except Exception:
                pass

            # Ajan performance feedback
            try:
                outcome = "WIN" if pnl > 0 else "LOSS"
                self.bot.agent_perf.record_outcome(
                    symbol=underlying, outcome=outcome
                )
            except Exception:
                pass

            # Metadata kaydet
            try:
                self.bot._save_position_metadata()
            except Exception:
                pass

            return True

        except Exception as e:
            logger.error(f"  Opsiyon kapatma hatası: {e}")
            return False

    def close_partial(
        self, contract_symbol: str, qty_to_close: int, reason: str = "PARTIAL_PROFIT"
    ) -> bool:
        """Opsiyon pozisyonunun bir kısmını kapat."""
        try:
            pos = self.bot.options_positions.get(contract_symbol)
            if not pos:
                return False

            total_qty = pos.get("qty", 1)
            if qty_to_close >= total_qty:
                return self.close_option(contract_symbol, reason)

            # Kısmi satış
            order_data = MarketOrderRequest(
                symbol=contract_symbol,
                qty=qty_to_close,
                side=OrderSide.SELL,
                time_in_force=TimeInForce.DAY,
            )
            self.bot.client.submit_order(order_data=order_data)

            # Qty güncelle
            pos["qty"] = total_qty - qty_to_close
            pos["cost_basis"] = pos["entry_price"] * 100 * pos["qty"]

            logger.info(
                f"  🔀 {pos['underlying']} {pos['type']} KISMEN KAPATILDI | "
                f"{qty_to_close}/{total_qty} | Sebep: {reason}"
            )

            return True

        except Exception as e:
            logger.debug(f"  Kısmi opsiyon kapatma hatası: {e}")
            return False

    def _get_contract_price(self, option_info: Dict) -> Optional[float]:
        """Kontrat fiyatını al."""
        try:
            # Önce close_price dene
            contract = option_info.get("contract")
            if contract and contract.close_price:
                return float(contract.close_price)

            # Snapshot dene
            if hasattr(self.bot, "options_analyzer"):
                snap = self.bot.options_analyzer.get_contract_snapshot(
                    option_info["symbol"]
                )
                if snap and snap.get("latest_trade_price"):
                    return snap["latest_trade_price"]
                if snap and snap.get("ask"):
                    return snap["ask"]

            return None
        except Exception:
            return None

    def _get_current_price(self, contract_symbol: str) -> Optional[float]:
        """Kontratın güncel fiyatını al."""
        try:
            if hasattr(self.bot, "options_analyzer"):
                snap = self.bot.options_analyzer.get_contract_snapshot(
                    contract_symbol
                )
                if snap:
                    return snap.get("latest_trade_price") or snap.get("bid")
            return None
        except Exception:
            return None
