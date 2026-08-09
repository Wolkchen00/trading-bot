"""Sunucu-tarafli pozisyon korumasi icin ortak sonuc ve dogrulama mantigi.

Broker'in bir emir nesnesi dondurmesi koruma kaniti degildir. Bu modul yalnizca
yeniden okunmus pozisyon + emir durumundan uretilen sonuclari "verified" sayar.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import hashlib
from typing import Any, Iterable, Optional

from alpaca.trading.enums import OrderSide

from utils.logger import logger


class ProtectionOutcome(str, Enum):
    VERIFIED = "VERIFIED"
    REPLACED_VERIFIED = "REPLACED_VERIFIED"
    ALREADY_FLAT = "ALREADY_FLAT"
    NO_LEG_RESUBMITTED = "NO_LEG_RESUBMITTED"
    NOOP_BETTER_PROTECTED = "NOOP_BETTER_PROTECTED"
    DEGRADED_PROTECTED = "DEGRADED_PROTECTED"
    FAILED_NAKED = "FAILED_NAKED"
    ELECTED_UNFILLED = "ELECTED_UNFILLED"
    SKIPPED_PARKING = "SKIPPED_PARKING"


VERIFIED_OUTCOMES = frozenset({
    ProtectionOutcome.VERIFIED,
    ProtectionOutcome.REPLACED_VERIFIED,
    ProtectionOutcome.NO_LEG_RESUBMITTED,
    ProtectionOutcome.NOOP_BETTER_PROTECTED,
    ProtectionOutcome.DEGRADED_PROTECTED,
})


@dataclass(frozen=True)
class ProtectionResult:
    outcome: ProtectionOutcome
    order_id: Optional[str]
    stop_price: Optional[float]
    qty_covered: float
    detail: str
    at_target: bool = False

    @property
    def verified(self) -> bool:
        return self.outcome in VERIFIED_OUTCOMES


@dataclass
class ProtectionSummary:
    """Bir reconciliation turundaki tum pozisyon sonuclari."""

    results: list[ProtectionResult] = field(default_factory=list)
    detail: str = ""

    @property
    def placed(self) -> int:
        return sum(
            result.outcome in {
                ProtectionOutcome.REPLACED_VERIFIED,
                ProtectionOutcome.NO_LEG_RESUBMITTED,
            }
            for result in self.results
        )

    @property
    def verified(self) -> int:
        return sum(result.verified for result in self.results)

    @property
    def failed(self) -> int:
        """Gercek koruma basarisizliklari.

        SKIPPED_PARKING BILEREK haric: index-parking nakit sleeve'i tasarim
        geregi stop'suzdur ve her turda mevcuttur. Onu "failed" saymak
        `ok` bayragini kalici olarak False'a cakar ve bayrak hicbir sey
        ifade etmez hale gelir.
        """
        return sum(
            result.outcome in {
                ProtectionOutcome.FAILED_NAKED,
                ProtectionOutcome.ELECTED_UNFILLED,
            }
            for result in self.results
        )

    @property
    def skipped_parking(self) -> int:
        return sum(
            result.outcome is ProtectionOutcome.SKIPPED_PARKING
            for result in self.results
        )

    @property
    def ok(self) -> bool:
        return self.failed == 0


ACTIVE_STATUSES = frozenset({
    "new",
    "partially_filled",
    "pending_cancel",
    "pending_replace",
    "pending_review",
    "accepted",
    "pending_new",
    "accepted_for_bidding",
    "calculated",
    "held",
})

COVERING_STATUSES = frozenset({
    "new",
    "partially_filled",
    "accepted",
    "accepted_for_bidding",
    # Alpaca bracket exit legs may be held by the parent/OCO relationship while
    # still being the server-side contingent protection for a filled position.
    "held",
})

TERMINAL_STATUSES = frozenset({
    "filled",
    "done_for_day",
    "canceled",
    "cancelled",
    "expired",
    "replaced",
    "rejected",
    "suspended",
    "stopped",
})

STOP_TYPES = frozenset({"stop", "stop_limit", "trailing_stop"})


def should_exit_locally(
    current_price: Any, stop_loss_price: Any, side: str
) -> bool:
    """Return whether an absolute stop trigger has been reached locally.

    ``stop_loss_price`` is the canonical local trigger. Percentage fields are
    planning distances only and must not participate in the exit comparison.
    Missing/invalid persisted values are treated as not armed until migration
    derives a valid absolute price.
    """
    try:
        current = float(current_price)
        trigger = float(stop_loss_price)
    except (TypeError, ValueError):
        return False
    if current <= 0 or trigger <= 0:
        return False

    normalized_side = str(side or "").upper()
    if normalized_side == "LONG":
        return current <= trigger
    if normalized_side == "SHORT":
        return current >= trigger
    raise ValueError(f"Unsupported position side: {side}")


def enum_value(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "").strip().lower()


def order_type(order: Any) -> str:
    return enum_value(
        getattr(order, "type", None) or getattr(order, "order_type", None)
    )


def order_id(order: Any) -> Optional[str]:
    value = getattr(order, "id", None)
    return str(value) if value is not None else None


def order_stop_price(order: Any) -> Optional[float]:
    try:
        value = getattr(order, "stop_price", None)
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def order_limit_price(order: Any) -> Optional[float]:
    try:
        value = getattr(order, "limit_price", None)
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def remaining_qty(order: Any) -> float:
    try:
        total = float(getattr(order, "qty", 0) or 0)
        filled = float(getattr(order, "filled_qty", 0) or 0)
        return max(total - filled, 0.0)
    except (TypeError, ValueError):
        return 0.0


def flatten_orders(orders: Iterable[Any]) -> list[Any]:
    """Nested bracket parent/leg yapisini tek listeye indir."""
    flattened: list[Any] = []
    seen: set[str] = set()

    def visit(order: Any) -> None:
        marker = order_id(order) or f"object:{id(order)}"
        if marker in seen:
            return
        seen.add(marker)
        flattened.append(order)
        for leg in getattr(order, "legs", None) or []:
            visit(leg)

    for item in orders or []:
        visit(item)
    return flattened


def is_stop_order(order: Any) -> bool:
    return order_type(order) in STOP_TYPES


def is_active_order(order: Any) -> bool:
    return enum_value(getattr(order, "status", None)) in ACTIVE_STATUSES


def is_terminal_order(order: Any) -> bool:
    return enum_value(getattr(order, "status", None)) in TERMINAL_STATUSES


def deterministic_client_order_id(
    symbol: str, side: str, stop_price: float, qty: float, salt: str
) -> str:
    """Ayni niyet retry'larini korele eden, cagriya ozel ve kisa ID."""
    material = (
        f"{symbol.upper()}|{side.upper()}|{stop_price:.4f}|{qty:.4f}|{salt}"
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]
    return f"r0b-{symbol.upper()[:10]}-{side.upper()[0]}-{digest}"


