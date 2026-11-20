"""Configuration for the version 5 trading engine."""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .packages import PACKAGES


class ConfigError(ValueError):
    """Raised when configuration is absent, invalid, or unsafe."""


@dataclass(frozen=True)
class RailNumbers:
    alert_dd: float
    breaker_day: float
    floor_dd: float


RAILS_BY_PACKAGE = {
    "moderate": (0.31, 0.12, 0.50),
    "high": (0.37, 0.15, 0.60),
    "very_high": (0.47, 0.20, 0.70),
}

_DEFAULT_NTFY_BASE_URL = "https://ntfy.sh"
_NTFY_TOPIC = re.compile(r"[A-Za-z0-9_-]{1,64}")
# http(s)://host[:port][/path]: no credentials, query or fragment, and no whitespace anywhere.
_NTFY_BASE_URL = re.compile(
    r"https?://(?:\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9][A-Za-z0-9._-]*)(?::[0-9]{1,5})?"
    r"(?:/[A-Za-z0-9._~%!$&'()*+,;=:@/-]*)?"
)


@dataclass(frozen=True, repr=False)
class Settings:
    mode: str
    api_key: str
    api_secret: str
    trading_base_url: str
    data_base_url: str
    state_dir: Path
    live_trading_enabled: bool
    package: str
    rails: RailNumbers
    ntfy_topic: str
    ntfy_base_url: str
    # Symbols the owner holds himself in this account. The engine never trades them, never treats them as
    # foreign, and sizes its own orders on the equity that is left without them.
    owner_symbols: tuple = ()

    @property
    def orders_enabled(self) -> bool:
        return self.mode == "paper" or self.live_trading_enabled

    def __repr__(self) -> str:
        # The key, the secret and the ntfy topic (a bearer secret) are never shown.
        return (
            "Settings("
            f"mode={self.mode!r}, trading_base_url={self.trading_base_url!r}, "
            f"data_base_url={self.data_base_url!r}, state_dir={self.state_dir!r}, "
            f"live_trading_enabled={self.live_trading_enabled!r}, "
            f"package={self.package!r}, rails={self.rails!r}, "
            f"ntfy_configured={bool(self.ntfy_topic)!r}, ntfy_base_url={self.ntfy_base_url!r}, "
            f"owner_symbols={self.owner_symbols!r})"
        )


def _required_credential(env: Mapping[str, str], primary: str, fallback: str | None) -> str:
    value = env.get(primary, "")
    if not value and fallback is not None:
        value = env.get(fallback, "")
    if not value:
        raise ConfigError(f"missing or empty credential: {primary}")
    return value


def _rail_value(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} must be a float strictly between 0 and 1") from exc
    if not math.isfinite(value) or not 0.0 < value < 1.0:
        raise ConfigError(f"{name} must be a float strictly between 0 and 1")
    return value


def _ntfy_topic(env: Mapping[str, str], mode: str = "live") -> str:
    name = "NTFY_TOPIC"
    if mode == "paper" and (env.get("NTFY_TOPIC_PAPER") or "").strip():
        # The practice account may write to a topic of its own, away from the owner's phone.
        name = "NTFY_TOPIC_PAPER"
    topic = (env.get(name) or "").strip()
    if topic and not _NTFY_TOPIC.fullmatch(topic):
        # The topic is a bearer secret: the message never repeats it.
        raise ConfigError(f"{name} must be 1 to 64 characters of letters, digits, '_' or '-'")
    return topic


def _ntfy_base_url(env: Mapping[str, str]) -> str:
    url = (env.get("NTFY_BASE_URL") or "").strip()
    if not url:
        return _DEFAULT_NTFY_BASE_URL
    if not _NTFY_BASE_URL.fullmatch(url):
        raise ConfigError("NTFY_BASE_URL must be http:// or https:// followed by a host")
    return url.rstrip("/")


_OWNER_SYMBOL = re.compile(r"[A-Z0-9.]{1,10}")


