"""R10 ölçüm doğruluğu raporu.

Salt okunur: yalnız broker emirlerini/barlarını ve yerel state/log dosyalarını
okur; emir verme, değiştirme veya iptal etme metodu çağırmaz. Kanıt üretmez;
yanlış kanıtı dört durumlu sözleşmeyle reddeder.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Callable, Iterable
from zoneinfo import ZoneInfo

# Dosya ``py tools/olcum_raporu.py`` ile doğrudan çalıştırıldığında Python yalnız
# ``tools/`` dizinini import yoluna ekler. Proje modüllerini aynı CLI yüzeyinden
# güvenilir biçimde okuyabilmek için repo kökünü importlardan önce görünür yap.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from alpaca.common.enums import Sort
from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import QueryOrderStatus
from alpaca.trading.requests import (
    GetCalendarRequest, GetOrdersRequest, GetPortfolioHistoryRequest,
)

from config import PAPER_AGGRESSIVE_CONFIG, STOCK_CONFIG
from core import fill_ledger, order_journal


MEASUREMENT_START = "2026-07-30"
ET = ZoneInfo("America/New_York")
EXIT_ACTIONS = {"SELL", "COVER"}
# Yalniz immutable backfill kapsamasinin bitisini dogrular. Metrik-2'nin gercek
# telemetri erasi asagida dosyadaki ilk kaydin zamanindan turetilir.
TELEMETRY_START = datetime(2026, 8, 9, 18, 3, 27, tzinfo=timezone.utc)
BACKFILL_PATH = ROOT / "tools" / "olcum_backfill.json"
MISSING_LEDGER_REMEDY = (
    "fill_ledger.jsonl hic olusmamis -> once "
    "'py tools/ledger_backfill.py --dry-run' sonra '--apply' kostur"
)
AUTHORITATIVE_STATE_FILES = (
    "telemetry.jsonl", "alarms.jsonl", "trade_history.json",
)
PARTIAL_EVENT_KINDS = {
    "PARTIAL_THRESHOLD", "PARTIAL_INTENT", "PARTIAL_STATE",
    "PARTIAL_RETRY_EXHAUSTED", "PARTIAL_ERROR",
    "PARTIAL_ABORTED_FLAT", "PARTIAL_ABORTED_POSITION_CHANGED",
}
LOG_PEAK_RE = re.compile(
    r"\b([A-Z][A-Z0-9.]{0,14})\s*:\s*\+?(\d+(?:\.\d+)?)%"
)


class Status(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"
    NOT_READY = "NOT_READY"


EXIT_CODES = {
    Status.PASS: 0,
    Status.FAIL: 1,
    Status.UNKNOWN: 2,
    Status.NOT_READY: 3,
}


@dataclass(frozen=True)
class MetricResult:
    status: Status
    detail: str


@dataclass
class Reconciliation:
    status: Status
    broker_missing_from_ledger: Counter = field(default_factory=Counter)
    ledger_missing_from_broker: Counter = field(default_factory=Counter)
    anonymous_ledger_rows: int = 0
    problems: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ReturnValue:
    value: float | None
    reason: str = ""


@dataclass(frozen=True)
class ProfileInfo:
    name: str
    is_live: bool
    config_hash: str
    git_sha: str
    config: dict


@dataclass
class Fill:
    symbol: str
    side: str
    qty: float
    price: float
    at: datetime
    order_id: str = ""
    execution_id: str = ""
    provenance: str = "UNKNOWN"


@dataclass
class ExitFill:
    qty: float
    price: float
    at: datetime
    order_id: str = ""
    was_partial: bool = False


@dataclass
class ClosedTrade:
    symbol: str
    side: str
    entry_price: float
    entry_at: datetime
    closed_at: datetime
    qty: float
    pnl: float
    exits: list[ExitFill] = field(default_factory=list)
    peak_pct: float | None = None
    peak_at: datetime | None = None
    provenance: str = "UNKNOWN"

    def profitable_partial_at_three(self) -> bool:
        for item in self.exits:
            if not item.was_partial:
                continue
            gain = (
                item.price / self.entry_price - 1
                if self.side == "LONG"
                else self.entry_price / item.price - 1
            )
            if gain + 1e-9 >= 0.03:
                return True
        return False


@dataclass
class PartialMetric:
    hits: int = 0
    opportunities: int = 0
    legacy_misses: int = 0
    event_completeness_misses: int = 0

    @property
    def rate(self) -> float | None:
        return self.hits / self.opportunities if self.opportunities else None

    @property
    def passed(self) -> bool:
        return self.status is Status.PASS

    @property
    def status(self) -> Status:
        if self.event_completeness_misses:
            return Status.FAIL
        if self.rate is None:
            return Status.UNKNOWN
        return Status.PASS if self.rate >= 0.60 else Status.FAIL


@dataclass
class AuthoritativeState:
    available: bool
    telemetry: list[dict] = field(default_factory=list)
    telemetry_start: datetime | None = None
    alarms: list[dict] = field(default_factory=list)
    trade_rows: list[dict] = field(default_factory=list)
    files: list[Path] = field(default_factory=list)
    backfill: list[dict] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    backfill_label: str = ""


@dataclass
class InvariantCounts:
    protection_alarms: int = 0
    stop_regressions: int = 0
    unique_collisions: int = 0


def _as_datetime(value) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    elif value:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _enum_text(value) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "").split(".")[-1].upper()


def flatten_orders(orders: Iterable) -> list:
    flattened = []
    seen: set[str] = set()

    def visit(order) -> None:
        marker = str(getattr(order, "id", "") or f"obj:{id(order)}")
        if marker in seen:
            return
        seen.add(marker)
        flattened.append(order)
        for leg in getattr(order, "legs", None) or []:
            visit(leg)

    for order in orders:
        visit(order)
    return flattened


def broker_fills(orders: Iterable) -> list[Fill]:
    result: list[Fill] = []
    for order in flatten_orders(orders):
        at = _as_datetime(
            getattr(order, "filled_at", None)
            or getattr(order, "updated_at", None)
        )
        try:
            qty = abs(float(getattr(order, "filled_qty", 0) or 0))
            price = float(getattr(order, "filled_avg_price", 0) or 0)
        except (TypeError, ValueError):
            continue
        side = _enum_text(getattr(order, "side", ""))
        symbol = str(getattr(order, "symbol", "") or "")
        if at and symbol and side in {"BUY", "SELL"} and qty > 0 and price > 0:
            result.append(Fill(
                symbol=symbol, side=side, qty=qty, price=price, at=at,
                order_id=str(getattr(order, "id", "") or ""),
            ))
    return sorted(result, key=lambda item: item.at)


def _qty_decimal(value) -> Decimal | None:
    try:
        qty = abs(Decimal(str(value or 0)))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return qty.normalize() if qty > 0 else None


def _canonical_broker_multiset(
    orders: Iterable, since: datetime, until: datetime | None,
) -> tuple[Counter, list[str]]:
    result: Counter = Counter()
    problems: list[str] = []
    for order in flatten_orders(orders):
        # Closed orders CANCELED/REJECTED satirlari da tasir. Metrik-4 broker
        # kumesine yalniz gercek dolumu olan emirler girer.
        qty = _qty_decimal(getattr(order, "filled_qty", 0))
        if qty is None:
            continue
        stamp = _as_datetime(
            getattr(order, "filled_at", None)
            or getattr(order, "updated_at", None)
        )
        if stamp is None:
            problems.append("filled_qty>0 broker emrinde UTC zaman damgasi yok")
            continue
        if stamp < since or (until is not None and stamp > until):
            continue
        try:
            price = float(getattr(order, "filled_avg_price", 0) or 0)
        except (TypeError, ValueError):
            price = 0.0
        if price <= 0:
            problems.append("filled_qty>0 broker emrinde filled_avg_price eksik")
        order_id = str(getattr(order, "id", "") or "").strip()
        symbol = str(getattr(order, "symbol", "") or "").strip().upper()
        side = _enum_text(getattr(order, "side", ""))
        if not order_id or not symbol or side not in {"BUY", "SELL"}:
            problems.append(
                "filled_qty>0 broker emrinde order_id/symbol/side eksik"
            )
            continue
        result[(order_id, symbol, side, str(qty))] += 1
    return result, problems


def _canonical_ledger_multiset(
    rows: Iterable[dict], since: datetime, until: datetime | None,
) -> tuple[Counter, int, list[str]]:
    grouped: dict[tuple[str, str, str], Decimal] = defaultdict(Decimal)
    anonymous = 0
    problems: list[str] = []
    for row in rows:
        stamp = _as_datetime(row.get("ts_utc"))
        if stamp is None:
            problems.append("fill ledger satirinda UTC ts_utc eksik/gecersiz")
            continue
        if stamp < since or (until is not None and stamp > until):
            continue
        order_id = str(row.get("order_id") or "").strip()
        execution_id = str(row.get("execution_id") or "").strip()
        if not order_id and not execution_id:
            anonymous += 1
            continue
        symbol = str(row.get("symbol") or "").strip().upper()
        side = str(row.get("side") or "").strip().upper()
        qty = _qty_decimal(row.get("qty"))
        if not symbol or side not in {"BUY", "SELL"} or qty is None:
            problems.append(
                "fill ledger satirinda symbol/side/pozitif qty eksik"
            )
            continue
        # R9 normal yolu order_id tasir ve execution partial'lari burada tek
        # emir toplaminda birlesir. Yalniz execution_id'si olan satir kimliklidir
        # ama broker order kimligiyle eslesemez; ayri namespace sahte eslesmeyi
        # engeller ve mutabakat FAIL verir.
        identity = order_id if order_id else f"execution:{execution_id}"
        grouped[(identity, symbol, side)] += qty

    result: Counter = Counter()
    for (identity, symbol, side), qty in grouped.items():
        result[(identity, symbol, side, str(qty.normalize()))] += 1
    return result, anonymous, problems


def reconcile_fill_ledger(
    orders: Iterable, ledger_rows: Iterable[dict], since: datetime,
    until: datetime | None = None, *, broker_available: bool = True,
    ledger_available: bool = True, ledger_problem: str = "",
) -> Reconciliation:
    """Broker ve R9 ledger dolumlarini iki yonlu kanonik multiset ile uzlastir."""
    broker_set, broker_problems = _canonical_broker_multiset(orders, since, until)
    ledger_set, anonymous, ledger_problems = _canonical_ledger_multiset(
        ledger_rows, since, until,
    )
    ledger_is_absent = ledger_problem == MISSING_LEDGER_REMEDY
    if broker_available and (ledger_available or ledger_is_absent):
        missing = broker_set - ledger_set
        extra = ledger_set - broker_set
    else:
        missing = Counter()
        extra = Counter()
    problems = [*broker_problems, *ledger_problems]
    if not broker_available:
        problems.insert(0, "broker closed-orders okunamadi")
    if not ledger_available:
        missing = Counter()
        extra = Counter()
    if ledger_problem:
        problems.append(ledger_problem)
    elif not ledger_available:
        problems.append("fill ledger okunamadi")

    if not broker_available or not ledger_available:
        missing = Counter()
        extra = Counter()

    # Gercek bir tutarsizlik, baska bir kor noktayla maskelenmez.
    if missing or extra:
        status = Status.FAIL
    elif problems or anonymous:
        status = Status.UNKNOWN
    else:
        status = Status.PASS
    return Reconciliation(
        status=status,
        broker_missing_from_ledger=missing,
        ledger_missing_from_broker=extra,
        anonymous_ledger_rows=anonymous,
        problems=problems,
    )


def load_fill_ledger(
    state_dir: Path, since: datetime,
) -> tuple[list[dict], bool, str]:
    """Secilen state profilindeki R9 ledger'i public read_fills ile oku."""
    path = state_dir / "fill_ledger.jsonl"
    if not path.is_file():
        # Dosyanin yoklugu okunabilir bir "yerel kayit yok" durumudur. Broker
        # dolumu varsa mutabakat FAIL; iki taraf da bossa kaynak kor noktasi UNKNOWN.
        return [], True, MISSING_LEDGER_REMEDY
    original_path = fill_ledger._ledger_path
    fill_ledger._ledger_path = lambda: str(path)
    try:
        return fill_ledger.read_fills(since=since), True, ""
    except Exception as exc:
        return [], False, f"fill_ledger.jsonl okunamadi ({exc})"
    finally:
        fill_ledger._ledger_path = original_path


