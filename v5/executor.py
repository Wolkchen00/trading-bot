"""Crash-safe, idempotent order execution for the version 5 engine."""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN
import math
from pathlib import Path

from .broker import AmbiguousSubmit, BrokerError, BrokerRejected, Order, is_terminal
from .clock import Window
from .state import StateCorrupt, atomic_write_json, read_json, session_record_path
from .strategy import READY


NEW = "NEW"
NOT_READY = "NOT_READY"
NO_TRADE = "NO_TRADE"
PLANNED = "PLANNED"
SUBMITTED = "SUBMITTED"
RECONCILING = "RECONCILING"
DONE = "DONE"
CAPPED = "CAPPED"
NETTED = "NETTED"
PARTIAL = "PARTIAL"
FAILED = "FAILED"
NOT_SUBMITTED = "NOT_SUBMITTED"
UNRESOLVED = "UNRESOLVED"
MISSED = "MISSED"
HOLD_NOT_READY = "HOLD_NOT_READY"
DRY_RUN = "DRY_RUN"

FINAL_STATUSES = {
    NO_TRADE, DONE, CAPPED, NETTED, FAILED, NOT_SUBMITTED, MISSED,
    HOLD_NOT_READY, DRY_RUN,
}


class SnapshotInvalid(ValueError):
    pass


@dataclass(frozen=True)
class Snapshot:
    equity: float
    cash: float
    product_qty: str
    product_qty_available: str
    product_price: float | None
    product_weight: float
    foreign_positions: tuple[str, ...]
    foreign_orders: tuple[str, ...]
    foreign_product_orders: tuple[str, ...]
    own_open_orders: tuple[Order, ...]
    # Owner-held symbols (plan Revision 10). `equity` above is the ENGINE's equity: the account equity minus
    # the owner's own holdings; weights and order sizes are relative to it.
    account_equity: float | None = None
    owner_value: float = 0.0
    owner_positions: tuple[str, ...] = ()
    owner_orders: tuple[str, ...] = ()
    spendable: float | None = None

    @property
    def foreign(self):
        return self.foreign_positions + self.foreign_orders


@dataclass(frozen=True)
class OrderPlan:
    kind: str
    side: str | None
    qty: str | None
    notional: str | None
    desired_usd: float
    desired_qty: str | None
    capped: bool


def _dec(value: float) -> Decimal:
    return Decimal(repr(value))


def _text(value: Decimal) -> str:
    result = format(value, "f")
    if "." in result:
        result = result.rstrip("0").rstrip(".")
    return result or "0"


def take_snapshot(broker, params, own_client_ids, owner_symbols=()) -> Snapshot:
    owner_symbols = {str(symbol).upper() for symbol in owner_symbols}
    account = broker.get_account()
    positions = broker.get_positions()
    orders = broker.get_open_orders()
    if (
        account.status != "ACTIVE" or account.trading_blocked or account.account_blocked
        or not math.isfinite(account.equity) or account.equity <= 0
        or not math.isfinite(account.cash)
    ):
        raise SnapshotInvalid("SNAPSHOT_INVALID")
    product = None
    foreign_positions = []
    owner_positions = []
    owner_value = 0.0
    negative = None
    for position in positions:
        if position.symbol.upper() in owner_symbols and position.symbol != params.product_symbol:
            # The owner's own holding: never foreign, never traded, but its value must be known.
            if not math.isfinite(position.market_value):
                raise SnapshotInvalid("SNAPSHOT_INVALID")
            owner_value += position.market_value
            owner_positions.append(f"position:{position.symbol}:{position.qty}:{position.market_value}")
            continue
        if position.symbol == params.product_symbol:
            product = position
            try:
                if Decimal(position.qty) < 0:
                    negative = f"position:{position.symbol}:{position.qty}:{position.market_value}"
                    foreign_positions.append(negative)
            except (InvalidOperation, TypeError) as exc:
                raise SnapshotInvalid("SNAPSHOT_INVALID") from exc
        elif not math.isfinite(position.market_value) or abs(position.market_value) >= 1.0:
            # A position that cannot be valued is never ignored as dust.
            foreign_positions.append(
                f"position:{position.symbol}:{position.qty}:{position.market_value}"
            )
    engine_equity = account.equity - owner_value
    if not math.isfinite(engine_equity) or engine_equity <= 0:
        raise SnapshotInvalid("SNAPSHOT_INVALID")
    if product is None:
        qty, available, price, weight = "0", "0", None, 0.0
    else:
        qty, available, price = product.qty, product.qty_available, product.current_price
        weight = product.market_value / engine_equity
    if not math.isfinite(weight) or not 0 <= weight <= 1.05:
        raise SnapshotInvalid("SNAPSHOT_INVALID")
    # Money the engine may spend: an open order of the owner reserves buying power the cash figure still shows.
    buying_power = getattr(account, "buying_power", account.cash)
    spendable = min(account.cash, buying_power) if math.isfinite(buying_power) else account.cash
    own, foreign_orders, foreign_product, owner_orders = [], [], [], []
    for order in orders:
        if order.symbol == params.product_symbol and order.client_order_id in own_client_ids:
            own.append(order)
        elif order.symbol.upper() in owner_symbols and order.symbol != params.product_symbol:
            owner_orders.append(f"order:{order.symbol}:{order.client_order_id}:{order.id}")
        else:
            item = f"order:{order.symbol}:{order.client_order_id}:{order.id}"
            foreign_orders.append(item)
            if order.symbol == params.product_symbol:
                foreign_product.append(item)
    if negative is not None:
        foreign_product.append(negative)
    return Snapshot(
        float(engine_equity), float(account.cash), qty, available,
        None if price is None else float(price), float(weight),
        tuple(foreign_positions), tuple(foreign_orders), tuple(foreign_product), tuple(own),
        account_equity=float(account.equity), owner_value=float(owner_value),
        owner_positions=tuple(owner_positions), owner_orders=tuple(owner_orders),
        spendable=float(spendable),
    )