def _owner_symbols(env: Mapping[str, str], package: str) -> tuple:
    raw = (env.get("V5_OWNER_SYMBOLS") or "").strip()
    if not raw:
        return ()
    params = PACKAGES[package]
    reserved = {params.product_symbol.upper(), params.signal_symbol.upper()}
    result = []
    for part in raw.split(","):
        symbol = part.strip().upper()
        if not symbol:
            continue
        if not _OWNER_SYMBOL.fullmatch(symbol):
            raise ConfigError("V5_OWNER_SYMBOLS must be a comma-separated list of ticker symbols")
        if symbol in reserved:
            raise ConfigError("V5_OWNER_SYMBOLS cannot contain the engine's own symbols")
        if symbol not in result:
            result.append(symbol)
    if len(result) > 5:
        raise ConfigError("V5_OWNER_SYMBOLS holds at most 5 symbols")
    return tuple(result)


def load_settings(
    env: Mapping[str, str] | None = None,
    app_dir: str | os.PathLike[str] | None = None,
) -> Settings:
    """Validate environment configuration without creating any directories."""

    env = os.environ if env is None else env
    mode = env.get("TRADING_MODE", "paper")
    if mode not in {"live", "paper"}:
        raise ConfigError("TRADING_MODE must be 'live' or 'paper'")

    prefix = env.get("ALPACA_KEY_PREFIX", "")
    if prefix not in {"", "LIVE", "PAPER", "PAPER2"}:
        raise ConfigError("ALPACA_KEY_PREFIX must be empty, LIVE, PAPER, or PAPER2")
    if mode == "live" and prefix in {"PAPER", "PAPER2"}:
        raise ConfigError("live mode cannot use paper credentials")
    if mode == "paper" and prefix == "LIVE":
        raise ConfigError("paper mode cannot use live credentials")

    selected = prefix or ("LIVE" if mode == "live" else "PAPER")
    if selected == "LIVE":
        api_key = _required_credential(env, "ALPACA_LIVE_API_KEY", "ALPACA_API_KEY")
        api_secret = _required_credential(env, "ALPACA_LIVE_SECRET_KEY", "ALPACA_SECRET_KEY")
    elif selected == "PAPER2":
        api_key = _required_credential(env, "ALPACA_PAPER2_API_KEY", "ALPACA_PAPER_API_KEY")
        api_secret = _required_credential(env, "ALPACA_PAPER2_SECRET_KEY", "ALPACA_PAPER_SECRET_KEY")
    else:
        api_key = _required_credential(env, "ALPACA_PAPER_API_KEY", "ALPACA_API_KEY")
        api_secret = _required_credential(env, "ALPACA_PAPER_SECRET_KEY", "ALPACA_SECRET_KEY")

    package = env.get("STRATEGY_PACKAGE", "high")
    if package not in PACKAGES or package not in RAILS_BY_PACKAGE:
        raise ConfigError(f"unknown STRATEGY_PACKAGE: {package!r}")
    rail_defaults = RAILS_BY_PACKAGE[package]
    rails = RailNumbers(
        _rail_value(env, "V5_ALERT_DD", rail_defaults[0]),
        _rail_value(env, "V5_BREAKER_DAY", rail_defaults[1]),
        _rail_value(env, "V5_FLOOR_DD", rail_defaults[2]),
    )
    if rails.alert_dd >= rails.floor_dd:
        raise ConfigError("V5_ALERT_DD must be less than V5_FLOOR_DD")

    root = Path(env.get("STATE_VOLUME_PATH") or app_dir or Path(__file__).resolve().parent.parent)
    state_dir = root / ("state_live" if mode == "live" else "state_paper") / "v5"
    enabled = env.get("LIVE_TRADING_ENABLED", "").lower() in {"1", "true", "yes", "on"}
    return Settings(
        mode=mode,
        api_key=api_key,
        api_secret=api_secret,
        trading_base_url=(
            "https://api.alpaca.markets"
            if mode == "live"
            else "https://paper-api.alpaca.markets"
        ),
        data_base_url="https://data.alpaca.markets",
        state_dir=state_dir,
        live_trading_enabled=enabled,
        package=package,
        rails=rails,
        ntfy_topic=_ntfy_topic(env, mode),
        ntfy_base_url=_ntfy_base_url(env),
        owner_symbols=_owner_symbols(env, package),
    )


def parse_env_file(path: str | os.PathLike[str]) -> dict[str, str]:
    """Parse the small KEY=VALUE subset used by operational tools."""

    result: dict[str, str] = {}
    with open(path, encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            if key:
                result[key] = value
    return result
