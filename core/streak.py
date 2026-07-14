"""
Kayıp/Kazanç Serisi — tek doğru kaynak (v4.12.1)

13 Tem 2026 vakası: kârlı bir bracket stop-out (AMZN +$0.12) etiket-bazlı
sayaçta ("STOP_LOSS" içeren her çıkış zarar sayılıyordu) seriyi 1→2 yapıp
KAYIP KORUYUCU kapısını armladı ve canlı long hunisini kilitledi. Aynı mantık
üç yerde kopyaydı (executor, short_executor, dış-kapanış) ve kopyalar
birbirinden sapmıştı — zararlı bir trailing-stop da seriyi SIFIRLIYORDU.

Seri artık YALNIZ gerçekleşen PnL işaretine göre güncellenir:
  pnl < 0 → seri +1 (sembol serisi de +1)
  pnl > 0 → seri sıfır (sembol serisi de)
  pnl = 0 → değişmez
Çıkışın etiketi (STOP_LOSS/TAKE_PROFIT/TRAILING/EXTERNAL) serinin tanımına
girmez; wash-sale gibi etiket/yön-bağımlı kayıtlar çağıran tarafta kalır.
"""
from utils.logger import logger


def update_loss_streak(bot, symbol: str, pnl_usd: float) -> None:
    """Bot'un ardışık zarar sayaçlarını gerçekleşen PnL işaretine göre günceller."""
    sym_losses = getattr(bot, "_symbol_consecutive_losses", None)
    if sym_losses is None:
        sym_losses = {}

    if pnl_usd < 0:
        bot._consecutive_losses = getattr(bot, "_consecutive_losses", 0) + 1
        sym_losses[symbol] = sym_losses.get(symbol, 0) + 1
        bot._symbol_consecutive_losses = sym_losses
        logger.info(
            f"  Ardisik zarar: {bot._consecutive_losses} | "
            f"{symbol}: {sym_losses[symbol]}"
        )
    elif pnl_usd > 0:
        bot._consecutive_losses = 0
        sym_losses[symbol] = 0
        bot._symbol_consecutive_losses = sym_losses
