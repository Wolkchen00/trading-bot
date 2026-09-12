"""Zarar serisinin profil-sahipli, UTC tabanli tek dogru kaynagi.

Seri yalniz gerceklesen PnL isaretine gore degisir. R20 ile birlikte her
profilin sayaci ve son zarar zamani kalici tutulur; kapilar bu zamana gore
seriyi sondurur. Naive zamanlar eski kayitlarla uyum icin UTC kabul edilir.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
from typing import Any

from utils.logger import logger


UTC = timezone.utc
_ENTRY_PROFILE_UNSET = object()


def utc_now() -> datetime:
    return datetime.now(UTC)


def parse_utc(value: Any) -> datetime | None:
    """ISO/datetime degerini aware UTC'ye cevir; bozuk degerde None dondur."""
    if value is None or value == "":
        return None
    try:
        if isinstance(value, datetime):
            parsed = value
        else:
            raw = str(value).strip()
            if raw.endswith("Z"):
                raw = raw[:-1] + "+00:00"
            parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except (TypeError, ValueError, OverflowError):
        return None


def utc_iso(value: Any) -> str | None:
    parsed = parse_utc(value)
    return parsed.isoformat() if parsed is not None else None


def _active_profile() -> str:
    from core.run_profile import aktif_profil

    return aktif_profil()


def _positive_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _decay_hours(config: dict, key: str) -> float:
    """Bozuk/asinmis config bile seriyi 24 saatten uzun tutamaz."""
    try:
        value = float(config.get(key, 24))
    except (TypeError, ValueError, OverflowError):
        return 24.0
    if not math.isfinite(value):
        return 24.0
    return min(24.0, max(0.0, value))


def _now(value: Any = None) -> datetime:
    parsed = parse_utc(value)
    return parsed if parsed is not None else utc_now()


def _history_loss_times(history: Any) -> tuple[datetime | None, dict[str, datetime]]:
    latest = None
    by_symbol: dict[str, datetime] = {}
    if not isinstance(history, list):
        return latest, by_symbol
    for row in history:
        if not isinstance(row, dict):
            continue
        try:
            if float(row.get("pnl", 0) or 0) >= 0:
                continue
        except (TypeError, ValueError):
            continue
        action = str(row.get("action", "")).upper()
        if action and action not in ("SELL", "COVER"):
            continue
        stamp = parse_utc(
            row.get("filled_at") or row.get("ts_utc")
            or row.get("timestamp") or row.get("time")
        )
        if stamp is None:
            continue
        symbol = str(row.get("symbol", "")).upper()
        if latest is None or stamp > latest:
            latest = stamp
        if symbol and (symbol not in by_symbol or stamp > by_symbol[symbol]):
            by_symbol[symbol] = stamp
    return latest, by_symbol


def _record_from_legacy(data: dict, history: Any, now: datetime) -> dict:
    """Duz v4 alanlarini yeni profil kaydina cevir."""
    losses = _positive_int(data.get("consecutive_losses", 0))
    raw_symbols = data.get("symbol_consecutive_losses", {})
    if not isinstance(raw_symbols, dict):
        raw_symbols = {}
    latest, by_symbol = _history_loss_times(history)

    raw_last = data.get("last_loss_at")
    if losses > 0 and raw_last is None:
        raw_last = latest or now
        if latest is not None:
            logger.info(
                f"  Zarar serisi eski durum gocu: son zarar trade_history'den "
                f"alindi ({latest.isoformat()})"
            )
        else:
            logger.info(
                "  Zarar serisi eski durum gocu: zararli kapanis bulunamadi; "
                "saat yukleme aninda baslatildi"
            )

    raw_symbol_times = data.get("symbol_last_loss_at", {})
    if not isinstance(raw_symbol_times, dict):
        raw_symbol_times = {}
    symbols = {}
    for symbol, value in raw_symbols.items():
        count = _positive_int(value)
        sym_last = raw_symbol_times.get(symbol)
        if count > 0 and sym_last is None:
            sym_last = by_symbol.get(str(symbol).upper()) or latest or now
        symbols[str(symbol)] = {
            "losses": count,
            "last_loss_at": utc_iso(sym_last) if sym_last is not None else None,
        }
    return {
        "consecutive_losses": losses,
        # Bozuk, ama mevcut damgayi burada koru: restore asamasinda "eski"
        # kabul edilip seri sifirlanacak. None ise yukarida history/now secildi.
        "last_loss_at": (
            utc_iso(raw_last) if parse_utc(raw_last) is not None else raw_last
        ),
        "symbols": symbols,
    }


