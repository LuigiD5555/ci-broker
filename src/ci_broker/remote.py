"""Opt-in remote CI dispatch. No cloud calls from plan or imports.

CircleCI is the first concrete backend. The remote process must own credentials;
a ChatGPT client or a GitHub comment must never contain provider tokens.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from urllib import error, parse, request
from uuid import UUID

from .config import accounts, project
from .ledger import Ledger
from .model import BrokerError
from .planner import plan

SHA = re.compile(r"^[0-9a-f]{40}$")
BRANCH = re.compile(r"^[A-Za-z0-9_./-]{1,200}$")
PARAM = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
SLUG = re.compile(r"^(?:gh|github|circleci|bb)/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def _uuid(value: str, name: str) -> str:
    try:
        return str(UUID(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise BrokerError(f"Invalid {name}; expected UUID") from exc


def _settings(cfg: dict, profile: str) -> dict:
    settings = cfg.get("remotes", {}).get("circleci", {})
    if not isinstance(settings, dict):
        raise BrokerError("remotes.circleci must be an object")
    slug = settings.get("project_slug") or os.environ.get("CI_BROKER_CIRCLECI_SLUG")
    if not isinstance(slug, str) or not SLUG.fullmatch(slug):
        raise BrokerError("Valid remotes.circleci.project_slug required")
    definition_id = _uuid(settings.get("definition_id") or os.environ.get("CI_BROKER_CIRCLECI_DEFINITION_ID"), "CircleCI definition_id")
    mapping = settings.get("profiles", {})
    if not isinstance(mapping, dict):
        raise BrokerError("remotes.circleci.profiles must be a mapping")
    flag = mapping.get(profile)
    sha_param = settings.get("sha_parameter", "ci_broker_sha")
    if not isinstance(flag, str) or not PARAM.fullmatch(flag):
        raise BrokerError(f"Profile {profile} lacks a valid CircleCI pipeline flag")
    if not isinstance(sha_param, str) or not PARAM.fullmatch(sha_param) or flag == sha_param:
        raise BrokerError("Invalid CircleCI SHA pipeline parameter")
    return {"project_slug": slug, "definition_id": definition_id,
            "flag": flag, "sha_parameter": sha_param}


def _reference(project_id: str, profile: str, sha: str) -> str:
    # Fixed request identity across providers, branches and CLI invocations.
    return "remote/" + hashlib.sha256(
        json.dumps([project_id, profile, sha], separators=(",", ":")).encode()
    ).hexdigest()


def _choose(cfg: dict, catalog: dict, profile: str, provider: str, ledger: Ledger) -> tuple[str, dict, dict]:
    remotes = {k: v for k, v in catalog.items()
               if v.get("remote_kind") == "circleci" and v.get("adapter") == "metered"}
    if not remotes:
        raise BrokerError("No CircleCI accounts configured; remote dispatch disabled")
    if provider != "auto" and provider not in remotes:
        raise BrokerError("Unknown or unsupported remote provider")
    pool = remotes if provider == "auto" else {provider: remotes[provider]}
    decision = plan(cfg, pool, profile, ledger.events(), independent=True)
    name = decision["selected"]
    if name is None:
        raise BrokerError("No eligible verified remote budget: " +
                          json.dumps(decision["candidates"], sort_keys=True))
    return name, pool[name], decision


class CircleCIClient:
    BASE = "https://circleci.com/api/v2"

    def __init__(self, token: str):
        if not token or any(x in token for x in "\r\n"):
            raise BrokerError("CIRCLECI_TOKEN environment variable required")
        self.token = token

    def call(self, method: str, endpoint: str, body: dict | None = None) -> dict:
        if not endpoint.startswith("/") or ".." in endpoint or "?" in endpoint:
            raise BrokerError("Invalid CircleCI API path")
        headers = {"Circle-Token": self.token, "Accept": "application/json"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        req = request.Request(self.BASE + endpoint, data=data, headers=headers, method=method)
        try:
            with request.urlopen(req, timeout=20) as response:
                payload = response.read(2_000_001)
                if len(payload) > 2_000_000:
                    raise BrokerError("CircleCI response too large")
                obj = json.loads(payload)
                if not isinstance(obj, dict):
                    raise BrokerError("Unexpected CircleCI response shape")
                return obj
        except error.HTTPError as exc:
            # Do not render response bodies; they may include account details.
            raise BrokerError(f"CircleCI HTTP {exc.code}; inspect the remote job before retrying") from None
        except (error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise BrokerError(f"CircleCI API unavailable or invalid response ({type(exc).__name__}); "
                              "remote outcome may be UNKNOWN, do not retry blindly") from None

    def trigger(self, settings: dict, branch: str, sha: str) -> dict:
        slug = parse.quote(settings["project_slug"], safe="/")
        return self.call("POST", f"/project/{slug}/pipeline/run", {
            "definition_id": settings["definition_id"],
            "config": {"branch": branch}, "checkout": {"branch": branch},
            "parameters": {settings["flag"]: True, settings["sha_parameter"]: sha}
        })

    def pipeline(self, pipeline_id: str) -> dict:
        return self.call("GET", f"/pipeline/{_uuid(pipeline_id, 'pipeline_id')}")

    def workflows(self, pipeline_id: str) -> list[dict]:
        # Fail closed on pagination: cannot attest partial workflow lists.
        payload = self.call("GET", f"/pipeline/{_uuid(pipeline_id, 'pipeline_id')}/workflow")
        if payload.get("next_page_token"):
            raise BrokerError("CircleCI workflows paginated; cannot attest incomplete result")
        items = payload.get("items")
        if not isinstance(items, list):
            raise BrokerError("CircleCI workflows missing")
        return items

    def cancel(self, workflow_id: str) -> dict:
        return self.call("POST", f"/workflow/{_uuid(workflow_id, 'workflow_id')}/cancel", {})


def _validate_identity(branch: str, sha: str) -> None:
    if not SHA.fullmatch(sha):
        raise BrokerError("Expected lowercase full 40-character git SHA")
    if not BRANCH.fullmatch(branch) or ".." in branch or "//" in branch or branch.endswith("/"):
        raise BrokerError("Invalid branch")


def dispatch(root: Path, profile: str, provider: str, sha: str, branch: str,
             approve: bool, client: CircleCIClient | None = None) -> dict:
    if not approve:
        raise BrokerError("Remote dispatch requires explicit --approve")
    _validate_identity(branch, sha)
    cfg = project(root)
    settings = _settings(cfg, profile)
    ledger = Ledger()
    name, spec, decision = _choose(cfg, accounts(), profile, provider, ledger)
    quote = next(c for c in decision["candidates"] if c["provider"] == name)
    ref = _reference(cfg["project_id"], profile, sha)
    # Resolve credentials BEFORE reserving capacity; never pass them in CLI arguments.
    client = client or CircleCIClient(os.environ.get("CIRCLECI_TOKEN", ""))
    # Atomic duplicate protection across *all* provider accounts.
    ledger.reserve(provider=name, spec=spec, project=cfg["project_id"],
                   run_ref=ref, units=quote["worst_units"], reject_existing=True,
                   global_ref=True)
    try:
        reply = client.trigger(settings, branch, sha)
        pipeline_id = _uuid(reply.get("id"), "pipeline_id")
        ledger.record_receipt(name, ref, pipeline_id, sha, branch)
        return {"state": "SUBMITTED_UNVERIFIED", "provider": name,
                "profile": profile, "sha": sha, "branch": branch,
                "run_ref": ref, "pipeline_id": pipeline_id,
                "reserved_worst_units": quote["worst_units"],
                "next": "ci-broker remote status --run-ref " + ref}
    except Exception as exc:
        # A timed-out POST might have succeeded remotely. Never auto-release
        # or auto-retry. The operator must inspect CircleCI and reconcile.
        return {"state": "UNKNOWN_DISPATCH_HOLD", "provider": name, "run_ref": ref,
                "sha": sha, "reason": type(exc).__name__,
                "next": "Inspect CircleCI for this SHA before any manual release or retry"}


def _receipt(ref: str) -> tuple[dict, dict]:
    events = Ledger().events()
    starts = [e for e in events if e.get("kind") == "reserve" and e.get("run_ref") == ref]
    receipts = [e for e in events if e.get("kind") == "receipt" and e.get("run_ref") == ref]
    if len(starts) != 1 or len(receipts) != 1:
        raise BrokerError("No unique reserved CircleCI dispatch receipt; inspect manually")
    if starts[0]["provider"] != receipts[0]["provider"]:
        raise BrokerError("Dispatch receipt does not match reservation")
    return starts[0], receipts[0]


def status(ref: str, client: CircleCIClient | None = None) -> dict:
    start, receipt = _receipt(ref)
    spec = accounts().get(start["provider"], {})
    if spec.get("remote_kind") != "circleci":
        raise BrokerError("Receipt provider is not CircleCI")
    client = client or CircleCIClient(os.environ.get("CIRCLECI_TOKEN", ""))
    pipeline = client.pipeline(receipt["pipeline_id"])
    workflows = client.workflows(receipt["pipeline_id"])
    revision = pipeline.get("vcs", {}).get("revision") if isinstance(pipeline.get("vcs"), dict) else None
    states = [w.get("status") for w in workflows]
    if revision != receipt["sha"]:
        result = "UNVERIFIED_SHA"
    elif not states or any(not isinstance(s, str) for s in states):
        result = "PENDING"
    elif all(s == "success" for s in states):
        result = "SUCCESS_CI_ONLY"
    elif any(s in {"failed", "error", "canceled", "not_run", "unauthorized"} for s in states):
        result = "FAILED"
    else:
        result = "PENDING"
    return {"state": result, "provider": start["provider"], "run_ref": ref,
            "expected_sha": receipt["sha"], "observed_sha": revision,
            "pipeline_id": receipt["pipeline_id"],
            "workflows": [{"id": w.get("id"), "name": w.get("name"), "status": w.get("status")}
                          for w in workflows],
            "acceptance": "NOT_ATTESTED: inspect required test cases and JUnit separately"}


def cancel(ref: str, approve: bool, client: CircleCIClient | None = None) -> dict:
    if not approve:
        raise BrokerError("Remote cancel requires explicit --approve")
    _, receipt = _receipt(ref)
    client = client or CircleCIClient(os.environ.get("CIRCLECI_TOKEN", ""))
    workflows = client.workflows(receipt["pipeline_id"])
    pending = [w for w in workflows if w.get("status") in {"running", "on_hold", "not_running", "failing"}]
    for w in pending:
        client.cancel(_uuid(w.get("id"), "workflow_id"))
    return {"state": "CANCEL_REQUESTED" if pending else "NO_RUNNING_WORKFLOWS",
            "run_ref": ref, "workflows_requested": len(pending),
            "note": "Budget reservation remains held until actual provider usage is reconciled"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ci-broker remote")
    parser.add_argument("--project-root", type=Path, default=Path("."))
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("plan", help="Remote budget-aware planning (no network)")
    p.add_argument("--profile", required=True)
    p.add_argument("--provider", default="auto")
    d = sub.add_parser("dispatch", help="Approve one CircleCI pipeline dispatch")
    d.add_argument("--profile", required=True)
    d.add_argument("--provider", default="auto")
    d.add_argument("--sha", required=True)
    d.add_argument("--branch", required=True)
    d.add_argument("--approve", action="store_true")
    s = sub.add_parser("status", help="One-shot remote observation; never polls")
    s.add_argument("--run-ref", required=True)
    c = sub.add_parser("cancel", help="Cancel verified reserved workflow(s)")
    c.add_argument("--run-ref", required=True)
    c.add_argument("--approve", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.action == "plan":
            cfg = project(args.project_root)
            _settings(cfg, args.profile)
            name, _, decision = _choose(cfg, accounts(), args.profile, args.provider, Ledger())
            result = {"state": "ADVISORY_ONLY", "selected": name, **decision}
        elif args.action == "dispatch":
            result = dispatch(args.project_root, args.profile, args.provider,
                              args.sha, args.branch, args.approve)
        elif args.action == "status":
            result = status(args.run_ref)
        else:
            result = cancel(args.run_ref, args.approve)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 2 if result.get("state") in {"UNKNOWN_DISPATCH_HOLD", "UNVERIFIED_SHA", "FAILED"} else 0
    except (BrokerError, KeyError, ValueError) as exc:
        print("CI_BROKER_REMOTE_BLOCKED: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