def resolve_order_provenance(order_id: str, state_dir: Path) -> str:
    """Secilen state profilindeki R9 journal'dan tahminsiz provenance coz."""
    path = state_dir / "order_journal.json"
    original_path = order_journal._journal_path
    order_journal._journal_path = lambda: str(path)
    try:
        return order_journal.resolve(order_id)
    except Exception:
        return "UNKNOWN"
    finally:
        order_journal._journal_path = original_path


def attach_provenance(
    fills: Iterable[Fill], ledger_rows: Iterable[dict],
    resolver: Callable[[str], str],
) -> list[Fill]:
    """Broker dolumlarina yalniz R9 ledger/journal kanitiyla provenance ekle."""
    by_order: dict[str, set[str]] = defaultdict(set)
    for row in ledger_rows:
        order_id = str(row.get("order_id") or "").strip()
        if order_id:
            provenance = str(row.get("provenance") or "UNKNOWN")
            by_order[order_id].add(
                provenance
                if provenance in fill_ledger.PROVENANCES else "UNKNOWN"
            )

    result = []
    for fill in fills:
        provenances = by_order.get(fill.order_id, set())
        known = {item for item in provenances if item != "UNKNOWN"}
        provenance = "UNKNOWN"
        if len(known) == 1 and "UNKNOWN" not in provenances:
            provenance = next(iter(known))
        elif len(known) <= 1:
            journal_value = resolver(fill.order_id) if fill.order_id else "UNKNOWN"
            if journal_value != "UNKNOWN" and (
                not known or journal_value in known
            ):
                provenance = journal_value
        result.append(Fill(
            symbol=fill.symbol,
            side=fill.side,
            qty=fill.qty,
            price=fill.price,
            at=fill.at,
            order_id=fill.order_id,
            execution_id=fill.execution_id,
            provenance=provenance,
        ))
    return result


