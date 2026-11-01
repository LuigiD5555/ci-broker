"""CLI: explicit project root; safe local runner; remote commands are advisory only."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4
from .config import accounts, project, PROJECT_FILE
from .graph import order
from .ledger import Ledger
from .model import BrokerError
from .planner import plan
from .providers import load_extensions, resolve

def emit(obj: object) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False))

def local_run(cfg: dict, root: Path, profile: str, approved: bool) -> int:
    if not approved:
        raise BrokerError("Local execution requires --approve")
    ids = order(cfg["tasks"], cfg["profiles"][profile])
    for task_id in ids:
        task = cfg["tasks"][task_id]
        cmd = task["command"]
        print(f"[ci-broker] {task_id}: {cmd!r}", flush=True)
        try:
            result = subprocess.run(cmd, cwd=root, timeout=float(task.get("timeout_minutes", task["expected_minutes"]))*60,
                                    check=False, shell=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f"[ci-broker] {task_id}: BLOCKED {type(exc).__name__}", file=sys.stderr)
            return 2
        if result.returncode:
            print(f"[ci-broker] {task_id}: FAIL exit={result.returncode}", file=sys.stderr)
            return result.returncode
    print("[ci-broker] LOCAL_PASS (remote CI and full system acceptance not implied)")
    return 0

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ci-broker", description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--plugin", action="append", default=[], help="Explicitly trust an installed provider extension")
    sub = parser.add_subparsers(dest="action", required=True)
    init = sub.add_parser("init", help="Create a minimal project profile; does not run tests")
    init.add_argument("--project-id", default=None)
    view = sub.add_parser("plan", help="Dry-run an ordered profile against provider accounts")
    view.add_argument("--profile", required=True)
    view.add_argument("--independent", action="store_true")
    sub.add_parser("providers", help="Show configured provider accounts")
    run = sub.add_parser("run", help="Execute local tasks only")
    run.add_argument("--profile", required=True)
    run.add_argument("--local", action="store_true", required=True)
    run.add_argument("--approve", action="store_true")
    reserve = sub.add_parser("reserve", help="Atomically reserve quota; NEVER dispatches")
    reserve.add_argument("--profile", required=True)
    reserve.add_argument("--provider", required=True)
    reserve.add_argument("--run-ref", required=True)
    finish = sub.add_parser("finish", help="Settle/cancel a reservation after inspecting provider")
    finish.add_argument("--provider", required=True)
    finish.add_argument("--run-ref", required=True)
    finish.add_argument("--units", type=float, default=None)
    observed = sub.add_parser("record", help="Manually import actual measured units")
    observed.add_argument("--provider", required=True)
    observed.add_argument("--run-ref", required=True)
    observed.add_argument("--units", type=float, required=True)
    sub.add_parser("status", help="Report shared ledger events, with no secrets")
    args = parser.parse_args(argv)
    root = args.project_root.resolve()
    try:
        load_extensions(args.plugin)
        if args.action == "init":
            target = root / PROJECT_FILE
            if target.exists():
                raise BrokerError("Project config exists; refusing overwrite")
            target.parent.mkdir(parents=True, exist_ok=True)
            template = {"schema": 1, "project_id": args.project_id or root.name,
                        "tasks": {"check": {"command": [sys.executable, "--version"],
                                            "expected_minutes": 1, "timeout_minutes": 2}},
                        "profiles": {"light": ["check"]}}
            target.write_text(json.dumps(template, indent=2) + "\n", encoding="utf-8")
            emit({"created": str(target), "next": "Edit tasks and profiles, then ci-broker plan --profile light"})
            return 0
        if args.action == "providers":
            emit(accounts());return 0
        if args.action == "status":
            emit({"events": Ledger().events()});return 0
        if args.action == "record":
            emit({"created": Ledger().record(args.provider, args.run_ref, args.units)})
            return 0
        if args.action == "finish":
            emit(Ledger().finish(args.provider, args.run_ref, units=args.units))
            return 0
        cfg = project(root)
        if args.action == "run":
            if not args.local:
                raise BrokerError("Remote execution not supported by this command")
            return local_run(cfg, root, args.profile, args.approve)
        if args.action in ("plan", "reserve"):
            catalog = accounts()
            p = plan(cfg, catalog, args.profile, Ledger().events(),
                     independent=bool(getattr(args, "independent", False)))
            if args.action == "plan":
                emit(p);return 0 if p["selected"] else 1
            match = next((c for c in p["candidates"] if c["provider"] == args.provider), None)
            if match is None or not match["eligible"]:
                raise BrokerError("Provider not eligible for profile")
            emit(Ledger().reserve(provider=args.provider, spec=catalog[args.provider],
                                  project=cfg["project_id"], run_ref=args.run_ref,
                                  units=match["worst_units"]))
            return 0
    except (BrokerError, KeyError, TypeError, ValueError) as exc:
        print(f"CI_BROKER_BLOCKED: {exc}", file=sys.stderr)
        return 2
    return 2

if __name__ == "__main__":
    raise SystemExit(main())
