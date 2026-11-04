"""Stable, project-independent contracts. No vendor imports."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol, Mapping, Any

class BrokerError(ValueError):
    """Invalid configuration, plan, budget, or execution request."""

@dataclass(frozen=True)
class Quote:
    expected_units: float
    worst_units: float
    unit: str

class ProviderAdapter(Protocol):
    def quote(self, provider: Mapping[str, Any], task: Mapping[str, Any]) -> Quote: ...
    def eligible(self, provider: Mapping[str, Any], task: Mapping[str, Any]) -> tuple[bool, str]: ...