def fetch_closed_orders(client, since: datetime) -> list:
    """Dönem girişlerini de kapsayan, sayfalı ve salt-okunur closed-order sorgusu."""
    cursor = since - timedelta(days=90)
    until = datetime.now(timezone.utc)
    result = []
    seen: set[str] = set()
    while cursor < until:
        page = list(client.get_orders(GetOrdersRequest(
            status=QueryOrderStatus.CLOSED,
            limit=500,
            after=cursor,
            until=until,
            direction=Sort.ASC,
            nested=True,
        )))
        if not page:
            break
        newest = cursor
        for order in page:
            marker = str(getattr(order, "id", "") or f"obj:{id(order)}")
            if marker not in seen:
                seen.add(marker)
                result.append(order)
            stamp = _as_datetime(
                getattr(order, "submitted_at", None)
                or getattr(order, "created_at", None)
                or getattr(order, "filled_at", None)
                or getattr(order, "updated_at", None)
            )
            if stamp and stamp > newest:
                newest = stamp
        if len(page) < 500 or newest <= cursor:
            break
        cursor = newest + timedelta(microseconds=1)
    return result


def reconstruct_closed_trades(fills: Iterable[Fill]) -> list[ClosedTrade]:
    """Broker fill'lerinden sembol bazında net pozisyon döngülerini kur."""
    states: dict[str, dict] = {}
    closed: list[ClosedTrade] = []

    for fill in sorted(fills, key=lambda item: item.at):
        state = states.get(fill.symbol)
        direction = 1 if fill.side == "BUY" else -1
        if not state:
            states[fill.symbol] = {
                "sign": direction, "qty": fill.qty, "entry_qty": fill.qty,
                "entry_price": fill.price, "entry_at": fill.at,
                "pnl": 0.0, "exits": [],
                "provenances": {fill.provenance},
            }
            continue

        if state["sign"] == direction:
            old_qty = state["qty"]
            new_qty = old_qty + fill.qty
            state["entry_price"] = (
                state["entry_price"] * old_qty + fill.price * fill.qty
            ) / new_qty
            state["qty"] = new_qty
            state["entry_qty"] += fill.qty
            state["provenances"].add(fill.provenance)
            continue

        close_qty = min(state["qty"], fill.qty)
        if state["sign"] > 0:
            state["pnl"] += (fill.price - state["entry_price"]) * close_qty
        else:
            state["pnl"] += (state["entry_price"] - fill.price) * close_qty
        remaining = state["qty"] - close_qty
        state["exits"].append(ExitFill(
            qty=close_qty, price=fill.price, at=fill.at,
            order_id=fill.order_id, was_partial=remaining > 1e-9,
        ))
        state["provenances"].add(fill.provenance)
        state["qty"] = remaining

        if remaining <= 1e-9:
            provenances = state["provenances"]
            closed.append(ClosedTrade(
                symbol=fill.symbol,
                side="LONG" if state["sign"] > 0 else "SHORT",
                entry_price=state["entry_price"],
                entry_at=state["entry_at"],
                closed_at=fill.at,
                qty=state["entry_qty"],
                pnl=state["pnl"],
                exits=list(state["exits"]),
                provenance=(
                    next(iter(provenances))
                    if len(provenances) == 1 else "UNKNOWN"
                ),
            ))
            states.pop(fill.symbol, None)

        crossed_qty = fill.qty - close_qty
        if crossed_qty > 1e-9:
            states[fill.symbol] = {
                "sign": direction, "qty": crossed_qty, "entry_qty": crossed_qty,
                "entry_price": fill.price, "entry_at": fill.at,
                "pnl": 0.0, "exits": [],
                "provenances": {fill.provenance},
            }
    return closed


def _fetch_iex_bars(
    data_client, symbol: str, timeframe, start: datetime, end: datetime,
) -> tuple[list, str | None, str]:
    """Barlari acik IEX feed'iyle oku; SIP duvari gorulurse IEX'i zorla yinele."""
    errors: list[str] = []
    for attempt in range(2):
        try:
            response = data_client.get_stock_bars(StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=timeframe,
                start=start,
                end=end,
                sort=Sort.ASC,
                feed=DataFeed.IEX,
            ))
            bars = list(
                (getattr(response, "data", {}) or {}).get(symbol, [])
            )
            return bars, DataFeed.IEX.value, ""
        except Exception as exc:
            message = " ".join(str(exc).split())
            label = "iex" if attempt == 0 else "iex(fallback)"
            errors.append(f"{label}: {message}")
            # Feed acikca IEX olsa da API/SDK SIP abonelik reddi dondururse
            # yeni bir explicit-IEX istek nesnesiyle bir kez daha dene.
            if attempt == 0 and "sip" in message.lower():
                continue
            break
    return [], None, "denenen feed'ler=iex; " + "; ".join(errors)


