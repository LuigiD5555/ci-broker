"""GitHub issue_comment control channel: strictly non-billable, no private CI data.

GitHub event input is untrusted. All commands are exact-match and owner-only.
This module does not accept SSH credentials, CircleCI secrets, or remote dispatch.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from urllib import request, error

from .gateway import Gateway
from .model import BrokerError

ALLOWED_COMMANDS = {"/ci-broker capabilities", "/ci-broker selftest"}


def authorize(event: dict, repository: str, issue_number: int, actor: str) -> str:
    if not isinstance(event, dict) or event.get("action") != "created":
        raise BrokerError("Unsupported GitHub event")
    if event.get("repository", {}).get("full_name") != repository:
        raise BrokerError("Unexpected repository")
    issue = event.get("issue", {})
    if (not isinstance(issue, dict) or type(issue.get("number")) is not int
            or issue["number"] != issue_number or "pull_request" in issue):
        raise BrokerError("Not the designated control issue")
    comment = event.get("comment", {})
    user = comment.get("user", {}) if isinstance(comment, dict) else {}
    if not isinstance(user, dict) or user.get("login") != actor:
        raise BrokerError("Unauthorized comment author")
    if comment.get("author_association") not in ("OWNER", "MEMBER"):
        raise BrokerError("Comment author lacks repository authority")
    command = comment.get("body")
    if command not in ALLOWED_COMMANDS:
        raise BrokerError("Unknown or disallowed CI Broker command")
    return command


def execute(command: str, *, test_runner=None) -> dict:
    if command == "/ci-broker capabilities":
        result = Gateway().capabilities()
        return {"state": "CAPABILITIES", "schema": result["gateway_schema"],
                "providers": result["providers"], "operations": result["operations"],
                "billable_remote_dispatch": "DISABLED_ON_PUBLIC_HOOK",
                "raw_logs": "NEVER_EXPORTED_TO_PUBLIC_ISSUE"}
    if command != "/ci-broker selftest":
        raise BrokerError("Unknown command")
    if test_runner is None:
        def test_runner():
            # Test code runs from trusted main. No GitHub token passed to subprocess.
            env = {k: v for k, v in os.environ.items() if k not in (
                "GITHUB_TOKEN", "GH_TOKEN", "CIRCLECI_TOKEN",
                "CI_BROKER_GITHUB_TOKEN", "CI_BROKER_WEBHOOK_SECRET")}
            return subprocess.run(
                [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"],
                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=180, check=False, text=True)
    result = test_runner()
    count = re.search(r"Ran (\d+) tests", result.stderr)
    return {"state": "OFFLINE_TEST_PASS" if result.returncode == 0 else "OFFLINE_TEST_FAIL",
            "tests": int(count.group(1)) if count else None,
            "remote_circleci_executed": False,
            "private_evidence_exported": False}


def send_reply(repository: str, number: int, message: str, token: str) -> None:
    if not token or any(c in token for c in "\r\n"):
        raise BrokerError("GitHub Actions token required")
    from urllib.parse import quote
    # Repo is validated against a fixed command-line allowlist in main.
    path = "/repos/" + quote(repository, safe="/") + f"/issues/{number}/comments"
    body = json.dumps({"body": message}).encode()
    req = request.Request("https://api.github.com" + path, data=body,
        headers={"Authorization": "Bearer " + token,
                 "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json",
                 "X-GitHub-Api-Version": "2022-11-28"},
        method="POST")
    try:
        with request.urlopen(req, timeout=15) as response:
            if response.status != 201:
                raise BrokerError("GitHub comment was not created")
    except (error.HTTPError, error.URLError, TimeoutError, OSError):
        raise BrokerError("Could not publish safe control result") from None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--event", required=True)
    p.add_argument("--repository", required=True)
    p.add_argument("--issue", type=int, required=True)
    p.add_argument("--actor", required=True)
    p.add_argument("--run-url", required=True)
    args = p.parse_args(argv)
    try:
        # Fail closed if the workflow is not running in the intended repository.
        if os.environ.get("GITHUB_REPOSITORY") != args.repository:
            raise BrokerError("GitHub runner repository mismatch")
        if args.repository != "LuigiD5555/ci-broker" or args.issue != 2 or args.actor != "LuigiD5555":
            raise BrokerError("Control channel identity mismatch")
        with open(args.event, "r", encoding="utf-8") as fh:
            event = json.load(fh)
        command = authorize(event, args.repository, args.issue, args.actor)
        result = execute(command)
        # No user-controlled command, test output, token or CI artifact in public reply.
        message = ("CI Broker Agent Gateway (public metadata only)\n\n"
                   f"- Command: {command}\n"
                   f"- State: {result['state']}\n"
                   f"- Result: {json.dumps(result, sort_keys=True)}\n"
                   f"- Evidence: {args.run_url}\n"
                   "- No CircleCI credits spent and no private logs published.")
        send_reply(args.repository, args.issue, message,
                   os.environ.get("GITHUB_TOKEN", ""))
        return 0 if result["state"] != "OFFLINE_TEST_FAIL" else 2
    except (BrokerError, ValueError, KeyError, OSError) as exc:
        print("CI_BROKER_CONTROL_BLOCKED: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
