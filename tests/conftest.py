"""Test izolasyonu (v4.12.2).

test_full_system.py script-stilidir: modül IMPORT anında koşar ve config/logger
zinciri üzerinden GERÇEK state ve log dosyalarına yazar. 11-13 Tem vakası:
pytest koşuları logs/bot_*.log'u mock çıktıyla doldurdu ve state_paper'daki
kurtarılmış 145KB agent_performance.json'ı ezdi (kalıcı veri kaybı).

Bu conftest, herhangi bir repo modülü import edilmeden ÖNCE state kökünü ve
log dizinini geçici bir dizine yönlendirir:
  - STATE_VOLUME_PATH -> config.STATE_DIR = <tmp>/state_paper (mevcut mekanizma)
  - BOT_LOG_DIR       -> utils/logger dosya handler'ı <tmp>/logs'a yazar

setdefault kullanılır: bilinçli olarak dışarıdan verilen override'lar korunur.
"""
import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="trading_bot_test_")
os.environ.setdefault("STATE_VOLUME_PATH", _tmp)
os.environ.setdefault("BOT_LOG_DIR", os.path.join(_tmp, "logs"))