def attach_peaks(trades: list[ClosedTrade], data_client) -> int:
    """Dakika barından trade ömründeki en iyi excursion'ı ekle; bilinmeyen sayısını döndür."""
    unknown = 0
    by_symbol: dict[str, list[ClosedTrade]] = defaultdict(list)
    for trade in trades:
        by_symbol[trade.symbol].append(trade)

    for symbol, symbol_trades in by_symbol.items():
        start = min(item.entry_at for item in symbol_trades) - timedelta(minutes=1)
        end = max(item.closed_at for item in symbol_trades) + timedelta(minutes=1)
        bars, _, problem = _fetch_iex_bars(
            data_client, symbol, TimeFrame.Minute, start, end,
        )
        if problem:
            print(
                f"UYARI: {symbol} dakika barlari alinamadi: {problem}",
                file=sys.stderr,
            )

        for trade in symbol_trades:
            inside = [
                bar for bar in bars
                if (stamp := _as_datetime(getattr(bar, "timestamp", None)))
                and trade.entry_at <= stamp <= trade.closed_at
            ]
            if not inside:
                unknown += 1
                continue
            if trade.side == "LONG":
                best_bar = max(inside, key=lambda bar: float(bar.high))
                best = float(best_bar.high)
                trade.peak_pct = best / trade.entry_price - 1
            else:
                best_bar = min(inside, key=lambda bar: float(bar.low))
                best = float(best_bar.low)
                trade.peak_pct = trade.entry_price / best - 1 if best > 0 else None
                if trade.peak_pct is None:
                    unknown += 1
            trade.peak_at = _as_datetime(getattr(best_bar, "timestamp", None))
    return unknown


def _read_jsonl(path: Path) -> tuple[list[dict], str | None]:
    records: list[dict] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    return records, f"{path.name}:{number} okunamadi ({exc})"
                if not isinstance(record, dict):
                    return records, f"{path.name}:{number} JSON nesnesi degil"
                records.append(record)
    except (OSError, UnicodeError) as exc:
        return [], f"{path.name} okunamadi ({exc})"
    return records, None


def _in_period(record: dict, since: datetime, until: datetime) -> bool:
    stamp = _as_datetime(
        record.get("ts") or record.get("time")
        or record.get("timestamp") or record.get("date")
    )
    return stamp is not None and since <= stamp <= until


def _load_backfill(path: Path, since: datetime) -> tuple[list[dict], str, list[str]]:
    if since >= TELEMETRY_START:
        return [], "", []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return [], "", [f"backfill okunamadi ({exc})"]
    if not isinstance(payload, dict) or payload.get("label") != "backfill":
        return [], "", ["backfill etiketi/gecerli snapshot yok"]
    coverage_start = _as_datetime(payload.get("period_start"))
    coverage_end = _as_datetime(payload.get("period_end"))
    sources = set(payload.get("sources") or [])
    if (
        coverage_start is None or coverage_start > since
        or coverage_end is None or coverage_end < TELEMETRY_START
    ):
        return [], "", ["backfill olcum/telemetri-oncesi araligini kapsamiyor"]
    if not {"alarms.jsonl", "broker_closed_orders"}.issubset(sources):
        return [], "", ["backfill otoriter kaynak etiketi eksik"]
    records = [item for item in payload.get("events", []) if isinstance(item, dict)]
    return records, str(payload.get("label")), []


def load_authoritative_state(
    state_dir: Path, since: datetime, until: datetime,
    backfill_path: Path = BACKFILL_PATH,
) -> AuthoritativeState:
    """Kalici state'in tamamini oku; eksik/bozuk otoriter kaynak FAIL'dir."""
    result = AuthoritativeState(available=True)
    if not state_dir.is_dir():
        result.available = False
        result.problems.append(f"state dizini eksik: {state_dir}")
    else:
        for name in AUTHORITATIVE_STATE_FILES:
            path = state_dir / name
            if not path.is_file():
                result.problems.append(f"{name} eksik")
                continue
            result.files.append(path)
            if name.endswith(".jsonl"):
                records, problem = _read_jsonl(path)
                if problem:
                    result.problems.append(problem)
                if name == "telemetry.jsonl":
                    result.telemetry = records
                    telemetry_stamps = [
                        stamp for row in records
                        if (stamp := _as_datetime(row.get("ts"))) is not None
                    ]
                    result.telemetry_start = (
                        min(telemetry_stamps) if telemetry_stamps else None
                    )
                    if not records and problem is None:
                        result.problems.append(
                            "telemetry.jsonl bos; kapsama dogrulanamadi"
                        )
                    elif records and result.telemetry_start is None:
                        result.problems.append(
                            "telemetry.jsonl kayit zamanlari okunamadi"
                        )
                else:
                    result.alarms = records
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                result.problems.append(f"{name} okunamadi ({exc})")
                continue
            if not isinstance(payload, list) or not all(
                isinstance(row, dict) for row in payload
            ):
                result.problems.append(f"{name} beklenen JSON listesi degil")
            else:
                result.trade_rows = [
                    row for row in payload
                    if (_row_time(row) is None or _row_time(row) >= since)
                    and str(row.get("action", "")).upper() in EXIT_ACTIONS
                ]

    backfill, label, backfill_problems = _load_backfill(backfill_path, since)
    result.backfill = backfill
    result.backfill_label = label
    result.problems.extend(backfill_problems)
    result.available = result.available and not result.problems
    result.telemetry = [
        row for row in result.telemetry if _in_period(row, since, until)
    ]
    result.alarms = [
        row for row in result.alarms if _in_period(row, since, until)
    ]
    result.backfill = [
        row for row in result.backfill if _in_period(row, since, until)
    ]
    return result


def _row_time(row: dict) -> datetime | None:
    return _as_datetime(row.get("time") or row.get("timestamp") or row.get("date"))