def client_order_id(mode, session_date, purpose, attempt) -> str:
    if mode not in {"paper", "live"} or purpose not in {"dec", "flr"}:
        raise ValueError("invalid order identity")
    if isinstance(attempt, bool) or attempt not in {1, 2, 3} or not isinstance(session_date, date):
        raise ValueError("invalid order identity")
    return f"v5-{mode}-{session_date:%Y%m%d}-{purpose}-{attempt}"


def plan_order(snapshot, total_new, params) -> OrderPlan:
    spendable = snapshot.cash if snapshot.spendable is None else snapshot.spendable
    equity, cash = _dec(snapshot.equity), _dec(spendable)
    weight, target = _dec(snapshot.product_weight), _dec(total_new)
    delta = target - weight
    desired = abs(delta) * equity
    position = Decimal(snapshot.product_qty)
    if total_new <= 1e-12 and position > 0:
        return OrderPlan(
            "sell_all", "sell", snapshot.product_qty, None, float(desired),
            snapshot.product_qty, False,
        )
    if desired < Decimal("1.00"):
        return OrderPlan("none", None, None, None, float(desired), None, False)
    if delta > 0:
        requested, limit = delta * equity, cash * Decimal("0.995")
        notional = min(requested, limit).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        capped = limit < requested
        if notional < Decimal("1.00"):
            return OrderPlan("none", None, None, None, float(desired), None, True)
        return OrderPlan("buy", "buy", None, _text(notional), float(desired), None, capped)
    if delta < 0:
        desired_qty = min(position, max(Decimal(0), position * (weight - target) / weight))
        qty = desired_qty.quantize(Decimal("0.000000001"), rounding=ROUND_DOWN)
        if qty <= 0:
            return OrderPlan("none", None, None, None, float(desired), _text(desired_qty), True)
        return OrderPlan(
            "sell", "sell", _text(qty), None, float(desired), _text(desired_qty), False
        )
    return OrderPlan("none", None, None, None, float(desired), None, False)


def allocate(core_b, ovl_b, core_new, ovl_new, theta, equity):
    dc, do = core_new - core_b, ovl_new - ovl_b
    net = dc + do
    if dc * do < 0:
        if abs(dc) >= abs(do):
            ovl_x = ovl_new
            core_x = core_b - do + theta * net
        else:
            core_x = core_new
            ovl_x = ovl_b - dc + theta * net
    else:
        core_x = core_b + theta * dc
        ovl_x = ovl_b + theta * do
    core_x, ovl_x = max(0.0, core_x), max(0.0, ovl_x)
    if ovl_x * equity < 1.0:
        core_x, ovl_x = core_x + ovl_x, 0.0
    elif core_x * equity < 1.0:
        core_x, ovl_x = 0.0, ovl_x + core_x
    total = core_x + ovl_x
    return core_x, ovl_x, ovl_x / total if total > 0 else 0.0


