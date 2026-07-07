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
import time
from datetime import datetime, timedelta
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

            # 0.5 v4.9: COOLDOWN — kapanış (özellikle stop-out) sonrası aynı
            # underlying'e hemen geri girme. 06 Tem: stop → 60-90sn sonra aynı
            # PUT tekrar alındı, 8 turda -$2,170 spread yandı.
            cooldowns = getattr(self.bot, "_opt_cooldowns", {})
            cd_until = cooldowns.get(underlying)
            if cd_until and datetime.now() < cd_until:
                logger.debug(
                    f"  {underlying} OPT: cooldown aktif "
                    f"({cd_until.strftime('%H:%M')} kadar), atlanıyor"
                )
                return False

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

            # === v4.9: CANLI QUOTE + SPREAD/LİKİDİTE KAPISI ===
            # 06 Tem dersi: bayat close_price ($0.71) ile emir atıp $0.42'ye
            # dolmak, sonra bid $0.20'ye karşı "-%72 stop" yemek = spread yakma
            # makinesi. Artık canlı bid/ask şart; genişse (illikit) İŞLEM YOK.
            snap = None
            if hasattr(self.bot, "options_analyzer"):
                snap = self.bot.options_analyzer.get_contract_snapshot(contract_symbol)
            bid = (snap or {}).get("bid") or 0
            ask = (snap or {}).get("ask") or 0
            if bid <= 0 or ask <= 0 or ask < bid:
                logger.info(
                    f"  {underlying} OPT: canlı quote yok/bozuk "
                    f"(bid={bid}, ask={ask}) — işlem YAPILMIYOR"
                )
                return False
            mid = (bid + ask) / 2.0
            max_spread = config.get("options_max_spread_pct", 0.10)
            spread_pct = (ask - bid) / mid if mid > 0 else 1.0
            if spread_pct > max_spread:
                logger.info(
                    f"  {underlying} OPT: spread %{spread_pct*100:.0f} > "
                    f"limit %{max_spread*100:.0f} (illikit) — işlem YAPILMIYOR"
                )
                return False

            # Emir fiyatı: en fazla ask öde (marketable limit)
            limit_price = round(ask, 2)
            cost_per_contract = limit_price * 100
            qty = int(max_usd / cost_per_contract)
            if qty < 1:
                logger.debug(
                    f"  {underlying} OPT: bütçe yetmiyor "
                    f"(${max_usd:.0f} < kontrat ${cost_per_contract:.0f})"
                )
                return False

            # === EMİR GÖNDER + FILL BEKLE (v4.9: dolum onayı olmadan kayıt YOK) ===
            try:
                order_data = LimitOrderRequest(
                    symbol=contract_symbol,
                    qty=qty,
                    side=OrderSide.BUY,
                    time_in_force=TimeInForce.DAY,
                    limit_price=limit_price,
                )
                order = self.bot.client.submit_order(order_data=order_data)

                filled_qty, fill_price = self._wait_for_fill(order.id)

                if filled_qty <= 0 or not fill_price:
                    # Dolmadı → emri iptal et, HİÇBİR ŞEY kaydetme.
                    # (Eski kod dolmamış limit emrini "ALINDI" diye loglayıp
                    # pozisyon defterine yazıyordu — 07 Tem SMCI vakası.)
                    try:
                        self.bot.client.cancel_order_by_id(order.id)
                    except Exception:
                        pass
                    logger.info(
                        f"  {underlying} {direction} emri DOLMADI "
                        f"(limit ${limit_price:.2f} × {qty}) — iptal edildi"
                    )
                    return False

                total_cost = fill_price * 100 * filled_qty

                # Pozisyon kaydet — GERÇEK dolum fiyatı ve adediyle
                self.bot.options_positions[contract_symbol] = {
                    "underlying": underlying,
                    "type": direction,
                    "strike": strike,
                    "expiry": expiry,
                    "qty": filled_qty,
                    "entry_price": fill_price,
                    "cost_basis": total_cost,
                    "entry_time": datetime.now().isoformat(),
                    "order_id": str(order.id) if order else None,
                    "confidence": analysis.get("confidence", 0),
                    "highest_price": fill_price,
                    "lowest_price": fill_price,
                }

                emoji = "📞" if direction == "CALL" else "📉"
                logger.info(
                    f"  {emoji} {underlying} {direction} ALINDI (dolum onaylı) | "
                    f"Strike: ${strike} | Vade: {expiry} | "
                    f"Dolum: ${fill_price:.2f} | Adet: {filled_qty} | "
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
                        f"Dolum: ${fill_price:.2f} × {filled_qty}\n"
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

    def _wait_for_fill(self, order_id, tries: int = 6, delay: float = 2.0):
        """Emrin dolmasını kısa süre bekle (v4.9).

        Returns:
            (filled_qty, filled_avg_price) — dolmadıysa (0, None)
        """
        for _ in range(tries):
            try:
                o = self.bot.client.get_order_by_id(order_id)
                status = getattr(o.status, "value", str(o.status))
                filled_qty = int(float(o.filled_qty or 0))
                if status == "filled" and filled_qty > 0:
                    return filled_qty, float(o.filled_avg_price)
                if status in ("canceled", "expired", "rejected"):
                    return filled_qty, (
                        float(o.filled_avg_price) if filled_qty > 0 else None
                    )
            except Exception:
                pass
            time.sleep(delay)
        # Süre doldu — kısmi dolum varsa onu raporla
        try:
            o = self.bot.client.get_order_by_id(order_id)
            filled_qty = int(float(o.filled_qty or 0))
            if filled_qty > 0:
                return filled_qty, float(o.filled_avg_price)
        except Exception:
            pass
        return 0, None

    def _set_cooldown(self, underlying: str):
        """Kapanış sonrası aynı underlying'e yeniden giriş yasağı (v4.9)."""
        try:
            from config import OPTIONS_CONFIG
            hours = OPTIONS_CONFIG.get("options_reentry_cooldown_hours", 4)
            if not hasattr(self.bot, "_opt_cooldowns"):
                self.bot._opt_cooldowns = {}
            self.bot._opt_cooldowns[underlying] = datetime.now() + timedelta(hours=hours)
        except Exception:
            pass

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

            # Alpaca close_position (dönen emri fill-takip için tut)
            close_order = None
            try:
                close_order = self.bot.client.close_position(contract_symbol)
            except Exception:
                # Fallback: market sell
                try:
                    order_data = MarketOrderRequest(
                        symbol=contract_symbol,
                        qty=qty,
                        side=OrderSide.SELL,
                        time_in_force=TimeInForce.DAY,
                    )
                    close_order = self.bot.client.submit_order(order_data=order_data)
                except Exception as e2:
                    logger.error(
                        f"  {underlying} OPT kapatma hatası: {e2}"
                    )
                    return False

            # v4.9: PnL GERÇEK kapanış dolumundan. Eski kod bozuk snapshot'a
            # düşüp hep "PnL: $+0.00" yazıyor ve ajan istatistiğine LOSS
            # kaydediyordu (06 Tem'de 8 sahte kayıt).
            exit_price = None
            if close_order is not None:
                _fq, exit_price = self._wait_for_fill(
                    close_order.id, tries=4, delay=1.5
                )
            if not exit_price:
                snap_price = self._get_current_price(contract_symbol)
                exit_price = snap_price if snap_price else None

            pnl = None
            if exit_price and entry_price:
                pnl = (exit_price - entry_price) * 100 * qty

            emoji = "✅" if (pnl or 0) >= 0 else "❌"
            pnl_str = f"${pnl:+.2f}" if pnl is not None else "bilinmiyor"
            exit_str = f"${exit_price:.2f}" if exit_price else "?"
            logger.info(
                f"  {emoji} {underlying} {direction} KAPATILDI | "
                f"Sebep: {reason} | Çıkış: {exit_str} | PnL: {pnl_str}"
            )

            # Pozisyondan çıkar
            if contract_symbol in self.bot.options_positions:
                del self.bot.options_positions[contract_symbol]

            # v4.9: churn kilidi — kapanan underlying'e cooldown
            self._set_cooldown(underlying)

            # Telegram bildirim
            try:
                self.bot.notifier.send_message(
                    f"{emoji} {direction} KAPATILDI\n"
                    f"Hisse: {underlying}\n"
                    f"Sebep: {reason}\n"
                    f"PnL: {pnl_str}"
                )
            except Exception:
                pass

            # Ajan performance feedback — yalnız PnL GERÇEKTEN biliniyorsa
            # (None → kayıt yok; 0'ı LOSS saymak istatistiği zehirliyordu)
            try:
                if pnl is not None and pnl != 0:
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
        """Kontrat fiyatını al.

        v4.9: sıralama düzeltildi — CANLI mid/trade önce, bayat close_price
        EN SON çare (eski sıralama 06 Tem churn'ünün fiyat halkasıydı).
        """
        try:
            if hasattr(self.bot, "options_analyzer"):
                snap = self.bot.options_analyzer.get_contract_snapshot(
                    option_info["symbol"]
                )
                if snap:
                    bid, ask = snap.get("bid"), snap.get("ask")
                    if bid and ask and ask >= bid > 0:
                        return (bid + ask) / 2.0
                    if snap.get("latest_trade_price"):
                        return snap["latest_trade_price"]

            contract = option_info.get("contract")
            if contract and contract.close_price:
                return float(contract.close_price)

            return None
        except Exception:
            return None

    def _get_current_price(self, contract_symbol: str) -> Optional[float]:
        """Kontratın güncel fiyatını al (v4.9: mid tercih — tek taraflı bid
        okumak geniş spread'de sahte '-%70 zarar' üretiyordu)."""
        try:
            if hasattr(self.bot, "options_analyzer"):
                snap = self.bot.options_analyzer.get_contract_snapshot(
                    contract_symbol
                )
                if snap:
                    bid, ask = snap.get("bid"), snap.get("ask")
                    if bid and ask and ask >= bid > 0:
                        return (bid + ask) / 2.0
                    return snap.get("latest_trade_price") or snap.get("bid")
            return None
        except Exception:
            return None