def load_local_exit_rows(state_dir: Path, since: datetime) -> tuple[list[dict], list[Path]]:
    """Önce persisted trades_today kullan; yoksa trade_history listelerine düş."""
    today_rows: list[dict] = []
    history_rows: list[dict] = []
    files: list[Path] = []
    if not state_dir.exists():
        return [], []
    for path in sorted(state_dir.rglob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        files.append(path)
        candidates: list[dict] = []
        is_trades_today = False
        if isinstance(payload, dict) and isinstance(payload.get("trades_today"), list):
            candidates = payload["trades_today"]
            is_trades_today = True
        elif isinstance(payload, list) and "trade_history" in path.name:
            candidates = payload
        target = today_rows if is_trades_today else history_rows
        for row in candidates:
            if not isinstance(row, dict) or str(row.get("action", "")).upper() not in EXIT_ACTIONS:
                continue
            stamp = _row_time(row)
            if stamp is None or stamp >= since:
                target.append(row)
    return (today_rows if today_rows else history_rows), files


def phantom_count(
    local_rows: list[dict], trades: list[ClosedTrade]
) -> tuple[int, int, int]:
    """Yakın-zaman aynı symbol+qty tekrarını ve broker ile eşleşmeyeni say."""
    usable = []
    for row in local_rows:
        try:
            qty = abs(float(row.get("qty", 0) or 0))
        except (TypeError, ValueError):
            continue
        if qty > 0:
            usable.append((row, qty, _row_time(row)))

    duplicates = 0
    grouped: dict[tuple[str, float], list[datetime | None]] = defaultdict(list)
    for row, qty, stamp in usable:
        grouped[(str(row.get("symbol", "")), round(qty, 4))].append(stamp)
    for stamps in grouped.values():
        known = sorted(item for item in stamps if item is not None)
        duplicates += sum(
            (right - left) <= timedelta(minutes=10)
            for left, right in zip(known, known[1:])
        )

    broker_exits = [
        [trade.symbol, round(item.qty, 4), False]
        for trade in trades for item in trade.exits
    ]
    unmatched = 0
    for row, qty, _stamp in usable:
        symbol, normalized = str(row.get("symbol", "")), round(qty, 4)
        match = next((
            item for item in broker_exits
            if not item[2] and item[0] == symbol and abs(item[1] - normalized) <= 1e-4
        ), None)
        if match:
            match[2] = True
        else:
            unmatched += 1
    return max(duplicates, unmatched), duplicates, unmatched


def _same_partial_trade(event: dict, trade: ClosedTrade) -> bool:
    if str(event.get("symbol", "")).upper() != trade.symbol.upper():
        return False
    side = str(event.get("side", "LONG")).upper()
    if side and side != trade.side:
        return False
    stamp = _as_datetime(event.get("ts"))
    if stamp is None or not (
        trade.entry_at - timedelta(minutes=1)
        <= stamp
        <= trade.closed_at + timedelta(minutes=5)
    ):
        return False
    try:
        event_entry = float(event.get("entry_price") or 0)
    except (TypeError, ValueError):
        event_entry = 0.0
    return event_entry <= 0 or abs(event_entry - trade.entry_price) <= max(
        0.01, trade.entry_price * 0.001,
    )


def _partial_episode_hit(events: list[dict], fills_by_id: dict[str, Fill]) -> bool:
    for event in events:
        order_id = str(event.get("order_id") or "")
        fill = fills_by_id.get(order_id)
        if fill is None:
            continue
        side = str(event.get("side") or "LONG").upper()
        expected_fill_side = "SELL" if side == "LONG" else "BUY"
        if (
            fill.side != expected_fill_side
            or fill.symbol.upper() != str(event.get("symbol") or "").upper()
        ):
            continue
        try:
            target = abs(float(event.get("target_qty") or 0))
            observed = abs(float(event.get("filled_qty") or 0))
        except (TypeError, ValueError):
            target = observed = 0.0
        tolerance = max(1e-4, target * 1e-4)
        status = str(event.get("intent_status") or "").upper()
        if target <= 0 or fill.qty + tolerance >= target:
            return True
        if status == "FILLED" and observed + tolerance >= target:
            return True
    return False


def evaluate_partial_metric(
    trades: list[ClosedTrade], telemetry: list[dict], fills: list[Fill],
    since: datetime, auxiliary_peaks: set[tuple[str, date]] | None = None,
    telemetry_start: datetime | None = None,
) -> PartialMetric:
    """Metrik-2'yi bot event'lerinden kur, broker fill ID'siyle doğrula."""
    period = [trade for trade in trades if trade.closed_at >= since]
    if telemetry_start is None:
        telemetry_stamps = [
            stamp for event in telemetry
            if (stamp := _as_datetime(event.get("ts"))) is not None
        ]
        telemetry_start = min(telemetry_stamps) if telemetry_stamps else None
    events = []
    for event in telemetry:
        kind = str(event.get("kind", "")).upper()
        stamp = _as_datetime(event.get("ts"))
        if (
            (kind in PARTIAL_EVENT_KINDS or kind.startswith("PARTIAL_ABORTED_"))
            and stamp is not None and stamp >= since
        ):
            events.append(event)
    fills_by_id = {fill.order_id: fill for fill in fills if fill.order_id}
    result = PartialMetric()

    for trade in period:
        matched = [
            event for event in events if _same_partial_trade(event, trade)
        ]
        if matched:
            result.opportunities += 1
            result.hits += int(_partial_episode_hit(matched, fills_by_id))
            continue
        log_dates = sorted(
            stamp for symbol, stamp in (auxiliary_peaks or set())
            if symbol == trade.symbol.upper()
            and trade.entry_at.astimezone(ET).date()
            <= stamp <= trade.closed_at.astimezone(ET).date()
        )
        bar_evidence = trade.peak_pct is not None and trade.peak_pct + 1e-9 >= 0.03
        if not bar_evidence and not log_dates:
            continue
        evidence_at = (
            trade.peak_at
            or (
                datetime.combine(log_dates[0], time.min, ET).astimezone(timezone.utc)
                if log_dates else trade.closed_at
            )
        )
        if telemetry_start is None or evidence_at < telemetry_start:
            result.legacy_misses += 1
            result.opportunities += 1
        else:
            result.event_completeness_misses += 1
    return result


def load_auxiliary_peak_evidence(
    log_dir: Path, since_date: date,
) -> set[tuple[str, date]]:
    """Rotating loglardan yalnız Metrik-2 için yardımcı +%3 kanıtı oku."""
    evidence: set[tuple[str, date]] = set()
    if not log_dir.is_dir():
        return evidence
    for path in log_dir.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".log", ".txt"}:
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            day_match = re.search(r"\d{4}-\d{2}-\d{2}", line)
            peak_match = LOG_PEAK_RE.search(line)
            if not day_match or not peak_match:
                continue
            try:
                day = date.fromisoformat(day_match.group(0))
                pnl_pct = float(peak_match.group(2)) / 100
            except ValueError:
                continue
            if day >= since_date and pnl_pct + 1e-9 >= 0.03:
                evidence.add((peak_match.group(1).upper(), day))
    return evidence


