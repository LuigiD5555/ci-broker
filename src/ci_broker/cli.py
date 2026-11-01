"""Stable CLI: project-root selection, planning, local execution, offline reservations."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

from .config import initialize, load_project, load_providers
from .ledger import Ledger, ledger_path, utc_month
from .models import ConfigError, adapter_for, load_trusted_adapters
from .render import render
from .scheduler import plan


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ci-broker", description="Project-agnostic, local-first CI orchestration")
    p.add_argument("--project-root", type=Path, default=Path.cwd())
    p.add_argument("--trusted-adapter", action="append", default=[], help="Explicitly load installed trusted provider entry point")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    sub.add_parser("validate")
    sub.add_parser("providers")
    sub.add_parser("usage")
    for command in ("plan", "run", "render", "reserve"):
        s = sub.add_parser(command)
        s.add_argument("--profile", required=True)
        if command == "plan":
            s.add_argument("--independent", action="store_true")
        elif command == "run":
            s.add_argument("--yes", action="store_true", help="Explicit approval to execute declared local commands")
        elif command == "render":
            s.add_argument("--provider", required=True)
        elif command == "reserve":
            s.add_argument("--provider", required=True)
            s.add_argument("--run-ref", required=True)
    for command in ("settle", "cancel"):
        s = sub.add_parser(command)
        s.add_argument("--provider", required=True)
        s.add_argument("--run-ref", required=True)
        if command == "settle":
            s.add_argument("--units", required=True, type=float)
    return p


def execute(root: Path, commands: tuple[str, ...], timeout_seconds: float, approved: bool) -> int:
    if not approved:
        raise ConfigError("local command execution requires --yes")
    deadline = time.monotonic() + timeout_seconds
    for command in commands:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            print("TIMEOUT: profile deadline exhausted", file=sys.stderr)
            return 124
        print("+ " + command, flush=True)
        try:
            # Shell is intentional: the project config is executable code.
            # Never call this on untrusted project configs.
            result = subprocess.run(command, shell=True, executable="/bin/sh", cwd=root, timeout=remaining, check=False)
        except subprocess.TimeoutExpired:
            print("TIMEOUT: command exceeded remaining profile deadline", file=sys.stderr)
            return 124
        if result.returncode:
            return result.returncode
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    root = args.project_root.expanduser().resolve()
    try:
        load_trusted_adapters(tuple(args.trusted_adapter))
        if args.command == "init":
            print(initialize(root))
            return 0
        project = load_project(root) if args.command in ("validate", "plan", "run", "render", "reserve") else None
        providers = load_providers() if args.command not in ("init", "run") else {}
        ledger = Ledger(ledger_path()) if args.command in ("plan", "usage", "reserve", "settle", "cancel") else None
        if args.command == "validate":
            print(json.dumps({"valid": True, "project": project.identifier, "profiles": sorted(project.profiles), "providers": sorted(providers)}))
            return 0
        if args.command == "providers":
            print(json.dumps(providers, indent=2, sort_keys=True))
            return 0
        if args.command == "usage":
            entries = ledger.entries()
            print(json.dumps({name: {"month": utc_month(), "used": Ledger.usage(entries, name, utc_month()),
                                     "reserved": sum(e["units"] for (p, _), e in Ledger.active(entries, utc_month()).items() if p == name)}
                              for name in sorted({e["provider"] for e in entries})}, indent=2))
            return 0
        if args.command in ("plan", "run", "render", "reserve"):
            profile = project.profiles.get(args.profile)
            if profile is None:
                raise ConfigError(f"unknown project profile {args.profile!r}")
            if args.command == "plan":
                result = plan(project, profile, providers, ledger.entries(), independent=args.independent)
                print(json.dumps(result, indent=2))
                return 0 if result["selected"] else 1
            if args.command == "run":
                return execute(root, profile.commands, profile.timeout_minutes * 60, args.yes)
            if args.command == "render":
                print(render(args.provider, profile.name))
                return 0
            if args.command == "reserve":
                if args.provider not in providers:
                    raise ConfigError("unknown provider")
                provider = providers[args.provider]
                check = plan(project, profile, {args.provider: provider}, ledger.entries())
                if check["selected"] != args.provider:
                    raise ConfigError("provider not eligible: " + check["options"][0]["reason"])
                quote = adapter_for(provider).quote(provider, profile)
                event = ledger.reserve(args.provider, project.identifier, profile.name, args.run_ref,
                                       quote.worst, provider.get("quota"), project.reserve_fraction,
                                       bool(provider.get("verified", False)), args.provider == "local")
                print(json.dumps(event, indent=2))
                return 0
        if args.command == "settle":
            print(json.dumps(ledger.finish(args.provider, args.run_ref, args.units)))
            return 0
        if args.command == "cancel":
            print(json.dumps(ledger.finish(args.provider, args.run_ref)))
            return 0
        raise ConfigError("unhandled command")
    except (ConfigError, FileExistsError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"CI BROKER: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
