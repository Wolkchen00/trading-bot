"""R14 canli/backtest karar yollarinin ortak, serilestirilebilir izi.

Bu modul karar VERMEZ. Iki ayri uygulamanin urettigi sonucu ayni duz semaya
tasir; ozellikle bir kapinin yoklugunu basari gibi gostermemek icin
``passed=None`` ve ``reason="kapi_yok"`` sozlesmesini merkezilestirir.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any, Optional


FINAL_ACTIONS = frozenset({"BUY", "SELL", "HOLD", "BLOCKED"})
PATHS = frozenset({"live", "backtest"})


@dataclass(frozen=True)
class GateTrace:
    """Tek bir kapi/asamanin karar anindaki sonucu."""

    name: str
    passed: Optional[bool]
    reason: str = ""

    @classmethod
    def missing(cls, name: str) -> "GateTrace":
        """Bu yolda hic bulunmayan kapi icin kanonik kayit."""
        return cls(name=name, passed=None, reason="kapi_yok")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class DecisionTrace:
    """Tek sembol ve tek bar icin kanonik karar izi."""

    symbol: str
    as_of: str
    bar_count: int
    tech_signal: Optional[str]
    tech_confidence: Optional[float]
    agent_votes: Optional[dict[str, dict[str, Any]]]
    weighted_score: Optional[float]
    coordinator_confidence: Optional[float]
    coordinator_signal: Optional[str]
    gates: tuple[GateTrace, ...]
    final_action: str
    path: str
    notes: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.final_action not in FINAL_ACTIONS:
            raise ValueError(f"Gecersiz final_action: {self.final_action}")
        if self.path not in PATHS:
            raise ValueError(f"Gecersiz path: {self.path}")
        if self.bar_count < 0:
            raise ValueError("bar_count negatif olamaz")
        for gate in self.gates:
            if gate.reason == "kapi_yok" and gate.passed is not None:
                raise ValueError(
                    f"{gate.name}: kapi_yok sonucu yalniz passed=None olabilir"
                )

    def to_dict(self) -> dict[str, Any]:
        """Alan sirasini sabitleyen JSON-uyumlu temsil."""
        return {
            "symbol": self.symbol,
            "as_of": self.as_of,
            "bar_count": self.bar_count,
            "tech_signal": self.tech_signal,
            "tech_confidence": self.tech_confidence,
            "agent_votes": self.agent_votes,
            "weighted_score": self.weighted_score,
            "coordinator_confidence": self.coordinator_confidence,
            "coordinator_signal": self.coordinator_signal,
            "gates": [gate.to_dict() for gate in self.gates],
            "final_action": self.final_action,
            "path": self.path,
            "notes": list(self.notes),
        }

    def to_json(self) -> str:
        """Bit-bit tekrar testi icin kanonik, tek satir JSON."""
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=False,
            separators=(",", ":"),
            allow_nan=False,
        )
