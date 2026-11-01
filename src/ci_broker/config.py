"""Explicit project profile and provider account configuration. JSON, no code evaluation."""
from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Any
from .model import BrokerError

PROJECT_FILE = ".ci-broker/project.json"

def _object(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise BrokerError(f"Symlink configuration refused: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BrokerError(f"Cannot read JSON configuration: {path}") from exc
    if not isinstance(data, dict):
        raise BrokerError(f"Expected JSON object: {path}")
    return data

def project(root: Path) -> dict[str, Any]:
    root = root.resolve()
    cfg = _object(root / PROJECT_FILE)
    if cfg.get("schema") != 1 or not isinstance(cfg.get("project_id"), str) or not cfg["project_id"]:
        raise BrokerError("project.json requires schema=1 and project_id")
    tasks = cfg.get("tasks")
    if not isinstance(tasks, dict) or not tasks:
        raise BrokerError("project.json requires a nonempty tasks mapping")
    for name, task in tasks.items():
        if not isinstance(name, str) or not isinstance(task, dict):
            raise BrokerError("Invalid task entry")
        command = task.get("command")
        if not isinstance(command, list) or not command or not all(isinstance(x, str) and x for x in command):
            raise BrokerError(f"{name}: command must be an argv array, never a shell string")
        if not isinstance(task.get("needs", []), list) or not all(isinstance(x, str) for x in task.get("needs", [])):
            raise BrokerError(f"{name}: needs must contain task IDs")
        if not isinstance(task.get("capabilities", []), list) or not all(isinstance(x, str) for x in task.get("capabilities", [])):
            raise BrokerError(f"{name}: invalid capabilities")
        from .providers import positive
        expected = positive(task.get("expected_minutes"), f"{name}.expected_minutes")
        if positive(task.get("timeout_minutes", expected), f"{name}.timeout_minutes") < expected:
            raise BrokerError(f"{name}: timeout must be >= expected")
    profiles = cfg.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        raise BrokerError("profiles must map names to selected task lists")
    for key, names in profiles.items():
        if not isinstance(names, list) or not names or any(n not in tasks for n in names):
            raise BrokerError(f"Profile {key}: unknown or empty task selection")
    return cfg

def account_path() -> Path:
    override = os.environ.get("CI_BROKER_ACCOUNTS")
    return Path(override).expanduser() if override else Path.home() / ".config/ci-broker/providers.json"

def accounts() -> dict[str, Any]:
    path = account_path()
    if not path.exists():
        if os.environ.get("CI_BROKER_ACCOUNTS") is not None:
            raise BrokerError(f"Explicit provider account config not found: {path}")
        return {
            "local": {
                "adapter": "local", "enabled": True, "verified": True,
                "independent": False, "preferred": True,
                "quota": None, "unit": "local-minutes",
                "capabilities": ["python", "rust", "node", "go", "java", "docker", "linux"],
            }
        }
    result = _object(path)
    if not result:
        raise BrokerError("Provider account catalog must not be empty")
    for name, spec in result.items():
        if not isinstance(spec, dict) or not isinstance(name, str) or not name:
            raise BrokerError("Invalid provider account entry")
        if type(spec.get("enabled")) is not bool or type(spec.get("verified")) is not bool:
            raise BrokerError(f"{name}: explicit boolean enabled/verified required")
        if spec.get("adapter", "metered") != "local":
            from .providers import positive
            positive(spec.get("units_per_minute"), f"{name}.units_per_minute")
            positive(spec.get("quota"), f"{name}.quota")
        elif spec.get("quota") is not None:
            from .providers import positive
            positive(spec["quota"], f"{name}.quota")
        margin = spec.get("reserve_fraction", 0.1)
        if isinstance(margin, bool) or not isinstance(margin, (int, float)):
            raise BrokerError(f"{name}: invalid reserve_fraction")
        try:
            margin = float(margin)
        except (TypeError, ValueError, OverflowError) as exc:
            raise BrokerError(f"{name}: invalid reserve_fraction") from exc
        from math import isfinite
        if not isfinite(margin) or not 0 <= margin < 1:
            raise BrokerError(f"{name}: invalid reserve_fraction")
        caps = spec.get("capabilities", [])
        if not isinstance(caps, list) or not all(isinstance(x, str) and x for x in caps):
            raise BrokerError(f"{name}: invalid capabilities")
    return result
