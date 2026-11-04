"""Optional GitHub PR comment -> CI Broker bridge, without GitHub Actions.

Operator-hosted HTTP receiver; never automatically public. GitHub validates event
delivery using HMAC-SHA256. Only an allowlisted comment author can request one
predeclared CI profile at the current same-repository PR head SHA.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
from urllib import error, parse, request

from .model import BrokerError
from .remote import dispatch

COMMAND = re.compile(r"^/ci-broker run ([A-Za-z][A-Za-z0-9_-]{0,63}) sha=([0-9a-f]{40}) --approve$")
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
MAX_BODY = 65_536


def verify_signature(raw: bytes, signature: str, secret: str) -> bool:
    if not isinstance(signature, str) or not signature.startswith("sha256=") or not secret:
        return False
    supplied = signature[7:]
    if not re.fullmatch(r"[0-9a-f]{64}", supplied):
        return False
    expected = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, supplied)


class GitHubClient:
    BASE = "https://api.github.com"

    def __init__(self, token: str, repository: str):
        if not token or "\r" in token or "\n" in token:
            raise BrokerError("CI_BROKER_GITHUB_TOKEN required for PR identity checks")
        if not REPOSITORY.fullmatch(repository):
            raise BrokerError("Invalid allowed repository")
        self.token = token
        self.repo = repository

    def call(self, method: str, path: str, data: dict | None = None) -> dict:
        if not path.startswith("/") or ".." in path or "?" in path:
            raise BrokerError("Invalid GitHub API path")
        headers = {"Authorization": "Bearer " + self.token,
                   "Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28"}
        body = None if data is None else json.dumps(data).encode("utf-8")
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = request.Request(self.BASE + path, data=body, headers=headers, method=method)
        try:
            with request.urlopen(req, timeout=20) as response:
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise BrokerError("GitHub response too large")
                obj = json.loads(raw)
                if not isinstance(obj, dict):
                    raise BrokerError("Unexpected GitHub response")
                return obj
        except error.HTTPError as exc:
            raise BrokerError(f"GitHub HTTP {exc.code}; refused webhook command") from None
        except (error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise BrokerError(f"GitHub verification unavailable ({type(exc).__name__})") from None

    def pull_request(self, number: int) -> dict:
        return self.call("GET", f"/repos/{self.repo}/pulls/{number}")

    def reply(self, number: int, body: str) -> dict:
        return self.call("POST", f"/repos/{self.repo}/issues/{number}/comments", {"body": body})


def process_event(payload: dict, repo: str, author: str, root: Path,
                  github: GitHubClient) -> dict:
    if not isinstance(payload, dict) or payload.get("action") != "created":
        return {"state": "IGNORED_EVENT"}
    if payload.get("repository", {}).get("full_name") != repo:
        return {"state": "IGNORED_REPOSITORY"}
    issue = payload.get("issue")
    comment = payload.get("comment")
    if not isinstance(issue, dict) or "pull_request" not in issue or not isinstance(comment, dict):
        return {"state": "IGNORED_NON_PR"}
    if comment.get("user", {}).get("login") != author:
        return {"state": "IGNORED_AUTHOR"}
    match = COMMAND.fullmatch(str(comment.get("body", "")).strip())
    if not match:
        return {"state": "IGNORED_COMMAND"}
    number = issue.get("number")
    if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
        raise BrokerError("Invalid pull request number")
    profile, expected_sha = match.groups()
    pr = github.pull_request(number)
    head = pr.get("head", {})
    if not isinstance(head, dict) or head.get("sha") != expected_sha:
        raise BrokerError("Requested SHA is not the current PR head")
    if head.get("repo", {}).get("full_name") != repo:
        raise BrokerError("Forked PR head is not permitted")
    branch = head.get("ref")
    result = dispatch(root, profile, "auto", expected_sha, branch, approve=True)
    # We never automatically retry on an ambiguous response.
    summary = ("CI Broker remote request\n\n"
               f"- state: `{result['state']}`\n"
               f"- profile: `{profile}`\n"
               f"- SHA: `{expected_sha}`\n"
               f"- run_ref: `{result.get('run_ref', 'none')}`\n"
               "- acceptance: NOT_ATTESTED; review exact CI witnesses before merging.")
    try:
        github.reply(number, summary)
    except BrokerError:
        # A lost reply is NOT a reason to repeat a chargeable dispatch.
        result["comment_state"] = "FAILED_NO_RETRY"
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ci-broker-webhook")
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--repo", required=True, help="Exact trusted owner/repository")
    parser.add_argument("--author", required=True, help="Only trusted GitHub comment author")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8791)
    args = parser.parse_args(argv)
    if not REPOSITORY.fullmatch(args.repo) or not re.fullmatch(r"[A-Za-z0-9-]{1,39}", args.author):
        parser.error("Invalid --repo or --author")
    if args.host not in ("127.0.0.1", "::1", "localhost"):
        parser.error("Only localhost binding is supported; use an authenticated tunnel")
    if not 1 <= args.port <= 65535:
        parser.error("Invalid port")
    secret = os.environ.get("CI_BROKER_WEBHOOK_SECRET", "")
    token = os.environ.get("CI_BROKER_GITHUB_TOKEN", "")
    if len(secret) < 32:
        parser.error("CI_BROKER_WEBHOOK_SECRET must be at least 32 characters")
    github = GitHubClient(token, args.repo)
    root = args.project_root.resolve(strict=True)
    if not (root / ".ci-broker" / "project.json").is_file():
        parser.error("Project root does not contain CI Broker config")

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path != "/github" or self.headers.get("X-GitHub-Event") != "issue_comment":
                self.send_error(404)
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                size = 0
            if size <= 0 or size > MAX_BODY:
                self.send_error(413)
                return
            raw = self.rfile.read(size)
            if not verify_signature(raw, self.headers.get("X-Hub-Signature-256", ""), secret):
                self.send_error(401)
                return
            try:
                payload = json.loads(raw)
                result = process_event(payload, args.repo, args.author, root, github)
                code = 202 if result.get("state") in {
                    "SUBMITTED_UNVERIFIED", "UNKNOWN_DISPATCH_HOLD"} else 200
            except (BrokerError, ValueError, KeyError, TypeError) as exc:
                result, code = {"state": "BLOCKED", "reason": str(exc)[:160]}, 400
            data = json.dumps(result, sort_keys=True).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format, *args):
            # No tokens, signed webhook body or user-supplied comments in logs.
            return

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