def protection_drift_severity(
    side: str,
    canonical_stop: float,
    active_stop: float,
    entry_price: float,
    critical_pct: float = 0.01,
) -> tuple[str, float]:
    """Hedeften kotu koruma sapmasini yon-bilincli siniflandir."""
    entry = float(entry_price)
    if entry <= 0:
        raise ValueError("entry_price pozitif olmali")
    normalized_side = str(side or "").upper()
    if normalized_side == "LONG":
        drift = (float(canonical_stop) - float(active_stop)) / entry
    elif normalized_side == "SHORT":
        drift = (float(active_stop) - float(canonical_stop)) / entry
    else:
        raise ValueError(f"Unsupported position side: {side}")
    drift = max(drift, 0.0)
    return ("CRITICAL" if drift > float(critical_pct) else "WARNING", drift)


def classify_covering_order(
    order: Any,
    position: Any,
    side: str,
    expected_stop: Optional[float] = None,
) -> ProtectionResult:
    """Tek emrin guncel pozisyonu gercekten koruyup korumadigini siniflandir."""
    oid = order_id(order)
    stop = order_stop_price(order)
    covered = remaining_qty(order)
    symbol = str(getattr(position, "symbol", "") or "")
    wanted_side = OrderSide.SELL if side == "LONG" else OrderSide.BUY

    try:
        pos_qty = abs(float(getattr(position, "qty", 0) or 0))
    except (TypeError, ValueError):
        pos_qty = 0.0

    if str(getattr(order, "symbol", "") or "") != symbol:
        return ProtectionResult(
            ProtectionOutcome.FAILED_NAKED, oid, stop, covered,
            f"{symbol}: emir baska sembole ait",
        )
    if enum_value(getattr(order, "side", None)) != enum_value(wanted_side):
        return ProtectionResult(
            ProtectionOutcome.FAILED_NAKED, oid, stop, covered,
            f"{symbol}: koruma yonu yanlis",
        )
    if not is_stop_order(order):
        return ProtectionResult(
            ProtectionOutcome.FAILED_NAKED, oid, stop, covered,
            f"{symbol}: emir stop bacagi degil ({order_type(order) or 'bilinmiyor'})",
        )

    status = enum_value(getattr(order, "status", None))
    if status == "stopped":
        return ProtectionResult(
            ProtectionOutcome.ELECTED_UNFILLED, oid, stop, covered,
            f"{symbol}: stop tetiklenmis ama pozisyon hala acik",
        )
    if status not in COVERING_STATUSES:
        return ProtectionResult(
            ProtectionOutcome.FAILED_NAKED, oid, stop, covered,
            f"{symbol}: stop kapsama için kesin aktif değil ({status or 'durum yok'})",
        )
    if pos_qty <= 0:
        return ProtectionResult(
            ProtectionOutcome.ALREADY_FLAT, oid, stop, 0.0,
            f"{symbol}: pozisyon zaten duz",
        )
    if covered + 1e-6 < pos_qty:
        return ProtectionResult(
            ProtectionOutcome.FAILED_NAKED, oid, stop, covered,
            f"{symbol}: stop miktari yetersiz ({covered:.4f} < {pos_qty:.4f})",
        )
    if not enum_value(getattr(order, "time_in_force", None)):
        return ProtectionResult(
            ProtectionOutcome.FAILED_NAKED, oid, stop, covered,
            f"{symbol}: stop TIF bilgisi yok",
        )
    if stop is None or stop <= 0:
        return ProtectionResult(
            ProtectionOutcome.FAILED_NAKED, oid, stop, covered,
            f"{symbol}: stop fiyati gecersiz",
        )

    try:
        entry = float(getattr(position, "avg_entry_price", 0) or 0)
        current = float(getattr(position, "current_price", 0) or 0)
    except (TypeError, ValueError):
        entry, current = 0.0, 0.0

    # Stop, pozisyonun guncel fiyatinin koruyucu tarafinda olmali. Entry'ye gore
    # genis bir sanity bandi BE/trailing stop'un karda entry'yi gecmesine izin verir.
    if entry <= 0 or stop < entry * 0.05 or stop > entry * 20:
        return ProtectionResult(
            ProtectionOutcome.FAILED_NAKED, oid, stop, covered,
            f"{symbol}: stop fiyati entry'ye gore mantiksiz ({stop} / {entry})",
        )

    limit = order_limit_price(order)
    is_stop_limit = order_type(order) == "stop_limit"
    if is_stop_limit and (limit is None or limit <= 0):
        return ProtectionResult(
            ProtectionOutcome.FAILED_NAKED, oid, stop, covered,
            f"{symbol}: stop-limit limit fiyati yok",
        )

    if current > 0:
        if side == "LONG" and current <= stop:
            if is_stop_limit and current < float(limit):
                return ProtectionResult(
                    ProtectionOutcome.ELECTED_UNFILLED, oid, stop, covered,
                    f"{symbol}: LONG stop-limit elected, piyasa ${current:.2f} "
                    f"limitin ${float(limit):.2f} altinda",
                )
            return ProtectionResult(
                ProtectionOutcome.FAILED_NAKED, oid, stop, covered,
                f"{symbol}: SELL stop tetik seviyesinde ama pozisyon hala acik",
            )
        if side == "SHORT" and current >= stop:
            if is_stop_limit and current > float(limit):
                return ProtectionResult(
                    ProtectionOutcome.ELECTED_UNFILLED, oid, stop, covered,
                    f"{symbol}: SHORT stop-limit elected, piyasa ${current:.2f} "
                    f"limitin ${float(limit):.2f} ustunde",
                )
            return ProtectionResult(
                ProtectionOutcome.FAILED_NAKED, oid, stop, covered,
                f"{symbol}: BUY stop tetik seviyesinde ama pozisyon hala acik",
            )

    if expected_stop is not None:
        expected = float(expected_stop)
        at_or_better = (
            stop + 0.011 >= expected
            if side == "LONG"
            else stop - 0.011 <= expected
        )
        if not at_or_better:
            return ProtectionResult(
                ProtectionOutcome.DEGRADED_PROTECTED, oid, stop, covered,
                f"{symbol}: aktif stop kapsiyor ama hedefin kotu tarafinda "
                f"(${stop:.2f} != ${expected:.2f})",
                at_target=False,
            )

    return ProtectionResult(
        ProtectionOutcome.VERIFIED, oid, stop, covered,
        f"{symbol}: aktif stop {covered:.4f} adedi kapsiyor",
        at_target=expected_stop is not None,
    )


