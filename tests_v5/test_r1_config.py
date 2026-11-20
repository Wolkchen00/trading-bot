from pathlib import Path

import pytest

from v5.config import ConfigError, RailNumbers, load_settings, parse_env_file


def base(**updates):
    env = {"ALPACA_PAPER_API_KEY": "paper-key", "ALPACA_PAPER_SECRET_KEY": "paper-secret"}
    env.update(updates)
    return env


def test_defaults_state_and_repr(tmp_path):
    settings = load_settings(base(), tmp_path)
    assert settings.mode == "paper" and settings.orders_enabled
    assert settings.state_dir == tmp_path / "state_paper" / "v5"
    assert not settings.state_dir.exists()
    assert settings.rails == RailNumbers(0.37, 0.15, 0.60)
    assert "paper-key" not in repr(settings) and "paper-secret" not in repr(settings)


def test_live_selection_gate_and_urls(tmp_path):
    env = {"TRADING_MODE": "live", "ALPACA_LIVE_API_KEY": "live-key", "ALPACA_LIVE_SECRET_KEY": "live-secret"}
    settings = load_settings(env, tmp_path)
    assert not settings.orders_enabled
    assert settings.trading_base_url == "https://api.alpaca.markets"
    env["LIVE_TRADING_ENABLED"] = "YeS"
    assert load_settings(env, tmp_path).orders_enabled


@pytest.mark.parametrize(
    "env",
    [
        base(TRADING_MODE="bad"),
        base(ALPACA_KEY_PREFIX="bad"),
        {"TRADING_MODE": "live", "ALPACA_KEY_PREFIX": "PAPER", "ALPACA_PAPER_API_KEY": "x", "ALPACA_PAPER_SECRET_KEY": "y"},
        base(ALPACA_KEY_PREFIX="LIVE"),
        {"ALPACA_PAPER_API_KEY": "", "ALPACA_PAPER_SECRET_KEY": "x"},
        {"ALPACA_PAPER_API_KEY": "x", "ALPACA_PAPER_SECRET_KEY": ""},
        base(STRATEGY_PACKAGE="unknown"),
        base(V5_ALERT_DD="0"),
        base(V5_BREAKER_DAY="1"),
        base(V5_FLOOR_DD="nan"),
        base(V5_ALERT_DD="0.7", V5_FLOOR_DD="0.6"),
    ],
)
def test_fail_closed(env, tmp_path):
    with pytest.raises(ConfigError):
        load_settings(env, tmp_path)


def test_prefix_fallbacks_overrides_and_state_volume(tmp_path):
    env = base(
        ALPACA_KEY_PREFIX="PAPER2", ALPACA_PAPER2_API_KEY="p2-key", ALPACA_PAPER2_SECRET_KEY="p2-secret",
        STATE_VOLUME_PATH=str(tmp_path), STRATEGY_PACKAGE="moderate", V5_ALERT_DD="0.2",
        V5_BREAKER_DAY="0.1", V5_FLOOR_DD="0.4", NTFY_TOPIC="topic", NTFY_BASE_URL="https://notify",
    )
    settings = load_settings(env, Path("ignored"))
    assert settings.api_key == "p2-key" and settings.api_secret == "p2-secret"
    assert settings.state_dir == tmp_path / "state_paper" / "v5"
    assert settings.rails == RailNumbers(0.2, 0.1, 0.4)
    assert settings.ntfy_topic == "topic" and settings.ntfy_base_url == "https://notify"

    fallback = load_settings(base(ALPACA_KEY_PREFIX="PAPER2"), tmp_path)
    assert fallback.api_key == "paper-key"
    legacy = load_settings({"ALPACA_API_KEY": "old", "ALPACA_SECRET_KEY": "old-secret"}, tmp_path)
    assert legacy.api_key == "old"


def test_ntfy_topic_is_stripped_and_never_printed(tmp_path):
    settings = load_settings(base(NTFY_TOPIC="  private-topic_1 \n"), tmp_path)
    assert settings.ntfy_topic == "private-topic_1"
    assert "private-topic_1" not in repr(settings) and "private-topic_1" not in str(settings)
    assert "ntfy_configured=True" in repr(settings)

    for blank in ("", "   ", "\t\n"):
        empty = load_settings(base(NTFY_TOPIC=blank), tmp_path)
        assert empty.ntfy_topic == "" and "ntfy_configured=False" in repr(empty)
    assert "ntfy_configured=False" in repr(load_settings(base(), tmp_path))
    assert load_settings(base(NTFY_TOPIC="x" * 64), tmp_path).ntfy_topic == "x" * 64


@pytest.mark.parametrize(
    "topic", ["a/b", "topic with space", "t?x=1", "t#frag", "a.b", "t" + chr(0xF6) + "pic", "x" * 65]
)
def test_ntfy_topic_fail_closed(topic, tmp_path):
    with pytest.raises(ConfigError) as caught:
        load_settings(base(NTFY_TOPIC=topic), tmp_path)
    assert topic not in str(caught.value)


def test_ntfy_base_url_is_normalised(tmp_path):
    assert load_settings(base(), tmp_path).ntfy_base_url == "https://ntfy.sh"
    for raw, expected in [
        ("", "https://ntfy.sh"),
        ("   ", "https://ntfy.sh"),
        (" https://ntfy.example.org/ \n", "https://ntfy.example.org"),
        ("http://localhost:8080", "http://localhost:8080"),
        ("https://ntfy.example.org/base/", "https://ntfy.example.org/base"),
        ("http://[::1]:8080/", "http://[::1]:8080"),
    ]:
        assert load_settings(base(NTFY_BASE_URL=raw), tmp_path).ntfy_base_url == expected


@pytest.mark.parametrize(
    "url",
    [
        "ntfy.sh", "ftp://ntfy.sh", "https://", "http:///path", "https://:8080", "javascript:alert(1)",
        "https://user:pass@ntfy.sh", "https://ntfy.sh?x=1", "https://ntfy.sh/#frag", "https://ntfy .sh",
    ],
)
def test_ntfy_base_url_fail_closed(url, tmp_path):
    with pytest.raises(ConfigError) as caught:
        load_settings(base(NTFY_BASE_URL=url), tmp_path)
    assert "user:pass" not in str(caught.value)  # a refused value is never repeated


def test_parse_env_file(tmp_path):
    path = tmp_path / ".env"
    path.write_text("# comment\n A = one \nB='two words'\nC=\"three\"\n\nnot-a-pair\n", encoding="utf-8")
    assert parse_env_file(path) == {"A": "one", "B": "two words", "C": "three"}
