"""
Options Engine — Ana Opsiyon Karar Motoru.

Hisse sinyal analizini opsiyon fırsatına dönüştürür.
Teknik analiz sinyali (BUY/SHORT) → optimal kontrat seçimi → emir.

Karar akışı:
  1. Sinyal güçlü mü? (confidence >= options_min_confidence)
  2. Opsiyon mu hisse mi? (güçlü sinyal → opsiyon, zayıf → hisse)
  3. CALL mı PUT mı? (BUY → CALL, SHORT → PUT)
  4. En optimal kontratı seç (strike, vade, Greeks)
"""
import logging
from typing import Dict, Optional

from utils.logger import logger


class OptionsEngine:
    """AI-destekli opsiyon trading motoru."""

    def __init__(self, bot):
        self.bot = bot

    def evaluate_option_trade(
        self,
        symbol: str,
        analysis: Dict,
        decision: Dict,
        options_config: Dict,
    ) -> Optional[Dict]:
        """Hisse sinyalini opsiyon fırsatına dönüştür.

        Args:
            symbol: Hisse sembolü
            analysis: Teknik analiz sonuçları
            decision: Agent coordinator kararı
            options_config: OPTIONS_CONFIG

        Returns:
            Opsiyon trade bilgisi veya None (opsiyon uygun değilse)
        """
        try:
            signal = decision.get("signal", "HOLD")
            confidence = decision.get("confidence", 0)

            # HOLD sinyalinde opsiyon yok
            if signal == "HOLD":
                return None

            # Kara liste kontrolü
            if symbol in options_config.get("options_blacklist", []):
                return None

            # Minimum güven kontrolü
            min_conf = options_config.get("options_min_confidence", 55)
            if confidence < min_conf:
                return None

            # Yön belirleme
            if signal == "BUY":
                direction = "CALL"
                min_dir_conf = options_config.get(
                    "options_call_min_confidence", 55
                )
            elif signal in ("SHORT", "SELL"):
                direction = "PUT"
                min_dir_conf = options_config.get(
                    "options_put_min_confidence", 55
                )
            else:
                return None

            if confidence < min_dir_conf:
                return None

            # Max pozisyon kontrolü
            max_positions = options_config.get("options_max_positions", 5)
            if len(self.bot.options_positions) >= max_positions:
                return None

            # Exposure kontrolü
            equity = self.bot.equity
            max_exposure = equity * options_config.get(
                "options_max_exposure_pct", 0.20
            )
            current_exposure = sum(
                pos.get("cost_basis", 0)
                for pos in self.bot.options_positions.values()
            )
            if current_exposure >= max_exposure:
                return None

            # En optimal kontratı bul
            option_info = self.bot.options_analyzer.find_optimal_contract(
                symbol=symbol,
                direction=direction,
                confidence=confidence,
                config=options_config,
            )

            if option_info is None:
                logger.debug(
                    f"  {symbol} {direction}: Uygun kontrat bulunamadı"
                )
                return None

            # Ek bilgiler ekle
            option_info["analysis"] = analysis
            option_info["decision"] = decision

            logger.info(
                f"  🎯 {symbol} {direction} OPSİYON FIRSATI | "
                f"Strike: ${option_info['strike']} | "
                f"Vade: {option_info['expiry']} | "
                f"Güven: {confidence:.0f} | "
                f"Skor: {option_info.get('score', 0):.0f}"
            )

            return option_info

        except Exception as e:
            logger.debug(f"  {symbol} opsiyon değerlendirme hatası: {e}")
            return None

    def should_prefer_options(
        self,
        symbol: str,
        confidence: float,
        options_config: Dict,
    ) -> bool:
        """Hisse yerine opsiyon tercih edilmeli mi?

        Koşullar:
        - Paper hesap
        - prefer_options_over_stock = True
        - Güven yüksek (>= 60)
        - Sembol tercih listesinde
        - Opsiyon pozisyon limiti dolmamış
        """
        if not self.bot.is_paper:
            return False

        if not options_config.get("options_enabled", False):
            return False

        # Paper agresif mod: güçlü sinyalde opsiyon tercih et
        from config import PAPER_AGGRESSIVE_CONFIG

        if not PAPER_AGGRESSIVE_CONFIG.get("prefer_options_over_stock", False):
            return False

        # Güven kontrolü
        if confidence < 60:
            return False

        # Tercih edilen sembol mü?
        preferred = options_config.get("options_preferred_symbols", [])
        if symbol in preferred:
            return True

        # Güven çok yüksek → her sembol için opsiyon
        if confidence >= 70:
            return True

        return False
