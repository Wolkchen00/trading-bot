"""R18 , GOLGE TOPLAYICI: ekle-sadece niyet/olay kaydi.

NEDEN VAR
---------
`LIVE_LOCK_R5` canli girisleri kesiyor ama stratejinin acilinca NE YAPACAGINI
kanitlamiyor. Kanit icin islem lazim, islem icin acik kilit, kilit icin kanit.
Bu kisir donguyu kiran tek sey golge kaydidir: kilit kapaliyken stratejinin ne
yapmak istedigini SIFIR DOLAR RISKLE diske yazmak.

KAPSAM SINIRI , BILINCLI
------------------------
Bu modul YALNIZ TOPLAR. IDDIA ETMEDIKLERI:
  - sonuc, getiri, PnL
  - dolum / dolmama, kayma, kismi dolum
  - cikis, stop, trailing
  - SPY karsilastirmasi
  - portfoy durumu, kapanmis episode
Dolum modellemesi ve cikis durum makinesi tekrari AYRI bir rock'tir
(RF-ISSUES-4.md::GOLGE-SONUC-ETIKETLEME). Toplama durust olmadan etiketleme
anlamsizdir; sira budur. Durumlu portfoy de oraya aittir: cikis tekrari
ertelenmisken nakdin ne zaman serbest kalacagini bilmenin yolu yoktur.

ASIRI SAYIM TUZAGI
------------------
Niyet MERKEZI KILIT REDDINDE yakalanir (core/risk_guard.can_open_new_risk).
Ama o noktada AŞAĞI-AKIŞ kontrolleri (equity floor, piyasa saati, nakit rezerv,
pozisyon boyutlandirma) HENUZ KOSMAMISTIR. Bunlari "gonderilecekti" saymak
asiri sayimdir. Bu yuzden her kayit `downstream_evaluated` bayragini ve
DEGERLENDIRILMEMIS kontrollerin listesini ACIKCA tasir. Etiketleyici bunlari
filtrelemek zorundadir; sessizce "islem acilacakti" denemez.

KIMLIK
------
Her kayit commit_sha + epoch_id + etkin karar profilinin kanonik hash'ini tasir.
Kimlik eksikse kayit `kapi_uygunsuz` isaretlenir: kimliksiz gozlem kanit degildir.

SINIR ve YANLILIK
-----------------
Boyut tavani MEVCUT EPOCH ICINDE de uygulanir (yoksa tek bir epoch tavani asar).
Dusurme rastgele degil DETERMINISTIK TABAKALI ornekleme ile yapilir ve dusurulen
sayi kaydedilir. Kapsama esigi tutmuyorsa kume `eksik` isaretlenir ve sonraki
kapi bunu UNKNOWN saymak zorundadir.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from utils.logger import logger

SCHEMA_VERSION = 1

# Etiketleyici bu surumu okuyup uyumu kontrol eder. Alan anlami degisirse ARTAR.
LABEL_CONTRACT_VERSION = 1

# Kilit reddi anında HENUZ KOSMAMIS aşağı-akış kontrolleri. Etiketleyici bunlari
# filtrelemek ZORUNDA; aksi halde "gonderilecekti" sayisi sisirilir.
DEGERLENDIRILMEMIS_KONTROLLER = (
    "equity_floor",
    "cash_reserve",
    "position_sizing",
    "asset_tradability",
    "buying_power",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _kanonik(veri: Any) -> str:
    """Sirali, kararli JSON , hash icin."""
    return json.dumps(veri, sort_keys=True, separators=(",", ":"), default=str)


def commit_sha() -> str:
    """Deploy edilen commit. Konteynerde .git yoktur; Coolify env enjekte eder."""
    try:
        import subprocess
        kok = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=kok, capture_output=True,
            text=True, timeout=5,
        )
        sha = (r.stdout or "").strip()
        if sha:
            return sha
    except Exception:
        pass
    return (
        os.environ.get("SOURCE_COMMIT")
        or os.environ.get("BOT_COMMIT_SHA")
        or "UNKNOWN"
    ).strip() or "UNKNOWN"


def profil_hash() -> str:
    """ETKIN karar profilinin kanonik hash'i.

    Config hash TEK BASINA yetmez ve commit TEK BASINA yetmez: v4.16'da paper
    config degismedi ama davranis degisti. Ikisi birlikte epoch'u ayirir.
    Kapsam: env bayraklari + ajan + short + option + rejim + kapi ayarlari.
    """
    try:
        from config import (
            AGENT_CONFIG,
            AV_QUOTA_CONFIG,
            SHORT_CONFIG,
            STOCK_CONFIG,
            TRADING_MODE,
        )
        yuk = {
            "trading_mode": TRADING_MODE,
            "stock": {
                k: STOCK_CONFIG.get(k) for k in sorted(STOCK_CONFIG)
                if not callable(STOCK_CONFIG.get(k))
            },
            "short": {k: SHORT_CONFIG.get(k) for k in sorted(SHORT_CONFIG)},
            "agent": dict(sorted(AGENT_CONFIG.items())),
            "av_quota": {
                k: AV_QUOTA_CONFIG.get(k) for k in sorted(AV_QUOTA_CONFIG)
            },
        }
        return hashlib.sha256(_kanonik(yuk).encode("utf-8")).hexdigest()
    except Exception as exc:
        logger.debug(f"Golge defter profil hash'i uretilemedi: {exc}")
        return "UNKNOWN"


def epoch_id(sha: Optional[str] = None, phash: Optional[str] = None) -> str:
    """Epoch = commit + profil. Ikisinden biri degisince gozlemler AYRISIR."""
    sha = sha or commit_sha()
    phash = phash or profil_hash()
    return hashlib.sha256(f"{sha}|{phash}".encode("utf-8")).hexdigest()[:16]


class ShadowLedger:
    """Ekle-sadece niyet/olay toplayicisi. Emir GONDERMEZ, kapi DEGISTIRMEZ."""

    def __init__(
        self,
        path: Optional[str] = None,
        *,
        max_bytes: int = 8 * 1024 * 1024,
        now_fn=None,
        quote_source: str = "alpaca:StockHistoricalData:IEX",
    ) -> None:
        self.path = path or self._default_path()
        self.max_bytes = int(max_bytes)
        self._now_fn = now_fn or _now
        # DEGISMEZ FIYAT KAYNAGI , etiketleyici bu tanimla yeniden cekecek.
        # Toplama baslamadan ONCE adlandirilmasi Codex sarti; sadece giris ani
        # bid/ask'i sonraki stop/trailing/dolum tekrari icin YETMEZ.
        self.quote_source = quote_source
        self._seq = 0
        self._sha = commit_sha()
        self._phash = profil_hash()
        self._epoch = epoch_id(self._sha, self._phash)
        self.dropped = 0

    @staticmethod
    def _default_path() -> str:
        try:
            from config import state_path
            return state_path("shadow_intents.jsonl")
        except Exception:
            return "shadow_intents.jsonl"

    # ------------------------------------------------------------------ kimlik

    def kimlik(self) -> dict:
        eksik = (
            self._sha == "UNKNOWN"
            or self._phash == "UNKNOWN"
        )
        return {
            "commit_sha": self._sha,
            "profile_hash": self._phash,
            "epoch_id": self._epoch,
            "schema_version": SCHEMA_VERSION,
            "label_contract_version": LABEL_CONTRACT_VERSION,
            # Kimliksiz gozlem KANIT DEGILDIR , kapi bunu disarida birakmali.
            "kapi_uygunsuz": bool(eksik),
        }

    def _intent_id(self, symbol: str, kind: str, ts: datetime) -> str:
        """Kalici, benzersiz niyet kimligi.

        Etiketler ve yasam dongusu olaylari buna baglanacak; sonradan uretilen
        bir kimlik toplanmis veriyi yeniden yazmayi gerektirirdi.
        """
        ham = f"{self._epoch}|{symbol}|{kind}|{ts.isoformat()}|{self._seq}"
        return hashlib.sha256(ham.encode("utf-8")).hexdigest()[:24]

    # ------------------------------------------------------------------ yazma

    def record_lock_rejection(
        self,
        *,
        symbol: str,
        kind: str,
        block_reason: str,
        decision: Optional[dict] = None,
        analysis: Optional[dict] = None,
        market_status: Optional[dict] = None,
        quote: Optional[dict] = None,
        asset_caps: Optional[dict] = None,
        order_params: Optional[dict] = None,
        state_snapshot: Optional[dict] = None,
    ) -> Optional[str]:
        """Kilit reddini kaydet. Doner: intent_id (yazilamadiysa None).

        SALT GOZLEM: hicbir emir metodu cagrilmaz, hicbir kapi degistirilmez.
        Arizasi cagirana SIZMAZ , kayit hatasi karari DEGISTIREMEZ (R13 disiplini).
        """
        try:
            ts = self._now_fn()
            self._seq += 1
            iid = self._intent_id(symbol, kind, ts)

            kayit = {
                "intent_id": iid,
                "seq": self._seq,
                "ts_utc": ts.isoformat(),
                "symbol": symbol,
                "kind": kind,
                "block_reason": block_reason,

                # --- kimlik
                "identity": self.kimlik(),

                # --- ORTAK KARAR ORNEGI (marjinal sayac DEGIL)
                "decision": self._karar_ozeti(decision),

                # --- girdi anlik goruntusu
                "analysis": self._analiz_ozeti(analysis),
                "market_status": dict(market_status or {}),
                "state_snapshot": dict(state_snapshot or {}),

                # --- emir parametreleri (boyut BU NOKTADA hesaplanmamistir)
                "order_params": dict(order_params or {}),
                "asset_capabilities": dict(asset_caps or {}),

                # --- fiyat gozlemi + DEGISMEZ KAYNAK TANIMI
                "quote": dict(quote or {}),
                "quote_source": self.quote_source,

                # --- ASIRI SAYIM KORUMASI
                "downstream_evaluated": False,
                "downstream_unevaluated_checks": list(
                    DEGERLENDIRILMEMIS_KONTROLLER
                ),
                "downstream_note": (
                    "Kilit reddi aninda equity floor / nakit rezerv / boyutlandirma "
                    "HENUZ KOSMAMISTIR. Bu kayit 'islem acilacakti' ANLAMINA GELMEZ; "
                    "etiketleyici bu kontrolleri filtrelemek ZORUNDADIR."
                ),

                # --- ETIKET BAGLANTI NOKTASI (sonraki rock doldurur)
                "label_ref": None,

                # --- ACIKCA IDDIA EDILMEYENLER
                "not_claimed": [
                    "outcome", "return", "pnl", "fill", "slippage",
                    "partial_fill", "exit", "benchmark", "portfolio_state",
                    "closed_episode",
                ],
            }
            return iid if self._ekle(kayit) else None
        except Exception as exc:
            # Kayit arizasi KARARI DEGISTIREMEZ.
            logger.debug(f"Golge defter kaydi basarisiz ({symbol}): {exc}")
            return None

    @staticmethod
    def _karar_ozeti(decision: Optional[dict]) -> dict:
        """Ortak (joint) karar ornegi , oy + guven + agirlik BIR ARADA.

        Mevcut agent_stats marjinal histogram tutuyor; ondan kalibrasyon
        yapilamaz. Burada her karar TEK KAYITTA saklanir.
        """
        d = decision or {}
        oylar = []
        for v in (d.get("votes") or []):
            if isinstance(v, dict):
                oylar.append({
                    "agent": v.get("agent"),
                    "signal": v.get("signal"),
                    "confidence": v.get("confidence"),
                })
        return {
            "signal": d.get("signal"),
            "confidence": d.get("confidence"),
            "weighted_score": d.get("weighted_score"),
            "majority": d.get("majority"),
            "risk_veto": d.get("risk_veto"),
            "buy_count": d.get("buy_count"),
            "sell_count": d.get("sell_count"),
            "hold_count": d.get("hold_count"),
            "votes": oylar,
            "dynamic_weights": dict(d.get("dynamic_weights") or {}),
        }

    @staticmethod
    def _analiz_ozeti(analysis: Optional[dict]) -> dict:
        a = analysis or {}
        return {
            "price": a.get("price"),
            "atr": a.get("atr"),
            "confidence": a.get("confidence"),
            "rsi": a.get("rsi"),
            "sector_weight": a.get("sector_weight"),
            # R17'den gelen bayatlik damgasi , kararin hangi yasta veriyle
            # verildigi etiketleme sirasinda kritik.
            "fundamental_data_age_hours": a.get("fundamental_data_age_hours"),
            "fundamental_is_stale": a.get("fundamental_is_stale"),
            "fundamental_data_source": a.get("fundamental_data_source"),
            "fundamental_data_ok": a.get("fundamental_data_ok"),
        }

    # ------------------------------------------------------------------ sinir

    def _ekle(self, kayit: dict) -> bool:
        """EKLE-SADECE yazim + boyut tavani (mevcut epoch ICINDE de)."""
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            satir = json.dumps(kayit, ensure_ascii=False, default=str)

            if os.path.exists(self.path):
                boyut = os.path.getsize(self.path)
                if boyut + len(satir) + 1 > self.max_bytes:
                    self._budama()

            with open(self.path, "a", encoding="utf-8") as f:
                f.write(satir + "\n")
            return True
        except Exception as exc:
            logger.debug(f"Golge defter yazilamadi: {exc}")
            return False

    def _budama(self) -> None:
        """DETERMINISTIK TABAKALI budama , rastgele degil.

        "Mevcut epoch'a dokunma" kurali kendi tavanini ihlal ederdi: tek bir
        epoch tek basina tavani asabilir. Bu yuzden budama MEVCUT epoch icinde
        de calisir, ama YANLILIK YARATMADAN: her epoch icinden esit araliklarla
        ornek alinir (rastgele degil, deterministik), ve DUSURULEN SAYI kaydedilir.
        """
        try:
            satirlar = []
            with open(self.path, "r", encoding="utf-8") as f:
                for s in f:
                    s = s.strip()
                    if s:
                        satirlar.append(s)
            if not satirlar:
                return

            # Epoch'a gore tabakala
            tabakalar: Dict[str, List[str]] = {}
            for s in satirlar:
                try:
                    e = (json.loads(s).get("identity") or {}).get("epoch_id", "?")
                except Exception:
                    e = "?"
                tabakalar.setdefault(str(e), []).append(s)

            hedef = max(1, self.max_bytes // 2)
            tutulan: List[str] = []
            toplam = 0
            # Her tabakadan ORANTILI pay , mevcut epoch da budanir.
            pay = max(1, hedef // max(1, len(tabakalar)))
            for e, kume in tabakalar.items():
                alt_toplam = 0
                # Deterministik esit aralikli ornekleme: en yeniden geriye,
                # sabit adimla. Rastgelelik YOK (tekrarlanabilirlik sart).
                adim = 1
                tahmini = sum(len(x) + 1 for x in kume)
                if tahmini > pay:
                    adim = max(1, tahmini // max(1, pay))
                secilen = kume[::-1][::adim][::-1]
                for s in secilen:
                    if alt_toplam + len(s) + 1 > pay:
                        break
                    tutulan.append(s)
                    alt_toplam += len(s) + 1
                toplam += alt_toplam

            self.dropped += len(satirlar) - len(tutulan)
            logger.warning(
                f"  GOLGE DEFTER BUDAMA: {len(satirlar)} -> {len(tutulan)} kayit "
                f"(toplam dusurulen {self.dropped}). Deterministik tabakali "
                f"ornekleme; kapsama esigi dusuyorsa kume EKSIK sayilmali."
            )

            dizin = os.path.dirname(self.path) or "."
            fd, gecici = tempfile.mkstemp(dir=dizin, prefix=".shw", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write("\n".join(tutulan) + ("\n" if tutulan else ""))
            os.replace(gecici, self.path)

            # DUSURULEN SAYI ACIKCA kaydedilir , sessiz veri kaybi yok.
            self._budama_kaydi()
        except Exception as exc:
            logger.debug(f"Golge defter budamasi basarisiz: {exc}")

    def _budama_kaydi(self) -> None:
        try:
            yol = self.path + ".meta.json"
            kayit = {"dropped_total": self.dropped,
                     "last_prune_utc": self._now_fn().isoformat()}
            with open(yol, "w", encoding="utf-8") as f:
                json.dump(kayit, f, indent=2)
        except Exception:
            pass

    # ------------------------------------------------------------------ okuma

    def read_all(self) -> List[dict]:
        out = []
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                for s in f:
                    s = s.strip()
                    if not s:
                        continue
                    try:
                        out.append(json.loads(s))
                    except Exception:
                        continue      # bozuk satir atlanir, coker degil
        except FileNotFoundError:
            pass
        except Exception as exc:
            logger.debug(f"Golge defter okunamadi: {exc}")
        return out

    def ozet(self) -> dict:
        kayitlar = self.read_all()
        epochlar: Dict[str, int] = {}
        uygunsuz = 0
        for k in kayitlar:
            kim = k.get("identity") or {}
            e = str(kim.get("epoch_id", "?"))
            epochlar[e] = epochlar.get(e, 0) + 1
            if kim.get("kapi_uygunsuz"):
                uygunsuz += 1
        return {
            "toplam_niyet": len(kayitlar),
            "epochlar": epochlar,
            "mevcut_epoch": self._epoch,
            "kimligi_eksik": uygunsuz,
            "dusurulen": self.dropped,
            "sema_surumu": SCHEMA_VERSION,
            "iddia_edilmeyen": [
                "outcome", "return", "fill", "exit", "benchmark",
                "portfolio_state", "closed_episode",
            ],
        }


_paylasilan: Optional[ShadowLedger] = None


def shared_ledger() -> ShadowLedger:
    global _paylasilan
    if _paylasilan is None:
        _paylasilan = ShadowLedger()
    return _paylasilan


def reset_shared_ledger() -> None:
    """Yalniz testler icin."""
    global _paylasilan
    _paylasilan = None
