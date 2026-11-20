@echo off
REM ============================================================
REM  LIVE TRACK - GERCEK PARA - SADECE LONG (al-sat), konservatif
REM ============================================================
REM  Mod/anahtar otomatik: TRADING_MODE=live -> LIVE Alpaca anahtari + live endpoint
REM  BOT_MODE=long_only -> short YOK, options YOK (yalniz uzun pozisyon)
REM  De-risk aktif: max $200/pozisyon, min_conf 60, %3 gunluk kill, %85 floor
REM  Durdurmak: bu pencerede Ctrl+C  (veya klasorde STOP_BOT dosyasi olustur)
REM ============================================================
cd /d "%~dp0"
set TRADING_MODE=live
set BOT_MODE=long_only
echo ============================================================
echo   LIVE TRACK baslatiliyor - GERCEK PARA - LONG ONLY
echo   Hesap ~$488 ^| Max poz $200 ^| min_conf 60 ^| gunluk kill %%3
echo ============================================================
py run_bot.py --live
echo.
echo Bot durdu. Pencereyi kapatabilirsiniz.
pause
