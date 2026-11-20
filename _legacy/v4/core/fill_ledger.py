"""Kalici, append-only broker dolum defteri.

Her JSONL satiri tek bir dolumu temsil eder. Bu modul muhasebe verisini yazar;
emir/koruma akisini yonetmez. Cagiranlar defter hatalarini yakalayip alarm
vermekten sorumludur, cunku defter arizasi bir cikisi asla engelleyemez.
"""
# NOT (olcekleme borcu): Yazimlar dedupe icin tum defteri O(n) okuyor ve dosya
# sinirsiz buyuyor; rotasyon ile kalici bir dedupe indeksi sonraki rock'a aittir.
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import os
import threading
from typing import Any, Iterator


SCHEMA_VERSION = 1
PROVENANCES = {
    "strategy",
    "index_parking",
    "dca",
    "option",
    "short",
    "bear_etf",
    "UNKNOWN",
}

_PROCESS_LOCK = threading.RLock()


def _canonical_decimal(value: Any) -> str:
    """Sayisal fill alanini R10 ile ayni kayipsiz metne kanonlastir."""
    try:
        number = abs(Decimal(str(value or 0)))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"gecersiz fill sayisi: {value}") from exc
    if number <= 0:
        raise ValueError(f"fill sayisi pozitif olmali: {value}")
    return str(number.normalize())


def canonical_fill_key(
    symbol: str, side: str, qty: Any, price: Any,
) -> tuple[str, str, str, str]:
    """Execution kimliginden bagimsiz, tek dolumun kanonik icerik anahtari."""
    return (
        str(symbol or "").strip().upper(),
        str(side or "").strip().upper(),
        _canonical_decimal(qty),
        _canonical_decimal(price),
    )


def order_fill_key(
    order_id: str, symbol: str, side: str, qty: Any,
) -> tuple[str, str, str, str]:
    """R10 mutabakatinin kullandigi emir-seviyesi kanonik anahtar."""
    return (
        str(order_id or "").strip(),
        str(symbol or "").strip().upper(),
        str(side or "").strip().upper(),
        _canonical_decimal(qty),
    )


def _ledger_path() -> str:
    # Runtime mode ve test state_path monkeypatch'leri cagri aninda gorulsun.
    from config import state_path

    return state_path("fill_ledger.jsonl")


def _utc_iso(value: Any = None) -> str:
    if value is None:
        parsed = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value).strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        # Eski pozisyon entry_time degerleri yerel saatle naive yazildi.
        parsed = parsed.astimezone()
    return parsed.astimezone(timezone.utc).isoformat()


def _as_utc_datetime(value: Any) -> datetime:
    return datetime.fromisoformat(_utc_iso(value))


@contextmanager
def _file_lock(path: str) -> Iterator[None]:
    """Ayni state volume'unu kullanan surecler arasinda kisa yazim kilidi."""
    lock_path = f"{path}.lock"
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    handle = open(lock_path, "a+b")
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def _read_path(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    records: list[dict] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            if not raw.strip():
                continue
            try:
                item = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"fill ledger bozuk JSONL satiri {line_number}: {exc}"
                ) from exc
            if not isinstance(item, dict):
                raise ValueError(
                    f"fill ledger satiri {line_number} nesne degil"
                )
            records.append(item)
    return records


