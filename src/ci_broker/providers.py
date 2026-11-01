"""Side-effect-free provider registry and explicit opt-in trusted extensions."""
from __future__ import annotations
from importlib.metadata import entry_points
from math import ceil, isfinite
from typing import Any, Mapping
from .model import BrokerError, Quote, ProviderAdapter

def positive(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise BrokerError(f"{label}: expected a positive number") from exc
    if not isfinite(number) or number <= 0:
        raise BrokerError(f"{label}: expected a finite positive number")
    return number

class MeteredProvider:
    def quote(self, provider: Mapping[str, Any], task: Mapping[str, Any]) -> Quote:
        rate = positive(provider.get("units_per_minute"), "units_per_minute")
        increment = positive(provider.get("billing_increment_seconds", 60), "billing_increment_seconds")
        expected = positive(task["expected_minutes"], "expected_minutes")
        timeout = positive(task.get("timeout_minutes", expected), "timeout_minutes")
        if timeout < expected:
            raise BrokerError("timeout_minutes must not be below expected_minutes")
        def units(minutes: float) -> float:
            amount = ceil(minutes * 60 / increment) * increment * rate / 60
            if not isfinite(amount):
                raise BrokerError("quoted cost overflow")
            return amount
        return Quote(units(expected), units(timeout), str(provider.get("unit", "credits")))

    def eligible(self, provider: Mapping[str, Any], task: Mapping[str, Any]) -> tuple[bool, str]:
        if provider.get("enabled", False) is not True:
            return False, "provider-disabled"
        required = set(task.get("capabilities", []))
        supported = set(provider.get("capabilities", []))
        missing = required - supported
        if missing:
            return False, "missing-capabilities:" + ",".join(sorted(missing))
        limit = provider.get("max_job_minutes")
        if limit is not None and positive(limit, "max_job_minutes") < positive(
            task.get("timeout_minutes", task["expected_minutes"]), "timeout_minutes"
        ):
            return False, "job-duration-exceeded"
        return True, "eligible"

class LocalProvider(MeteredProvider):
    def quote(self, provider: Mapping[str, Any], task: Mapping[str, Any]) -> Quote:
        normalized = {**provider, "units_per_minute": 1, "unit": "local-minutes"}
        return super().quote(normalized, task)

_REGISTRY: dict[str, ProviderAdapter] = {"metered": MeteredProvider(), "local": LocalProvider()}

def register(name: str, adapter: ProviderAdapter) -> None:
    if not name or name in _REGISTRY:
        raise BrokerError("Adapter name empty or already registered")
    if not callable(getattr(adapter, "quote", None)) or not callable(getattr(adapter, "eligible", None)):
        raise BrokerError("Adapter must implement quote and eligible")
    _REGISTRY[name] = adapter

def resolve(provider: Mapping[str, Any]) -> ProviderAdapter:
    name = str(provider.get("adapter", "metered"))
    if name not in _REGISTRY:
        raise BrokerError(f"Unknown adapter {name}; load trusted extension explicitly")
    return _REGISTRY[name]

def load_extensions(approved: list[str]) -> None:
    """Never import plugin code merely because a project config names it."""
    if not approved:
        return
    if len(approved) != len(set(approved)):
        raise BrokerError("Duplicate plugin approval")
    available = {ep.name: ep for ep in entry_points(group="ci_broker.providers")}
    if any(key not in available or key in _REGISTRY for key in approved):
        raise BrokerError("Unknown or already registered trusted plugin")
    prepared = []
    for key in approved:
        try:
            obj = available[key].load()
            instance = obj() if isinstance(obj, type) else obj
        except Exception as exc:
            raise BrokerError(f"Cannot load trusted plugin {key}: {type(exc).__name__}") from exc
        if not callable(getattr(instance, "quote", None)) or not callable(getattr(instance, "eligible", None)):
            raise BrokerError(f"Plugin {key} violates provider adapter contract")
        prepared.append((key, instance))
    for key, instance in prepared:
        register(key, instance)
