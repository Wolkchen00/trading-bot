"""
Position Sizer — Kelly-ATR Adaptif Pozisyon Boyutlandırma

Geleneksel sabit pozisyon boyutu yerine, performans ve volatiliteye göre
dinamik boyutlandırma yapar:
  1. Fractional Kelly: Kazanma oranı ve ortalama K/Z'ye göre optimal boy
  2. ATR Ölçekleme: Yüksek volatilitede küçültme, düşükte büyütme
  3. Kayıp Serisi Dampingi: Ardışık kayıplarla pozisyon otomatik küçülür
  4. Rejim Uyumluluğu: BEAR modda daha küçük long, daha büyük short

Hem LONG hem SHORT pozisyonlar için çalışır (live + paper).
"""
from typing import Dict, Optional
from utils.logger import logger


class PositionSizer:
    """Kelly-ATR adaptif pozisyon boyutlandırma motoru."""

    # Kelly fraction — tam Kelly çok agresif, %35'ini kullanıyoruz
    KELLY_FRACTION = 0.20     # Edge kanıtsız → minimal Kelly (önceki: 0.35 agresif)

    # ATR bazlı volatilite ölçekleme
    ATR_BASE_PCT = 2.0       # %2 ATR = normal volatilite
    ATR_SCALE_FACTOR = 0.12  # ATR her %1 artışta pozisyon %12 küçülür (önceki: %15)

    # Kayıp serisi dampingi
    LOSS_DAMPING_FACTOR = 0.15  # Her ardışık kayıpta %15 küçültme
    MAX_LOSS_DAMPING = 0.60     # En fazla %60 küçültme (min %40 pozisyon)

    # Pozisyon limitleri
    MIN_POSITION_PCT = 0.05   # Equity'nin min %5'i (önceki: %3)
    MAX_POSITION_PCT = 0.20   # Equity'nin max %20'si (de-risk: önceki %50 konsantrasyon riski)

    def __init__(self, performance_tracker=None):
        """
        Args:
            performance_tracker: PerformanceTracker instance (win_rate, avg_win/loss için)
        """
        self.performance = performance_tracker
        logger.info("PositionSizer başlatıldı — Kelly-ATR adaptif mod aktif")

    def calculate_position_size(
        self,
        equity: float,
        price: float,
        atr: float,
        config: Dict,
        side: str = "LONG",
        consecutive_losses: int = 0,
        market_regime: str = "NORMAL",
        sector_weight: float = 1.0,
        confidence: float = 0,
    ) -> Dict:
        """
        Adaptif pozisyon boyutu hesapla.

        Args:
            equity: Toplam hesap bakiyesi ($)
            price: Hisse fiyatı ($)
            atr: Average True Range değeri
            config: İşlem konfigürasyonu (STOCK_CONFIG veya SHORT_CONFIG)
            side: "LONG" veya "SHORT"
            consecutive_losses: Ardışık kayıp sayısı
            market_regime: "BULL", "BEAR", "NORMAL"
            sector_weight: Sektör rotasyonu ağırlığı (0.5-1.5)

        Returns:
            {
                "position_usd": float,    # Yatırılacak $ miktarı
                "qty": float,             # Hisse adedi
                "kelly_pct": float,       # Kelly oranı
                "vol_scale": float,       # Volatilite ölçekleme faktörü
                "loss_damping": float,    # Kayıp dampingi
                "regime_adj": float,      # Rejim ayarı
                "reasoning": str,         # Açıklama
            }
        """
        if equity <= 0 or price <= 0:
            return self._empty_result("Geçersiz equity/price")

        # === KULLANICI BOYUT MODLARI (Kelly/ATR/damping BYPASS) ===
        # Kelly negatifken adaptif yol %5 tabana düşüp küçük hesapta ~$25'lik işlem
        # üretiyor. İki kullanıcı modu (İhsan 2026-07-02):
        #   1) conf_position_bands: güvene göre kademeli boyut (normal $100-200,
        #      çok güvenilir $300) — güveni karşılayan EN YÜKSEK bant seçilir.
        #   2) fixed_position_usd: düz sabit boyut (bantlar tanımlı değilse).
        # Koruma: equity-oranlı tavan kalır (drawdown'da pozisyon otomatik küçülür).
        bands = config.get("conf_position_bands") or []
        fixed_usd = float(config.get("fixed_position_usd", 0) or 0)
        if side == "LONG" and (bands or fixed_usd > 0):
            if bands:
                target_usd = 0.0
                for band_conf, band_usd in bands:
                    if confidence >= band_conf and band_usd > target_usd:
                        target_usd = float(band_usd)
                if target_usd <= 0:
                    return self._empty_result(
                        f"Güven {confidence:.0f} en düşük bandın ({bands[0][0]}) altında"
                    )
                mode_str = f"GÜVEN {confidence:.0f} → ${target_usd:.0f}"
            else:
                target_usd = fixed_usd
                mode_str = f"SABİT ${fixed_usd:.0f}"
            cap_pct = float(config.get("fixed_position_max_pct", 0.55))
            max_pos_usd = config.get("max_position_usd", 500)
            position_usd = min(target_usd, equity * cap_pct, max_pos_usd)
            min_trade = config.get("min_trade_value", 10)
            if position_usd < min_trade:
                return self._empty_result(f"Boyut ${position_usd:.2f} < min ${min_trade}")
            qty = round(position_usd / price, 4)
            reasoning = f"{mode_str} | tavan {cap_pct:.0%}×equity=${equity * cap_pct:.0f}"
            logger.info(f"  📐 PositionSizer [LONG-KADEMELI]: ${position_usd:.2f} | {reasoning}")
            return {
                "position_usd": round(position_usd, 2),
                "qty": qty,
                "kelly_pct": 0.0,
                "vol_scale": 1.0,
                "loss_damping": 1.0,
                "regime_adj": 1.0,
                "reasoning": reasoning,
            }

        # === 1. KELLY ORANI ===
        kelly_pct = self._calculate_kelly()

        # === 2. ATR VOLATİLİTE ÖLÇEKLEMESİ ===
        vol_scale = self._calculate_vol_scale(atr, price)

        # === 3. KAYIP SERİSİ DAMPİNGİ ===
        loss_damping = self._calculate_loss_damping(consecutive_losses)

        # === 4. REJİM AYARI ===
        regime_adj = self._calculate_regime_adjustment(side, market_regime)

        # === 5. FİNAL HESAPLAMA ===
        base_pct = kelly_pct * vol_scale * loss_damping * regime_adj * sector_weight

        # Limitleri uygula
        base_pct = max(base_pct, self.MIN_POSITION_PCT)
        base_pct = min(base_pct, self.MAX_POSITION_PCT)

        # Config limitleri de uygula
        if side == "LONG":
            max_pos_usd = config.get("max_position_usd", 500)
            max_pos_pct = config.get("max_position_pct", 0.15)
        else:
            max_pos_usd = config.get("short_max_position_usd", 150)
            max_pos_pct = config.get("short_max_position_pct", 0.20)

        position_usd = min(
            equity * base_pct,
            equity * max_pos_pct,
            max_pos_usd,
        )

        # Minimum işlem kontrolü
        min_trade = config.get("min_trade_value", 10)
        if position_usd < min_trade:
            return self._empty_result(f"Pozisyon ${position_usd:.2f} < min ${min_trade}")

        qty = round(position_usd / price, 4)

        # Reasoning
        parts = []
        if kelly_pct != self.MAX_POSITION_PCT:
            parts.append(f"Kelly:{kelly_pct:.1%}")
        parts.append(f"Vol:{vol_scale:.2f}x")
        if loss_damping < 1.0:
            parts.append(f"Loss:-{(1-loss_damping):.0%}")
        if regime_adj != 1.0:
            parts.append(f"Rejim:{regime_adj:.2f}x")
        if sector_weight != 1.0:
            parts.append(f"Sektör:{sector_weight:.2f}x")

        reasoning = " | ".join(parts)

        logger.info(
            f"  📐 PositionSizer [{side}]: ${position_usd:.2f} "
            f"({base_pct:.1%} of ${equity:,.0f}) | {reasoning}"
        )

        return {
            "position_usd": round(position_usd, 2),
            "qty": qty,
            "kelly_pct": round(kelly_pct, 4),
            "vol_scale": round(vol_scale, 4),
            "loss_damping": round(loss_damping, 4),
            "regime_adj": round(regime_adj, 4),
            "reasoning": reasoning,
        }

    def _calculate_kelly(self) -> float:
        """Fractional Kelly hesapla — performans verilerinden."""
        if self.performance is None:
            return 0.20  # Veri yoksa %20 başla (önceki: %10)

        stats = self.performance.get_stats(days=30)  # Son 30 gün
        total_trades = stats.get("total_trades", 0)

        if total_trades < 5:
            # Yeterli veri yok — konsarvatif başla
            return 0.15  # Yeterli veri yok — %15 başla (önceki: %8)

        win_rate = stats.get("win_rate", 50) / 100  # 0-1 arası
        avg_win = stats.get("avg_win", 0)
        avg_loss = stats.get("avg_loss", 0)

        if avg_loss <= 0 or win_rate <= 0:
            return 0.08

        # Kelly formülü: f* = (bp - q) / b
        # b = avg_win / avg_loss (kazanç/kayıp oranı)
        # p = win_rate, q = 1 - p
        b = avg_win / avg_loss
        q = 1 - win_rate
        kelly_full = (b * win_rate - q) / b

        if kelly_full <= 0:
            # Kelly negatif = strateji zararda → minimum pozisyon
            logger.warning(
                f"  Kelly NEGATİF ({kelly_full:.2%}) — Strateji optimizasyona ihtiyac duyor! "
                f"Win:{win_rate:.0%} AvgW:${avg_win:.2f} AvgL:${avg_loss:.2f}"
            )
            return self.MIN_POSITION_PCT

        # Fractional Kelly (agresifliği azalt)
        kelly_adj = kelly_full * self.KELLY_FRACTION

        # Cap
        kelly_adj = min(kelly_adj, self.MAX_POSITION_PCT)
        kelly_adj = max(kelly_adj, self.MIN_POSITION_PCT)

        return kelly_adj

    def _calculate_vol_scale(self, atr: float, price: float) -> float:
        """ATR bazlı volatilite ölçekleme — yüksek volatilite = küçük pozisyon."""
        if atr <= 0 or price <= 0:
            return 1.0

        atr_pct = (atr / price) * 100  # ATR yüzdesi

        if atr_pct <= self.ATR_BASE_PCT:
            # Düşük volatilite — normal veya biraz büyük
            return min(1.2, 1.0 + (self.ATR_BASE_PCT - atr_pct) * 0.05)
        else:
            # Yüksek volatilite — küçült
            excess = atr_pct - self.ATR_BASE_PCT
            scale = 1.0 - (excess * self.ATR_SCALE_FACTOR)
            return max(0.3, scale)  # Min %30'a kadar küçült

    def _calculate_loss_damping(self, consecutive_losses: int) -> float:
        """Ardışık kayıp dampingi — her kayıpta pozisyon küçülür."""
        if consecutive_losses <= 0:
            return 1.0

        damping = 1.0 - (consecutive_losses * self.LOSS_DAMPING_FACTOR)
        return max(1.0 - self.MAX_LOSS_DAMPING, damping)

    def _calculate_regime_adjustment(self, side: str, market_regime: str) -> float:
        """Piyasa rejimi bazlı boyut ayarı."""
        if market_regime == "BEAR":
            if side == "LONG":
                return 0.70  # Bear modda long %30 küçült
            else:
                return 1.20  # Bear modda short %20 büyüt
        elif market_regime == "BULL":
            if side == "LONG":
                return 1.10  # Bull modda long %10 büyüt
            else:
                return 0.80  # Bull modda short %20 küçült
        return 1.0

    def _empty_result(self, reason: str) -> Dict:
        return {
            "position_usd": 0,
            "qty": 0,
            "kelly_pct": 0,
            "vol_scale": 0,
            "loss_damping": 0,
            "regime_adj": 0,
            "reasoning": reason,
        }
"""Position Sizer modülü sonu."""