def record_fill(
    *,
    symbol: str,
    side: str,
    qty: float,
    price: float,
    pnl_usd: float | None = None,
    provenance: str = "UNKNOWN",
    execution_id: str | None = None,
    order_id: str | None = None,
    client_order_id: str | None = None,
    episode_id: str | None = None,
    ts_utc: Any = None,
    degraded: bool = False,
    source: str | None = None,
    reconcile_order_qty: Any = None,
) -> bool:
    """Dolumu tekillestirerek kalici yaz; normal replay'de True dondur.

    ``reconcile_order_qty`` yalniz mutabakat supurgesi icindir. Bu modda
    execution kimligi farkli olsa bile ayni emir dolumu defterde kanitliysa
    atomik kontrol ikinci satiri yazmaz ve False dondurur.
    """
    normalized_side = str(side).upper()
    if normalized_side not in {"BUY", "SELL"}:
        raise ValueError(f"gecersiz fill side: {side}")
    normalized_provenance = str(provenance)
    if normalized_provenance not in PROVENANCES:
        raise ValueError(f"gecersiz fill provenance: {provenance}")

    normalized_qty = abs(float(qty))
    normalized_price = float(price)
    if normalized_qty <= 0 or normalized_price <= 0:
        raise ValueError(
            f"fill qty/price pozitif olmali: qty={qty}, price={price}"
        )

    normalized_execution_id = str(execution_id or "").strip() or None
    normalized_order_id = str(order_id or "").strip() or None
    if normalized_execution_id:
        dedupe_key = normalized_execution_id
    else:
        dedupe_key = (
            f"{normalized_order_id}|{normalized_side}|"
            f"{normalized_qty}|{normalized_price}"
        )
        degraded = True

    record = {
        "schema_version": SCHEMA_VERSION,
        "ts_utc": _utc_iso(ts_utc),
        "symbol": str(symbol).upper(),
        "side": normalized_side,
        "qty": normalized_qty,
        "price": normalized_price,
        "pnl_usd": None if pnl_usd is None else float(pnl_usd),
        "provenance": normalized_provenance,
        "execution_id": normalized_execution_id,
        "order_id": normalized_order_id,
        "client_order_id": str(client_order_id or "").strip() or None,
        "episode_id": str(episode_id or "").strip() or None,
        "dedupe_key": dedupe_key,
        "degraded": bool(degraded),
    }
    normalized_source = str(source or "").strip() or None
    if normalized_source is not None:
        record["source"] = normalized_source

    path = _ledger_path()
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
    with _PROCESS_LOCK, _file_lock(path):
        existing = _read_path(path)
        if any(item.get("dedupe_key") == dedupe_key for item in existing):
            return False if reconcile_order_qty is not None else True
        if reconcile_order_qty is not None:
            target_order_key = order_fill_key(
                normalized_order_id,
                record["symbol"],
                normalized_side,
                reconcile_order_qty,
            )
            grouped_qty = Decimal("0")
            current_fill_key = canonical_fill_key(
                record["symbol"], normalized_side,
                normalized_qty, normalized_price,
            )
            for item in existing:
                if (
                    str(item.get("order_id") or "").strip()
                    != str(normalized_order_id or "")
                    or str(item.get("symbol") or "").strip().upper()
                    != record["symbol"]
                    or str(item.get("side") or "").strip().upper()
                    != normalized_side
                ):
                    continue
                try:
                    grouped_qty += abs(Decimal(str(item.get("qty") or 0)))
                except (InvalidOperation, TypeError, ValueError):
                    continue
                # Executor execution_id olmadan ayni fill'i yazmissa activity
                # kimligi farkli diye ikinci kez ekleme.
                if not str(item.get("execution_id") or "").strip():
                    try:
                        if canonical_fill_key(
                            item.get("symbol"), item.get("side"),
                            item.get("qty"), item.get("price"),
                        ) == current_fill_key:
                            return False
                    except ValueError:
                        continue
            if grouped_qty > 0 and order_fill_key(
                normalized_order_id,
                record["symbol"],
                normalized_side,
                grouped_qty,
            ) == target_order_key:
                return False
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
    return True


def read_fills(symbol: str | None = None, since: Any = None) -> list[dict]:
    """Defteri oku; istege bagli sembol ve UTC baslangic zamani uygula."""
    records = _read_path(_ledger_path())
    normalized_symbol = str(symbol).upper() if symbol else None
    cutoff = _as_utc_datetime(since) if since is not None else None
    result = []
    for item in records:
        if normalized_symbol and str(item.get("symbol", "")).upper() != normalized_symbol:
            continue
        if cutoff is not None:
            try:
                item_ts = _as_utc_datetime(item.get("ts_utc"))
            except Exception as exc:
                raise ValueError(
                    f"fill ledger gecersiz ts_utc: {item.get('ts_utc')}"
                ) from exc
            if item_ts < cutoff:
                continue
        result.append(item)
    return result


def episode_realized_pnl(symbol: str, entry_ts_iso: Any) -> float:
    """Acilistan sonraki strategy SELL bacaklarinin gerceklesen PnL toplami."""
    total = 0.0
    for fill in read_fills(symbol=symbol, since=entry_ts_iso):
        if fill.get("provenance") != "strategy" or fill.get("side") != "SELL":
            continue
        pnl = fill.get("pnl_usd")
        if pnl is not None:
            total += float(pnl)
    return total