def count_broker_stop_rejections(orders: Iterable, since: datetime) -> int:
    """Broker closed-orders içindeki rejected stop emirlerini doğrudan say."""
    count = 0
    for order in flatten_orders(orders):
        status = _enum_text(getattr(order, "status", ""))
        order_type = _enum_text(
            getattr(order, "type", None) or getattr(order, "order_type", "")
        )
        stamp = _as_datetime(
            getattr(order, "updated_at", None)
            or getattr(order, "submitted_at", None)
            or getattr(order, "created_at", None)
        )
        if (
            status == "REJECTED"
            and order_type in {"STOP", "STOP_LIMIT"}
            and stamp is not None and stamp >= since
        ):
            count += 1
    return count


def invariant_counts(
    alarms: list[dict], telemetry: list[dict], backfill: list[dict],
) -> InvariantCounts:
    result = InvariantCounts()
    for record in [*alarms, *telemetry, *backfill]:
        kind = str(record.get("kind", "")).upper()
        text = " ".join(str(record.get(field, "")) for field in (
            "kind", "message", "detail", "outcome", "code",
        )).upper()
        try:
            amount = max(int(record.get("count", 1) or 1), 0)
        except (TypeError, ValueError):
            amount = 1
        if kind in {"KORUMA", "PROTECTION_ALARM"}:
            result.protection_alarms += amount
        if kind == "STOP_REGRESSION" or "REGRES" in text:
            result.stop_regressions += amount
        if kind == "UNIQUE_COLLISION" or "40010001" in text:
            result.unique_collisions += amount
    return result


def trading_days(start: date, end: date) -> int:
    if end < start:
        return 0
    return sum(
        (start + timedelta(days=offset)).weekday() < 5
        for offset in range((end - start).days + 1)
    )


def broker_trading_days(client, start: date, end: date) -> int | None:
    """NYSE işlem günü sayısını broker takviminden al; tahmin üretme."""
    try:
        return len(client.get_calendar(GetCalendarRequest(start=start, end=end)))
    except Exception as exc:
        print(f"UYARI: broker takvimi alinamadi: {exc}", file=sys.stderr)
        return None


def _metric(label: str, result: MetricResult) -> None:
    print(f"[{result.status.value}] {label}: {result.detail}")


def evaluate_pnl_metric(
    trades: list[ClosedTrade], broker_available: bool = True,
) -> MetricResult:
    if not broker_available:
        return MetricResult(Status.UNKNOWN, "broker closed-orders okunamadi")
    unknown = [trade for trade in trades if trade.provenance == "UNKNOWN"]
    strategy = [trade for trade in trades if trade.provenance == "strategy"]
    excluded = len(trades) - len(strategy) - len(unknown)
    net_pnl = sum(trade.pnl for trade in strategy)
    if unknown:
        return MetricResult(
            Status.UNKNOWN,
            f"provenance UNKNOWN kapali islem={len(unknown)}; "
            f"dogrulanmis strategy={len(strategy)}, haric tutulan={excluded}, "
            f"bilinen strategy net PnL=${net_pnl:+.2f}; R9 journal deploy "
            "oncesi bostu; backfill + yeni donem ile cozulur",
        )
    if not strategy:
        return MetricResult(
            Status.UNKNOWN,
            f"dogrulanmis kapali strategy islem yok; haric tutulan={excluded}",
        )
    if not strategy:
        return MetricResult(
            Status.UNKNOWN,
            f"dogrulanmis kapali strategy islem yok; haric tutulan={excluded}",
        )
    return MetricResult(
        Status.PASS if net_pnl > 0 else Status.FAIL,
        f"dogrulanmis strategy kapali islem={len(strategy)}, "
        f"haric tutulan={excluded}, net PnL=${net_pnl:+.2f}",
    )


def evaluate_never_green_metric(
    trades: list[ClosedTrade], breakeven_trigger_pct: float,
) -> MetricResult:
    unknown = [trade for trade in trades if trade.peak_pct is None]
    if unknown:
        return MetricResult(
            Status.UNKNOWN,
            f"peak_pct UNKNOWN kapali strategy islem={len(unknown)}; "
            f"yesil esigi=config breakeven_trigger_pct={breakeven_trigger_pct:.2%}",
        )
    if not trades:
        return MetricResult(
            Status.UNKNOWN,
            "kapali strategy islem yok; never-green orani belirlenemiyor",
        )
    never_green = [
        trade for trade in trades
        if float(trade.peak_pct) < breakeven_trigger_pct
    ]
    never_rate = len(never_green) / len(trades)
    total_losses = sum(-trade.pnl for trade in trades if trade.pnl < 0)
    never_losses = sum(-trade.pnl for trade in never_green if trade.pnl < 0)
    loss_share = never_losses / total_losses if total_losses > 0 else 0.0
    status = (
        Status.PASS
        if never_rate <= 0.20 and loss_share <= 0.30
        else Status.FAIL
    )
    return MetricResult(
        status,
        f"esik {breakeven_trigger_pct:.2%}; oran "
        f"{len(never_green)}/{len(trades)} = {never_rate:.1%}; "
        f"toplam zarar payi {loss_share:.1%}",
    )


