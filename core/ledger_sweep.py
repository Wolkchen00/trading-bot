"""Broker dolumlariyla fill ledger'i periyodik ve best-effort uzlastirir.

Bu modul yalniz muhasebe telemetrisidir. Emir gondermez, iptal etmez ve
trading kararlarina veri tasimaz.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import time
from typing import Any, Callable, Iterable

from alpaca.common.enums import Sort
from alpaca.trading.enums import QueryOrderStatus
from alpaca.trading.requests import GetOrdersRequest

from core.fill_ledger import (
    canonical_fill_key,
    order_fill_key,
    read_fills,
    record_fill,
)
from core.sweep_watermark import SweepWatermark
from utils.logger import logger


def _field(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _enum_text(value: Any) -> str:
    return str(getattr(value, "value", value) or "").split(".")[-1].upper()


def _as_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _positive_decimal(value: Any) -> Decimal | None:
    try:
        number = abs(Decimal(str(value or 0)))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number > 0 else None


def _flatten_orders(orders: Iterable[Any]) -> list[Any]:
    result: list[Any] = []
    seen: set[str] = set()

    def visit(order: Any) -> None:
        marker = str(_field(order, "id", "") or f"obj:{id(order)}")
        if marker in seen:
            return
        seen.add(marker)
        result.append(order)
        for leg in _field(order, "legs", None) or []:
            visit(leg)

    for order in orders or []:
        visit(order)
    return result


class LedgerSweep:
    """Son broker dolumlarini tamamlayan, idempotent defter emniyet agi."""

    DEFAULT_WINDOW_HOURS = 24.0
    DEFAULT_INTERVAL_MINUTES = 15.0

    def __init__(
        self,
        client: Any,
        config: dict | None = None,
        *,
        now_fn: Callable[[], datetime] | None = None,
        monotonic_fn: Callable[[], float] | None = None,
        watermark: Any = None,
        bootstrap_from: datetime | None = None,
    ) -> None:
        config = config or {}
        self.client = client
        self.window_hours = max(
            0.01,
            float(config.get(
                "ledger_sweep_window_hours", self.DEFAULT_WINDOW_HOURS
            )),
        )
        self.interval_minutes = max(
            0.01,
            float(config.get(
                "ledger_sweep_interval_minutes", self.DEFAULT_INTERVAL_MINUTES
            )),
        )
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._monotonic_fn = monotonic_fn or time.monotonic
        self._last_run_monotonic = 0.0

        # R17: SABIT PENCERE YERINE TAAHHUT EDILMIS YUKSEK-SU ISARETI.
        # Sabit 24s (ya da 72s) pencere, o sureden uzun her kesintide
        # KALICI defter deligi birakiyordu; pencereyi buyutmek 73 saatlik
        # kesintide ayni deligi acar. Isaret yalniz TAM BASARIDA ilerler.
        self.watermark = watermark if watermark is not None else SweepWatermark(
            now_fn=self._now_fn
        )
        # Ilk acilista isaret yoksa nereden baslanacagi (olcum epoch'u ya da
        # dogrulanmis backfill siniri). None ise min pencere kullanilir.
        # Taban verilmediyse DEFTERDEKI SON DOLUMDAN turet: dogrulanmis
        # backfill siniri odur. Ondan sonrasi potansiyel olarak eksiktir.
        self.bootstrap_from = bootstrap_from or self._defterden_taban()
        self.last_plan: dict = {}

    @staticmethod
    def _defterden_taban() -> datetime | None:
        """Defterdeki EN SON dolum zamani , dogrulanmis backfill siniri.

        Nemotron bagimsiz incelemesi: taban verilmeyince plan_window
        sessizce 24 saatlik pencereye dusuyordu ve bot daha uzun kapali
        kaldiysa aradaki dolumlar KALICI olarak kaciyordu.

        Defterin son kaydi dogal sinirdir: ondan oncesini zaten biliyoruz,
        ondan sonrasi supurulmeli.
        """
        try:
            satirlar = read_fills() or []
            zamanlar = []
            for s in satirlar:
                ts = _as_utc(s.get("ts_utc"))
                if ts is not None:
                    zamanlar.append(ts)
            return max(zamanlar) if zamanlar else None
        except Exception:
            return None

    def _activity_pages(self, cutoff: datetime, until: datetime) -> list[Any]:
        """Sayfalari cek. EKSIK kalirsa `self._pages_complete` False olur.

        R17: sayfa ortasi hata sessizce kismi sonuc dondururse, isaret
        ilerletildiginde alinmamis dolumlar SONSUZA DEK atlanir.
        """
        self._pages_complete = True
        custom = getattr(self.client, "get_account_activities", None)
        if callable(custom):
            try:
                rows = custom(
                    activity_type="FILL", after=cutoff, until=until
                )
            except TypeError:
                rows = custom("FILL", after=cutoff, until=until)
            return list(rows or [])

        raw_get = getattr(self.client, "get", None)
        if not callable(raw_get):
            raise AttributeError("account activities istemcide yok")

        result: list[Any] = []
        page_token = None
        for _ in range(100):
            params = {
                "after": cutoff.isoformat(),
                "until": until.isoformat(),
                "direction": "asc",
                "page_size": 100,
            }
            if page_token:
                params["page_token"] = page_token
            try:
                response = raw_get("/account/activities/FILL", params)
            except Exception:
                # SAYFA ORTASI HATA: elimizdeki kismi sonucu kullanabiliriz
                # ama isaret ILERLEMEMELI, yoksa alinmamis sayfalardaki
                # dolumlar sonsuza dek atlanir.
                self._pages_complete = False
                raise
            if isinstance(response, dict):
                page = response.get("activities", [])
            else:
                page = response
            page = list(page or [])
            result.extend(page)
            if len(page) < 100:
                break
            next_token = str(_field(page[-1], "id", "") or "").strip()
            if not next_token or next_token == page_token:
                break
            page_token = next_token
        else:
            # 100 sayfa tavanina dayandik , daha fazlasi olabilir.
            self._pages_complete = False
        return result

    def _filled_orders(self, cutoff: datetime, until: datetime) -> list[Any]:
        request = GetOrdersRequest(
            status=QueryOrderStatus.CLOSED,
            limit=500,
            after=cutoff,
            until=until,
            direction=Sort.ASC,
            nested=True,
        )
        return _flatten_orders(self.client.get_orders(request))

    def _broker_rows(self, cutoff: datetime, until: datetime) -> list[dict]:
        try:
            source_rows = self._activity_pages(cutoff, until)
            from_activities = True
        except Exception as activity_exc:
            logger.debug(
                "  Ledger sweep activities okunamadi; filled order fallback: "
                f"{activity_exc}"
            )
            source_rows = self._filled_orders(cutoff, until)
            from_activities = False

        normalized: list[dict] = []
        for item in source_rows:
            qty = _positive_decimal(
                _field(item, "qty" if from_activities else "filled_qty", 0)
            )
            price = _positive_decimal(
                _field(item, "price" if from_activities else "filled_avg_price", 0)
            )
            stamp = _as_utc(
                _field(item, "transaction_time", None)
                if from_activities
                else (
                    _field(item, "filled_at", None)
                    or _field(item, "updated_at", None)
                )
            )
            symbol = str(_field(item, "symbol", "") or "").strip().upper()
            side = _enum_text(_field(item, "side", ""))
            order_id = str(_field(item, "order_id", "") or "").strip()
            if not order_id and not from_activities:
                order_id = str(_field(item, "id", "") or "").strip()
            execution_id = ""
            if from_activities:
                execution_id = str(
                    _field(item, "id", "")
                    or _field(item, "activity_id", "")
                    or ""
                ).strip()
            else:
                for name in ("execution_id", "activity_id", "fill_id"):
                    execution_id = str(_field(item, name, "") or "").strip()
                    if execution_id:
                        break
            if qty is None:
                logger.debug(
                    "  Ledger sweep normal broker kaydi atlandi "
                    "(neden=qty_yok_veya_sifir)"
                )
                continue

            anomaly_fields = []
            if not order_id:
                anomaly_fields.append("order_id")
            if not symbol:
                anomaly_fields.append("symbol")
            if side not in {"BUY", "SELL"}:
                anomaly_fields.append("side")
            if stamp is None:
                anomaly_fields.append("timestamp")
            if anomaly_fields:
                logger.warning(
                    "  LEDGER SWEEP: dolumu olan anomalili broker fill atlandi "
                    f"(eksik/gecersiz alanlar={','.join(anomaly_fields)}; "
                    f"order_id={order_id or 'bilinmiyor'}, "
                    f"symbol={symbol or 'bilinmiyor'})"
                )
                continue
            if price is None:
                logger.debug(
                    "  Ledger sweep normal broker kaydi atlandi "
                    f"(neden=price_yok_veya_sifir, order_id={order_id})"
                )
                continue
            if stamp < cutoff or stamp > until:
                logger.debug(
                    "  Ledger sweep normal broker kaydi atlandi "
                    f"(neden=pencere_disinda, order_id={order_id})"
                )
                continue
            normalized.append({
                "execution_id": execution_id or None,
                "order_id": order_id,
                "client_order_id": (
                    str(_field(item, "client_order_id", "") or "").strip()
                    or None
                ),
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "price": price,
                "ts_utc": stamp,
            })
        return normalized

    @staticmethod
    def _existing_index(rows: Iterable[dict]) -> tuple[set[str], Counter, dict]:
        execution_ids: set[str] = set()
        degraded_fills: Counter = Counter()
        order_totals: dict[tuple[str, str, str], Decimal] = defaultdict(Decimal)
        for row in rows:
            execution_id = str(row.get("execution_id") or "").strip()
            if execution_id:
                execution_ids.add(execution_id)
            try:
                order_id = str(row.get("order_id") or "").strip()
                symbol = str(row.get("symbol") or "").strip().upper()
                side = str(row.get("side") or "").strip().upper()
                qty = _positive_decimal(row.get("qty"))
                if not order_id or not symbol or side not in {"BUY", "SELL"} or qty is None:
                    continue
                order_totals[(order_id, symbol, side)] += qty
                if not execution_id:
                    degraded_fills[(str(order_id),) + canonical_fill_key(
                        symbol, side, qty, row.get("price")
                    )] += 1
            except (TypeError, ValueError):
                continue
        return execution_ids, degraded_fills, order_totals

    def run(self, now: datetime | None = None) -> dict:
        """Bir mutabakat turu kos; hata trading akisina asla sizmaz."""
        self._last_run_monotonic = self._monotonic_fn()
        until = _as_utc(now or self._now_fn()) or datetime.now(timezone.utc)

        # R17: araligi ISARET belirler, sabit pencere degil.
        cutoff, plan_durum, eksiksiz = self.watermark.plan_window(
            until,
            bootstrap_from=self.bootstrap_from,
            min_window_hours=self.window_hours,
        )
        self.last_plan = {
            "cutoff": cutoff.isoformat(),
            "until": until.isoformat(),
            "durum": plan_durum,
            "eksiksiz_kurtarilabilir": eksiksiz,
        }
        self._pages_complete = True
        writes_ok = True
        try:
            broker_rows = self._broker_rows(cutoff, until)
            ledger_rows = read_fills()
            execution_ids, degraded_fills, order_totals = self._existing_index(
                ledger_rows
            )

            broker_totals: dict[tuple[str, str, str], Decimal] = defaultdict(Decimal)
            for fill in broker_rows:
                group = (fill["order_id"], fill["symbol"], fill["side"])
                broker_totals[group] += fill["qty"]

            added = 0
            symbols: Counter = Counter()
            for fill in broker_rows:
                group = (fill["order_id"], fill["symbol"], fill["side"])
                broker_total = broker_totals[group]
                ledger_total = order_totals.get(group, Decimal("0"))
                totals_match = (
                    ledger_total > 0
                    and order_fill_key(*group, ledger_total)
                    == order_fill_key(*group, broker_total)
                )
                if totals_match or ledger_total > broker_total:
                    continue
                execution_id = fill["execution_id"]
                if execution_id and execution_id in execution_ids:
                    continue
                fill_key = (str(fill["order_id"]),) + canonical_fill_key(
                    fill["symbol"], fill["side"], fill["qty"], fill["price"]
                )
                if degraded_fills[fill_key] > 0:
                    degraded_fills[fill_key] -= 1
                    continue

                was_added = record_fill(
                    symbol=fill["symbol"],
                    side=fill["side"],
                    qty=fill["qty"],
                    price=fill["price"],
                    pnl_usd=None,
                    provenance="UNKNOWN",
                    execution_id=execution_id,
                    order_id=fill["order_id"],
                    client_order_id=fill["client_order_id"],
                    episode_id=None,
                    ts_utc=fill["ts_utc"],
                    source="ledger_sweep",
                    reconcile_order_qty=broker_total,
                )
                if not was_added:
                    # Defter yazimi BASARISIZ: isaret ILERLEMEMELI, yoksa bu
                    # dolum bir daha hic taranmaz.
                    writes_ok = False
                    continue
                added += 1
                symbols[fill["symbol"]] += 1
                order_totals[group] += fill["qty"]
                if execution_id:
                    execution_ids.add(execution_id)
                logger.warning(
                    "  LEDGER SWEEP ONARIM: 1 kayip dolum eklendi | "
                    f"{fill['symbol']} {fill['side']} {fill['qty']} @ {fill['price']} | "
                    f"order_id={fill['order_id']}"
                )

            if added:
                symbol_text = ", ".join(
                    f"{symbol}({count})" for symbol, count in sorted(symbols.items())
                )
                logger.warning(
                    f"  LEDGER SWEEP OZET: {added} kayip dolum eklendi | "
                    f"semboller: {symbol_text}"
                )
            # R17: ISARETI YALNIZ TAM BASARIDA ILERLET.
            # Ortusme zararsizdir (dedupe var); BOSLUK kalicidir.
            ilerledi = self.watermark.commit(
                until,
                pages_complete=bool(self._pages_complete),
                writes_ok=bool(writes_ok),
            )
            return {
                "seen": len(broker_rows),
                "added": added,
                "symbols": dict(symbols),
                "error": None,
                "plan": dict(self.last_plan),
                "pages_complete": bool(self._pages_complete),
                "writes_ok": bool(writes_ok),
                "watermark_advanced": bool(ilerledi),
                "degraded": not eksiksiz,
            }
        except Exception as exc:
            logger.warning(
                "  LEDGER SWEEP HATASI: broker/defter mutabakati yapilamadi; "
                f"bot akisi devam ediyor: {exc}"
            )
            # Hata halinde isaret ILERLEMEZ , bir sonraki kosu ayni araligi
            # yeniden tarar.
            return {
                "seen": 0,
                "added": 0,
                "symbols": {},
                "error": str(exc),
                "plan": dict(self.last_plan),
                "pages_complete": bool(getattr(self, "_pages_complete", False)),
                "writes_ok": False,
                "watermark_advanced": False,
                "degraded": True,
            }

    def maybe_run(self) -> dict | None:
        """Config araligindan daha sik broker okumasi yapma."""
        now = self._monotonic_fn()
        if (
            self._last_run_monotonic
            and now - self._last_run_monotonic < self.interval_minutes * 60
        ):
            return None
        return self.run()
