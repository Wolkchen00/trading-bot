"""R6 paper ölçüm raporu.

Salt okunur: yalnız broker emirlerini/barlarını ve yerel state/log dosyalarını
okur; emir verme, değiştirme veya iptal etme metodu çağırmaz.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sys
from typing import Iterable
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from alpaca.common.enums import Sort
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import QueryOrderStatus
from alpaca.trading.requests import GetCalendarRequest, GetOrdersRequest


ROOT = Path(__file__).resolve().parents[1]
MEASUREMENT_START = "2026-07-30"
ET = ZoneInfo("America/New_York")
EXIT_ACTIONS = {"SELL", "COVER"}
# Yalniz immutable backfill kapsamasinin bitisini dogrular. Metrik-2'nin gercek
# telemetri erasi asagida dosyadaki ilk kaydin zamanindan turetilir.
TELEMETRY_START = datetime(2026, 8, 9, 18, 3, 27, tzinfo=timezone.utc)
BACKFILL_PATH = ROOT / "tools" / "olcum_backfill.json"
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


@dataclass
class Fill:
    symbol: str
    side: str
    qty: float
    price: float
    at: datetime
    order_id: str = ""


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
        return (
            self.rate is not None
            and self.rate >= 0.60
            and self.event_completeness_misses == 0
        )


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
        state["qty"] = remaining

        if remaining <= 1e-9:
            closed.append(ClosedTrade(
                symbol=fill.symbol,
                side="LONG" if state["sign"] > 0 else "SHORT",
                entry_price=state["entry_price"],
                entry_at=state["entry_at"],
                closed_at=fill.at,
                qty=state["entry_qty"],
                pnl=state["pnl"],
                exits=list(state["exits"]),
            ))
            states.pop(fill.symbol, None)

        crossed_qty = fill.qty - close_qty
        if crossed_qty > 1e-9:
            states[fill.symbol] = {
                "sign": direction, "qty": crossed_qty, "entry_qty": crossed_qty,
                "entry_price": fill.price, "entry_at": fill.at,
                "pnl": 0.0, "exits": [],
            }
    return closed


def attach_peaks(trades: list[ClosedTrade], data_client) -> int:
    """Dakika barından trade ömründeki en iyi excursion'ı ekle; bilinmeyen sayısını döndür."""
    unknown = 0
    by_symbol: dict[str, list[ClosedTrade]] = defaultdict(list)
    for trade in trades:
        by_symbol[trade.symbol].append(trade)

    for symbol, symbol_trades in by_symbol.items():
        start = min(item.entry_at for item in symbol_trades) - timedelta(minutes=1)
        end = max(item.closed_at for item in symbol_trades) + timedelta(minutes=1)
        try:
            response = data_client.get_stock_bars(StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=TimeFrame.Minute,
                start=start,
                end=end,
                sort=Sort.ASC,
            ))
            bars = list((getattr(response, "data", {}) or {}).get(symbol, []))
        except Exception as exc:
            print(f"UYARI: {symbol} dakika barları alınamadı: {exc}", file=sys.stderr)
            bars = []

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
    consumed: set[int] = set()

    for trade in period:
        matched = [
            event for event in events if _same_partial_trade(event, trade)
        ]
        if matched:
            consumed.update(id(event) for event in matched)
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

    unmatched: dict[tuple, list[dict]] = defaultdict(list)
    for event in events:
        if id(event) in consumed:
            continue
        try:
            entry = round(float(event.get("entry_price") or 0), 4)
        except (TypeError, ValueError):
            entry = 0.0
        key = (
            str(event.get("symbol", "")).upper(),
            str(event.get("side", "LONG")).upper(),
            entry,
        )
        unmatched[key].append(event)
    for episode in unmatched.values():
        result.opportunities += 1
        result.hits += int(_partial_episode_hit(episode, fills_by_id))
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


def broker_trading_days(client, start: date, end: date) -> int:
    """NYSE işlem günü sayısını broker takviminden al; erişilemezse weekday fallback."""
    try:
        return len(client.get_calendar(GetCalendarRequest(start=start, end=end)))
    except Exception as exc:
        print(
            f"UYARI: broker takvimi alınamadı, hafta içi fallback: {exc}",
            file=sys.stderr,
        )
        return trading_days(start, end)


def _metric(label: str, passed: bool, detail: str) -> None:
    print(f"[{'PASS' if passed else 'FAIL'}] {label}: {detail}")