def exit_flag_cache_matches_entry(cached: Any, entry_price: Any) -> bool:
    """A6 cikis-bayragi cache'i yalnizca AYNI girise aittir; kimligi dogrular.

    Cache gecici sync-dususlerinde bayraklari korumak icin var. Ama sembol
    kapanip AYNI surecte yeniden alinirsa, eski girisin mutlak tetigi
    (stop_loss_price) yeni pozisyona enjekte olur ve taze pozisyon aninda
    yanlis STOP_LOSS ile satilabilir (deploy-gate incelemesinde dogrulanmis
    zincir). Kimlik = stash aninda kaydedilen entry_price; %0.1'den fazla
    sapan ya da kimliksiz kayit ESLESMEZ ve cagiran cache'i dusurmelidir.
    """
    if not isinstance(cached, dict):
        return False
    try:
        cached_entry = float(cached.get("entry_price", 0) or 0)
        new_entry = float(entry_price or 0)
    except (TypeError, ValueError):
        return False
    if cached_entry <= 0 or new_entry <= 0:
        return False
    return abs(new_entry - cached_entry) / cached_entry <= 0.001


def protection_alarm(bot: Any, key: str, detail: str, dedupe_seconds: int = 900) -> bool:
    """Yerel CRITICAL alarm + varsa Telegram; ayni alarmi sureli tekillestirir."""
    now = datetime.now().timestamp()
    cache = getattr(bot, "_protection_alarm_cache", None)
    if not isinstance(cache, dict):
        cache = {}
        setattr(bot, "_protection_alarm_cache", cache)
    last = cache.get(key)
    if isinstance(last, tuple) and len(last) == 2:
        last_time, last_detail = last
        if last_detail == detail and now - float(last_time) < dedupe_seconds:
            logger.debug(f"  Koruma alarmi tekrarlandigi icin atlandi: {key}")
            return False

    cache[key] = (now, detail)
    message = f"KORUMA ALARMI [{key}] {detail}"
    logger.critical(f"  {message}")

    notifier = getattr(bot, "notifier", None)
    if notifier is not None and (
        hasattr(notifier, "notify_critical") or hasattr(notifier, "notify_error")
    ):
        try:
            if hasattr(notifier, "notify_critical"):
                delivered = notifier.notify_critical("KORUMA", message)
            else:
                delivered = notifier.notify_error(message)
            if delivered is not True:
                logger.error(
                    f"  Koruma alarmi Telegram teslimi dogrulanamadi: {key}"
                )
        except Exception as exc:
            logger.error(f"  Koruma alarm kanali hatasi {key}: {exc}")
    return True


