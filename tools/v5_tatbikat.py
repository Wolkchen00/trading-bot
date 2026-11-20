"""Paper drill: real PAPER orders through the v5 executor, run by the operator during deployment.

It refuses anything but a paper account, keeps its records in a temporary folder of its own (never the bot's
state folder) and uses session dates from the years 1990 to 2009, so its order ids can never meet the bot's.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from v5.broker import AlpacaBroker, BrokerRejected  # noqa: E402
from v5.clock import Window  # noqa: E402
from v5.config import load_settings  # noqa: E402
from v5.executor import CAPPED, DONE, Executor, SessionRecord, client_order_id, take_snapshot  # noqa: E402
from v5.notify import Notifier, Outbox, default_post  # noqa: E402
from v5.packages import get_package  # noqa: E402


STEPS = ("inventory", "flatten_foreign", "round_trip", "duplicate_id", "restart", "notify")
DRILL_WEIGHT = 0.05


class _SimulatedDeath(BaseException):
    """The process "dies" at the submit call: not an Exception, so nothing in the executor may swallow it."""


class _DieOnSubmit:
    def __init__(self, broker):
        self._broker = broker

    def __getattr__(self, name):
        return getattr(self._broker, name)

    def submit_order(self, *args, **kwargs):
        raise _SimulatedDeath()


class _Drill:
    def __init__(self, settings, broker, now_fn, sleep_fn, post_fn, confirm):
        self.settings, self.broker = settings, broker
        self.now_fn, self.sleep_fn, self.post_fn = now_fn, sleep_fn, post_fn
        self.confirm = confirm
        self.params = get_package(settings.package)
        self.product = self.params.product_symbol
        self.owner_symbols = tuple(getattr(settings, "owner_symbols", ()) or ())
        self.folder = Path(tempfile.mkdtemp(prefix="v5-drill-"))
        self._used_days = set()
        self._known_client_id = None

    # ------------------------------------------------------------------ helpers
    def _executor(self, broker=None):
        return Executor(broker or self.broker, self.settings, self.folder, self.params, self.now_fn, self.sleep_fn)

    def _snapshot(self):
        return take_snapshot(self.broker, self.params, set(), self.owner_symbols)

    def _fresh_day(self) -> date:
        """A synthetic session date (1990 to 2009) whose order id the broker has never seen."""
        base = int(self.now_fn().timestamp())
        for step in range(400):
            day = date(1990, 1, 1) + timedelta(days=(base + step * 977) % 7300)
            if day in self._used_days:
                continue
            if self.broker.get_order_by_client_id(client_order_id("paper", day, "dec", 1)) is None:
                self._used_days.add(day)
                return day
        raise RuntimeError("no unused drill date found")

    def _window(self) -> Window:
        now = self.now_fn()
        return Window(
            now, now + timedelta(minutes=2), now + timedelta(minutes=3), now + timedelta(minutes=4),
            now + timedelta(minutes=6), now + timedelta(minutes=10),
        )

    def _new_record(self, target):
        record = SessionRecord.new(self.folder, self._fresh_day(), "paper")
        record.data["target"] = {"core_new": target, "ovl_new": 0.0, "total_new": target}
        record.save()
        return record

    def _drive(self, executor, record, window):
        for _ in range(40):
            record = executor.run_chain(record, window)
            if record.is_final:
                break
            self.sleep_fn(2)
        return record

    def _chain(self, target):
        record = self._new_record(target)
        return self._drive(self._executor(), record, self._window())

    def _flat(self) -> bool:
        return Decimal(self._snapshot().product_qty) <= 0

    # ------------------------------------------------------------------ steps
    def step_inventory(self):
        account = self.broker.get_account()
        positions = self.broker.get_positions()
        orders = self.broker.get_open_orders()
        held = ", ".join(f"{p.symbol} {p.qty} ({p.market_value:.2f})" for p in positions) or "none"
        return True, (
            f"status {account.status}, equity {account.equity:.2f}, cash {account.cash:.2f}; "
            f"positions: {held}; open orders: {len(orders)}"
        )

    def step_flatten_foreign(self):
        if not self.confirm:
            return False, "skipped: this step sells positions, run it again with --yes"
        stamp = int(self.now_fn().timestamp())
        cancelled = sold = 0
        owners = {s.upper() for s in self.owner_symbols}
        for order in self.broker.get_open_orders():
            if order.symbol.upper() in owners:
                continue
            self.broker.cancel_order(order.id)
            cancelled += 1
        open_symbols = []
        for attempt in range(30):
            open_symbols = sorted({
                order.symbol for order in self.broker.get_open_orders()
                if order.symbol.upper() not in owners
            })
            if not open_symbols:
                break
            if attempt != 29:
                self.sleep_fn(1)
        if open_symbols:
            return False, "open orders did not cancel: " + ", ".join(open_symbols)
        for position in self.broker.get_positions():
            if position.symbol == self.product or position.symbol.upper() in owners:
                continue
            if abs(position.market_value) < 1.0:
                continue
            quantity = Decimal(position.qty)
            if quantity == 0:
                continue
            safe = "".join(ch for ch in position.symbol if ch.isalnum())
            # A long is sold, a short is bought back: either way the position is closed.
            side = "sell" if quantity > 0 else "buy"
            self.broker.submit_order(
                position.symbol, side, f"v5drill-flat-{safe}-{stamp}", qty=format(abs(quantity), "f")
            )
            sold += 1
        left = []
        for _ in range(30):
            left = [
                p.symbol for p in self.broker.get_positions()
                if (
                    p.symbol != self.product and p.symbol.upper() not in owners
                    and abs(p.market_value) >= 1.0 and Decimal(p.qty) != 0
                )
            ]
            if not left:
                break
            self.sleep_fn(1)
        return not left, f"cancelled {cancelled} orders, closed {sold} positions, still held: {', '.join(left) or 'none'}"

    def step_round_trip(self):
        snapshot = self._snapshot()
        if snapshot.foreign:
            return False, "the account holds foreign items: " + "; ".join(snapshot.foreign)
        if Decimal(snapshot.product_qty) > 0:
            return False, f"the account already holds {snapshot.product_qty} of {self.product}; flatten it first"
        opened = self._chain(DRILL_WEIGHT)
        weight = self._snapshot().product_weight
        intents = [i for i in opened.data["intents"] if i.get("client_order_id")]
        if intents:
            self._known_client_id = intents[0]["client_order_id"]
        if opened.status not in (DONE, CAPPED) or abs(weight - DRILL_WEIGHT) > 0.02:
            return False, f"entry ended {opened.status} at weight {weight:.4f}"
        closed = self._chain(0.0)
        flat = self._flat()
        return (
            closed.status == DONE and flat,
            f"entry {opened.status} at weight {weight:.4f} ({self._known_client_id}), exit {closed.status}, flat {flat}",
        )

    def step_duplicate_id(self):
        if not self._flat():
            # Whatever this step buys is sold again at its end: on a flat account that is exactly its own buy.
            return False, f"the account already holds {self.product}; flatten it first"
        created = False
        if self._known_client_id is None:
            day = self._fresh_day()
            self._known_client_id = client_order_id("paper", day, "dec", 1)
            self.broker.submit_order(self.product, "buy", self._known_client_id, notional="1.00")
            created = True
            self.sleep_fn(2)
        try:
            self.broker.submit_order(self.product, "buy", self._known_client_id, notional="1.00")
        except BrokerRejected as exc:
            result = exc.code == 40010001, f"second submit of {self._known_client_id} refused with code {exc.code}"
        else:
            result = False, f"second submit of {self._known_client_id} was ACCEPTED"
        if created or not result[0]:
            self._chain(0.0)                              # put back what this step bought
        return result

    def step_restart(self):
        if not self._flat():
            return False, f"the account already holds {self.product}; flatten it first"
        record = self._new_record(DRILL_WEIGHT)
        window = self._window()
        try:
            self._executor(_DieOnSubmit(self.broker)).run_chain(record, window)
            return False, "the simulated death did not happen"
        except _SimulatedDeath:
            pass
        day = date.fromisoformat(record.data["session"])
        reloaded = SessionRecord.load(self.folder, day)
        before = reloaded.status
        finished = self._drive(self._executor(), reloaded, window)       # a new process on the same folder
        intent = [i for i in finished.data["intents"] if i.get("client_order_id")][-1]
        order = self.broker.get_order_by_client_id(intent["client_order_id"])
        weight = self._snapshot().product_weight
        ok = (
            finished.status in (DONE, CAPPED) and order is not None
            and Decimal(order.filled_qty) > 0 and abs(weight - DRILL_WEIGHT) <= 0.02
        )
        closed = self._chain(0.0)
        return (
            ok and closed.status == DONE and self._flat(),
            f"died at {before}, restart ended {finished.status} after {intent['submit_count']} submit calls, "
            f"one order {intent['client_order_id']} at weight {weight:.4f}, exit {closed.status}",
        )

    def step_notify(self):
        outbox = Outbox(self.folder)
        notifier = Notifier(self.settings, outbox, self.post_fn, self.now_fn)
        if not notifier.configured:
            return False, "no notification topic is configured"
        notifier.alert("TEST")
        delivered = not outbox.pending(self.now_fn() + timedelta(hours=3))
        return delivered, "delivered" if delivered else "the post was refused; nothing reached the phone"


def run_steps(settings, broker, steps, now_fn, sleep_fn, post_fn, confirm=False) -> dict:
    """Run the named steps in order and return {step: (passed, evidence)}."""
    if getattr(settings, "mode", None) != "paper":
        raise ValueError("the drill runs on a paper account only")
    unknown = [step for step in steps if step not in STEPS]
    if unknown:
        raise ValueError("unknown drill step: " + ", ".join(unknown))
    drill = _Drill(settings, broker, now_fn, sleep_fn, post_fn, confirm)
    results = {}
    for step in steps:
        try:
            results[step] = getattr(drill, "step_" + step)()
        except _SimulatedDeath:
            results[step] = (False, "a simulated death escaped the step")
        except Exception as exc:
            results[step] = (False, f"{type(exc).__name__}: {exc}")
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="v5 paper drill (real paper orders).")
    parser.add_argument("--inventory", action="store_true")
    parser.add_argument("--flatten-foreign", action="store_true")
    parser.add_argument("--round-trip", action="store_true")
    parser.add_argument("--duplicate-id", action="store_true")
    parser.add_argument("--restart", action="store_true")
    parser.add_argument("--notify", action="store_true")
    parser.add_argument("--yes", action="store_true", help="confirm the steps that sell positions")
    args = parser.parse_args(argv)
    chosen = [step for step in STEPS if getattr(args, step)]
    if not chosen:
        parser.error("choose at least one step")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    settings = load_settings()
    results = run_steps(
        settings, AlpacaBroker(settings), chosen, lambda: datetime.now(timezone.utc), time.sleep, default_post,
        confirm=args.yes,
    )
    for step in chosen:
        passed, evidence = results[step]
        print(("PASS " if passed else "FAIL ") + step + ": " + evidence)
    return 0 if all(results[step][0] for step in chosen) else 1


if __name__ == "__main__":
    raise SystemExit(main())