def _snapshot_data(snapshot, fraction=None):
    result = {
        "equity": snapshot.equity, "cash": snapshot.cash,
        "product_qty": snapshot.product_qty,
        "product_qty_available": snapshot.product_qty_available,
        "product_price": snapshot.product_price,
        "product_weight": snapshot.product_weight,
        "account_equity": snapshot.account_equity,
        "owner_value": snapshot.owner_value,
        "spendable": snapshot.spendable,
    }
    if fraction is not None:
        result["overlay_fraction_committed"] = fraction
    return result


class SessionRecord:
    def __init__(self, path, data):
        self.path, self._data = Path(path), data

    @staticmethod
    def _path(state_dir, session_date, purpose, dry_run):
        base = session_record_path(state_dir, session_date, dry_run)
        if purpose == "dec":
            return base
        if purpose == "flr":
            return base.with_name(f"{session_date.isoformat()}.flr.json")
        raise ValueError("invalid purpose")

    @classmethod
    def load(cls, state_dir, session_date, purpose="dec", dry_run=False):
        path = cls._path(state_dir, session_date, purpose, dry_run)
        obj = read_json(path, default=None)
        if obj is None:
            return None
        if (
            not isinstance(obj, dict) or obj.get("session") != session_date.isoformat()
            or obj.get("purpose") != purpose or obj.get("dry_run") is not dry_run
        ):
            raise StateCorrupt(path)
        return cls(path, obj)

    @classmethod
    def new(cls, state_dir, session_date, mode, purpose="dec", dry_run=False):
        path = cls._path(state_dir, session_date, purpose, dry_run)
        existing = cls.load(state_dir, session_date, purpose, dry_run)
        if existing is not None:
            return existing
        obj = {
            "session": session_date.isoformat(), "mode": mode, "purpose": purpose,
            "dry_run": bool(dry_run), "status": NEW, "closed": False,
            "status_detail": "", "inputs": None, "decision": None, "target": None,
            "before": None, "intents": [], "overlay_fraction_committed": None,
            "after": None, "residual": None, "hold": None, "events": [],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        record = cls(path, obj)
        record.save()
        return record

    def save(self):
        self._data["updated_at"] = datetime.now(timezone.utc).isoformat()
        atomic_write_json(self.path, self._data)

    @property
    def data(self):
        return self._data

    @property
    def status(self):
        return self._data["status"]

    @property
    def is_final(self):
        return self.status in FINAL_STATUSES or (
            self.status == PARTIAL and self._data.get("closed") is True
        )


class Executor:
    def __init__(self, broker, settings, state_dir, params, now_fn, sleep_fn):
        self.broker, self.settings = broker, settings
        self.state_dir, self.params = Path(state_dir), params
        self.now_fn, self.sleep_fn = now_fn, sleep_fn
        self._cache = None
        self._blocked = None

    def _now(self):
        return self.now_fn().isoformat()

    def _owner_symbols(self):
        return tuple(getattr(self.settings, "owner_symbols", ()) or ())

    def _event(self, record, what):
        events = record.data["events"]
        if events and events[-1].get("what") == what:
            events[-1]["last_time"] = self._now()
            events[-1]["count"] = events[-1].get("count", 1) + 1
        else:
            events.append({"time": self._now(), "what": what})
        record.save()
        if self._cache is not None and not record.data.get("dry_run"):
            self._cache[record.path] = record.data
            getattr(self, "_stamps", {}).pop(record.path, None)     # written by us: read it back from disk next time

    def _records(self, refresh=False):
        if self._cache is not None and not refresh:
            return list(self._cache.items())
        folder = self.state_dir / "decisions"
        values, stamps = {}, {}
        known = getattr(self, "_stamps", {})
        if folder.exists():
            for path in sorted(folder.glob("*.json")):
                try:
                    stat = path.stat()
                except FileNotFoundError:
                    continue
                stamp = (stat.st_mtime_ns, stat.st_size)
                if self._cache is not None and known.get(path) == stamp and path in self._cache:
                    obj = self._cache[path]                 # unchanged on disk since it was last read
                else:
                    obj = read_json(path)
                    if not isinstance(obj, dict) or not isinstance(obj.get("intents"), list):
                        raise StateCorrupt(path)
                values[path], stamps[path] = obj, stamp
        self._cache, self._stamps = values, stamps
        return list(values.items())

    @staticmethod
    def _final_data(obj):
        return obj.get("status") in FINAL_STATUSES or (
            obj.get("status") == PARTIAL and obj.get("closed") is True
        )

    @staticmethod
    def _ids(records):
        result = set()
        for _, obj in records:
            if obj.get("dry_run") or Executor._final_data(obj):
                continue
            for intent in obj["intents"]:
                if isinstance(intent, dict) and isinstance(intent.get("client_order_id"), str):
                    result.add(intent["client_order_id"])
        return result

    def own_client_ids(self):
        return self._ids(self._records(refresh=True))

    def _cached_ids(self):
        return self._ids(self._records())

    def has_unresolved(self):
        return any(
            not obj.get("dry_run") and obj.get("status") == UNRESOLVED
            for _, obj in self._records(refresh=True)
        )

    def unresolved_records(self):
        """Every strategy or floor record whose order outcome is still unknown (never a dry-run record)."""
        return [
            SessionRecord(path, obj)
            for path, obj in self._records(refresh=True)
            if not obj.get("dry_run") and obj.get("status") == UNRESOLVED
        ]

    def open_records(self):
        """Every strategy or floor record that has a target and is not final yet (never a dry-run record): its
        order may still exist and its bookkeeping is not done."""
        return [
            SessionRecord(path, obj)
            for path, obj in self._records(refresh=True)
            if not obj.get("dry_run") and obj.get("target") is not None and not self._final_data(obj)
        ]

    def _other_unresolved(self, record):
        # Any other unfinished chain blocks a new one, not only one whose status is UNRESOLVED: two chains that
        # trade the same position at once cannot both be booked.
        return any(
            path != record.path and not obj.get("dry_run")
            and obj.get("target") is not None and not self._final_data(obj)
            for path, obj in self._records()
        )

    def load_overlay_fraction(self):
        path = self.state_dir / "overlay.json"
        obj = read_json(path, default=None)
        if obj is None:
            return 0.0
        try:
            value = obj["fraction"]
            if isinstance(value, bool):
                raise ValueError
            result = float(value)
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise StateCorrupt(path) from exc
        if not math.isfinite(result) or not 0 <= result <= 1:
            raise StateCorrupt(path)
        return result

    def _commit(self, record, fraction):
        atomic_write_json(
            self.state_dir / "overlay.json",
            {"fraction": float(fraction), "session": record.data["session"], "updated_at": self._now()},
        )

    @staticmethod
    def _decision(decision):
        return {field.name: getattr(decision, field.name) for field in fields(decision)}

    def mark_not_ready(self, record, code, detail=""):
        if record.data.get("target") is not None or record.data.get("intents"):
            raise ValueError("execution already attached")
        record.data["status"] = NOT_READY
        record.data["status_detail"] = code if not detail else f"{code}:{detail}"
        record.data["inputs"] = {"code": code, "detail": detail}
        self._event(record, NOT_READY)
        return record

    def finalize(self, record, status, detail="", inputs=None, decision=None):
        if status not in {NO_TRADE, MISSED, HOLD_NOT_READY}:
            raise ValueError("invalid final status")
        if record.data.get("target") is not None or record.data.get("intents") or record.is_final:
            raise ValueError("record has execution state")
        record.data.update(status=status, closed=True, status_detail=detail)
        if inputs is not None:
            record.data["inputs"] = inputs.to_bundle() if hasattr(inputs, "to_bundle") else inputs
        if decision is not None:
            record.data["decision"] = self._decision(decision)
        self._event(record, status)
        return record

    def attach_decision(self, record, inputs, decision):
        if (
            record.data.get("purpose") != "dec" or decision.status != READY or not decision.trade
            or record.data.get("target") is not None
        ):
            raise ValueError("decision cannot be attached")
        record.data["inputs"] = inputs.to_bundle()
        record.data["decision"] = self._decision(decision)
        record.data["target"] = {
            "core_new": decision.core_new, "ovl_new": decision.ovl_new,
            "total_new": decision.total_new,
        }
        self._event(record, "DECISION_ATTACHED")
        return record

    def attach_floor(self, record):
        if record.data.get("purpose") != "flr" or record.data.get("target") is not None:
            raise ValueError("floor cannot be attached")
        record.data["target"] = {"core_new": 0.0, "ovl_new": 0.0, "total_new": 0.0}
        self._event(record, "FLOOR_ATTACHED")
        return record

    def _error(self, record, exc):
        record.data["hold"] = "SNAPSHOT_INVALID" if isinstance(exc, SnapshotInvalid) else "BROKER_DOWN"
        self._event(record, record.data["hold"])

    def _final(self, record, status, detail=""):
        record.data.update(status=status, closed=True, status_detail=detail, hold=None)
        self._event(record, status)
        return record

    @staticmethod
    def _attempt_count(record):
        return sum(bool(item.get("client_order_id")) for item in record.data["intents"])

    @staticmethod
    def _any_fill(record):
        for intent in record.data["intents"]:
            try:
                if Decimal(str(intent.get("filled_qty") or "0")) > 0:
                    return True
            except InvalidOperation:
                pass
        return False

    def _close(self, record, detail=""):
        if self._any_fill(record):
            record.data.update(status=PARTIAL, closed=True, status_detail=detail)
            self._event(record, "PARTIAL_CLOSED")
            return record
        return self._final(record, FAILED, detail)

    def _intent(self, record, plan, snapshot, attempt, fraction):
        target, weight = record.data["target"], snapshot.product_weight
        return {
            "client_order_id": client_order_id(
                record.data["mode"], date.fromisoformat(record.data["session"]),
                record.data["purpose"], attempt,
            ),
            "purpose": record.data["purpose"], "attempt": attempt,
            "kind": plan.kind, "side": plan.side, "qty": plan.qty,
            "notional": plan.notional, "desired_usd": plan.desired_usd,
            "desired_qty": plan.desired_qty, "capped": plan.capped,
            "before_qty": snapshot.product_qty, "w_before": weight,
            "equity_before": snapshot.equity,
            "core_b": weight * (1 - fraction), "ovl_b": weight * fraction,
            "core_new": target["core_new"], "ovl_new": target["ovl_new"],
            "submit_started": False, "submit_count": 0,
            "broker_order_id": None, "order_status": None, "filled_qty": None,
            "filled_avg_price": None, "fill_ratio": None, "theta": None,
            "fraction_after": None, "outcome": None, "error": None,
        }

    @staticmethod
    def _allocation(intent, theta):
        return allocate(
            intent["core_b"], intent["ovl_b"], intent["core_new"], intent["ovl_new"],
            theta, intent["equity_before"],
        )

    def _no_order(self, record, intent, status, theta, snapshot):
        _, _, fraction = self._allocation(intent, theta)
        intent.update(theta=theta, fill_ratio=0.0, fraction_after=fraction, outcome=status)
        record.data.update(
            overlay_fraction_committed=fraction,
            after=_snapshot_data(snapshot),
            residual=snapshot.product_weight - record.data["target"]["total_new"],
            status=status, closed=True,
        )
        self._event(record, status)
        if not record.data["dry_run"]:
            self._commit(record, fraction)
        return record

    def _inspect(self, record):
        snapshot = take_snapshot(self.broker, self.params, self._cached_ids(), self._owner_symbols())
        blocked = snapshot.foreign if record.data["purpose"] == "dec" else snapshot.foreign_product_orders
        if blocked:
            record.data["hold"] = "FOREIGN_ASSET"
            record.data["status_detail"] = "|".join(blocked)
            self._event(record, "FOREIGN_ASSET")
            return None
        if record.data.get("hold") is not None:
            record.data["hold"], record.data["status_detail"] = None, ""
            self._event(record, "HOLD_CLEARED")
        for order in snapshot.own_open_orders:
            self._event(record, f"CANCEL:{order.client_order_id}")
            self.broker.cancel_order(order.id)
            current = order
            for index in range(20):
                current = self.broker.get_order(order.id)
                if is_terminal(current.status):
                    break
                if index != 19:
                    self.sleep_fn(1)
            if not is_terminal(current.status):
                return None
        return take_snapshot(self.broker, self.params, self._cached_ids(), self._owner_symbols())

    def _start(self, record, window, replan=False):
        if self.now_fn() >= window.submit_until:
            if self._any_fill(record):
                return self._close(record, record.data.get("hold") or "")
            return self._final(
                record, NOT_SUBMITTED, record.data.get("hold") or record.data.get("status_detail", "")
            )
        if self._blocked:
            # The caller forbids a new order right now (clock doubt, market closed, pause, a rail ...).
            record.data["hold"] = self._blocked
            self._event(record, "HOLD:" + str(self._blocked))
            return record
        if self._other_unresolved(record):
            record.data["hold"] = "UNRESOLVED"
            self._event(record, UNRESOLVED)
            return record
        snapshot = self._inspect(record)
        if snapshot is None:
            return record
        fraction = self.load_overlay_fraction()
        if record.data["before"] is None:
            record.data["before"] = _snapshot_data(snapshot, fraction)
            record.data["overlay_fraction_committed"] = fraction
            self._event(record, "SNAPSHOT")
        target = record.data["target"]
        plan = plan_order(snapshot, target["total_new"], self.params)
        if replan:
            attempt = record.data["intents"][-1]["attempt"]
        else:
            attempt = self._attempt_count(record) + 1
        if attempt > 3:
            return self._close(record, "ATTEMPTS_EXHAUSTED")
        intent = self._intent(record, plan, snapshot, attempt, fraction)
        if replan:
            record.data["intents"][-1] = intent
        else:
            record.data["intents"].append(intent)
        if record.data["dry_run"]:
            intent.update(theta=0.0, fraction_after=fraction, outcome=DRY_RUN)
            record.data.update(
                status=DRY_RUN, closed=True, after=_snapshot_data(snapshot),
                residual=snapshot.product_weight - target["total_new"],
            )
            self._event(record, DRY_RUN)
            return record
        if record.data["purpose"] == "flr" and Decimal(snapshot.product_qty) <= 0:
            return self._no_order(record, intent, DONE, 1.0, snapshot)
        if plan.kind == "none":
            return self._no_order(
                record, intent, CAPPED if plan.capped else NETTED,
                0.0 if plan.capped else 1.0, snapshot,
            )
        record.data["status"] = PLANNED
        self._event(record, PLANNED)
        intent["submit_started"] = True
        self._event(record, "SUBMIT_STARTED")
        return self._submit(record, window)

    def _lookup(self, client_id, polls=20):
        for index in range(polls):
            order = self.broker.get_order_by_client_id(client_id)
            if order is not None:
                return order
            if index != polls - 1:
                self.sleep_fn(1)
        return None

    def _zero_attempt(self, record, intent, error, outcome=FAILED):
        _, _, fraction = self._allocation(intent, 0.0)
        intent.update(
            order_status="rejected", filled_qty="0", fill_ratio=0.0, theta=0.0,
            fraction_after=fraction, outcome=outcome, error=error,
        )
        record.data["overlay_fraction_committed"] = fraction
        return fraction

    def _rejected(self, record, window, exc):
        intent = record.data["intents"][-1]
        fraction = self._zero_attempt(record, intent, f"{exc.status}:{exc.code}")
        record.data.update(status=PARTIAL, closed=False)
        self._event(record, "REJECTED")
        self._commit(record, fraction)
        resize_count = sum(
            item.get("error") == "403:40310000" for item in record.data["intents"]
        )
        if (
            exc.status == 403 and exc.code == 40310000 and resize_count == 1
            and self._attempt_count(record) < 3 and self.now_fn() < window.submit_until
        ):
            return self._start(record, window)
        return self._close(record, intent["error"])

    def _submit(self, record, window):
        intent = record.data["intents"][-1]
        while True:
            if intent["submit_count"] >= 3:
                record.data.update(status=UNRESOLVED, hold="UNRESOLVED")
                self._event(record, UNRESOLVED)
                return record
            if self.now_fn() >= window.submit_until:
                if intent["submit_count"] == 0:
                    # The counter is saved before every call: zero proves nothing was ever sent.
                    intent["outcome"] = NOT_SUBMITTED
                    if self._any_fill(record):
                        return self._close(record, "CUTOFF_BEFORE_SUBMIT")
                    return self._final(record, NOT_SUBMITTED, "CUTOFF_BEFORE_SUBMIT")
                record.data.update(status=UNRESOLVED, hold="UNRESOLVED")
                self._event(record, UNRESOLVED)
                return record
            if self._blocked:
                record.data["hold"] = self._blocked
                self._event(record, "HOLD:" + str(self._blocked))
                return record
            intent["submit_count"] += 1
            record.save()
            try:
                order = self.broker.submit_order(
                    self.params.product_symbol, intent["side"], intent["client_order_id"],
                    notional=intent["notional"], qty=intent["qty"],
                )
            except AmbiguousSubmit:
                order = self._lookup(intent["client_order_id"])
                if order is None:
                    if self.now_fn() < window.submit_until and intent["submit_count"] < 3:
                        continue
                    record.data.update(status=UNRESOLVED, hold="UNRESOLVED")
                    self._event(record, UNRESOLVED)
                    return record
            except BrokerRejected as exc:
                if exc.code == 40010001:
                    order = self._lookup(intent["client_order_id"])
                    if order is None:
                        if self.now_fn() < window.submit_until and intent["submit_count"] < 3:
                            continue
                        record.data.update(status=UNRESOLVED, hold="UNRESOLVED")
                        self._event(record, UNRESOLVED)
                        return record
                else:
                    return self._rejected(record, window, exc)
            except ValueError:
                fraction = self._zero_attempt(record, intent, "VALIDATION")
                self._event(record, "VALIDATION")
                self._commit(record, fraction)
                return self._close(record, "VALIDATION")
            intent["broker_order_id"], intent["order_status"] = order.id, order.status
            record.data.update(status=SUBMITTED, hold=None)
            self._event(record, SUBMITTED)
            return self._poll(record, window, order)

    def _poll(self, record, window, order=None, allow_retry=True):
        intent = record.data["intents"][-1]
        canceled = False
        for index in range(20):
            if order is None or not is_terminal(order.status):
                order = self.broker.get_order(intent["broker_order_id"])
            if is_terminal(order.status):
                return self._terminal(record, order, window, allow_retry)
            if self.now_fn() >= window.cancel_at and not canceled:
                self._event(record, f"CANCEL:{intent['client_order_id']}")
                self.broker.cancel_order(order.id)
                canceled = True
            if index != 19:
                self.sleep_fn(1)
            order = None
        return record

    def _terminal(self, record, order, window, allow_retry=True):
        intent = record.data["intents"][-1]
        filled = Decimal(order.filled_qty)
        intent.update(
            broker_order_id=order.id, order_status=order.status,
            filled_qty=order.filled_qty, filled_avg_price=order.filled_avg_price,
        )
        if filled <= 0:
            fill_ratio = measured_theta = 0.0
        elif intent["side"] == "buy":
            if order.filled_avg_price is None:
                raise BrokerError("FILLED_PRICE_MISSING")
            value = filled * _dec(order.filled_avg_price)
            fill_ratio = float(value / Decimal(intent["notional"]))
            measured_theta = float(value / _dec(intent["desired_usd"]))
        else:
            fill_ratio = float(filled / Decimal(intent["qty"]))
            measured_theta = float(filled / Decimal(intent["desired_qty"]))
        fill_ratio = min(1.0, max(0.0, fill_ratio))
        measured_theta = min(1.0, max(0.0, measured_theta))
        theta = 1.0 if order.status == "filled" and not intent["capped"] else measured_theta
        _, _, fraction = self._allocation(intent, theta)
        intent.update(fill_ratio=fill_ratio, theta=theta, fraction_after=fraction)
        record.data.update(overlay_fraction_committed=fraction, status=RECONCILING)
        self._event(record, RECONCILING)
        self._commit(record, fraction)

        after = take_snapshot(self.broker, self.params, self._cached_ids(), self._owner_symbols())
        residual = after.product_weight - record.data["target"]["total_new"]
        record.data["after"], record.data["residual"] = _snapshot_data(after), residual
        tolerance = max(0.015, 2 / after.equity)
        if order.status == "filled":
            outcome = DONE if abs(residual) <= tolerance else CAPPED
            intent["outcome"] = outcome
            return self._final(record, outcome)
        intent["outcome"] = PARTIAL
        record.data.update(status=PARTIAL, closed=False)
        self._event(record, PARTIAL)
        if (
            allow_retry and self.now_fn() < window.submit_until
            and self._attempt_count(record) < 3
        ):
            return self._start(record, window)
        return self._close(record, "ATTEMPT_TERMINAL")

    def _resume(self, record, window):
        intent = record.data["intents"][-1] if record.data["intents"] else None
        if record.status == PARTIAL:
            if self.now_fn() >= window.submit_until or self._attempt_count(record) >= 3:
                return self._close(record, "ATTEMPTS_CLOSED")
            return self._start(record, window)
        if record.status == PLANNED:
            if not intent["submit_started"] or intent["submit_count"] == 0:
                return self._start(record, window, replan=True)
            order = self._lookup(intent["client_order_id"])
            if order is None:
                if self.now_fn() >= window.submit_until:
                    record.data.update(status=UNRESOLVED, hold="UNRESOLVED")
                    self._event(record, UNRESOLVED)
                    return record
                return self._submit(record, window)
            intent["broker_order_id"] = order.id
            record.data.update(status=SUBMITTED, hold=None)
            self._event(record, SUBMITTED)
            return self._poll(record, window, order)
        if record.status == UNRESOLVED:
            order = self._lookup(intent["client_order_id"], polls=1)
            if order is None:
                return record
            intent["broker_order_id"] = order.id
            record.data.update(status=SUBMITTED, hold=None)
            self._event(record, SUBMITTED)
            return self._poll(record, window, order)
        if record.status == SUBMITTED:
            return self._poll(record, window)
        if record.status == RECONCILING:
            order = self.broker.get_order(intent["broker_order_id"])
            if not is_terminal(order.status):
                return record
            return self._terminal(record, order, window)
        return self._start(record, window)

    def run_chain(self, record, window, blocked=None):
        """Drive the chain one step. `blocked` (a reason code) forbids any NEW submission in this call; an order
        that was already accepted is still polled, cancelled at the cancel time and booked."""
        if record.data["dry_run"] != (not self.settings.orders_enabled):
            raise ValueError("dry-run mismatch")
        if record.is_final:
            return record
        if record.data.get("target") is None:
            raise ValueError("target missing")
        if self._cache is not None and not record.data.get("dry_run"):
            self._cache[record.path] = record.data
            getattr(self, "_stamps", {}).pop(record.path, None)
        self._blocked = blocked
        try:
            return self._resume(record, window)
        except (BrokerError, SnapshotInvalid) as exc:
            self._error(record, exc)
            return record
        finally:
            self._blocked = None

    def _not_found(self, record):
        intent = record.data["intents"][-1]
        fraction = self._zero_attempt(record, intent, "NOT_FOUND")
        intent["order_status"] = "not_found"
        self._event(record, "NOT_FOUND")
        self._commit(record, fraction)
        return self._close(record, "NOT_FOUND")

    @staticmethod
    def _after_window(session):
        return Window(
            session.close, session.close, session.close,
            session.close - timedelta(seconds=90),
            session.close + timedelta(seconds=60), session.close + timedelta(minutes=5),
        )

    def reconcile(self, record, session):
        if record.is_final:
            return record
        if record.data.get("target") is None:
            raise ValueError("target missing")
        try:
            if record.status == PARTIAL:
                return self._close(record, "AFTER_CLOSE")
            intent = record.data["intents"][-1] if record.data["intents"] else None
            if intent is None:
                return self._final(record, NOT_SUBMITTED)
            if record.status == PLANNED and not intent["submit_started"]:
                return self._close(record, "AFTER_CLOSE") if self._any_fill(record) else self._final(record, NOT_SUBMITTED)
            order = None
            if intent.get("broker_order_id"):
                try:
                    order = self.broker.get_order(intent["broker_order_id"])
                except BrokerRejected as exc:
                    if not (exc.status == 404 and exc.code == 40410000):
                        raise
            if order is None:
                order = self.broker.get_order_by_client_id(intent["client_order_id"])
            if order is not None:
                intent["broker_order_id"] = order.id
                record.data.update(status=SUBMITTED, hold=None)
                self._event(record, SUBMITTED)
                return self._poll(record, self._after_window(session), order, allow_retry=False)
            orders, complete = self.broker.list_orders(
                after=session.open, symbol=self.params.product_symbol
            )
            listed = next(
                (value for value in orders if value.client_order_id == intent["client_order_id"]), None
            )
            if listed is not None:
                intent["broker_order_id"] = listed.id
                record.data["status"] = SUBMITTED
                self._event(record, SUBMITTED)
                return self._poll(record, self._after_window(session), listed, allow_retry=False)
            positions = self.broker.get_positions()
            current_qty = next(
                (value.qty for value in positions if value.symbol == self.params.product_symbol), "0"
            )
            try:
                unchanged = Decimal(str(current_qty)) == Decimal(str(intent["before_qty"]))
            except InvalidOperation:
                unchanged = False
            if complete and unchanged:
                return self._not_found(record)
            record.data.update(status=UNRESOLVED, hold="UNRESOLVED")
            self._event(record, UNRESOLVED)
            return record
        except (BrokerError, SnapshotInvalid) as exc:
            self._error(record, exc)
            return record
