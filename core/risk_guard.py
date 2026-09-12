"""Yeni risk acan tum emir yollari icin merkezi guvenlik kapisi."""

from utils.logger import logger


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


def _bayrak(config, anahtar: str) -> bool:
    """Bayragi once cagri config'inden, yoksa STOCK_CONFIG'ten oku , fail-closed."""
    if anahtar in config:
        return bool(config.get(anahtar, False))
    try:
        from config import STOCK_CONFIG
        return bool(STOCK_CONFIG.get(anahtar, False))
    except Exception:
        return False


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

        try:
            from config import TRADING_MODE
            default_paper = TRADING_MODE != "live"
        except Exception:
            default_paper = True
        is_live = not bool(getattr(bot, "is_paper", default_paper))

        # R24a , OTOMATIK KILIT: BUTUN yeni risk turlerini kapsar, parking DAHIL.
        # Parking R5'ten muaftir (savunma amacli nakit parki) ama emniyet
        # kilidinden muaf DEGILDIR: bot kendi durumundan emin degilken yeni
        # ALIM yapmaz. Parking COZME (satis) ve tum cikis/koruma yollari bu
        # kapidan gecmez, dolayisiyla serbest kalir.
        if is_live:
            from core.safety_state import (
                auto_lock_durumu, guvenlik_dizini_yazilabilir,
            )
            # State dizini bot'a degil KURULUMA ait bir gercektir; bot uzerinde
            # yoksa config'ten okunur. Ikisi de yoksa fail-closed.
            state_dir = getattr(bot, "_state_dir", None)
            if not state_dir:
                try:
                    from config import STATE_DIR
                    state_dir = STATE_DIR
                except Exception:
                    state_dir = None
            if not state_dir:
                logger.error("  State dizini belirlenemedi , canli giris reddedildi")
                return _deny(bot, "LIVE_AUTO_LOCK")
            yazilabilir, yazma_hatasi = guvenlik_dizini_yazilabilir(state_dir)
            if not yazilabilir:
                # Kilit YAZILAMIYORSA canli giris BASTAN reddedilir: aksi halde
                # bir arizada kilit yazilamaz ve restart'ta buharlasir.
                logger.error(
                    f"  Guvenlik dizini YAZILAMIYOR ({yazma_hatasi}) , "
                    "canli giris reddedildi"
                )
                return _deny(bot, "LIVE_AUTO_LOCK")
            kilitli, kilit_sebep = auto_lock_durumu(bot, state_dir)
            if kilitli:
                logger.error(f"  OTOMATIK KILIT aktif ({kilit_sebep}) , yeni risk YOK")
                return _deny(bot, "LIVE_AUTO_LOCK")

        # R24b , BEKLEYEN KILL TASFIYESI: tasfiye bitmeden yeni risk ACILMAZ.
        # Kill dosyasi elle silinip surec yeniden baslatilsa BILE bu kapi
        # tutar; bekleyen kayit kendi basina bir giris kilididir.
        # Paper'da da gecerlidir: yarim tasfiyenin ustune yeni pozisyon acmak
        # olcumu de bozar.
        from core.kill_liquidation import pending_var_mi
        state_dir_k = getattr(bot, "_state_dir", None)
        if not state_dir_k:
            try:
                from config import STATE_DIR
                state_dir_k = STATE_DIR
            except Exception:
                state_dir_k = None
        if state_dir_k:
            bekleyen, bekleyen_sebep = pending_var_mi(state_dir_k)
            if bekleyen:
                logger.error(
                    f"  BEKLEYEN KILL TASFIYESI ({bekleyen_sebep}) , yeni risk YOK"
                )
                return _deny(bot, "KILL_CLOSE_PENDING")

        # Parking strateji girisi degil, savunma amacli nakit parkidir. R5 canli
        # giris kilidinden muaftir; kill/risk-halt kapilari yine yukarida gecerlidir.
        if kind != "index_parking":
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

        # R24a , BEARBRAIN AYRI KILIT. R5'TEN SONRA bakilir: R5 kapaliyken asil
        # sebep R5'tir, bear kilidi ancak R5 ACIKKEN anlamlidir. Tek basina
        # LIVE_ENTRIES_ENABLED=true ters-ETF ACMAZ (Ihsan karari 2026-09-12:
        # bear canlida KAPALI kalir, kendi anahtarini ister).
        if is_live and kind == "bear_etf":
            if not _bayrak(config, "live_bear_entries_enabled"):
                return _deny(bot, "LIVE_BEAR_LOCK")

        return True, ""
    except Exception:
        return _deny(bot, "GUARD_ERROR")
