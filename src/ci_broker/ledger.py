"""Append-only, account-shared accounting with POSIX locking and conservative settlement."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import stat
from typing import Iterator

from .model import BrokerError

KINDS = frozenset(("reserve", "settle", "cancel", "observed", "billing"))


def utc() -> datetime:
    return datetime.now(timezone.utc)


def state_path() -> Path:
    return Path(os.environ.get("CI_BROKER_LEDGER",
                str(Path.home() / ".local/state/ci-broker/ledger.jsonl"))).expanduser()


def month(event: dict) -> str:
    """Billing period, which can differ from the date an event was recorded."""
    return event.get("month") or event["at"][:7]


def active(events: list[dict], through: str) -> dict[tuple[str, str], dict]:
    """Reservations open at the END of the requested calendar month.

    Terminal events are filtered by their actual timestamp, not by the
    reservation's billing month. This matters for November jobs closed in
    December, and prevents the same job from being settled twice.
    """
    jobs: dict[tuple[str, str], dict] = {}
    for event in events:
        if event["at"][:7] > through:
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
    """Conservative unique-run consumption in a billing period.

    Observations and settlements for one run are NOT additive, and a late
    smaller observation must never overwrite a larger settled amount.
    """
    origins = {(e["provider"], e["run_ref"]): month(e) for e in events
               if e.get("kind") == "reserve"}
    run_max: dict[str, float] = {}
    anonymous = 0.0
    snapshots: list[float] = []
    for entry in events:
        if entry.get("provider") != provider:
            continue
        kind = entry.get("kind")
        billing_period = origins.get((provider, entry.get("run_ref")), month(entry))
        if billing_period != period:
            continue
        if kind == "billing":
            snapshots.append(float(entry["units"]))
        elif kind in ("settle", "observed"):
            value = float(entry["units"])
            if entry.get("run_ref"):
                ref = entry["run_ref"]
                run_max[ref] = max(run_max.get(ref, 0.0), value)
            else:
                anonymous += value
    actual = math.fsum([*run_max.values(), anonymous])
    return max([actual, *snapshots]) if snapshots else actual


def _finite(value: float, *, allow_zero: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BrokerError("Expected numeric accounting units")
    try:
        amount = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise BrokerError("Expected finite accounting units") from exc
    if not math.isfinite(amount) or amount < 0 or (not allow_zero and amount == 0):
        raise BrokerError("Expected finite nonnegative accounting units")
    return amount


class Ledger:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or state_path())

    @contextmanager
    def locked(self) -> Iterator[list[dict]]:
        parent = self.path.parent
        if parent.is_symlink() or self.path.is_symlink():
            raise BrokerError("Symlink state directory/ledger refused")
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if parent.is_symlink() or self.path.is_symlink():
            raise BrokerError("Symlink state directory/ledger refused")
        lock = parent / ".ledger.lock"
        if lock.is_symlink():
            raise BrokerError("Symlink lock refused")
        try:
            fd = os.open(lock, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        except OSError as exc:
            raise BrokerError(f"Cannot lock ledger: {exc}") from exc
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            events = self._read()
            yield events
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _read(self) -> list[dict]:
        if self.path.is_symlink():
            raise BrokerError("Symlink ledger refused")
        try:
            if not self.path.exists():
                return []
            metadata = self.path.stat()
            if not stat.S_ISREG(metadata.st_mode):
                raise BrokerError("Ledger must be a regular file")
            if metadata.st_mode & 0o077:
                raise BrokerError("Ledger permissions are not private (expected 0600)")
            raw = self.path.read_bytes()
            if raw and not raw.endswith(b"\n"):
                raise BrokerError("Truncated ledger: missing final newline")
            result: list[dict] = []
            for number, line in enumerate(raw.splitlines(), 1):
                if not line.strip():
                    raise BrokerError(f"Ledger line {number} is blank")
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeDecodeError) as exc:
                    raise BrokerError(f"Ledger line {number} is corrupt") from exc
                if not isinstance(event, dict) or event.get("kind") not in KINDS:
                    raise BrokerError(f"Ledger line {number} has invalid event type")
                if not isinstance(event.get("provider"), str) or not event["provider"]:
                    raise BrokerError(f"Ledger line {number} missing provider")
                if not isinstance(event.get("at"), str) or len(event["at"]) < 7:
                    raise BrokerError(f"Ledger line {number} missing timestamp")
                if event["kind"] != "billing" and (not isinstance(event.get("run_ref"), str) or not event["run_ref"]):
                    raise BrokerError(f"Ledger line {number} missing run_ref")
                if event["kind"] in ("reserve", "settle", "observed", "billing"):
                    _finite(event.get("units"), allow_zero=event["kind"] != "reserve")
                result.append(event)
            return result
        except OSError as exc:
            raise BrokerError(f"Cannot read ledger: {exc}") from exc

    def _write(self, event: dict) -> None:
        if self.path.is_symlink():
            raise BrokerError("Symlink ledger refused")
        data = (json.dumps(event, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        try:
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT |
                         getattr(os, "O_NOFOLLOW", 0), 0o600)
        except OSError as exc:
            raise BrokerError(f"Cannot open ledger for append: {exc}") from exc
        try:
            if os.fstat(fd).st_mode & 0o077:
                raise BrokerError("Ledger permissions are not private (expected 0600)")
            view = memoryview(data)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise BrokerError("Incomplete ledger write")
                view = view[written:]
            os.fsync(fd)
        finally:
            os.close(fd)

    def reserve(self, *, provider: str, spec: dict, project: str, run_ref: str,
                units: float, now: datetime | None = None) -> dict:
        now = now or utc()
        units = _finite(units)
        if not all(isinstance(x, str) and x for x in (provider, project, run_ref)):
            raise BrokerError("Provider, project and unique run_ref required")
        if spec.get("enabled") is not True:
            raise BrokerError("Provider disabled")
        if spec.get("adapter") != "local" and spec.get("verified") is not True:
            raise BrokerError("Remote provider account quota unverified")
        period = now.strftime("%Y-%m")
        with self.locked() as events:
            previous = [e for e in events if e.get("provider") == provider and e.get("run_ref") == run_ref]
            if previous:
                first = next((e for e in previous if e["kind"] == "reserve"), None)
                if first is not None and first["project"] == project and first["units"] == units and (
                    provider, run_ref) in active(events, period):
                    return first
                raise BrokerError("Run reference already used")
            quota = spec.get("quota")
            if quota is None and spec.get("adapter") != "local":
                raise BrokerError("Remote provider requires verified finite quota")
            if quota is not None:
                quota = _finite(quota)
                margin = spec.get("reserve_fraction", 0.1)
                try:
                    margin = float(margin)
                except (TypeError, ValueError, OverflowError) as exc:
                    raise BrokerError("Invalid quota margin") from exc
                if not math.isfinite(margin) or not 0 <= margin < 1:
                    raise BrokerError("Invalid quota margin")
                held = math.fsum(float(x["units"]) for (p, _), x in active(events, period).items()
                                 if p == provider)
                if usage(events, period, provider) + held + units > quota * (1 - margin):
                    raise BrokerError("Insufficient protected quota")
            event = {"kind": "reserve", "provider": provider, "project": project,
                     "run_ref": run_ref, "units": units, "month": period, "at": now.isoformat()}
            self._write(event)
            return event

    def finish(self, provider: str, run_ref: str, *, units: float | None = None,
               now: datetime | None = None) -> dict:
        now = now or utc()
        if units is not None:
            units = _finite(units, allow_zero=True)
        with self.locked() as events:
            matching = [e for e in events if e.get("kind") == "reserve" and
                        e.get("provider") == provider and e.get("run_ref") == run_ref]
            if not matching:
                raise BrokerError("Reservation not found")
            start = matching[-1]
            open_jobs = active(events, now.strftime("%Y-%m"))
            if (provider, run_ref) not in open_jobs:
                terminal = next((e for e in reversed(events) if e.get("provider") == provider and
                                 e.get("run_ref") == run_ref and e["kind"] in ("settle", "cancel")), None)
                if terminal and ((units is None and terminal["kind"] == "cancel") or
                                 (units is not None and terminal["kind"] == "settle" and terminal["units"] == units)):
                    return terminal
                raise BrokerError("Run already closed")
            event = {"kind": "settle" if units is not None else "cancel",
                     "provider": provider, "project": start["project"],
                     "run_ref": run_ref, "at": now.isoformat(), "month": month(start)}
            if units is not None:
                event["units"] = units
            self._write(event)
            return event

    def record(self, provider: str, run_ref: str, units: float, *,
               now: datetime | None = None) -> bool:
        units = _finite(units, allow_zero=True)
        if not isinstance(provider, str) or not provider or not isinstance(run_ref, str) or not run_ref:
            raise BrokerError("Provider and run_ref required")
        now = now or utc()
        with self.locked() as events:
            previous = [e for e in events if e.get("kind") == "observed" and
                        e.get("provider") == provider and e.get("run_ref") == run_ref]
            if previous:
                if previous[-1]["units"] == units:
                    return False
                raise BrokerError("Conflicting observation; explicit audited reconciliation required")
            self._write({"kind": "observed", "provider": provider, "run_ref": run_ref,
                         "units": units, "at": now.isoformat()})
            return True

    def events(self) -> list[dict]:
        with self.locked() as events:
            return list(events)
