"""Strict declarative configuration, independent of the source language."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tomllib
from .models import ConfigError, Profile, Project, positive, adapter_for

SAMPLE = '''# ci-broker is independent of this project's programming language.
schema = 1

[project]
id = "my-project"

[profiles.light]
commands = ["python -m unittest discover -s tests"]
minutes = 3
timeout_minutes = 10
requires = ["python"]

[policy]
prefer_local = true
require_approval = true
allow_paid_overage = false
reserve_fraction = 0.10
'''

BUNDLED = {
    "local": {"label": "Local host", "adapter": "unmetered", "enabled": True,
              "verified": True, "independent": False, "quota": None,
              "unit": "minutes", "rate_per_minute": 1, "capabilities": ["python", "rust", "go", "node", "java", "docker", "podman", "postgresql", "chromium", "tls"]},
    "github": {"label": "GitHub Actions", "adapter": "metered", "enabled": False,
               "verified": False, "independent": True, "quota": 2000,
               "unit": "minutes", "rate_per_minute": 1, "capabilities": ["python", "rust", "go", "node", "java"]},
    "circleci": {"label": "CircleCI", "adapter": "metered", "enabled": False,
                 "verified": False, "independent": True, "quota": 30000,
                 "unit": "credits", "rate_per_minute": 10, "capabilities": ["python"]},
    "gitlab": {"label": "GitLab CI", "adapter": "metered", "enabled": False,
               "verified": False, "independent": True, "quota": 400,
               "unit": "compute_minutes", "rate_per_minute": 1, "capabilities": ["python"]},
}


def root_config_path(root: Path) -> Path:
    return root / ".ci-broker" / "project.toml"


def initialize(root: Path) -> Path:
    path = root_config_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(SAMPLE)
    return path


def load_project(root: Path) -> Project:
    path = root_config_path(root)
    if not path.exists():
        raise ConfigError(f"project configuration not found: {path}; run ci-broker init")
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != 1:
        raise ConfigError("schema version must be 1")
    identity = data.get("project", {}).get("id")
    if not isinstance(identity, str) or not identity.strip():
        raise ConfigError("project.id must be a non-empty string")
    profiles: dict[str, Profile] = {}
    for name, definition in data.get("profiles", {}).items():
        if not isinstance(definition, dict):
            raise ConfigError(f"profiles.{name} must be a table")
        commands = definition.get("commands", [])
        if not isinstance(commands, list) or not commands or not all(isinstance(c, str) and c.strip() for c in commands):
            raise ConfigError(f"profiles.{name}.commands must be a nonempty list of commands")
        minutes = positive(definition.get("minutes", 3), f"{name}.minutes")
        worst = positive(definition.get("timeout_minutes", minutes), f"{name}.timeout_minutes")
        if worst < minutes:
            raise ConfigError(f"{name}: timeout_minutes < minutes")
        requirements = definition.get("requires", [])
        if not isinstance(requirements, list) or not all(isinstance(x, str) for x in requirements):
            raise ConfigError(f"{name}.requires must be a list of strings")
        profiles[name] = Profile(name, tuple(commands), minutes, worst, frozenset(requirements), bool(definition.get("independent", False)))
    if not profiles:
        raise ConfigError("at least one profiles.NAME table is required")
    policy = data.get("policy", {})
    margin = positive(policy.get("reserve_fraction", 0.10), "reserve_fraction", allow_zero=True)
    if not 0 <= margin < 1:
        raise ConfigError("reserve_fraction must be less than 1")
    return Project(identity, profiles, bool(policy.get("prefer_local", True)),
                   bool(policy.get("require_approval", True)),
                   bool(policy.get("allow_paid_overage", False)), margin)


def provider_config_dir() -> Path:
    path = os.getenv("CI_BROKER_PROVIDERS_DIR")
    if path:
        target = Path(path).expanduser()
        if not target.is_absolute():
            raise ConfigError("CI_BROKER_PROVIDERS_DIR must be an absolute path")
        return target
    return Path.home() / ".config" / "ci-broker" / "providers.d"


def load_providers() -> dict[str, dict]:
    catalog = {key: dict(value) for key, value in BUNDLED.items()}
    folder = provider_config_dir()
    if folder.is_symlink():
        raise ConfigError("symlink provider directory refused")
    if folder.exists() and not folder.is_dir():
        raise ConfigError("providers path is not a directory")
    if folder.exists():
        for file in sorted(folder.glob("*.json")):
            if file.is_symlink():
                raise ConfigError(f"symlink provider configuration refused: {file}")
            doc = json.loads(file.read_text(encoding="utf-8"))
            if not isinstance(doc, dict):
                raise ConfigError(f"provider document {file} must be an object")
            for name, values in doc.items():
                if not isinstance(name, str) or not name or name.startswith("_"):
                    raise ConfigError(f"invalid provider id {name!r}")
                if not isinstance(values, dict):
                    raise ConfigError(f"provider {name}: expected object")
                # Override existing provider metadata in a separate local config file.
                if name in catalog:
                    catalog[name].update(values)
                else:
                    catalog[name] = values
    for name, spec in catalog.items():
        adapter_for(spec)
        if not isinstance(spec.get("enabled", False), bool) or not isinstance(spec.get("verified", False), bool):
            raise ConfigError(f"{name}: enabled/verified must be booleans")
        if not isinstance(spec.get("independent", False), bool):
            raise ConfigError(f"{name}: independent must be boolean")
        if not isinstance(spec.get("capabilities", []), list):
            raise ConfigError(f"{name}: capabilities must be an array")
        quota = spec.get("quota")
        if quota is not None:
            positive(quota, f"{name}.quota")
        positive(spec.get("rate_per_minute", 1), f"{name}.rate_per_minute")
    return catalog
