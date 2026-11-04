"""Canonical CI Broker agent-gateway CLI; legacy operations remain accessible."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .gateway import Gateway, load_trusted_providers
from .model import BrokerError


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "legacy":
        from .cli import main as legacy
        return legacy(argv[1:])
    p = argparse.ArgumentParser(prog="ci-broker",
                                description="Provider-independent CI gateway")
    p.add_argument("--trust-provider", action="append", default=[])
    sub = p.add_subparsers(dest="action", required=True)
    sub.add_parser("capabilities")
    d = sub.add_parser("dispatch")
    d.add_argument("--kind", default="circleci")
    d.add_argument("--project-root", type=Path, required=True)
    d.add_argument("--profile", required=True)
    d.add_argument("--provider", default="auto")
    d.add_argument("--sha", required=True)
    d.add_argument("--branch", required=True)
    d.add_argument("--approve", action="store_true")
    for cmd in ("status", "jobs", "artifacts", "logs", "cancel"):
        a = sub.add_parser(cmd)
        a.add_argument("--run-ref", required=True)
        if cmd in ("artifacts", "logs"):
            a.add_argument("--job-number", type=int, required=True)
        if cmd == "cancel":
            a.add_argument("--approve", action="store_true")
    a = p.parse_args(argv)
    try:
        load_trusted_providers(a.trust_provider)
        gateway = Gateway()
        if a.action == "capabilities":
            result = gateway.capabilities()
        elif a.action == "dispatch":
            result = gateway.dispatch(a.kind, a.project_root, a.profile,
                                      a.provider, a.sha, a.branch, a.approve)
        elif a.action in ("status", "jobs"):
            result = getattr(gateway, a.action)(a.run_ref)
        elif a.action in ("artifacts", "logs"):
            result = getattr(gateway, a.action)(a.run_ref, a.job_number)
        else:
            result = gateway.cancel(a.run_ref, a.approve)
        print(json.dumps(result, sort_keys=True, indent=2))
        return 2 if result.get("state") in {
            "FAILED", "UNKNOWN_DISPATCH_HOLD", "UNVERIFIED_SHA"} else 0
    except (BrokerError, ValueError, KeyError, TypeError) as exc:
        print("CI_BROKER_GATEWAY_BLOCKED: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
