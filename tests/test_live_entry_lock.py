"""I-13 / R5 canlı alım kilidi testleri (pytest).

Kilit sözleşmesi:
- Canlı modda `live_entries_enabled` False (veya anahtar YOK) ise execute_buy
  broker'a TEK çağrı bile yapmadan False döner ve funnel'a
  (gate_block, LIVE_LOCK_R5) işler.
- Paper mod kilitten hiç etkilenmez.
- Anahtar True ise canlı akış kilidi geçer (broker çağrısı başlar).
"""
from types import SimpleNamespace

import pytest

from core.executor import OrderExecutor


class FakeClient:
    def __init__(self):
        self.get_account_calls = 0

    def get_account(self):
        self.get_account_calls += 1
        return SimpleNamespace(cash="1000", equity="1000")


class FakeBot:
    def __init__(self, is_paper):
        self.is_paper = is_paper
        self.client = FakeClient()
        # Kilit geçilirse akışı hemen ve zararsız durdursun diye floor devasa:
        # equity 1000 < floor -> execute_buy False döner ama get_account ÇAĞRILMIŞ
        # olur, yani "kilidi geçti" kanıtı get_account_calls == 1.
        self.equity_floor = 10**9
        self.funnel_calls = []

    def _funnel_bump(self, stage, reason=None):
        self.funnel_calls.append((stage, reason))


ANALYSIS = {"price": 100.0, "confidence": 90}


def test_live_locked_blocks_before_any_broker_call():
    bot = FakeBot(is_paper=False)
    result = OrderExecutor(bot).execute_buy(
        "AMZN", ANALYSIS, {"live_entries_enabled": False}
    )
    assert result is False
    assert bot.client.get_account_calls == 0
    assert ("gate_block", "LIVE_LOCK_R5") in bot.funnel_calls


def test_live_lock_fail_closed_when_key_missing():
    bot = FakeBot(is_paper=False)
    result = OrderExecutor(bot).execute_buy("AMZN", ANALYSIS, {})
    assert result is False
    assert bot.client.get_account_calls == 0
    assert ("gate_block", "LIVE_LOCK_R5") in bot.funnel_calls


def test_live_unlocked_proceeds_past_lock():
    bot = FakeBot(is_paper=False)
    result = OrderExecutor(bot).execute_buy(
        "AMZN", ANALYSIS, {"live_entries_enabled": True}
    )
    assert result is False  # equity floor durdurdu, kilit DEĞİL
    assert bot.client.get_account_calls == 1
    assert ("gate_block", "LIVE_LOCK_R5") not in bot.funnel_calls


def test_paper_never_touched_by_lock():
    bot = FakeBot(is_paper=True)
    result = OrderExecutor(bot).execute_buy(
        "AMZN", ANALYSIS, {"live_entries_enabled": False}
    )
    assert result is False  # equity floor durdurdu, kilit DEĞİL
    assert bot.client.get_account_calls == 1
    assert ("gate_block", "LIVE_LOCK_R5") not in bot.funnel_calls


def test_funnel_error_does_not_block_lock_result():
    bot = FakeBot(is_paper=False)

    def boom(stage, reason=None):
        raise RuntimeError("telemetri patladi")

    bot._funnel_bump = boom
    # Funnel patlasa bile kilit kararı değişmemeli ve exception sızmamalı
    # (execute_buy'ın genel try'ı yutar; sonuç yine False, broker'a çağrı yok).
    result = OrderExecutor(bot).execute_buy(
        "AMZN", ANALYSIS, {"live_entries_enabled": False}
    )
    assert result is False
    assert bot.client.get_account_calls == 0


def test_config_ships_with_lock_key_and_bool_type():
    from config import STOCK_CONFIG

    assert "live_entries_enabled" in STOCK_CONFIG
    assert isinstance(STOCK_CONFIG["live_entries_enabled"], bool)