def evaluate_integrity_metric(
    reconciliation: Reconciliation, state: AuthoritativeState,
    stop_rejections: int,
) -> MetricResult:
    missing = sum(reconciliation.broker_missing_from_ledger.values())
    extra = sum(reconciliation.ledger_missing_from_broker.values())
    source = "broker filled orders + R9 fill ledger + persistent state"
    if state.backfill_label:
        source += f" + {state.backfill_label}"
    detail = (
        f"broker'da var/ledger'da yok={missing}, "
        f"ledger'da var/broker'da yok={extra}, "
        f"kimliksiz legacy={reconciliation.anonymous_ledger_rows}, "
        f"server-stop rejection={stop_rejections}, kaynak={source}"
    )
    problems = [*reconciliation.problems, *state.problems]
    if missing or extra or stop_rejections:
        if problems:
            detail += "; ek kor nokta: " + "; ".join(problems)
        return MetricResult(Status.FAIL, detail)
    if reconciliation.status is Status.UNKNOWN or not state.available:
        if problems:
            detail += "; UNKNOWN: " + "; ".join(problems)
        if reconciliation.anonymous_ledger_rows:
            detail += "; execution_id ve order_id'siz legacy satir var"
        return MetricResult(Status.UNKNOWN, detail)
    return MetricResult(Status.PASS, detail)


def gate_status(
    metric_statuses: Iterable[Status], strategy_trade_count: int,
    elapsed_days: int | None,
) -> Status:
    statuses = list(metric_statuses)
    if Status.FAIL in statuses:
        return Status.FAIL
    if elapsed_days is None or Status.UNKNOWN in statuses or any(
        status not in {Status.PASS, Status.FAIL, Status.UNKNOWN}
        for status in statuses
    ):
        return Status.UNKNOWN
    if strategy_trade_count < 20 or elapsed_days < 30:
        return Status.NOT_READY
    return Status.PASS if len(statuses) == 4 and all(
        status is Status.PASS for status in statuses
    ) else Status.UNKNOWN


def measured_profile() -> ProfileInfo:
    config = dict(STOCK_CONFIG)
    config.update(PAPER_AGGRESSIVE_CONFIG)
    payload = json.dumps(
        config, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
            text=True, check=True, timeout=5,
        )
        git_sha = completed.stdout.strip() or "UNKNOWN"
    except (OSError, subprocess.SubprocessError):
        git_sha = "UNKNOWN"
    return ProfileInfo(
        name="PAPER_AGGRESSIVE",
        is_live=False,
        config_hash=hashlib.sha256(payload).hexdigest(),
        git_sha=git_sha,
        config=config,
    )


def fetch_account_return(
    client, since: datetime, until: datetime,
) -> ReturnValue:
    try:
        history = client.get_portfolio_history(GetPortfolioHistoryRequest(
            start=since, end=until, timeframe="1D", extended_hours=False,
        ))
        equities = getattr(history, "equity", None)
        if equities is None and isinstance(history, dict):
            equities = history.get("equity")
        values = [float(item) for item in (equities or []) if item is not None]
        if len(values) < 2 or values[0] <= 0:
            return ReturnValue(None, "portfolio history yetersiz")
        return ReturnValue(values[-1] / values[0] - 1)
    except Exception as exc:
        return ReturnValue(None, f"portfolio history okunamadi ({exc})")


def fetch_spy_return(
    data_client, since: datetime, until: datetime,
) -> ReturnValue:
    bars, feed, problem = _fetch_iex_bars(
        data_client, "SPY", TimeFrame.Day, since, until,
    )
    if problem:
        return ReturnValue(None, f"SPY barlari okunamadi ({problem})")
    closes = [float(bar.close) for bar in bars if float(bar.close) > 0]
    if len(closes) < 2:
        return ReturnValue(None, f"SPY gunluk barlari yetersiz; feed={feed}")
    return ReturnValue(closes[-1] / closes[0] - 1, f"feed={feed}")


def strategy_return(trades: list[ClosedTrade]) -> ReturnValue:
    unknown = sum(trade.provenance == "UNKNOWN" for trade in trades)
    if unknown:
        return ReturnValue(None, f"provenance UNKNOWN kapali islem={unknown}")
    strategy = [trade for trade in trades if trade.provenance == "strategy"]
    invested = sum(abs(trade.entry_price * trade.qty) for trade in strategy)
    if invested <= 0:
        return ReturnValue(None, "dogrulanmis kapali strategy sermayesi yok")
    return ReturnValue(sum(trade.pnl for trade in strategy) / invested)


def _return_line(label: str, result: ReturnValue) -> None:
    value = f"{result.value:+.2%}" if result.value is not None else "UNKNOWN"
    suffix = f" ({result.reason})" if result.reason else ""
    print(f"{label}: {value}{suffix}")


