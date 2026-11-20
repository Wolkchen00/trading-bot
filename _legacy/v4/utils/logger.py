"""
Loglama Sistemi - Tüm bot aktivitelerini loglar.
Process ID ekleyerek çift instance sorunlarını tespit eder.

Docker/Coolify uyumlu: hem dosyaya hem stdout'a yazar.
stdout flush ile Docker log driver'ın yakalamasını garanti eder.
"""
import logging
import os
import sys
from datetime import datetime
from config import LOG_CONFIG


class FlushStreamHandler(logging.StreamHandler):
    """Her log satırından sonra flush yapan StreamHandler.
    Docker/Coolify loglarında anında görünmesini sağlar."""

    def emit(self, record):
        super().emit(record)
        self.flush()


def setup_logger(name: str = "TradingBot") -> logging.Logger:
    """Ana logger'ı oluşturur ve yapılandırır. PID ile çoklu instance tespiti."""
    log_dir = LOG_CONFIG["log_dir"]
    os.makedirs(log_dir, exist_ok=True)

    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)  # Logger seviyesi en düşük, handler'lar filtreler
    # v4.10: root'a propagation KAPALI — root'taki ek handler'lar (eski stock_bot
    # bloğu + bağımlılıkların basicConfig'i) her satırı 3 kez bastırıyordu.
    # Tek çıkış noktası: aşağıdaki console + file handler.
    logger.propagate = False

    # Format — PID eklendi (çift instance tespiti için)
    pid = os.getpid()
    formatter = logging.Formatter(
        f"%(asctime)s | %(levelname)-8s | %(name)s[{pid}] | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if not logger.handlers:
        # Dosya handler - her gün ayrı log (DEBUG seviyesi)
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            file_handler = logging.FileHandler(
                os.path.join(log_dir, f"bot_{today}.log"), encoding="utf-8"
            )
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except Exception:
            # Docker container'da dosya yazılamazsa devam et
            pass

        # Konsol handler — stdout'a yaz + her satır flush (Docker uyumlu)
        console_handler = FlushStreamHandler(sys.stdout)
        console_handler.setLevel(getattr(logging, LOG_CONFIG.get("log_level", "INFO")))
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    return logger


# Global logger instance
logger = setup_logger()