def _normal_record(record: Any) -> dict:
    if not isinstance(record, dict):
        record = {}
    raw_symbols = record.get("symbols", {})
    if not isinstance(raw_symbols, dict):
        raw_symbols = {}
    symbols = {}
    for symbol, state in raw_symbols.items():
        if isinstance(state, dict):
            symbols[str(symbol)] = {
                "losses": _positive_int(state.get("losses", 0)),
                "last_loss_at": state.get("last_loss_at"),
            }
        else:
            # Erken/ara surum sayisal sembol degerlerini de kilitlemeden oku.
            symbols[str(symbol)] = {
                "losses": _positive_int(state),
                "last_loss_at": None,
            }
    return {
        "consecutive_losses": _positive_int(record.get("consecutive_losses", 0)),
        "last_loss_at": record.get("last_loss_at"),
        "symbols": symbols,
    }


def restore_streak_state(
    bot,
    data: dict,
    *,
    legacy_profile: str,
    history: Any = None,
    config: dict | None = None,
    now: Any = None,
) -> bool:
    """Aktif profilin serisini yukle ve gerekiyorsa eski durumu gocur.

    Diger profil kayitlari bot uzerinde aynen tutulur; sonraki atomik metadata
    yazimi yalniz aktif profil kaydini yeniler.
    """
    current = _now(now)
    raw_profiles = data.get("streaks_by_profile", {})
    profiles = dict(raw_profiles) if isinstance(raw_profiles, dict) else {}
    changed = not isinstance(raw_profiles, dict)
    has_legacy = (
        "consecutive_losses" in data or "symbol_consecutive_losses" in data
        or "last_loss_at" in data or "symbol_last_loss_at" in data
    )
    if has_legacy and legacy_profile not in profiles:
        profiles[legacy_profile] = _record_from_legacy(data, history, current)
        changed = True

    active = _active_profile()
    if active not in profiles:
        profiles[active] = {
            "consecutive_losses": 0,
            "last_loss_at": None,
            "symbols": {},
        }
        changed = True

    original_record = profiles[active]
    record = _normal_record(original_record)
    changed = changed or record != original_record
    latest, by_symbol = _history_loss_times(history)
    if record["consecutive_losses"] > 0 and record["last_loss_at"] is None:
        record["last_loss_at"] = (latest or current).isoformat()
        changed = True
        logger.info(
            "  Zarar serisi saati "
            + (
                f"trade_history'den alindi ({latest.isoformat()})"
                if latest is not None
                else "bulunamadi; yukleme aninda baslatildi"
            )
        )
    for symbol, state in record["symbols"].items():
        if state["losses"] > 0 and state["last_loss_at"] is None:
            state["last_loss_at"] = (
                by_symbol.get(symbol.upper()) or latest or current
            ).isoformat()
            changed = True

    profiles[active] = record
    bot._streaks_by_profile = profiles
    bot._consecutive_losses = record["consecutive_losses"]
    bot._last_loss_at = record["last_loss_at"]
    bot._symbol_consecutive_losses = {
        symbol: state["losses"] for symbol, state in record["symbols"].items()
    }
    bot._symbol_last_loss_at = {
        symbol: state["last_loss_at"] for symbol, state in record["symbols"].items()
    }
    decayed = decay_loss_streaks(bot, config or {}, now=current, persist=False)
    return changed or decayed