def note_expected_uncovered(bot: Any, key: str, detail: str) -> bool:
    """Tasarim geregi korumasiz bir durumu KAYDET, alarm verme.

    Index-parking sleeve'i icin. protection_alarm kullanilirsa mutabakat her
    turda (5 dk) ates eder, dedupe yalnizca 15 dk susturur ve kanal 7/24
    gurultuye bogulur — gercek bir korumasiz pozisyon o gurultude kaybolur.
    Bu yuzden yalniz DURUM DEGISTIGINDE bir INFO satiri yazilir.
    """
    seen = getattr(bot, "_expected_uncovered_seen", None)
    if not isinstance(seen, dict):
        seen = {}
        setattr(bot, "_expected_uncovered_seen", seen)
    if seen.get(key) == detail:
        return False
    seen[key] = detail
    logger.info(f"  ℹ️ Beklenen korumasiz durum [{key}]: {detail}")
    return True


def clear_expected_uncovered(bot: Any, key: str) -> None:
    seen = getattr(bot, "_expected_uncovered_seen", None)
    if isinstance(seen, dict):
        seen.pop(key, None)


def clear_protection_alarm(bot: Any, key: str) -> None:
    cache = getattr(bot, "_protection_alarm_cache", None)
    if isinstance(cache, dict):
        cache.pop(key, None)
