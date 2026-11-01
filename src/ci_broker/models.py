"""Stable project, provider and quotation contracts (no project/vendor imports)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
import math


class ConfigError(ValueError):
    """Invalid local configuration or incompatible adapter."""


def positive(value: object, label: str, *, allow_zero: bool = False) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ConfigError(f"{label}: expected a finite number") from exc
    if not math.isfinite(number) or number < 0 or (not allow_zero and number == 0):
        raise ConfigError(f"{label}: expected {'nonnegative' if allow_zero else 'positive'} finite value")
    return number


@dataclass(frozen=True)
class Profile:
    name: str
    commands: tuple[str, ...]
    minutes: float
    timeout_minutes: float
    requires: frozenset[str]
    independent: bool = False


@dataclass(frozen=True)
class Project:
    identifier: str
    profiles: dict[str, Profile]
    prefer_local: bool
    require_approval: bool
    allow_paid_overage: bool
    reserve_fraction: float


@dataclass(frozen=True)
class Quote:
    expected: float
    worst: float
    unit: str


class ProviderAdapter(Protocol):
    def quote(self, provider: dict, profile: Profile) -> Quote: ...
    def eligible(self, provider: dict, profile: Profile) -> tuple[bool, str]: ...


class MeteredAdapter:
    def quote(self, provider: dict, profile: Profile) -> Quote:
        rate = positive(provider.get("rate_per_minute", 1), "rate_per_minute")
        increment = positive(provider.get("billing_increment_seconds", 60), "billing_increment_seconds")
        def charge(minutes: float) -> float:
            units = math.ceil(minutes * 60 / increment) * increment / 60 * rate
            if not math.isfinite(units):
                raise ConfigError("quoted units overflow")
            return round(units, 9)
        return Quote(charge(profile.minutes), charge(profile.timeout_minutes), str(provider.get("unit", "minutes")))

    def eligible(self, provider: dict, profile: Profile) -> tuple[bool, str]:
        if not provider.get("enabled", False):
            return False, "disabled"
        available = set(provider.get("capabilities", []))
        missing = sorted(profile.requires - available)
        if missing:
            return False, "missing-capabilities:" + ",".join(missing)
        maximum = provider.get("max_job_minutes")
        if maximum is not None and profile.timeout_minutes > positive(maximum, "max_job_minutes"):
            return False, "max-job-duration-exceeded"
        return True, "eligible"


class UnmeteredAdapter(MeteredAdapter):
    """Local hosts still consume finite wall time; 'unmetered' is not infinite CPU."""


_ADAPTERS: dict[str, ProviderAdapter] = {"metered": MeteredAdapter(), "unmetered": UnmeteredAdapter()}


def register_adapter(name: str, implementation: ProviderAdapter) -> None:
    if not name or name in _ADAPTERS or not callable(getattr(implementation, "quote", None)) or not callable(getattr(implementation, "eligible", None)):
        raise ConfigError(f"invalid or duplicate provider adapter: {name!r}")
    _ADAPTERS[name] = implementation


def load_trusted_adapters(names: tuple[str, ...]) -> None:
    """Entry points are executed ONLY when explicitly named by operator."""
    if not names:
        return
    from importlib.metadata import entry_points
    available = {entry.name: entry for entry in entry_points(group="ci_broker.adapters")}
    staged = []
    for name in names:
        if name not in available or name in _ADAPTERS:
            raise ConfigError(f"unknown or duplicate trusted adapter {name!r}")
        try:
            obj = available[name].load()
            obj = obj() if isinstance(obj, type) else obj
        except Exception as exc:
            raise ConfigError(f"cannot import trusted adapter {name!r}: {type(exc).__name__}") from exc
        if not callable(getattr(obj, "quote", None)) or not callable(getattr(obj, "eligible", None)):
            raise ConfigError(f"adapter {name!r} violates contract")
        staged.append((name, obj))
    if len({name for name, _ in staged}) != len(staged):
        raise ConfigError("duplicate adapter extension names")
    for name, obj in staged:
        register_adapter(name, obj)


def adapter_for(provider: dict) -> ProviderAdapter:
    name = provider.get("adapter", "metered")
    if not isinstance(name, str) or name not in _ADAPTERS:
        raise ConfigError(f"unknown provider adapter: {name!r}")
    return _ADAPTERS[name]