def streaks_for_persistence(bot) -> dict:
    """Tum profilleri koruyup aktif profilin bellek durumunu yerlestir."""
    profiles = dict(getattr(bot, "_streaks_by_profile", {}) or {})
    symbols = {}
    counts = getattr(bot, "_symbol_consecutive_losses", {}) or {}
    times = getattr(bot, "_symbol_last_loss_at", {}) or {}
    for symbol in set(counts) | set(times):
        count = _positive_int(counts.get(symbol, 0))
        stamp = times.get(symbol)
        symbols[str(symbol)] = {
            "losses": count,
            "last_loss_at": utc_iso(stamp) if stamp is not None else None,
        }
    profiles[_active_profile()] = {
        "consecutive_losses": _positive_int(
            getattr(bot, "_consecutive_losses", 0)
        ),
        "last_loss_at": utc_iso(getattr(bot, "_last_loss_at", None)),
        "symbols": symbols,
    }
    bot._streaks_by_profile = profiles
    return profiles


def _persist(bot) -> None:
    save = getattr(bot, "_save_position_metadata", None)
    if callable(save):
        saved = save()
        if saved is not True:
            logger.error("  Zarar serisi kalici metadata'ya yazilamadi")


def decay_loss_streaks(
    bot, config: dict, *, now: Any = None, persist: bool = True,
) -> bool:
    """Suresi dolan genel/sembol serilerini sifirla; tek yazim yap."""
    current = _now(now)
    changed = False
    general_count = _positive_int(getattr(bot, "_consecutive_losses", 0))
    general_raw = getattr(bot, "_last_loss_at", None)
    general_at = parse_utc(general_raw)

    if general_count > 0:
        if general_raw is not None and general_at is None:
            logger.warning("  Bozuk last_loss_at eski durum sayildi; zarar serisi sondu")
            general_count = 0
            general_raw = None
            changed = True
        else:
            if general_at is None:
                general_at = current
                general_raw = current
                changed = True
            if general_at > current:
                logger.warning("  Gelecek tarihli last_loss_at simdiye kirpildi")
                general_at = current
                general_raw = current
                changed = True
            decay_hours = _decay_hours(config, "loss_streak_decay_hours")
            if current - general_at >= timedelta(hours=decay_hours):
                logger.info(
                    f"  Zarar serisi {decay_hours:g} saat sonunda sondu "
                    f"({general_count} -> 0)"
                )
                general_count = 0
                general_raw = None
                changed = True
    elif general_raw is not None:
        general_raw = None
        changed = True

    bot._consecutive_losses = general_count
    bot._last_loss_at = utc_iso(general_raw)

    counts = dict(getattr(bot, "_symbol_consecutive_losses", {}) or {})
    times = dict(getattr(bot, "_symbol_last_loss_at", {}) or {})
    symbol_decay = _decay_hours(config, "symbol_loss_decay_hours")
    for symbol in set(counts) | set(times):
        count = _positive_int(counts.get(symbol, 0))
        raw = times.get(symbol)
        stamp = parse_utc(raw)
        if count > 0:
            if raw is not None and stamp is None:
                logger.warning(
                    f"  {symbol} bozuk zarar zamani eski sayildi; sembol serisi sondu"
                )
                count = 0
                raw = None
                changed = True
            else:
                if stamp is None:
                    stamp = current
                    raw = current
                    changed = True
                if stamp > current:
                    logger.warning(
                        f"  {symbol} gelecek tarihli zarar zamani simdiye kirpildi"
                    )
                    stamp = current
                    raw = current
                    changed = True
                if current - stamp >= timedelta(hours=symbol_decay):
                    logger.info(
                        f"  {symbol} sembol zarar serisi {symbol_decay:g} saat "
                        f"sonunda sondu ({count} -> 0)"
                    )
                    count = 0
                    raw = None
                    changed = True
        elif raw is not None:
            raw = None
            changed = True
        counts[symbol] = count
        times[symbol] = utc_iso(raw)
    bot._symbol_consecutive_losses = counts
    bot._symbol_last_loss_at = times

    if changed and persist:
        _persist(bot)
    return changed


