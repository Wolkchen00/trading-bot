"""R24b PROOF: durust kill tasfiyesi , flat olana kadar birakilmaz.

Vaat: kill tetiklendikten sonra hicbir pozisyonun kaydi broker FLAT olmadan
silinmez; kapanmayan pozisyon stopsuz ve sahipsiz kalmaz; bot tasfiye bitene
kadar pesini birakmaz ve bu sure boyunca yeni risk acmaz.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from core.kill_liquidation import (
    acik_cikis_miktari,
    broker_envanteri,
    niyet_baslat,
    pending_oku,
    pending_temizle,
    pending_var_mi,
    pending_yaz,
    tasfiye_turu,
)
from core.risk_guard import _RISK_KINDS, can_open_new_risk

UTC = timezone.utc


# ----------------------------------------------------------------- sahteler
class SahtePoz:
    def __init__(self, symbol, qty):
        self.symbol = symbol
        self.qty = str(qty)


class SahteEmir:
    def __init__(self, symbol, side, qty, order_type="market", status="new"):
        self.symbol = symbol
        self.side = side
        self.qty = str(qty)
        self.order_type = order_type
        self.status = status


class SahteClient:
    def __init__(self, pozisyonlar=None, emirler=None, *, kapat_hatasi=None):
        self._poz = list(pozisyonlar or [])
        self._emir = list(emirler or [])
        self.kapat_cagrilari = []
        self.close_all_cagrildi = 0
        self.kapat_hatasi = kapat_hatasi

    def get_all_positions(self):
        return list(self._poz)

    def get_orders(self, filter=None):
        return list(self._emir)

    def close_position(self, symbol):
        self.kapat_cagrilari.append(symbol)
        if self.kapat_hatasi:
            raise RuntimeError(self.kapat_hatasi)
        return object()

    def close_all_positions(self, cancel_orders=True):
        self.close_all_cagrildi += 1
        return object()

    # test yardimcisi
    def flat_yap(self, symbol):
        self._poz = [p for p in self._poz if p.symbol != symbol]


class SahtePM:
    def __init__(self):
        self.stop_cagrilari = 0

    def ensure_protective_stops(self, config):
        self.stop_cagrilari += 1
        return type("S", (), {"failed": 0, "ok": True, "detail": ""})()


class SahteBot:
    def __init__(self, state_dir, client, *, is_paper=False):
        self._state_dir = str(state_dir)
        self.client = client
        self.is_paper = is_paper
        self.positions = {}
        self.short_positions = {}
        self.options_positions = {}
        self.position_manager = SahtePM()
        self.kill_switch = None
        self._auto_lock_memory = None
        self.uzlastirilan = []
        self.kritik = []
        self.equity = 491.0
        self.consecutive_errors = 0
        self._heartbeat_counter = 0

    def _reconcile_external_exit(self, symbol):
        self.uzlastirilan.append(symbol)

    def _funnel_bump(self, stage, reason=None, symbol=None):
        pass

    class _N:
        def __init__(self, k): self.k = k
        def notify_critical(self, b, m): self.k.append((b, m))
        def notify_kill_switch(self, r, e): self.k.append(("KILL", r))

    @property
    def notifier(self):
        return self._N(self.kritik)


CFG = {"kill_liquidation_timeout_min": 10}


def _gecmise_al(state_dir, dakika):
    """Tasfiye baslangicini ve son turu geriye cek (backoff'u atla)."""
    kayit, _ = pending_oku(str(state_dir))
    kayit["baslangic"] = (datetime.now(UTC) - timedelta(minutes=dakika)).isoformat()
    kayit.pop("son_tur", None)
    pending_yaz(str(state_dir), kayit)
    return kayit


# ============================================ (a) KABUL != DOLUM
def test_a_kabul_dolum_sayilmaz_kayit_DURUR(tmp_path):
    """close_all kabul edildi ama broker hala pozisyon gosteriyor."""
    client = SahteClient([SahtePoz("AAPL", 10)])
    bot = SahteBot(tmp_path, client)
    bot.positions["AAPL"] = {"qty": 10}

    niyet_baslat(bot, "gunluk kayip")
    kayit, hata = pending_oku(str(tmp_path))
    assert hata is None and "AAPL" in kayit["envanter"]

    _gecmise_al(tmp_path, 0)
    tasfiye_turu(bot, CFG)
    # Broker hala acik -> kayit DURUYOR, uzlastirma YOK
    kayit, _ = pending_oku(str(tmp_path))
    assert "AAPL" in kayit["envanter"], "pozisyon flat olmadan kayittan dusuruldu"
    assert bot.uzlastirilan == []
    assert "AAPL" in bot.positions, "yerel kayit erken silindi"


def test_a2_broker_flat_olunca_uzlastirilir_ve_duser(tmp_path):
    client = SahteClient([SahtePoz("AAPL", 10)])
    bot = SahteBot(tmp_path, client)
    bot.positions["AAPL"] = {"qty": 10}
    niyet_baslat(bot, "gunluk kayip")

    client.flat_yap("AAPL")
    _gecmise_al(tmp_path, 0)
    tasfiye_turu(bot, CFG)

    assert bot.uzlastirilan == ["AAPL"], "PnL/seri uzlastirmasi kosmadi"
    assert "AAPL" not in bot.positions
    kayit, hata = pending_oku(str(tmp_path))
    assert kayit is None and hata is None, "tasfiye bitince kayit temizlenmedi"


# ============================================ (b) CAGRI ONCESI KALICILIK
def test_b_niyet_cagriDAN_ONCE_yazilir(tmp_path, monkeypatch):
    """close_all istisna atsa bile niyet DISKTE olmali."""
    class PatlayanClient(SahteClient):
        def close_all_positions(self, cancel_orders=True):
            raise RuntimeError("broker timeout")

    client = PatlayanClient([SahtePoz("AAPL", 10)])
    bot = SahteBot(tmp_path, client)
    import stock_bot as sb
    sb.StockBot._emergency_close_all(bot, "test")

    kayit, hata = pending_oku(str(tmp_path))
    assert hata is None and kayit is not None
    assert "AAPL" in kayit["envanter"], "niyet cagri oncesi yazilmadi"


def test_b2_yeni_surec_tasfiyeyi_surdurur(tmp_path):
    """Kayit diskte; TAMAMEN YENI bot nesnesi isi devralir."""
    client = SahteClient([SahtePoz("AAPL", 10)])
    bot = SahteBot(tmp_path, client)
    niyet_baslat(bot, "crash testi")

    yeni_bot = SahteBot(tmp_path, client)      # yeni surec
    _gecmise_al(tmp_path, 0)
    tasfiye_turu(yeni_bot, CFG)
    assert client.kapat_cagrilari == ["AAPL"], "yeni surec tasfiyeyi surdurmedi"


# ================================ (e) TEK CIKIS OTORITESI , ters pozisyon yok
def test_e_aktif_tam_miktarli_close_varken_IKINCI_emir_YOK(tmp_path):
    """Iki SELL dolarsa ters (short) pozisyon acilir , kritik regresyon."""
    emirler = [SahteEmir("AAPL", "sell", 10, "market", "new")]
    client = SahteClient([SahtePoz("AAPL", 10)], emirler)
    bot = SahteBot(tmp_path, client)
    niyet_baslat(bot, "test")
    _gecmise_al(tmp_path, 0)
    tasfiye_turu(bot, CFG)

    assert client.kapat_cagrilari == [], "aktif cikis emri varken ikinci emir gonderildi"
    assert bot.position_manager.stop_cagrilari == 0, "close varken stop da gonderildi"


def test_e2_kismi_cikis_yetmez_yeniden_kapatilir(tmp_path):
    emirler = [SahteEmir("AAPL", "sell", 4, "market", "new")]   # 10'un 4'u
    client = SahteClient([SahtePoz("AAPL", 10)], emirler)
    bot = SahteBot(tmp_path, client)
    niyet_baslat(bot, "test")
    _gecmise_al(tmp_path, 0)
    tasfiye_turu(bot, CFG)
    assert client.kapat_cagrilari == ["AAPL"]


def test_e3_kapatma_basarisizsa_KORUYUCU_STOP_yerlestirilir(tmp_path):
    """Kapanmayan pozisyon stopsuz birakilmaz."""
    client = SahteClient([SahtePoz("AAPL", 10)], [], kapat_hatasi="reddedildi")
    bot = SahteBot(tmp_path, client)
    niyet_baslat(bot, "test")
    _gecmise_al(tmp_path, 0)
    tasfiye_turu(bot, CFG)

    assert client.kapat_cagrilari == ["AAPL"]
    assert bot.position_manager.stop_cagrilari == 1, "stopsuz birakildi"


def test_e4_emir_sorgusu_patlarsa_IKINCI_EMIR_GONDERILMEZ(tmp_path):
    """Belirsizlikte 'cikis emri VAR' varsayilir , ters pozisyon riski yok."""
    class KorClient(SahteClient):
        def get_orders(self, filter=None):
            raise RuntimeError("api down")

    client = KorClient([SahtePoz("AAPL", 10)])
    bot = SahteBot(tmp_path, client)
    pending_yaz(str(tmp_path), {
        "sebep": "t", "baslangic": datetime.now(UTC).isoformat(),
        "envanter": {"AAPL": {"qty": 10.0, "side": "long", "accounted": False}},
        "deneme_sayisi": 0,
    })
    toplam, _ = acik_cikis_miktari(client, "AAPL", True)
    assert toplam == float("inf")
    tasfiye_turu(bot, CFG)
    assert client.kapat_cagrilari == [], "belirsizlikte ikinci emir gonderildi"


# ==================================== (e2) GEC DOLAN GIRIS EMRI
def test_e2b_gec_dolan_pozisyon_envantere_ALINIR(tmp_path):
    client = SahteClient([SahtePoz("AAPL", 10)])
    bot = SahteBot(tmp_path, client)
    niyet_baslat(bot, "test")
    kayit, _ = pending_oku(str(tmp_path))
    assert sorted(kayit["envanter"]) == ["AAPL"]

    # Anlik goruntuden SONRA MSFT dolar
    client._poz.append(SahtePoz("MSFT", 3))
    _gecmise_al(tmp_path, 0)
    tasfiye_turu(bot, CFG)

    kayit, _ = pending_oku(str(tmp_path))
    assert "MSFT" in kayit["envanter"], "gec dolan pozisyon gozden kacti"
    assert "MSFT" in client.kapat_cagrilari


def test_envanter_acik_GIRIS_emrini_de_kapsar(tmp_path):
    client = SahteClient([], [SahteEmir("NVDA", "buy", 5, "market", "new")])
    envanter, hata = broker_envanteri(client)
    assert hata is None and "NVDA" in envanter
    assert envanter["NVDA"]["kaynak"] == "acik_giris_emri"


# ================================ (e3) MUHASEBE TAM BIR KEZ
def test_e3b_muhasebe_TAM_BIR_KEZ(tmp_path):
    """Uzlastirma ile kayit temizligi arasinda crash olsa bile seri bir kez."""
    client = SahteClient([SahtePoz("AAPL", 10)])
    bot = SahteBot(tmp_path, client)
    niyet_baslat(bot, "test")
    client.flat_yap("AAPL")

    _gecmise_al(tmp_path, 0)
    tasfiye_turu(bot, CFG)
    assert bot.uzlastirilan == ["AAPL"]

    # Kayit temizlendi; ikinci tur hicbir sey yapmamali
    tasfiye_turu(bot, CFG)
    assert bot.uzlastirilan == ["AAPL"], "muhasebe iki kez kosuldu"


def test_e3c_uzlastirma_patlarsa_accounted_ISARETLENMEZ(tmp_path):
    """Basarisiz muhasebe 'yapildi' sayilmaz , tekrar denenir."""
    client = SahteClient([SahtePoz("AAPL", 10)])
    bot = SahteBot(tmp_path, client)

    def patla(symbol):
        raise RuntimeError("ledger yazilamadi")
    bot._reconcile_external_exit = patla

    niyet_baslat(bot, "test")
    client.flat_yap("AAPL")
    _gecmise_al(tmp_path, 0)
    tasfiye_turu(bot, CFG)

    kayit, _ = pending_oku(str(tmp_path))
    assert kayit is not None and "AAPL" in kayit["envanter"]
    assert kayit["envanter"]["AAPL"]["accounted"] is False


# ================================ (d) SURE ASIMI , VAZGECME YOK
def test_d_sure_asimi_alarm_ve_kilit_ama_DONGU_DEVAM(tmp_path):
    client = SahteClient([SahtePoz("AAPL", 10)])
    bot = SahteBot(tmp_path, client)
    niyet_baslat(bot, "test")
    _gecmise_al(tmp_path, 15)          # 15 dk > 10 dk esik

    tasfiye_turu(bot, CFG)
    assert any(k[0] == "KILL_TASFIYE_ASIMI" for k in bot.kritik), "asim alarmi yok"
    assert bot._auto_lock_memory is not None, "asim otomatik kilit yazmadi"
    assert client.kapat_cagrilari == ["AAPL"], "asimda VAZGECILDI"

    kayit, _ = pending_oku(str(tmp_path))
    assert kayit["asim_alarmi"] is True
    # Alarm bir kez; dongu devam
    bot.kritik.clear()
    _gecmise_al(tmp_path, 20)
    kayit, _ = pending_oku(str(tmp_path))
    kayit["asim_alarmi"] = True
    pending_yaz(str(tmp_path), kayit)
    tasfiye_turu(bot, CFG)
    assert not any(k[0] == "KILL_TASFIYE_ASIMI" for k in bot.kritik), "alarm tekrarladi"
    assert client.kapat_cagrilari == ["AAPL", "AAPL"], "dongu durdu"


def test_d2_paperda_asim_kilit_YAZMAZ(tmp_path):
    client = SahteClient([SahtePoz("AAPL", 10)])
    bot = SahteBot(tmp_path, client, is_paper=True)
    niyet_baslat(bot, "test")
    _gecmise_al(tmp_path, 15)
    tasfiye_turu(bot, CFG)
    assert bot._auto_lock_memory is None
    assert any(k[0] == "KILL_TASFIYE_ASIMI" for k in bot.kritik), "paper alarmi da olmali"


# ================================ (g) KAPI VE SAGLIK
@pytest.mark.parametrize("kind", sorted(_RISK_KINDS))
def test_g_bekleyen_tasfiye_BUTUN_risk_turlerini_reddeder(tmp_path, kind):
    pending_yaz(str(tmp_path), {
        "sebep": "gunluk kayip", "baslangic": datetime.now(UTC).isoformat(),
        "envanter": {"AAPL": {"qty": 10.0, "side": "long", "accounted": False}},
    })

    class G:
        _state_dir = str(tmp_path)
        is_paper = False
        kill_switch = None
        _auto_lock_memory = None
        def _funnel_bump(self, *a, **k): pass

    izin, sebep = can_open_new_risk(
        G(), {"live_entries_enabled": True, "live_bear_entries_enabled": True},
        kind=kind, symbol="X",
    )
    assert (izin, sebep) == (False, "KILL_CLOSE_PENDING")


def test_g2_kill_dosyasi_silinse_bile_kapi_TUTAR(tmp_path):
    """Kill dosyasi elle silinip surec yeniden baslasa bile yeni risk YOK."""
    pending_yaz(str(tmp_path), {
        "sebep": "x", "baslangic": datetime.now(UTC).isoformat(),
        "envanter": {"AAPL": {"qty": 1.0, "side": "long"}},
    })
    bekleyen, _ = pending_var_mi(str(tmp_path))
    assert bekleyen is True


def test_g3_bozuk_pending_kaydi_BEKLEYEN_sayilir(tmp_path):
    (tmp_path / "kill_close_pending.json").write_text("{bozuk", encoding="utf-8")
    bekleyen, sebep = pending_var_mi(str(tmp_path))
    assert bekleyen is True and "OKUNAMADI" in sebep


def test_g4_kayit_yokken_kapi_ACIK(tmp_path):
    bekleyen, _ = pending_var_mi(str(tmp_path))
    assert bekleyen is False


def test_g5_saglik_bekleyen_tasfiyeyi_soyler(tmp_path):
    from core.health_status import Durum
    import tools.saglik as saglik

    pending_yaz(str(tmp_path), {
        "sebep": "gunluk kayip", "baslangic": datetime.now(UTC).isoformat(),
        "envanter": {"AAPL": {"qty": 10.0, "side": "long"}}, "deneme_sayisi": 3,
    })
    p = saglik.profil_sagligi("live", str(tmp_path), datetime.now(UTC))
    assert p.entry_authorization.durum is Durum.DEGRADED
    assert "KILL_CLOSE_PENDING" in p.entry_authorization.sebep
    assert "AAPL" in p.entry_authorization.sebep


# ================================ (f)(i) PARKING VE KISMI BASARI
def test_f_parking_da_envantere_girer(tmp_path):
    """Canli SPY parki yerel defterde YOK ama tasfiyede kapatilmali."""
    client = SahteClient([SahtePoz("SPY", 0.45)])
    bot = SahteBot(tmp_path, client)        # yerel defter BOS
    niyet_baslat(bot, "test")
    kayit, _ = pending_oku(str(tmp_path))
    assert "SPY" in kayit["envanter"], "parking envantere girmedi"


def test_i_kismi_basari_yalniz_FLAT_olani_duser(tmp_path):
    client = SahteClient([SahtePoz("AAPL", 10), SahtePoz("MSFT", 5)])
    bot = SahteBot(tmp_path, client)
    bot.positions.update({"AAPL": {}, "MSFT": {}})
    niyet_baslat(bot, "test")

    client.flat_yap("AAPL")               # yalniz biri kapandi
    _gecmise_al(tmp_path, 0)
    tasfiye_turu(bot, CFG)

    kayit, _ = pending_oku(str(tmp_path))
    assert sorted(kayit["envanter"]) == ["MSFT"]
    assert bot.uzlastirilan == ["AAPL"]
    assert "AAPL" not in bot.positions and "MSFT" in bot.positions


# ================================ BACKOFF
def test_backoff_cok_sik_tur_kosmaz(tmp_path):
    client = SahteClient([SahtePoz("AAPL", 10)])
    bot = SahteBot(tmp_path, client)
    niyet_baslat(bot, "test")
    _gecmise_al(tmp_path, 0)
    tasfiye_turu(bot, CFG)
    n = len(client.kapat_cagrilari)
    tasfiye_turu(bot, CFG)               # hemen ardindan
    assert len(client.kapat_cagrilari) == n, "backoff uygulanmadi"


# ============================ (c) GERCEK run() DONGUSU , yardimci cagrisi YETMEZ
def test_c_GERCEK_run_dongusu_kill_dalinda_uzlastirmayi_cagirir(tmp_path, monkeypatch):
    """Eski kod kill dalinda `sleep(60); continue` yapip HER SEYI atliyordu.

    Yardimci metodu dogrudan cagiran bir test bu hatayi YAKALAYAMAZDI: kod
    dogruydu, ama uretimde ASLA cagrilmiyordu. Bu test gercek `run()` dongusunu
    kosar.
    """
    import stock_bot as sb

    client = SahteClient([SahtePoz("AAPL", 10)])
    bot = SahteBot(tmp_path, client)
    niyet_baslat(bot, "gunluk kayip")
    _gecmise_al(tmp_path, 0)

    class _KS:
        is_active = True
        kill_reason = "gunluk kayip"
    bot.kill_switch = _KS()
    bot.ledger_sweep = type("L", (), {"maybe_run": lambda self: None})()

    class _DonguyuKes(BaseException):
        pass

    def _sleep(_saniye):
        raise _DonguyuKes()
    monkeypatch.setattr(sb.time, "sleep", _sleep)

    with pytest.raises(_DonguyuKes):
        sb.StockBot.run(bot)

    # Gercek dongu tasfiyeyi ILERLETTI mi?
    assert client.kapat_cagrilari == ["AAPL"], (
        "kill dalinda tasfiye turu KOSMADI , uretimde bekleyen tasfiye ilerlemez"
    )
    kayit, _ = pending_oku(str(tmp_path))
    assert kayit is not None and "AAPL" in kayit["envanter"]


def test_c2_run_dongusu_kill_dalinda_YENI_GIRIS_ACMAZ(tmp_path, monkeypatch):
    """Tasfiye kosuyor ama tarama/giris yolu CALISMAMALI."""
    import stock_bot as sb

    client = SahteClient([SahtePoz("AAPL", 10)])
    bot = SahteBot(tmp_path, client)
    niyet_baslat(bot, "test")
    _gecmise_al(tmp_path, 0)

    class _KS:
        is_active = True
        kill_reason = "test"
    bot.kill_switch = _KS()
    bot.ledger_sweep = type("L", (), {"maybe_run": lambda self: None})()

    cagrildi = []
    bot._daily_reset = lambda: cagrildi.append("daily_reset")
    bot._analyze_and_trade = lambda s, c: cagrildi.append("analyze")

    class _Kes(BaseException):
        pass
    monkeypatch.setattr(sb.time, "sleep", lambda _s: (_ for _ in ()).throw(_Kes()))

    with pytest.raises(_Kes):
        sb.StockBot.run(bot)

    assert cagrildi == [], f"kill dalinda giris yolu kosuldu: {cagrildi}"
    assert client.kapat_cagrilari == ["AAPL"], "tasfiye kosmadi"


# ============================ (h) COK-EMIRLI KISMI DOLUM MUHASEBESI
def test_h_cok_emirli_kismi_dolum_MIKTAR_AGIRLIKLI(tmp_path, monkeypatch):
    """Uc ayri execution -> PnL miktar agirlikli olmali, tek fiyat DEGIL.

    Eski kod EN YENI dolan cikis emrinin fiyatini TUM miktara uyguluyordu.
    Kill tasfiyesinin yeniden denemeleri kismi dolum uretir; tek fiyat
    PnL'i, ledger'i ve zarar serisini yanlis besler.
    """
    from datetime import datetime as _dt
    import stock_bot as sb
    from alpaca.trading.enums import OrderSide

    giris_zamani = (_dt.now() - timedelta(hours=2)).isoformat()

    class _O:
        def __init__(self, qty, price, ts):
            self.side = OrderSide.SELL
            self.filled_qty = str(qty)
            self.filled_avg_price = str(price)
            self.order_type = "market"
            self.id = f"o-{qty}-{price}"
            self.filled_at = ts

    ts = _dt.now() - timedelta(minutes=5)
    # 10 pay: 2@110, 3@120, 5@100 -> agirlikli ort = (220+360+500)/10 = 108.0
    emirler = [_O(2, 110, ts), _O(3, 120, ts), _O(5, 100, ts)]

    class _C:
        def get_orders(self, req=None):
            return list(emirler)

    bot = sb.StockBot.__new__(sb.StockBot)
    bot.client = _C()
    bot._state_dir = str(tmp_path)
    bot.is_paper = True
    bot.positions = {"AAPL": {
        "entry_price": 100.0, "qty": 10.0, "entry_time": giris_zamani,
    }}
    bot.short_positions = {}
    bot.options_positions = {}
    bot.trades_today = []
    bot._exit_flag_cache = {}
    bot._consecutive_losses = 0
    bot._last_loss_at = None
    bot._symbol_consecutive_losses = {}
    bot._symbol_last_loss_at = {}
    bot._streaks_by_profile = {}
    bot._daily_buys_count = 0
    bot.last_trade_time = {}
    bot.POSITIONS_FILE = str(tmp_path / "bot_positions.json")
    bot.wash_sale_tracker = type("W", (), {"record_loss_sale": lambda *a, **k: None})()
    bot.performance = type("P", (), {"record_trade": lambda *a, **k: None})()

    monkeypatch.setattr(
        sb.StockBot, "_already_recorded_exit", lambda *a, **k: False, raising=False
    )
    monkeypatch.setattr(sb.StockBot, "_stash_exit_flags", lambda *a, **k: None)

    sb.StockBot._reconcile_external_exit(bot, "AAPL", side="LONG")

    assert bot.trades_today, "dis kapanis kaydedilmedi"
    kayit = bot.trades_today[0]
    assert kayit["price"] == pytest.approx(108.0), (
        f"agirlikli ortalama yanlis: {kayit['price']} (tek-fiyat hatasi 100 verirdi)"
    )
    # PnL = (108 - 100) * 10 = 80
    assert kayit["pnl"] == pytest.approx(80.0)
