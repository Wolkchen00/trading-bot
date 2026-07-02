"""
Agent Performance Tracker — Ajan Öz-Değerlendirme & Dinamik Ağırlık Sistemi

Her ajan, geçmiş kararlarının doğruluğunu takip eder ve coordinator'daki
ağırlığını otomatik günceller:
  - Doğru tahmin eden ajanın ağırlığı artar
  - Sürekli yanlış yapanın ağırlığı azalır
  - Minimum ağırlık (0.08) ile hiçbir ajan tamamen devre dışı kalmaz
  - Son 30 güne bakılır (yakın geçmiş daha önemli)

JSON dosyaya kaydedilir (restart-safe).
"""
import json
import math
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from utils.logger import logger


class AgentPerformanceTracker:
    """Her ajanın tahmin doğruluğunu takip eder ve dinamik ağırlıklar hesaplar."""

    HISTORY_FILE = "agent_performance.json"
    LOOKBACK_DAYS = 30         # Son 30 gün
    MIN_TRADES_FOR_EVAL = 5    # Değerlendirme için minimum işlem
    MIN_WEIGHT = 0.08          # Minimum ajan ağırlığı (%8)
    MAX_WEIGHT = 0.35          # Maximum ajan ağırlığı (%35)

    # Varsayılan ağırlıklar (yeterli veri yoksa)
    DEFAULT_WEIGHTS = {
        "TechAgent": 0.25,
        "FundAgent": 0.20,
        "SentAgent": 0.20,
        "SocialAgent": 0.15,
        "RiskAgent": 0.20,
    }

    def __init__(self, history_file: str = None):
        if history_file is None:
            try:
                from config import state_path
                history_file = state_path("agent_performance.json")
            except Exception:
                history_file = self.HISTORY_FILE
        self.HISTORY_FILE = history_file  # instance, live/paper izole
        self.predictions: Dict[str, List[Dict]] = self._load()
        total_preds = sum(len(v) for v in self.predictions.values())
        logger.info(
            f"AgentPerformanceTracker başlatıldı — "
            f"{total_preds} geçmiş tahmin, "
            f"{len(self.predictions)} ajan takip ediliyor"
        )

    def _load(self) -> Dict[str, List[Dict]]:
        if os.path.exists(self.HISTORY_FILE):
            try:
                with open(self.HISTORY_FILE, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save(self):
        try:
            with open(self.HISTORY_FILE, "w") as f:
                json.dump(self.predictions, f, indent=2, default=str)
        except Exception as e:
            logger.debug(f"Agent performance kayıt hatası: {e}")

    def record_prediction(self, symbol: str, agent_votes: List[Dict],
                           coordinator_signal: str):
        """
        İşlem anında her ajanın tahminini kaydet.
        Sonuç henüz bilinmiyor — record_outcome ile sonra güncellenecek.

        Args:
            symbol: Hisse sembolü
            agent_votes: Coordinator'dan gelen oy listesi
            coordinator_signal: Final sinyal (BUY/SELL/SHORT)
        """
        timestamp = datetime.now().isoformat()

        for vote in agent_votes:
            agent_name = vote.get("agent", "Unknown")

            if agent_name not in self.predictions:
                self.predictions[agent_name] = []

            self.predictions[agent_name].append({
                "symbol": symbol,
                "predicted_signal": vote.get("signal", "HOLD"),
                "confidence": vote.get("confidence", 0),
                "coordinator_signal": coordinator_signal,
                "actual_outcome": None,  # Henüz bilinmiyor
                "timestamp": timestamp,
                "correct": None,         # Henüz bilinmiyor
            })

        self._save()

    def record_outcome(self, symbol: str, outcome: str, pnl: float = 0):
        """
        İşlem sonucunu kaydet ve her ajanın YÖN-FARKINDA doğruluğunu güncelle.

        Kredi-atama (v2 — eski bug düzeltmesi):
          - İşlemin YÖNÜ önemli: long girişi BUY oyuyla, short girişi SELL oyuyla
            "onaylanmış" sayılır (coordinator_signal'dan okunur). Eski kod yönü
            yok sayıyordu → kazanan bir SHORT'ta BUY diyen ajan "doğru" sayılıyordu.
          - Onaylayan oy → işlem kazandıysa doğru, kaybettiyse yanlış.
          - Karşı çıkan oy → tam tersi.
          - HOLD ("fikrim yok") ve NEUTRAL sonuç → doğruluğa SAYILMAZ (correct=None).
            Eski bug: HOLD her zararda "doğru" sayılıp Risk/temkinli ajanları şişiriyordu
            (label leakage). Artık HOLD oyları accuracy'den tamamen düşer.

        Args:
            symbol: Hisse sembolü
            outcome: "WIN" (kar), "LOSS" (zarar), "NEUTRAL"
            pnl: Gerçek kar/zarar ($) — magnitude ağırlığı için saklanır
        """
        trade_won = outcome == "WIN"
        directional = outcome in ("WIN", "LOSS")  # NEUTRAL/scratch → sayma

        for agent_name, preds in self.predictions.items():
            for pred in reversed(preds):
                if pred["symbol"] == symbol and pred["actual_outcome"] is None:
                    predicted = pred.get("predicted_signal", "HOLD")
                    # İşlemin yönü: long → BUY ile onaylanır, short/sell → SELL ile
                    side = pred.get("coordinator_signal", "BUY")
                    endorse = "BUY" if side in ("BUY", "LONG") else "SELL"

                    if not directional or predicted == "HOLD":
                        pred["correct"] = None             # nötr — accuracy'den düşer
                    elif predicted == endorse:
                        pred["correct"] = trade_won         # yönü onayladı
                    else:
                        pred["correct"] = not trade_won     # yöne karşı çıktı

                    pred["actual_outcome"] = outcome
                    pred["pnl"] = pnl
                    pred["cred_v"] = 2                      # yeni şema işareti
                    break  # Bu ajan için sadece en son kaydı güncelle

        self._save()

    def get_dynamic_weights(self) -> Dict[str, float]:
        """
        Son 30 gün doğruluk oranına göre dinamik ağırlıklar hesapla.

        Returns:
            {
                "TechAgent": 0.28,
                "FundAgent": 0.18,
                ...
            }
        """
        cutoff = (datetime.now() - timedelta(days=self.LOOKBACK_DAYS)).isoformat()

        raw_weights = {}

        for agent_name in self.DEFAULT_WEIGHTS:
            preds = self.predictions.get(agent_name, [])

            # Son 30 gün + outcome'u belli olanlar
            recent = [
                p for p in preds
                if p.get("timestamp", "") >= cutoff
                and p.get("correct") is not None
            ]

            if len(recent) < self.MIN_TRADES_FOR_EVAL:
                # Yeterli YÖN bilgisi yok (HOLD'lar sayılmaz) — varsayılan ağırlık
                raw_weights[agent_name] = self.DEFAULT_WEIGHTS[agent_name]
                continue

            # PnL-magnitude ağırlıklı doğruluk: büyük hamlede haklı olmak daha değerli.
            # Ağırlık = 1 + log1p(|pnl|) → sıfır/küçük pnl bile en az 1 sayılır (count'a
            # düşer), tek dev işlem domine edemez (logaritmik bastırma).
            num = 0.0
            den = 0.0
            for p in recent:
                w = 1.0 + math.log1p(abs(float(p.get("pnl", 0) or 0)))
                den += w
                if p["correct"]:
                    num += w
            accuracy = (num / den) if den > 0 else 0.5

            # Ağırlığı doğruluk oranına göre ayarla
            # Accuracy 0.5 (yarı yarıya) = varsayılan ağırlık
            # Accuracy 0.7+ = ağırlık artışı
            # Accuracy 0.3- = ağırlık azalması
            default_w = self.DEFAULT_WEIGHTS[agent_name]

            if accuracy >= 0.6:
                # Doğru tahmin ediyor → ağırlık artır
                boost = (accuracy - 0.5) * 0.5  # Max +0.25 boost
                weight = default_w + boost
            elif accuracy < 0.4:
                # Yanlış tahmin ediyor → ağırlık azalt
                penalty = (0.5 - accuracy) * 0.4  # Max -0.20 penalty
                weight = default_w - penalty
            else:
                weight = default_w

            # Limitleri uygula
            weight = max(self.MIN_WEIGHT, weight)
            weight = min(self.MAX_WEIGHT, weight)

            raw_weights[agent_name] = weight

        # Normalize (toplam = 1.0)
        total = sum(raw_weights.values())
        if total > 0:
            normalized = {k: v / total for k, v in raw_weights.items()}
        else:
            normalized = dict(self.DEFAULT_WEIGHTS)

        return normalized

    def get_agent_stats(self) -> Dict:
        """Her ajan için performans istatistikleri döndür."""
        cutoff = (datetime.now() - timedelta(days=self.LOOKBACK_DAYS)).isoformat()
        stats = {}

        for agent_name in self.DEFAULT_WEIGHTS:
            preds = self.predictions.get(agent_name, [])
            recent = [
                p for p in preds
                if p.get("timestamp", "") >= cutoff
                and p.get("correct") is not None
            ]

            if not recent:
                stats[agent_name] = {
                    "total": 0, "correct": 0, "accuracy": 0,
                    "status": "VERİ YOK"
                }
                continue

            correct = sum(1 for p in recent if p["correct"])
            accuracy = correct / len(recent) * 100

            if accuracy >= 60:
                status = "✅ GÜÇLÜ"
            elif accuracy >= 45:
                status = "⚖️ ORTA"
            else:
                status = "⚠️ ZAYIF"

            stats[agent_name] = {
                "total": len(recent),
                "correct": correct,
                "accuracy": round(accuracy, 1),
                "status": status,
            }

        return stats

    def cleanup_old(self, days: int = 90):
        """90 günden eski kayıtları temizle."""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        cleaned = 0

        for agent_name in list(self.predictions.keys()):
            before = len(self.predictions[agent_name])
            self.predictions[agent_name] = [
                p for p in self.predictions[agent_name]
                if p.get("timestamp", "") >= cutoff
            ]
            cleaned += before - len(self.predictions[agent_name])

        if cleaned > 0:
            self._save()
            logger.info(f"AgentPerformance: {cleaned} eski kayıt temizlendi")
