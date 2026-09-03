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


def _golge_kaydet(bot, config, kind: str, symbol: str) -> None:
    """R18: R5 kilidiyle reddedilen niyeti golge deftere yaz.

    BURASI TEK NOKTADIR: bes ayri executor (stock/short/option/bear_etf/
    parking) bu kapidan geciyor, dolayisiyla niyet tek yerde ve TUTARLI
    sekilde yakalanir. Coordinator kararini kaydetmek yanlis olurdu:
    asagi-akis kontrollerinde elenecek adaylari da "gonderilecekti" sayardi.

    SALT GOZLEM: kapi kararini DEGISTIRMEZ, arizasi cagirana SIZMAZ.
    """
    try:
        from core.shadow_ledger import shared_ledger

        analysis = getattr(bot, "_son_analiz", {}).get(symbol) or {}
        decision = getattr(bot, "_son_karar", {}).get(symbol) or {}

        # Piyasa durumu YEREL bir hesap , broker cagrisi yok, hot path guvenli.
        market = {}
        mh = getattr(bot, "market_hours", None)
        if mh is not None:
            try:
                market = dict(mh.get_market_status() or {})
            except Exception:
                market = {}

        shared_ledger().record_lock_rejection(
            symbol=symbol,
            kind=kind,
            block_reason="LIVE_LOCK_R5",
            decision=decision,
            analysis=analysis,
            market_status=market,
            quote={
                "price": analysis.get("price"),
                "observed_at": None,
                "note": (
                    "Giris ani fiyati; bid/ask ve yasam dongusu gozlemleri "
                    "etiketleyici tarafindan quote_source uzerinden yeniden "
                    "cekilecek. Tek bir giris fiyati stop/trailing/dolum "
                    "tekrari icin YETMEZ."
                ),
            },
            order_params={
                "side": "buy" if kind in ("stock_long", "bear_etf",
                                          "index_parking") else "sell",
                "size_usd": None,   # BU NOKTADA HESAPLANMAMISTIR
                "qty": None,
                "type": "market",
                "note": "boyutlandirma kilit reddinden SONRA kosuyor",
            },
            state_snapshot={
                "open_positions": len(getattr(bot, "positions", {}) or {}),
                "consecutive_losses": getattr(bot, "_consecutive_losses", None),
                "market_regime": getattr(bot, "_market_regime", None),
                "floor_block": getattr(bot, "_floor_block", None),
            },
        )
    except Exception:
        pass   # golge kaydi ASLA kapi kararini etkilemez


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
                    # R18: kilit kapaliyken stratejinin NE YAPMAK ISTEDIGINI
                    # sifir dolar riskle kaydet. Kanit icin islem, islem icin
                    # acik kilit, kilit icin kanit gerekiyordu , bu kisir
                    # donguyu kiran tek sey golge kaydidir.
                    _golge_kaydet(bot, config, kind, symbol)
                    return _deny(bot, "LIVE_LOCK_R5")

        return True, ""
    except Exception:
        return _deny(bot, "GUARD_ERROR")
