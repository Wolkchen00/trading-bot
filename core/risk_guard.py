"""Yeni risk acan tum emir yollari icin merkezi guvenlik kapisi."""


_RISK_KINDS = {
    "stock_long",
    "stock_short",
    "option",
    "bear_etf",
    "index_parking",
}

_CODE_ERRORS = (
    AttributeError,
    TypeError,
    KeyError,
    NameError,
    IndexError,
    ZeroDivisionError,
    ImportError,
    UnboundLocalError,
)


def classify_error(exc: Exception) -> str:
    """Istisnayi kod hatasi veya broker/ag/API hatasi olarak siniflandir."""
    return "code" if isinstance(exc, _CODE_ERRORS) else "broker"


def _deny(bot, reason: str) -> tuple[bool, str]:
    """Red telemetrisini best-effort yaz; karar telemetriye bagli degildir."""
    try:
        bot._funnel_bump("gate_block", reason=reason)
    except Exception:
        pass
    return False, reason


def can_open_new_risk(
    bot,
    config,
    kind: str,
    symbol: str = "",
) -> tuple[bool, str]:
    """Broker'a dokunmadan yeni risk acma izni ver veya fail-closed reddet."""
    try:
        if kind not in _RISK_KINDS:
            raise ValueError(f"Bilinmeyen risk turu: {kind}")

        kill_switch = getattr(bot, "kill_switch", None)
        if kill_switch is not None and bool(
            getattr(kill_switch, "is_active", False)
        ):
            return _deny(bot, "KILL_SWITCH")
        if kill_switch is not None and bool(
            getattr(kill_switch, "risk_halted", False)
        ):
            return _deny(bot, "RISK_HALT")

        # Parking strateji girisi degil, savunma amacli nakit parkidir. R5 canli
        # giris kilidinden muaftir; kill/risk-halt kapilari yine yukarida gecerlidir.
        if kind != "index_parking":
            try:
                from config import TRADING_MODE
                default_paper = TRADING_MODE != "live"
            except Exception:
                default_paper = True
            is_live = not bool(getattr(bot, "is_paper", default_paper))
            if is_live:
                if "live_entries_enabled" in config:
                    live_entries_enabled = config.get(
                        "live_entries_enabled", False
                    )
                else:
                    try:
                        from config import STOCK_CONFIG
                        live_entries_enabled = STOCK_CONFIG.get(
                            "live_entries_enabled", False
                        )
                    except Exception:
                        live_entries_enabled = False
                if not live_entries_enabled:
                    return _deny(bot, "LIVE_LOCK_R5")

        return True, ""
    except Exception:
        return _deny(bot, "GUARD_ERROR")
