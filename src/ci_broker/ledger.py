"""Append-only, project-shared accounting protected by an OS file lock."""
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path
from typing import Iterator
from .model import BrokerError

def utc() -> datetime:
    return datetime.now(timezone.utc)

def state_path() -> Path:
    return Path(os.environ.get("CI_BROKER_LEDGER", str(Path.home()/".local/state/ci-broker/ledger.jsonl"))).expanduser()

def month(event: dict) -> str:
    return event.get("month") or event["at"][:7]

def active(events: list[dict], through: str) -> dict[tuple[str, str], dict]:
    jobs: dict[tuple[str, str], dict] = {}
    for event in events:
        if month(event) > through:
            continue
        kind = event["kind"]
        if kind not in ("reserve", "settle", "cancel"):
            continue
        key = (event["provider"], event["run_ref"])
        if kind == "reserve":
            jobs[key] = event
        else:
            jobs.pop(key, None)
    return jobs

def usage(events: list[dict], period: str, provider: str) -> float:
    values: dict[str, float] = {}
    anonymous = 0.0
    snapshots: list[float] = []
    for e in events:
        if e.get("provider") != provider or month(e) != period:
            continue
        kind = e.get("kind")
        if kind == "billing":
            snapshots.append(float(e["units"]))
        elif kind in ("settle", "observed"):
            value = float(e["units"])
            if e.get("run_ref"):
                values[e["run_ref"]] = value
            else:
                anonymous += value
    actual = sum(values.values()) + anonymous
    return max([actual, *snapshots]) if snapshots else actual

class Ledger:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or state_path())

    @contextmanager
    def locked(self) -> Iterator[list[dict]]:
        parent = self.path.parent
        if parent.is_symlink() or self.path.is_symlink():
            raise BrokerError("Symlink state directory/ledger refused")
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise BrokerError("Symlink ledger refused")
        lock = parent / ".ledger.lock"
        if lock.is_symlink():
            raise BrokerError("Symlink lock refused")
        fd = os.open(lock, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            if self.path.is_symlink():
                raise BrokerError("Symlink ledger refused")
            if self.path.exists():
                events = [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]
            else:
                events = []
            yield events
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _write(self, event: dict) -> None:
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", closefd=False) as out:
                out.write(json.dumps(event, sort_keys=True, ensure_ascii=False) + "\n")
                out.flush()
                os.fsync(out.fileno())
        finally:
            os.close(fd)

    def reserve(self, *, provider: str, spec: dict, project: str, run_ref: str,
                units: float, now: datetime | None = None) -> dict:
        now = now or utc()
        if not provider or not project or not run_ref or not isinstance(run_ref, str):
            raise BrokerError("Provider, project and unique run_ref required")
        if not math.isfinite(units) or units <= 0:
            raise BrokerError("Invalid reservation units")
        if spec.get("enabled") is not True:
            raise BrokerError("Provider disabled")
        if spec.get("adapter") != "local" and spec.get("verified") is not True:
            raise BrokerError("Remote provider account quota unverified")
        period = now.strftime("%Y-%m")
        with self.locked() as events:
            for e in reversed(events):
                if e.get("provider") == provider and e.get("run_ref") == run_ref:
                    if e["kind"] == "reserve" and e["project"] == project and e["units"] == units:
                        if (provider, run_ref) in active(events, period):
                            return e
                    raise BrokerError("Run reference already used")
            quota = spec.get("quota")
            if quota is not None:
                margin = float(spec.get("reserve_fraction", .1))
                if not math.isfinite(margin) or not 0 <= margin < 1:
                    raise BrokerError("Invalid quota margin")
                reserved = sum(float(x["units"]) for (p, _), x in active(events, period).items() if p == provider)
                if usage(events, period, provider) + reserved + units > float(quota) * (1 - margin):
                    raise BrokerError("Insufficient protected quota")
            event = {"kind": "reserve", "provider": provider, "project": project,
                     "run_ref": run_ref, "units": units, "month": period, "at": now.isoformat()}
            self._write(event)
            return event

    def finish(self, provider: str, run_ref: str, *, units: float | None = None,
               now: datetime | None = None) -> dict:
        now = now or utc()
        if units is not None and (not math.isfinite(units) or units < 0):
            raise BrokerError("Invalid measured units")
        with self.locked() as events:
            matching = [e for e in events if e.get("kind") == "reserve" and
                        e.get("provider") == provider and e.get("run_ref") == run_ref]
            if not matching:
                raise BrokerError("Reservation not found")
            start = matching[-1]
            if (provider, run_ref) not in active(events, now.strftime("%Y-%m")):
                raise BrokerError("Run already closed")
            event = {"kind": "settle" if units is not None else "cancel",
                     "provider": provider, "project": start["project"],
                     "run_ref": run_ref, "at": now.isoformat(), "month": month(start)}
            if units is not None:
                event["units"] = units
            self._write(event)
            return event

    def record(self, provider: str, run_ref: str, units: float, *, now: datetime | None = None) -> bool:
        if not math.isfinite(units) or units < 0 or not provider or not run_ref:
            raise BrokerError("Invalid observation")
        now = now or utc()
        with self.locked() as events:
            if any(e.get("kind") == "observed" and e.get("provider") == provider
                   and e.get("run_ref") == run_ref for e in events):
                return False
            self._write({"kind": "observed", "provider": provider, "run_ref": run_ref,
                         "units": units, "at": now.isoformat()})
            return True

    def events(self) -> list[dict]:
        with self.locked() as events:
            return list(events)