def print_report(
    trades: list[ClosedTrade], since: datetime, unknown_peaks: int,
    partial: PartialMetric, stop_rejections: int,
    state: AuthoritativeState, broker_available: bool,
    elapsed_days: int | None, until: datetime,
    reconciliation: Reconciliation, profile: ProfileInfo,
    account_result: ReturnValue, spy_result: ReturnValue,
) -> Status:
    period = [
        trade for trade in trades if since <= trade.closed_at <= until
    ]
    strategy_period = [
        trade for trade in period if trade.provenance == "strategy"
    ]
    strategy_count = len(strategy_period)
    elapsed_text = str(elapsed_days) if elapsed_days is not None else "UNKNOWN"
    print(
        f"Olcum donemi (UTC): {since.isoformat()} -> {until.isoformat()} "
        f"(islem gunu={elapsed_text}/30, strategy n={strategy_count}/20)"
    )
    if elapsed_days is None:
        print("Tempo projeksiyonu: UNKNOWN (broker islem takvimi okunamadi)")
    else:
        projected = strategy_count * 30 / elapsed_days if elapsed_days > 0 else 0.0
        print(f"Tempo projeksiyonu: 30 islem gunu sonunda strategy n={projected:.1f}")
        if projected + 1e-9 < 20:
            print(
                f"TEMPO UYARISI: mevcut tempoyla hedef 20'nin altinda "
                f"({projected:.1f})"
            )

    print(
        f"Olculen profil: {profile.name} | config SHA-256={profile.config_hash} "
        f"| git commit SHA={profile.git_sha}"
    )
    if not profile.is_live:
        print(
            "UYARI: agresif paper sonucu canli R5 kilidini acmanin kaniti "
            "SAYILMAZ (farkli esik/boyut/MTF/cikis geometrisi)."
        )
    print("Getiri karsilastirmasi (ayni UTC pencere; bilgi, kapiyi degistirmez):")
    _return_line("  Hesap getirisi", account_result)
    _return_line("  Strateji getirisi", strategy_return(period))
    _return_line("  SPY getirisi", spy_result)

    metric1 = evaluate_pnl_metric(period, broker_available)
    _metric("1 strategy PnL", metric1)

    partial_rate = partial.rate
    partial_detail = (
        f"{partial.hits}/{partial.opportunities} = {partial_rate:.1%}"
        if partial_rate is not None
        else "bot partial telemetrisinde kapali episode firsati yok"
    )
    completeness = (
        f"FAIL={partial.event_completeness_misses}"
        if partial.event_completeness_misses else "PASS"
    )
    partial_detail += (
        f"; legacy miss={partial.legacy_misses}; "
        f"event-completeness {completeness}; peak UNKNOWN={unknown_peaks}"
    )
    metric2 = MetricResult(partial.status, partial_detail)
    _metric("2 +%3 kademeli satis", metric2)
    if partial.event_completeness_misses:
        print(
            "[FAIL] Metrik-2 veri butunlugu: bar/log +%3 kaniti olan "
            f"{partial.event_completeness_misses} telemetri-era kapali "
            "episode'unda partial event yok"
        )

    trigger = float(profile.config["breakeven_trigger_pct"])
    metric3 = evaluate_never_green_metric(strategy_period, trigger)
    _metric("3 never-green", metric3)

    metric4 = evaluate_integrity_metric(reconciliation, state, stop_rejections)
    _metric("4 kayit/stop butunlugu", metric4)

    invariants = invariant_counts(state.alarms, state.telemetry, state.backfill)
    print("Sistem invariant (bilgi; 4-metrik kapi tanimi degismedi):")
    print(f"  KORUMA alarmi={invariants.protection_alarms}")
    print(f"  stop-regresyon gozlemi={invariants.stop_regressions}")
    print(f"  unique-collision (40010001)={invariants.unique_collisions}")

    result = gate_status(
        [metric1.status, metric2.status, metric3.status, metric4.status],
        strategy_count,
        elapsed_days,
    )
    print(
        f"GENEL: {result.value} - strategy n={strategy_count}, "
        f"hedef 20 islem / 30 islem gunu (gun={elapsed_text})"
    )
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="R10 ölçüm doğruluğu raporu (tamamen salt okunur).",
        epilog=(
            "Ham/rotate loglar yalnız Metrik-2 için yardımcı +%3 kanıtıdır; "
            "otoriter Metrik-4 kaynağı değildir."
        ),
    )
    parser.add_argument("--mode", choices=["paper"], default="paper")
    parser.add_argument(
        "--since", default=MEASUREMENT_START,
        help=f"Başlangıç tarihi (YYYY-MM-DD; varsayılan: {MEASUREMENT_START})",
    )
    parser.add_argument(
        "--state-dir", type=Path,
        default=Path(os.getenv("OLCUM_STATE_DIR", ROOT / "state_paper")),
        help="Yerel veya VPS'ten kopyalanmış paper state dizini",
    )
    parser.add_argument(
        "--log-dir", type=Path,
        default=Path(os.getenv("OLCUM_LOG_DIR", ROOT / "logs")),
        help="Yardımcı +%%3 kanıtı aranacak log dizini",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    load_dotenv(ROOT / ".env")
    args = parse_args(argv)
    try:
        since_day = date.fromisoformat(args.since)
    except ValueError:
        print("HATA: --since YYYY-MM-DD biciminde olmali.", file=sys.stderr)
        return 2
    since = datetime.combine(since_day, time.min, ET).astimezone(timezone.utc)

    key = os.getenv("ALPACA_PAPER_API_KEY")
    secret = os.getenv("ALPACA_PAPER_SECRET_KEY")
    if not key or not secret:
        print("HATA: ALPACA_PAPER_API_KEY/SECRET eksik.", file=sys.stderr)
        return 2

    trading = TradingClient(key, secret, paper=True)
    data = StockHistoricalDataClient(api_key=key, secret_key=secret)
    broker_available = True
    try:
        orders = fetch_closed_orders(trading, since)
    except Exception as exc:
        print(f"UYARI: broker closed-orders alinamadi: {exc}", file=sys.stderr)
        orders = []
        broker_available = False

    raw_fills = broker_fills(orders)
    ledger_rows, ledger_available, ledger_problem = load_fill_ledger(
        args.state_dir, since - timedelta(days=90),
    )
    fills = attach_provenance(
        raw_fills,
        ledger_rows,
        lambda order_id: resolve_order_provenance(order_id, args.state_dir),
    )
    today = datetime.now(ET).date()
    until = datetime.combine(today, time.max, ET).astimezone(timezone.utc)
    trades = reconstruct_closed_trades(fills)
    period = [
        trade for trade in trades if since <= trade.closed_at <= until
    ]
    strategy_period = [
        trade for trade in period if trade.provenance == "strategy"
    ]
    unknown = attach_peaks(strategy_period, data)
    state = load_authoritative_state(args.state_dir, since, until)
    reconciliation = reconcile_fill_ledger(
        orders, ledger_rows, since, until,
        broker_available=broker_available,
        ledger_available=ledger_available,
        ledger_problem=ledger_problem,
    )
    auxiliary_peaks = load_auxiliary_peak_evidence(args.log_dir, since_day)
    partial = evaluate_partial_metric(
        strategy_period, state.telemetry, fills, since, auxiliary_peaks,
        telemetry_start=state.telemetry_start,
    )
    stop_rejections = count_broker_stop_rejections(orders, since)
    elapsed_days = broker_trading_days(
        trading, since.astimezone(ET).date(), today,
    )
    result = print_report(
        period, since, unknown, partial, stop_rejections, state,
        broker_available, elapsed_days, until, reconciliation,
        measured_profile(), fetch_account_return(trading, since, until),
        fetch_spy_return(data, since, until),
    )
    return EXIT_CODES[result]


if __name__ == "__main__":
    raise SystemExit(main())