def update_loss_streak(
    bot,
    symbol: str,
    pnl_usd: float,
    *,
    filled_at: Any = None,
    entry_profile: Any = _ENTRY_PROFILE_UNSET,
    now: Any = None,
    persist: bool = True,
) -> bool:
    """Seriyi broker dolum zamaniyla guncelle ve ayni adimda kalici yaz.

    ``entry_profile`` acikca None verilirse pozisyon devralinmis/eski kabul
    edilir ve ne zarar ne kar aktif profil serisine dokunur. Parametrenin
    verilmemesi yalniz eski dogrudan yardimci cagrilariyla uyumluluk icindir.
    """
    active = _active_profile()
    owned = entry_profile is _ENTRY_PROFILE_UNSET or entry_profile == active
    if not owned:
        if persist:
            _persist(bot)
        return False

    current = _now(now)
    try:
        from config import STOCK_CONFIG
        config = STOCK_CONFIG
    except Exception:
        config = {}
    decay_loss_streaks(bot, config, now=current, persist=False)

    if filled_at is None or filled_at == "":
        event_at = current
    else:
        event_at = parse_utc(filled_at)
        if event_at is None:
            logger.warning("  Bozuk broker filled_at eski durum sayildi")
        elif event_at > current:
            logger.warning("  Gelecek tarihli broker filled_at simdiye kirpildi")
            event_at = current

    sym_losses = dict(getattr(bot, "_symbol_consecutive_losses", {}) or {})
    sym_times = dict(getattr(bot, "_symbol_last_loss_at", {}) or {})
    changed = False
    if pnl_usd < 0:
        bot._consecutive_losses = _positive_int(
            getattr(bot, "_consecutive_losses", 0)
        ) + 1
        sym_losses[symbol] = _positive_int(sym_losses.get(symbol, 0)) + 1
        bot._last_loss_at = utc_iso(event_at)
        sym_times[symbol] = utc_iso(event_at)
        changed = True
        logger.info(
            f"  Ardisik zarar: {bot._consecutive_losses} | "
            f"{symbol}: {sym_losses[symbol]}"
        )
    elif pnl_usd > 0:
        bot._consecutive_losses = 0
        bot._last_loss_at = None
        sym_losses[symbol] = 0
        sym_times[symbol] = None
        changed = True

    bot._symbol_consecutive_losses = sym_losses
    bot._symbol_last_loss_at = sym_times
    if pnl_usd < 0 and event_at is None:
        # Bozuk dolum damgasi eski durumdur; yeni bir 24 saatlik saat baslatmaz.
        bot._consecutive_losses = 0
        bot._last_loss_at = None
        bot._symbol_consecutive_losses[symbol] = 0
        bot._symbol_last_loss_at[symbol] = None
    elif pnl_usd < 0:
        # Bot kapaliyken dolmus bir stop restartta uzlastiriliyorsa broker
        # zamani zaten bayat olabilir; ayni adimda hemen sondur.
        decay_loss_streaks(bot, config, now=current, persist=False)

    if persist:
        _persist(bot)
    return changed


def loss_streak_expires_at(bot, config: dict) -> str | None:
    if _positive_int(getattr(bot, "_consecutive_losses", 0)) <= 0:
        return None
    stamp = parse_utc(getattr(bot, "_last_loss_at", None))
    if stamp is None:
        return None
    hours = _decay_hours(config, "loss_streak_decay_hours")
    return (stamp + timedelta(hours=hours)).isoformat()
