"""Deterministic transitive dependency selection and cycle detection."""
from __future__ import annotations
from .model import BrokerError

def order(tasks: dict, selected: list[str]) -> list[str]:
    status: dict[str, int] = {}
    ordered: list[str] = []
    def visit(name: str) -> None:
        if name not in tasks:
            raise BrokerError(f"Unknown task dependency: {name}")
        if status.get(name) == 1:
            raise BrokerError(f"Task dependency cycle involving {name}")
        if status.get(name) == 2:
            return
        status[name] = 1
        for dep in tasks[name].get("needs", []):
            visit(dep)
        status[name] = 2
        ordered.append(name)
    for name in selected:
        visit(name)
    return ordered

def estimate(tasks: dict, names: list[str]) -> dict:
    elapsed = sum(float(tasks[n]["expected_minutes"]) for n in names)
    critical: dict[str, float] = {}
    for n in names:
        task = tasks[n]
        upstream = max((critical[dep] for dep in task.get("needs", [])), default=0.0)
        critical[n] = upstream + float(task["expected_minutes"])
    return {
        "tasks": len(names),
        "serial_expected_minutes": round(elapsed, 3),
        "critical_path_minutes": round(max(critical.values(), default=0), 3),
    }
