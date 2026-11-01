"""Append-only, process-safe per-account quota ledger (POSIX flock)."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path
from typing import Iterator
from .models import ConfigError


def utc_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def ledger_path() -> Path:
    return Path(os.environ.get("CI_BROKER_LEDGER", str(Path.home() / ".local/state/ci-broker/ledger.jsonl"))).expanduser()


class Ledger:
    def __init__(self, path: Path):
        self.path = Path(path)

    @contextmanager
    def locked(self) -> Iterator[list[dict]]:
        parent = self.path.parent
        if parent.is_symlink() or self.path.is_symlink():
            raise ConfigError("symlinked ledger is not allowed")
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        lockfile = parent / ".ci-broker.lock"
        if lockfile.is_symlink():
            raise ConfigError("symlinked lock is not allowed")
        fd = os.open(lockfile, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            if self.path.is_symlink():
                raise ConfigError("symlinked ledger is not allowed")
            yield [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()] if self.path.exists() else []
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _append(self, entry: dict) -> None:
        fd = os.open(self.path, os.O_CREAT | os.O_WRONLY | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write((json.dumps(entry, sort_keys=True) + "\n").encode())
                stream.flush()
                os.fsync(fd)
        finally:
            os.close(fd)

    def entries(self) -> list[dict]:
        with self.locked() as events:
            return events

    @staticmethod
    def active(entries: list[dict], month: str) -> dict[tuple[str, str], dict]:
        holds = {}
        for item in entries:
            if item["month"] > month:
                continue
            key = (item["provider"], item["run_ref"])
            if item["kind"] == "reserve":
                holds[key] = item
            elif item["kind"] in ("settle", "cancel"):
                holds.pop(key, None)
        return holds

    @staticmethod
    def usage(entries: list[dict], provider: str, month: str) -> float:
        return sum(float(e["units"]) for e in entries if e["kind"] == "settle" and e["month"] == month and e["provider"] == provider)

    def reserve(self, provider: str, project: str, profile: str, run_ref: str, units: float,
                quota: float | None, reserve_fraction: float, verified: bool, local: bool) -> dict:
        if not run_ref or not isinstance(run_ref, str):
            raise ConfigError("unique run-ref required")
        if not math.isfinite(units) or units <= 0:
            raise ConfigError("invalid reserved units")
        if not local and not verified:
            raise ConfigError("unverified external budget cannot be reserved")
        month = utc_month()
        with self.locked() as entries:
            matching = [e for e in entries if (e["provider"], e["run_ref"]) == (provider, run_ref)]
            if matching:
                if len(matching) == 1 and matching[0]["kind"] == "reserve" and matching[0]["units"] == units and matching[0]["project"] == project and matching[0]["profile"] == profile:
                    return matching[0]
                raise ConfigError("run-ref already used or settled")
            outstanding = sum(float(e["units"]) for (name, _), e in self.active(entries, month).items() if name == provider)
            used = self.usage(entries, provider, month)
            if quota is not None and quota - used - outstanding - units < quota * reserve_fraction:
                raise ConfigError("insufficient protected quota for worst-case execution")
            event = {"kind": "reserve", "provider": provider, "project": project, "profile": profile,
                     "run_ref": run_ref, "month": month, "units": units, "at": datetime.now(timezone.utc).isoformat()}
            self._append(event)
            return event

    def finish(self, provider: str, run_ref: str, units: float | None = None) -> dict:
        if units is not None and (not math.isfinite(units) or units < 0):
            raise ConfigError("settlement units must be finite and nonnegative")
        with self.locked() as entries:
            reserve = next((e for e in entries if e["kind"] == "reserve" and e["provider"] == provider and e["run_ref"] == run_ref), None)
            if reserve is None or (provider, run_ref) not in self.active(entries, utc_month()):
                raise ConfigError("no active reservation to settle/cancel")
            event = {"kind": "settle" if units is not None else "cancel", "provider": provider,
                     "run_ref": run_ref, "month": reserve["month"], "at": datetime.now(timezone.utc).isoformat()}
            if units is not None:
                event["units"] = units
            self._append(event)
            return event
