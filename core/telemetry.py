"""Kalici, append-only bot telemetrisi.

Bu yazici bilerek kucuk tutulur. Rock 1 yalnizca partial karar/intent olaylarini
uretir; sonraki raporlama rock'i ayni JSONL akisini okuyacaktir.
"""
from __future__ import annotations

from datetime import datetime
import json
from typing import Any

from utils.logger import logger


def append_telemetry(kind: str, **fields: Any) -> bool:
    """Bir olayi aktif live/paper state dizinine kalici olarak ekle."""
    record = {
        "ts": datetime.now().isoformat(),
        "kind": str(kind),
        **fields,
    }
    try:
        # Testlerin ve runtime mode seciminin state_path monkeypatch'ini gormesi
        # icin import cagri aninda yapilir.
        from config import state_path

        with open(state_path("telemetry.jsonl"), "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            handle.flush()
        return True
    except Exception as exc:
        logger.error(f"  Telemetri yazilamadi ({kind}): {exc}")
        return False
