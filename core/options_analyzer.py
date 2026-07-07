"""
Options Analyzer — Opsiyon Kontrat Seçimi ve Greeks Analizi.

Görevler:
  1. Alpaca API'den opsiyon kontratlarını çek
  2. Greeks (Delta, Theta, Gamma) bazlı filtrele
  3. Likidite kontrolü (Open Interest, Bid-Ask Spread)
  4. En optimal kontratı seç ve döndür
"""
import logging
from datetime import datetime, timedelta, date
from typing import Dict, List, Optional

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOptionContractsRequest
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.requests import OptionSnapshotRequest

from utils.logger import logger


class OptionsAnalyzer:
    """Opsiyon kontrat seçimi ve Greeks analizi."""

    def __init__(self, trading_client: TradingClient, api_key: str, secret_key: str):
        self.trading_client = trading_client
        # Options data client (snapshot / Greeks için)
        try:
            self.data_client = OptionHistoricalDataClient(
                api_key=api_key, secret_key=secret_key
            )
        except Exception as e:
            logger.debug(f"  OptionHistoricalDataClient init hatası: {e}")
            self.data_client = None

        # Contract cache (API çağrısını azalt)
        self._contract_cache = {}
        self._cache_time = {}
        self._cache_ttl = 300  # 5 dakika

    def find_optimal_contract(
        self,
        symbol: str,
        direction: str,  # "CALL" veya "PUT"
        confidence: float,
        config: Dict,
    ) -> Optional[Dict]:
        """En uygun opsiyon kontratını bul.

        Args:
            symbol: Hisse sembolü (AAPL, MSFT vs.)
            direction: "CALL" veya "PUT"
            confidence: Sinyal güven puanı (0-100)
            config: OPTIONS_CONFIG

        Returns:
            Optimal kontrat bilgisi veya None
        """
        try:
            # Kara liste kontrolü
            if symbol in config.get("options_blacklist", []):
                return None

            # Kontratları çek
            contracts = self._fetch_contracts(symbol, direction, config)
            if not contracts:
                return None

            # Filtrele ve skorla
            scored = []
            for contract in contracts:
                score = self._score_contract(contract, config, confidence)
                if score is not None:
                    scored.append((score, contract))

            if not scored:
                return None

            # En yüksek skorlu kontratı seç
            scored.sort(key=lambda x: x[0], reverse=True)

            # Top 5 kontratı Greeks ile yeniden skorla
            best_contract = None
            best_score = -1
            for score, contract in scored[:5]:
                greeks_bonus = self._get_greeks_bonus(contract, config)
                final_score = score + greeks_bonus
                if final_score > best_score:
                    best_score = final_score
                    best_contract = contract

            if best_contract is None:
                return None

            return {
                "contract": best_contract,
                "symbol": best_contract.symbol,
                "underlying": symbol,
                "type": direction,
                "strike": float(best_contract.strike_price),
                "expiry": str(best_contract.expiration_date),
                "score": best_score,
                "open_interest": best_contract.open_interest,
            }

        except Exception as e:
            logger.debug(f"  {symbol} opsiyon kontrat arama hatası: {e}")
            return None

    def _fetch_contracts(
        self, symbol: str, direction: str, config: Dict
    ) -> List:
        """Alpaca'dan opsiyon kontratlarını çek (cache'li)."""
        cache_key = f"{symbol}_{direction}"
        now = datetime.now()

        # Cache kontrolü
        if (
            cache_key in self._contract_cache
            and (now - self._cache_time.get(cache_key, datetime.min)).total_seconds()
            < self._cache_ttl
        ):
            return self._contract_cache[cache_key]

        try:
            # Vade aralığı
            min_expiry = date.today() + timedelta(
                days=config.get("options_min_expiry_days", 5)
            )
            max_expiry = date.today() + timedelta(
                days=config.get("options_max_expiry_days", 21)
            )

            request = GetOptionContractsRequest(
                underlying_symbols=[symbol],
                expiration_date_gte=min_expiry.isoformat(),
                expiration_date_lte=max_expiry.isoformat(),
                type=direction.lower(),  # "call" veya "put"
                status="active",
            )

            result = self.trading_client.get_option_contracts(request)
            contracts = result.option_contracts if result else []

            # Cache'e kaydet
            self._contract_cache[cache_key] = contracts
            self._cache_time[cache_key] = now

            return contracts

        except Exception as e:
            logger.debug(f"  {symbol} kontrat çekme hatası: {e}")
            return []

    def _score_contract(
        self, contract, config: Dict, confidence: float
    ) -> Optional[float]:
        """Kontratı skorla. None dönerse kontrat reddedilir."""
        score = 0.0

        try:
            strike = float(contract.strike_price)
            close_price = float(contract.close_price) if contract.close_price else None
            oi = int(contract.open_interest) if contract.open_interest else 0
            expiry = contract.expiration_date

            # Open Interest filtresi
            min_oi = config.get("options_min_open_interest", 50)
            if oi < min_oi:
                return None

            # Open Interest skoru (daha fazla = daha iyi)
            if oi > 1000:
                score += 20
            elif oi > 500:
                score += 15
            elif oi > 100:
                score += 10
            else:
                score += 5

            # Vade skoru — ideal vadeye yakınlık
            if isinstance(expiry, str):
                expiry_date = datetime.strptime(expiry, "%Y-%m-%d").date()
            else:
                expiry_date = expiry
            days_to_expiry = (expiry_date - date.today()).days
            ideal_days = config.get("options_preferred_expiry_days", 10)
            day_diff = abs(days_to_expiry - ideal_days)
            score += max(0, 15 - day_diff * 2)

            # Fiyat skoru — çok pahalı veya çok ucuz olmasın
            if close_price is not None:
                if 1.0 <= close_price <= 15.0:
                    score += 15  # İdeal aralık ($100-$1500 yatırım)
                elif 0.5 <= close_price < 1.0:
                    score += 10
                elif 15.0 < close_price <= 30.0:
                    score += 8
                else:
                    score += 3

            # Tercih edilen sembol bonusu
            if contract.underlying_symbol in config.get(
                "options_preferred_symbols", []
            ):
                score += 10

            # Güven bonusu (güçlü sinyal = daha yüksek skor)
            score += confidence * 0.2

            return score

        except Exception as e:
            logger.debug(f"  Kontrat skorlama hatası: {e}")
            return None

    def get_contract_snapshot(self, contract_symbol: str) -> Optional[Dict]:
        """Kontrat snapshot'ı al (Greeks dahil)."""
        if not self.data_client:
            return None

        try:
            # Snapshot API (delta, gamma, theta, vega).
            # v4.9 FIX: alpaca-py Request OBJESİ bekler; düz str geçilince
            # "'str' object has no attribute 'to_request_fields'" ile HER çağrı
            # sessizce ölüyordu → fiyatlar bayat close_price'a düşüyordu (06 Tem
            # churn'ünün kök halkası; v4.7.1'deki get_stock_snapshot fix'inin ikizi).
            # Dönüş Dict[str, OptionsSnapshot] — sembol anahtarıyla çekilir.
            req = OptionSnapshotRequest(symbol_or_symbols=contract_symbol)
            snaps = self.data_client.get_option_snapshot(req)
            snapshot = snaps.get(contract_symbol) if isinstance(snaps, dict) else snaps
            if snapshot:
                return {
                    "symbol": contract_symbol,
                    "latest_trade_price": (
                        float(snapshot.latest_trade.price)
                        if snapshot.latest_trade
                        else None
                    ),
                    "bid": (
                        float(snapshot.latest_quote.bid_price)
                        if snapshot.latest_quote
                        else None
                    ),
                    "ask": (
                        float(snapshot.latest_quote.ask_price)
                        if snapshot.latest_quote
                        else None
                    ),
                    "greeks": snapshot.greeks if hasattr(snapshot, "greeks") else None,
                    "implied_volatility": (
                        float(snapshot.implied_volatility)
                        if hasattr(snapshot, "implied_volatility")
                        else None
                    ),
                }
        except Exception as e:
            logger.debug(f"  {contract_symbol} snapshot hatası: {e}")

        return None

    def _get_greeks_bonus(self, contract, config: Dict) -> float:
        """Greeks bazlı bonus skor hesapla (snapshot API ile).
        
        Delta filtresi:
          - CALL: 0.25-0.65 arası ideal (ATM civarı)
          - PUT: -0.65 ile -0.25 arası ideal
          - Çok düşük delta = çok OTM (ucuz ama düşük kazanç ihtimali)
          - Çok yüksek delta = çok ITM (pahalı, az kaldıraç)
        """
        try:
            if not self.data_client:
                return 0.0

            snapshot = self.get_contract_snapshot(contract.symbol)
            if not snapshot:
                return 0.0

            bonus = 0.0
            greeks = snapshot.get("greeks")

            if greeks and hasattr(greeks, "delta") and greeks.delta is not None:
                delta = abs(float(greeks.delta))
                min_delta = config.get("options_min_delta", 0.25)
                max_delta = config.get("options_max_delta", 0.65)
                preferred_delta = config.get("options_preferred_delta", 0.40)

                # Delta aralık kontrolü
                if delta < min_delta or delta > max_delta:
                    return -50  # Aralık dışı → büyük ceza
                
                # İdeal delta'ya yakınlık bonusu
                delta_diff = abs(delta - preferred_delta)
                bonus += max(0, 20 - delta_diff * 60)

            # Theta cezası (günlük değer kaybı)
            if greeks and hasattr(greeks, "theta") and greeks.theta is not None:
                theta = float(greeks.theta)
                if theta < -0.15:
                    bonus -= 10  # Çok fazla theta kaybı
                elif theta < -0.05:
                    bonus -= 5

            # IV skoru
            iv = snapshot.get("implied_volatility")
            if iv is not None:
                iv = float(iv)
                if 0.3 <= iv <= 0.8:
                    bonus += 5  # Makul IV
                elif iv > 1.5:
                    bonus -= 10  # Aşırı pahalı

            # Bid-Ask Spread kontrolü (likidite)
            bid = snapshot.get("bid")
            ask = snapshot.get("ask")
            if bid and ask and bid > 0:
                spread_pct = (ask - bid) / bid
                max_spread = config.get("options_max_spread_pct", 0.15)
                if spread_pct > max_spread:
                    return -50  # Çok geniş spread → reddet
                elif spread_pct < 0.05:
                    bonus += 10  # Dar spread = iyi likidite

            return bonus

        except Exception as e:
            logger.debug(f"  Greeks bonus hatası: {e}")
            return 0.0

    def estimate_max_loss(self, contract_price: float, qty: int = 1) -> float:
        """Opsiyon alımında max kayıp = premium × 100 × adet."""
        return contract_price * 100 * qty

    def estimate_breakeven(
        self, strike: float, premium: float, direction: str
    ) -> float:
        """Break-even noktası hesapla."""
        if direction.upper() == "CALL":
            return strike + premium
        else:  # PUT
            return strike - premium