def print_report(
    trades: list[ClosedTrade], since: datetime, unknown_peaks: int,
    phantom: int, duplicate_rows: int, unmatched_rows: int,
    partial: PartialMetric, stop_rejections: int,
    state: AuthoritativeState, broker_available: bool,
    elapsed_days: int, today: date,
) -> bool:
    period = [trade for trade in trades if trade.closed_at >= since]
    since_day = since.astimezone(ET).date()
    print(
        f"Olcum donemi: {since_day.isoformat()} -> {today.isoformat()} "
        f"(gun={elapsed_days}/30, n={len(period)}/20)"
    )
    projected = len(period) * 30 / elapsed_days if elapsed_days > 0 else 0.0
    print(f"Tempo projeksiyonu: 30 gun sonunda n={projected:.1f}")
    if projected + 1e-9 < 20:
        print(f"TEMPO UYARISI: mevcut tempoyla hedef 20'nin altinda ({projected:.1f})")

    net_pnl = sum(trade.pnl for trade in period)
    metric1_ok = broker_available and net_pnl > 0
    _metric(
        "1 PnL", metric1_ok,
        f"{len(period)} kapalı işlem, net ${net_pnl:+.2f}"
        if broker_available else "UNKNOWN (broker closed-orders okunamadi)",
    )

    partial_rate = partial.rate
    partial_detail = (
        f"{partial.hits}/{partial.opportunities} = {partial_rate:.1%}"
        if partial_rate is not None
        else "UNKNOWN (bot partial telemetrisinde firsat yok)"
    )
    completeness = (
        f"FAIL={partial.event_completeness_misses}"
        if partial.event_completeness_misses else "PASS"
    )
    partial_detail += (
        f"; legacy miss={partial.legacy_misses}; "
        f"event-completeness {completeness}"
        f"; peak UNKNOWN={unknown_peaks}"
    )
    _metric(
        "2 +%3 kademeli satış",
        partial.passed,
        partial_detail,
    )
    if partial.event_completeness_misses:
        print(
            "[FAIL] Metrik-2 veri bütünlüğü: bar/log +%3 kaniti olan "
            f"{partial.event_completeness_misses} telemetri-era isleminde "
            "partial event yok"
        )

    known = [trade for trade in period if trade.peak_pct is not None]
    never_green = [trade for trade in known if trade.peak_pct <= 0]
    never_rate = len(never_green) / len(known) if known else None
    total_losses = sum(-trade.pnl for trade in period if trade.pnl < 0)
    never_losses = sum(-trade.pnl for trade in never_green if trade.pnl < 0)
    loss_share = never_losses / total_losses if total_losses > 0 else 0.0
    metric3_ok = (
        never_rate is not None and never_rate <= 0.20 and loss_share <= 0.30
    )
    _metric(
        "3 never-green",
        metric3_ok,
        (
            f"oran {len(never_green)}/{len(known)} = {never_rate:.1%}"
            if never_rate is not None else "oran UNKNOWN"
        ) + f"; toplam zarar payı {loss_share:.1%}",
    )

    metric4_known = broker_available and state.available
    metric4_ok = metric4_known and phantom == 0 and stop_rejections == 0
    source_detail = "broker closed-orders + persistent state"
    if state.backfill_label:
        source_detail += f" + {state.backfill_label}"
    if not metric4_known:
        problems = list(state.problems)
        if not broker_available:
            problems.insert(0, "broker closed-orders okunamadi")
        metric4_detail = "UNKNOWN; " + "; ".join(problems)
    else:
        metric4_detail = (
            f"phantom={phantom} (yakın tekrar={duplicate_rows}, "
            f"broker eşleşmeyen={unmatched_rows}), "
            f"server-stop rejection={stop_rejections}, "
            f"otoriter kaynak={source_detail}, state dosyasi={len(state.files)}"
        )
    _metric(
        "4 kayıt/stop bütünlüğü",
        metric4_ok,
        metric4_detail,
    )
    invariants = invariant_counts(state.alarms, state.telemetry, state.backfill)
    print("Sistem invariant (bilgi; 4-metrik kapi tanimi degismedi):")
    print(f"  KORUMA alarmi={invariants.protection_alarms}")
    print(f"  stop-regresyon gozlemi={invariants.stop_regressions}")
    print(f"  unique-collision (40010001)={invariants.unique_collisions}")
    overall = (
        metric1_ok and partial.passed and metric3_ok and metric4_ok
    )
    print(
        f"GENEL: {'PASS' if overall else 'FAIL'} — "
        f"n={len(period)}, hedef 20 islem / 30 gun (gun={elapsed_days})"
    )
    return overall


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="R6 paper ölçüm raporu (tamamen salt okunur).",
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
        print("HATA: --since YYYY-MM-DD biçiminde olmalı.", file=sys.stderr)
        return 2
    # "Gün" borsa günü anlamındadır: ET gece yarısından itibaren ölç.
    since = datetime.combine(since_day, time.min, ET).astimezone(timezone.utc)

    key = os.getenv("ALPACA_PAPER_API_KEY")
    secret = os.getenv("ALPACA_PAPER_SECRET_KEY")
    if not key or not secret:
        print("HATA: ALPACA_PAPER_API_KEY/SECRET eksik.", file=sys.stderr)
        return 2

    trading = TradingClient(key, secret, paper=True)
    data = StockHistoricalDataClient(api_key=key, secret_key=secret)
    # Girişleri dönem başından önce olan swing'leri eşlemek için 90 gün geri git.
    broker_available = True
    try:
        orders = fetch_closed_orders(trading, since)
    except Exception as exc:
        print(f"UYARI: broker closed-orders alinamadi: {exc}", file=sys.stderr)
        orders = []
        broker_available = False
    fills = broker_fills(orders)
    trades = reconstruct_closed_trades(fills)
    period = [trade for trade in trades if trade.closed_at >= since]
    unknown = attach_peaks(period, data)
    today = datetime.now(ET).date()
    until = datetime.combine(today, time.max, ET).astimezone(timezone.utc)
    state = load_authoritative_state(args.state_dir, since, until)
    phantom, duplicates, unmatched = phantom_count(state.trade_rows, period)
    auxiliary_peaks = load_auxiliary_peak_evidence(args.log_dir, since_day)
    partial = evaluate_partial_metric(
        period, state.telemetry, fills, since, auxiliary_peaks,
        telemetry_start=state.telemetry_start,
    )
    stop_rejections = count_broker_stop_rejections(orders, since)
    elapsed_days = broker_trading_days(
        trading, since.astimezone(ET).date(), today
    )
    passed = print_report(
        period, since, unknown, phantom, duplicates, unmatched,
        partial, stop_rejections, state, broker_available, elapsed_days, today,
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
