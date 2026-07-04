"""
Signal Queue — Bekleyen Sinyal Kuyrugu (Entry Timing Optimizasyonu)

Sinyal geldiginde hemen alma, pullback bekle:
  1. Sinyal gelir -> kuyruga eklenir
  2. Fiyat %1-2 pullback yaparsa -> GERCEK ALIS
  3. 2 saat icinde pullback gelmezse -> Sinyal iptal
  4. Opsiyonel: breakout confirmation (momentum devam ederse al)

Bu sayede "dusen bicagi yakalama" riski azalir.
"""
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from utils.logger import logger


class SignalQueue:
    """Bekleyen sinyal kuyrugu — pullback ile optimum giris."""

    # Varsayilan parametreler
    DEFAULT_PULLBACK_PCT = 0.015   # %1.5 pullback hedef
    DEFAULT_EXPIRY_HOURS = 2       # 2 saat sonra iptal
    MAX_QUEUE_SIZE = 5             # Maksimum kuyruk boyutu

    def __init__(self):
        self.pending: Dict[str, Dict] = {}
        self.executed: List[Dict] = []  # Son tetiklenenler
        logger.info("SignalQueue baslatildi — pullback entry optimizasyonu aktif")

    def add_signal(self, symbol: str, signal: str, analysis: Dict,
                    decision: Dict, pullback_pct: float = None) -> bool:
        """
        Yeni sinyal ekle — hemen islem yapma, kuyruga al.

        Args:
            symbol: Hisse sembolu
            signal: "BUY" veya "SHORT"
            analysis: Teknik analiz verisi
            decision: Coordinator karari
            pullback_pct: Custom pullback yuzdesi (None = varsayilan)

        Returns:
            True = kuyruga eklendi, False = zaten kuyrukta veya dolu
        """
        # Zaten kuyrukta mi?
        if symbol in self.pending:
            logger.debug(f"  {symbol} zaten kuyrukta")
            return False

        # Kuyruk dolu mu?
        self._cleanup_expired()
        if len(self.pending) >= self.MAX_QUEUE_SIZE:
            logger.debug(f"  Sinyal kuyrugu dolu ({self.MAX_QUEUE_SIZE})")
            return False

        price = analysis.get("price", 0)
        if price <= 0:
            return False

        pb_pct = pullback_pct or self.DEFAULT_PULLBACK_PCT

        if signal == "BUY":
            target_entry = price * (1 - pb_pct)  # Daha dusuk fiyat
        else:  # SHORT
            target_entry = price * (1 + pb_pct)  # Daha yuksek fiyat (short icin)

        self.pending[symbol] = {
            "signal": signal,
            "original_price": round(price, 2),
            "target_entry": round(target_entry, 2),
            "max_entry": round(price, 2),  # Bundan yukarida alma
            "pullback_pct": pb_pct,
            "confidence": decision.get("confidence", 0),
            "analysis": analysis,
            "decision": decision,
            "created_at": datetime.now(),
            "expiry": datetime.now() + timedelta(hours=self.DEFAULT_EXPIRY_HOURS),
        }

        logger.info(
            f"  KUYRUK + {symbol}: {signal} @ ${price:.2f} "
            f"-> Hedef: ${target_entry:.2f} ({pb_pct:.1%} pullback) "
            f"| Suresi: {self.DEFAULT_EXPIRY_HOURS}sa"
        )
        return True

    def check_entries(self, bot) -> List[Dict]:
        """
        Kuyruktaki sinyalleri kontrol et — fiyat hedefe ulastiysa tetikle.

        Args:
            bot: StockBot instance (guncel fiyat icin)

        Returns:
            Tetiklenen sinyallerin listesi
        """
        self._cleanup_expired()
        ready = []

        for symbol, sig in list(self.pending.items()):
            try:
                # Guncel fiyati al (dogru snapshot API kullanimi)
                from core.gap_scanner import fetch_latest_price
                current_price = fetch_latest_price(bot.data_client, symbol)
                if current_price is None:
                    continue

                triggered = False

                if sig["signal"] == "BUY":
                    # BUY: fiyat target'a dustuyse AL
                    if current_price <= sig["target_entry"]:
                        triggered = True
                        trigger_reason = f"Pullback hedefine ulasti (${current_price:.2f} <= ${sig['target_entry']:.2f})"

                elif sig["signal"] == "SHORT":
                    # SHORT: fiyat target'a ciktiysa SHORT
                    if current_price >= sig["target_entry"]:
                        triggered = True
                        trigger_reason = f"Bounce hedefine ulasti (${current_price:.2f} >= ${sig['target_entry']:.2f})"

                if triggered:
                    logger.info(
                        f"  SINYAL TETIKLENDI: {symbol} {sig['signal']} "
                        f"@ ${current_price:.2f} | {trigger_reason}"
                    )

                    # Analiz fiyatini guncelle
                    sig["analysis"]["price"] = current_price
                    sig["trigger_price"] = current_price
                    sig["trigger_reason"] = trigger_reason

                    ready.append(sig)
                    ready[-1]["symbol"] = symbol
                    del self.pending[symbol]

                    self.executed.append({
                        "symbol": symbol,
                        "signal": sig["signal"],
                        "original_price": sig["original_price"],
                        "trigger_price": current_price,
                        "savings_pct": abs(current_price - sig["original_price"]) / sig["original_price"] * 100,
                        "timestamp": datetime.now().isoformat(),
                    })

            except Exception as e:
                logger.debug(f"  Kuyruk kontrol hatasi {symbol}: {e}")

        return ready

    def _cleanup_expired(self):
        """Suresi dolmus sinyalleri temizle."""
        now = datetime.now()
        expired = [
            sym for sym, sig in self.pending.items()
            if now > sig["expiry"]
        ]
        for sym in expired:
            logger.debug(f"  Sinyal suresi doldu: {sym}")
            del self.pending[sym]

    def get_queue_status(self) -> Dict:
        """Kuyruk durumu."""
        self._cleanup_expired()
        return {
            "pending_count": len(self.pending),
            "pending_symbols": list(self.pending.keys()),
            "executed_count": len(self.executed),
            "last_executed": self.executed[-1] if self.executed else None,
        }

    def cancel_signal(self, symbol: str) -> bool:
        """Manuel sinyal iptali."""
        if symbol in self.pending:
            del self.pending[symbol]
            logger.info(f"  Sinyal iptal edildi: {symbol}")
            return True
        return False
