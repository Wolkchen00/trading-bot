"""Named parameter packages for the version 5 strategy."""

from dataclasses import dataclass


@dataclass(frozen=True)
class PackageParams:
    name: str
    target_vol: float
    sma: tuple = (150, 200, 250)
    vol_window: int = 20
    lmax: float = 3.0
    leverage: float = 3.0
    band: float = 0.10
    overlay_enabled: bool = True
    ibs_threshold: float = 0.2
    ibs_add: float = 1.0 / 3.0
    cap: float = 0.99
    signal_symbol: str = "QQQ"
    product_symbol: str = "TQQQ"


PACKAGES = {
    "moderate": PackageParams("moderate", 0.20),
    "high": PackageParams("high", 0.30),
    "very_high": PackageParams("very_high", 0.45),
}


def get_package(name: str) -> PackageParams:
    """Return a named package or reject an unknown name."""
    try:
        return PACKAGES[name]
    except (KeyError, TypeError):
        valid = ", ".join(PACKAGES)
        raise ValueError(f"unknown package {name!r}; valid keys: {valid}") from None
