"""Broker ACK oncesi kalici emir provenance journal'i."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import threading
from typing import Any

from core.fill_ledger import PROVENANCES, _file_lock


SCHEMA_VERSION = 1
_PROCESS_LOCK = threading.RLock()


def _journal_path() -> str:
    from config import state_path

    return state_path("order_journal.json")


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty() -> dict:
    return {"schema_version": SCHEMA_VERSION, "orders": {}}


def _load(path: str) -> dict:
    if not os.path.exists(path):
        return _empty()
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or not isinstance(data.get("orders"), dict):
        raise ValueError("order journal semasi bozuk")
    return data


def _save(path: str, data: dict) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    temp_path = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass


def prepare(
    client_order_id: str,
    symbol: str,
    side: str,
    provenance: str,
    qty: float | None = None,
    notional: float | None = None,
) -> dict:
    """Emir provenance'ini broker cagrisindan once kalici PREPARED yaz."""
    cid = str(client_order_id or "").strip()
    if not cid:
        raise ValueError("client_order_id zorunlu")
    normalized_side = str(side).upper()
    if normalized_side not in {"BUY", "SELL"}:
        raise ValueError(f"gecersiz journal side: {side}")
    normalized_provenance = str(provenance)
    if normalized_provenance not in PROVENANCES:
        raise ValueError(f"gecersiz journal provenance: {provenance}")

    path = _journal_path()
    with _PROCESS_LOCK, _file_lock(path):
        data = _load(path)
        existing = data["orders"].get(cid)
        identity = {
            "symbol": str(symbol).upper(),
            "side": normalized_side,
            "provenance": normalized_provenance,
        }
        if existing is not None:
            if any(existing.get(key) != value for key, value in identity.items()):
                raise ValueError(f"client_order_id farkli emirle kullanildi: {cid}")
            return dict(existing)
        record = {
            "client_order_id": cid,
            **identity,
            "qty": None if qty is None else float(qty),
            "notional": None if notional is None else float(notional),
            "status": "PREPARED",
            "order_id": None,
            "prepared_ts_utc": _now_utc(),
            "bound_ts_utc": None,
        }
        data["schema_version"] = SCHEMA_VERSION
        data["orders"][cid] = record
        _save(path, data)
        return dict(record)


def bind(client_order_id: str, order_id: str) -> dict:
    """Broker ACK order_id'sini daha once hazirlanan cid'ye bagla."""
    cid = str(client_order_id or "").strip()
    oid = str(order_id or "").strip()
    if not cid or not oid:
        raise ValueError("client_order_id ve order_id zorunlu")
    path = _journal_path()
    with _PROCESS_LOCK, _file_lock(path):
        data = _load(path)
        record = data["orders"].get(cid)
        if record is None:
            raise KeyError(f"PREPARED kaydi yok: {cid}")
        old_order_id = str(record.get("order_id") or "")
        if old_order_id and old_order_id != oid:
            raise ValueError(f"client_order_id baska order_id'ye bagli: {cid}")
        for other_cid, other in data["orders"].items():
            if other_cid != cid and str(other.get("order_id") or "") == oid:
                raise ValueError(f"order_id baska client_order_id'ye bagli: {oid}")
        record["order_id"] = oid
        record["status"] = "BOUND"
        record["bound_ts_utc"] = record.get("bound_ts_utc") or _now_utc()
        _save(path, data)
        return dict(record)


def resolve(order_id: str) -> str:
    """Kesin journal kaniti yoksa provenance tahmin etmeden UNKNOWN dondur."""
    oid = str(order_id or "").strip()
    if not oid:
        return "UNKNOWN"
    path = _journal_path()
    if not os.path.exists(path):
        return "UNKNOWN"
    # os.replace atomik oldugu icin salt-okuma ayri lock dosyasi yaratmaz.
    with _PROCESS_LOCK:
        data = _load(path)
        for record in data["orders"].values():
            if str(record.get("order_id") or "") == oid:
                provenance = str(record.get("provenance") or "UNKNOWN")
                return provenance if provenance in PROVENANCES else "UNKNOWN"
    return "UNKNOWN"


def stale_prepared() -> list[dict[str, Any]]:
    """Restart uzlastirmasi icin ACK'e baglanmamis PREPARED kayitlari getir."""
    path = _journal_path()
    if not os.path.exists(path):
        return []
    with _PROCESS_LOCK:
        data = _load(path)
        return [
            dict(record)
            for record in data["orders"].values()
            if record.get("status") == "PREPARED" and not record.get("order_id")
        ]
