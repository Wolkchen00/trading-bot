@echo off
REM ============================================================
REM  PAPER TRACK - SANAL PARA - LONG + SHORT (ogrenme/deney sandbox)
REM ============================================================
REM  Mod/anahtar otomatik: TRADING_MODE=paper -> PAPER Alpaca anahtari + paper endpoint
REM  BOT_MODE=both -> long + short + options (daha yuksek riskli, ogrenme)
REM  Agent performans takibi (state_paper/agent_performance.json) tecrube biriktirir.
REM  Durdurmak: bu pencerede Ctrl+C  (veya klasorde STOP_BOT dosyasi olustur)
REM ============================================================
cd /d "%~dp0"
set TRADING_MODE=paper
set BOT_MODE=both
echo ============================================================
echo   PAPER TRACK baslatiliyor - SANAL - LONG + SHORT (ogrenme)
echo   Hesap ~$64k sanal ^| short+long+options ^| agresif sandbox
echo ============================================================
py run_bot.py
echo.
echo Bot durdu. Pencereyi kapatabilirsiniz.
pause
