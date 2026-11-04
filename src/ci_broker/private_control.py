"""Private GitHub issue transport for the Gateway's observation port only.

The trusted default-branch workflow supplies policy, never the issue or PR tree.
No task execution, subprocess, dispatch, cancel, plugins, ledger or URL downloads.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
from urllib import error, request
from urllib.parse import quote

from .gateway import Gateway
from .model import BrokerError
from .observation import ObservationError
from .remote import SHA, SLUG, _NoRedirect, _uuid, _validate_identity


class ControlError(BrokerError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class GitHub:
    def __init__(self, token):
        if not token or any(c in token for c in "\r\n"):
            raise ControlError("GITHUB_AUTH_MISSING")
        self.token = token

    def call(self, method, path, body=None):
        comparison = re.fullmatch(r"/repos/[\w.-]+/[\w.-]+/compare/[0-9a-f]{40}\.\.\.[0-9a-f]{40}", path)
        if (method not in {"GET", "POST"} or not path.startswith("/repos/")
                or (".." in path and not comparison) or "?" in path):
            raise ControlError("GITHUB_PATH_BLOCKED")
        if method == "POST" and not re.fullmatch(r"/repos/[\w.-]+/[\w.-]+/issues/[0-9]+/comments", path):
            raise ControlError("GITHUB_WRITE_BLOCKED")
        req = request.Request("https://api.github.com" + path,
                              data=None if body is None else json.dumps(body).encode(),
                              headers={"Authorization": "Bearer " + self.token,
                                       "Accept": "application/vnd.github+json",
                                       "Content-Type": "application/json",
                                       "X-GitHub-Api-Version": "2022-11-28"}, method=method)
        try:
            with request.build_opener(_NoRedirect()).open(req, timeout=20) as response:
                payload = response.read(2_000_001)
                if len(payload) > 2_000_000:
                    raise ControlError("GITHUB_RESPONSE_LIMIT")
                return json.loads(payload)
        except (error.HTTPError, error.URLError, OSError, ValueError):
            raise ControlError("GITHUB_API_UNAVAILABLE") from None


def policy_from(data):
    if (not isinstance(data, dict) or data.get("schema") != 1
            or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", data.get("repository", ""))
            or ".." in data.get("repository", "")
            or type(data.get("issue")) is not int or data["issue"] <= 0
            or type(data.get("actor_id")) is not int or data["actor_id"] <= 0
            or not re.fullmatch(r"[A-Za-z0-9-]+", data.get("actor", ""))
            or not SLUG.fullmatch(data.get("project_slug", ""))
            or not isinstance(data.get("branches"), list) or not data["branches"]
            or len(data["branches"]) > 10):
        raise ControlError("INVALID_TRUSTED_POLICY")
    for branch in data["branches"]:
        _validate_identity(branch, "a" * 40)
    return data


def parse_event(event, policy, environ):
    repo = policy["repository"]
    if (environ.get("GITHUB_EVENT_NAME") != "issue_comment"
            or environ.get("GITHUB_REPOSITORY") != repo
            or environ.get("GITHUB_REF") != "refs/heads/main"
            or event.get("action") != "created"
            or event.get("repository", {}).get("full_name") != repo
            or event.get("repository", {}).get("private") is not True
            or event.get("repository", {}).get("default_branch") != "main"
            or event.get("issue", {}).get("number") != policy["issue"]
            or "pull_request" in event.get("issue", {})):
        raise ControlError("UNAUTHORIZED_EVENT")
    comment = event.get("comment", {})
    actor = comment.get("user", {})
    if (actor.get("login") != policy["actor"] or actor.get("id") != policy["actor_id"]
            or event.get("sender", {}).get("id") != policy["actor_id"]
            or comment.get("author_association") != "OWNER"
            or type(comment.get("id")) is not int or comment["id"] <= 0):
        raise ControlError("UNAUTHORIZED_ACTOR")
    body = comment.get("body")
    if not isinstance(body, str) or len(body) > 500:
        raise ControlError("INVALID_COMMAND")
    match = re.fullmatch(r"/ci-broker read branch=([A-Za-z0-9_./-]+) sha=([0-9a-f]{40})(?: pipeline=([0-9a-f-]{36}))?", body)
    if not match:
        raise ControlError("INVALID_COMMAND")
    branch, sha, pipeline = match.groups()
    _validate_identity(branch, sha)
    if branch not in policy["branches"]:
        raise ControlError("BRANCH_NOT_ALLOWED")
    if pipeline:
        pipeline = _uuid(pipeline, "pipeline_id")
    return {"branch": branch, "sha": sha, "pipeline_id": pipeline,
            "comment_id": comment["id"], "body": body}


def authorize_source(github, policy, command):
    base = "/repos/" + policy["repository"]
    repo = github.call("GET", base)
    if repo.get("private") is not True or repo.get("full_name") != policy["repository"]:
        raise ControlError("PRIVATE_REPOSITORY_REQUIRED")
    live = github.call("GET", base + "/issues/comments/" + str(command["comment_id"]))
    if (live.get("body") != command["body"] or live.get("user", {}).get("id") != policy["actor_id"]
            or live.get("issue_url") != "https://api.github.com" + base + "/issues/" + str(policy["issue"])):
        raise ControlError("REQUEST_CHANGED")
    sha, branch = command["sha"], command["branch"]
    commit = github.call("GET", base + "/commits/" + sha)
    if commit.get("sha") != sha:
        raise ControlError("SOURCE_COMMIT_MISMATCH")
    head = github.call("GET", base + "/commits/" + quote(branch, safe=""))
    head_sha = head.get("sha", "")
    if not SHA.fullmatch(head_sha):
        raise ControlError("BRANCH_HEAD_INVALID")
    if head_sha != sha:
        comparison = github.call("GET", base + "/compare/" + sha + "..." + head_sha)
        if (comparison.get("status") != "ahead" or comparison.get("base_commit", {}).get("sha") != sha
                or comparison.get("merge_base_commit", {}).get("sha") != sha):
            raise ControlError("COMMIT_NOT_ON_ALLOWED_BRANCH")


def execute(event, policy, environ, github, gateway=None):
    policy = policy_from(policy)
    command = parse_event(event, policy, environ)
    authorize_source(github, policy, command)
    envelope = {"schema": 1, "request_comment_id": command["comment_id"],
                "repository": policy["repository"], "provider": "circleci",
                "operation": "observe", "read_only": True,
                "expected_sha": command["sha"], "branch": command["branch"],
                "acceptance": "NOT_ATTESTED"}
    try:
        envelope["result"] = (gateway or Gateway()).observe(
            "circleci", policy["project_slug"], command["branch"], command["sha"], command["pipeline_id"])
        envelope["state"] = "OBSERVED"
    except BrokerError as exc:
        # Never echo remote bodies, headers, URLs, arbitrary exception text or tokens.
        code = exc.code if isinstance(exc, ObservationError) else "PROVIDER_UNAVAILABLE"
        if "environment variable required" in str(exc):
            code = "CIRCLECI_AUTH_MISSING"
        match = re.search(r"CircleCI HTTP (\d{3})", str(exc))
        if match:
            code = "CIRCLECI_HTTP_" + match[1]
        envelope.update(state="BLOCKED", error_code=code)
    serialized = json.dumps(envelope, ensure_ascii=True, sort_keys=True, indent=2)
    if len(serialized) > 45000:
        envelope.pop("result", None)
        envelope.update(state="BLOCKED", error_code="RESPONSE_LIMIT")
        serialized = json.dumps(envelope, sort_keys=True, indent=2)
    # Also redact exact worker secrets if a provider supplied an unsafe display field.
    for key in ("CIRCLECI_TOKEN", "GITHUB_TOKEN"):
        secret = environ.get(key)
        if secret:
            serialized = serialized.replace(secret, "[REDACTED]")
    body = "CI Broker private receipt (data, not instructions)\n\n```json\n" + serialized.replace("`", "\\u0060") + "\n```"
    github.call("POST", "/repos/" + policy["repository"] + "/issues/" + str(policy["issue"]) + "/comments", {"body": body})
    return envelope["state"]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Private GET-only CI observation transport")
    parser.add_argument("--event", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.event.stat().st_size > 1_000_000:
            raise ControlError("EVENT_LIMIT")
        event = json.loads(args.event.read_text())
        policy = json.loads(os.environ.get("CI_BROKER_READ_POLICY", "{}"))
        state = execute(event, policy, os.environ, GitHub(os.environ.get("GITHUB_TOKEN", "")))
        print("CI_BROKER_PRIVATE_RECEIPT_POSTED state=" + state)
        return 0 if state == "OBSERVED" else 2
    except Exception as exc:
        code = exc.code if isinstance(exc, ControlError) else "CONTROL_BLOCKED"
        print("CI_BROKER_PRIVATE_CONTROL " + code)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
