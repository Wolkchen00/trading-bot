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
ET = ZoneInfo("America/New_York")
EXIT_ACTIONS = {"SELL", "COVER"}
STOP_REJECTION_RE = re.compile(
    r"(FAILED_NAKED|ELECTED_UNFILLED|"
    r"(?:stop|stop-loss).{0,80}(?:redded|reject)|"
    r"(?:redded|reject).{0,80}(?:stop|stop-loss))",
    re.IGNORECASE,
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
                best = max(float(bar.high) for bar in inside)
                trade.peak_pct = best / trade.entry_price - 1
            else:
                best = min(float(bar.low) for bar in inside)
                trade.peak_pct = trade.entry_price / best - 1 if best > 0 else None
                if trade.peak_pct is None:
                    unknown += 1
    return unknown


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


def count_stop_rejections(log_dir: Path, since_date: date) -> int:
    if not log_dir.exists():
        return 0
    count = 0
    date_prefix = since_date.isoformat()
    for path in log_dir.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".log", ".txt", ".jsonl"}:
            continue
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                if STOP_REJECTION_RE.search(line):
                    line_date = re.search(r"\d{4}-\d{2}-\d{2}", line)
                    if not line_date or line_date.group(0) >= date_prefix:
                        count += 1
        except OSError:
            continue
    return count


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
    stop_rejections: int, local_files: list[Path], elapsed_days: int,
) -> bool:
    period = [trade for trade in trades if trade.closed_at >= since]
    net_pnl = sum(trade.pnl for trade in period)
    _metric("1 PnL", net_pnl > 0, f"{len(period)} kapalı işlem, net ${net_pnl:+.2f}")

    eligible = [
        trade for trade in period
        if trade.peak_pct is not None and trade.peak_pct >= 0.025
    ]
    partial_hits = sum(trade.profitable_partial_at_three() for trade in eligible)
    partial_rate = partial_hits / len(eligible) if eligible else None
    _metric(
        "2 +%3 kademeli satış",
        partial_rate is not None and partial_rate >= 0.60,
        (
            f"{partial_hits}/{len(eligible)} = {partial_rate:.1%}"
            if partial_rate is not None else "UNKNOWN (uygun, barı bilinen işlem yok)"
        ) + f"; peak UNKNOWN={unknown_peaks}",
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

    _metric(
        "4 kayıt/stop bütünlüğü",
        phantom == 0 and stop_rejections == 0,
        f"phantom={phantom} (yakın tekrar={duplicate_rows}, broker eşleşmeyen={unmatched_rows}), "
        f"server-stop rejection={stop_rejections}, local JSON={len(local_files)}",
    )
    overall = (
        net_pnl > 0
        and partial_rate is not None and partial_rate >= 0.60
        and metric3_ok and phantom == 0 and stop_rejections == 0
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
            "Server stop reddi için VPS log ipucu: "
            "grep -Eai 'FAILED_NAKED|ELECTED_UNFILLED|stop.{0,80}(redded|reject)' logs/*.log"
        ),
    )
    parser.add_argument("--mode", choices=["paper"], default="paper")
    parser.add_argument(
        "--since", default=date.today().isoformat(),
        help="Başlangıç tarihi (YYYY-MM-DD; varsayılan: bugün)",
    )
    parser.add_argument(
        "--state-dir", type=Path,
        default=Path(os.getenv("OLCUM_STATE_DIR", ROOT / "state_paper")),
        help="Yerel veya VPS'ten kopyalanmış paper state dizini",
    )
    parser.add_argument(
        "--log-dir", type=Path,
        default=Path(os.getenv("OLCUM_LOG_DIR", ROOT / "logs")),
        help="Server-stop reddi aranacak log dizini",
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
    orders = fetch_closed_orders(trading, since)
    trades = reconstruct_closed_trades(broker_fills(orders))
    period = [trade for trade in trades if trade.closed_at >= since]
    unknown = attach_peaks(period, data)
    local_rows, local_files = load_local_exit_rows(args.state_dir, since)
    phantom, duplicates, unmatched = phantom_count(local_rows, period)
    stop_rejections = count_stop_rejections(args.log_dir, since_day)
    elapsed_days = broker_trading_days(
        trading, since.astimezone(ET).date(), datetime.now(ET).date()
    )
    passed = print_report(
        period, since, unknown, phantom, duplicates, unmatched,
        stop_rejections, local_files, elapsed_days,
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
