"""
Kill Switch - Acil durum güvenlik modülü.
Felaket senaryolarında botu otomatik durdurup tüm pozisyonları kapatır.

Tetikleyiciler:
1. Ardışık API hataları (3+)
2. Günlük kayıp limiti aşımı (%5)
3. Beklenmedik büyük pozisyon değer düşüşü
4. Manuel tetikleme (kullanıcı)
"""
import os
import json
from datetime import datetime
from typing import Optional, Callable
from utils.logger import logger


class KillSwitch:
    """Acil durum botu durdurucu."""

    def __init__(
        self,
        max_consecutive_errors: int = 3,
        max_daily_loss_pct: float = 0.05,  # %5
        kill_file: str = None,
    ):
        self.max_consecutive_errors = max_consecutive_errors
        self.max_daily_loss_pct = max_daily_loss_pct
        if kill_file is None:
            try:
                from config import state_path
                kill_file = state_path("kill_switch.json")
            except Exception:
                kill_file = "kill_switch.json"
        self.kill_file = kill_file
        self.consecutive_errors = 0
        self.is_killed = False
        self.kill_reason = ""
        self.on_kill_callback: Optional[Callable] = None

        # Önceki kill durumunu kontrol et
        if os.path.exists(kill_file):
            try:
                with open(kill_file, "r") as f:
                    data = json.load(f)
                if data.get("killed", False):
                    # PAPER: "günlük" kayıp kill'i yeni ET gününde OTOMATİK sıfırlanır.
                    # 6 May 2026: paper -%3.17 kill dosyası 2 ay botu sessizce kilitledi
                    # (günlük limit kalıcı kilide dönüşüyordu). LIVE bilinçli olarak
                    # manuel kalır (gerçek parada insan onayı şart).
                    stale_daily = False
                    try:
                        from config import TRADING_MODE
                        if TRADING_MODE != "live" and "Günlük" in str(data.get("reason", "")):
                            ts = datetime.fromisoformat(data.get("timestamp", ""))
                            stale_daily = ts.date() < datetime.now().date()
                    except Exception:
                        pass
                    if stale_daily:
                        try:
                            os.remove(kill_file)
                        except Exception:
                            pass
                        logger.warning(
                            "⚠️ PAPER: önceki güne ait GÜNLÜK-kayıp kill dosyası "
                            "otomatik sıfırlandı (yeni işlem günü)."
                        )
                    else:
                        self.is_killed = True
                        self.kill_reason = data.get("reason", "Önceki oturumdan kill")
                        logger.error(
                            f"🚨 KILL SWITCH AKTİF (önceki oturum): {self.kill_reason}\n"
                            f"   Tekrar başlatmak için kill_switch.json dosyasını silin."
                        )
            except Exception:
                pass

        logger.info(
            f"KillSwitch başlatıldı - "
            f"Max hata: {max_consecutive_errors}, "
            f"Max kayıp: {max_daily_loss_pct:.0%}"
        )

    def check_api_error(self, error: Exception) -> bool:
        """
        API hatasını kaydeder. Ardışık hata limiti aşılırsa kill tetikler.
        Returns: True = kill tetiklendi
        """
        self.consecutive_errors += 1
        logger.warning(
            f"⚠️ API Hatası ({self.consecutive_errors}/{self.max_consecutive_errors}): "
            f"{str(error)[:100]}"
        )

        if self.consecutive_errors >= self.max_consecutive_errors:
            self._trigger_kill(
                f"Ardışık {self.consecutive_errors} API hatası! "
                f"Son hata: {str(error)[:200]}"
            )
            return True
        return False

    def reset_error_count(self):
        """Başarılı API çağrısı sonrası hata sayacını sıfırlar."""
        if self.consecutive_errors > 0:
            self.consecutive_errors = 0

    def check_daily_loss(self, equity: float, starting_equity: float) -> bool:
        """
        Günlük kayıp kontrolü. Limit aşılırsa kill tetikler.
        Returns: True = kill tetiklendi
        """
        if starting_equity <= 0:
            return False

        daily_change_pct = (equity - starting_equity) / starting_equity

        if daily_change_pct <= -self.max_daily_loss_pct:
            self._trigger_kill(
                f"Günlük kayıp limiti aşıldı! "
                f"Değişim: {daily_change_pct:.2%} "
                f"(limit: -{self.max_daily_loss_pct:.0%}) "
                f"Bakiye: ${equity:,.2f} → ${starting_equity:,.2f}"
            )
            return True
        return False

    def manual_kill(self, reason: str = "Manuel tetikleme"):
        """Kullanıcı tarafından manuel kill."""
        self._trigger_kill(reason)

    def _trigger_kill(self, reason: str):
        """Kill switch'i tetikler."""
        self.is_killed = True
        self.kill_reason = reason
        timestamp = datetime.now().isoformat()

        logger.error("=" * 60)
        logger.error("🚨🚨🚨 KILL SWITCH TETİKLENDİ 🚨🚨🚨")
        logger.error(f"  Sebep: {reason}")
        logger.error(f"  Zaman: {timestamp}")
        logger.error("  TÜM POZİSYONLAR KAPATILACAK!")
        logger.error("=" * 60)

        # Durumu dosyaya kaydet
        try:
            with open(self.kill_file, "w") as f:
                json.dump({
                    "killed": True,
                    "reason": reason,
                    "timestamp": timestamp,
                }, f, indent=2)
        except Exception as e:
            logger.error(f"Kill durumu kayıt hatası: {e}")

        # Callback çalıştır (pozisyon kapatma vb.)
        if self.on_kill_callback:
            try:
                self.on_kill_callback(reason)
            except Exception as e:
                logger.error(f"Kill callback hatası: {e}")

    def set_callback(self, callback: Callable):
        """Kill tetiklendiğinde çağrılacak fonksiyon (pozisyon kapatma vb.)."""
        self.on_kill_callback = callback

    def reset(self):
        """Kill switch'i sıfırlar (tekrar başlatma)."""
        self.is_killed = False
        self.kill_reason = ""
        self.consecutive_errors = 0
        if os.path.exists(self.kill_file):
            os.remove(self.kill_file)
        logger.info("✅ Kill switch sıfırlandı")

    @property
    def is_active(self) -> bool:
        """Kill switch aktif mi?"""
        return self.is_killed
