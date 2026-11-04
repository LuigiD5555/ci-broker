"""No live network: hostile issue requests never reach provider credentials."""
import copy
import json
import unittest
from unittest.mock import Mock

from ci_broker.model import BrokerError
from ci_broker.private_control import execute, parse_event, policy_from, ControlError

SHA = "a" * 40
PIPE = "12345678-1234-1234-1234-123456789abc"
POLICY = {"schema": 1, "repository": "owner/private", "issue": 8,
          "actor": "owner", "actor_id": 7, "project_slug": "circleci/org/project", "branches": ["main"]}
BODY = f"/ci-broker read branch=main sha={SHA} pipeline={PIPE}"
EVENT = {"action": "created", "repository": {"full_name": "owner/private", "private": True, "default_branch": "main"},
         "issue": {"number": 8}, "sender": {"id": 7},
         "comment": {"id": 3, "body": BODY, "author_association": "OWNER", "user": {"id": 7, "login": "owner"}}}
ENV = {"GITHUB_EVENT_NAME": "issue_comment", "GITHUB_REPOSITORY": "owner/private", "GITHUB_REF": "refs/heads/main"}


class GitHubFake:
    def __init__(self):
        self.posts = []
        self.private = True
        self.body = BODY
        self.head = SHA
        self.comparison = "ahead"

    def call(self, method, path, body=None):
        if method == "POST":
            self.posts.append((path, body))
            return {"id": 4}
        if path.endswith("/issues/comments/3"):
            return {"body": self.body, "user": {"id": 7}, "issue_url": "https://api.github.com/repos/owner/private/issues/8"}
        if "/compare/" in path:
            return {"status": self.comparison, "base_commit": {"sha": SHA}, "merge_base_commit": {"sha": SHA}}
        if path.endswith("/commits/main"):
            return {"sha": self.head}
        if "/commits/" in path:
            return {"sha": SHA}
        return {"private": self.private, "full_name": "owner/private"}


class PrivateControlTests(unittest.TestCase):
    def test_success_calls_only_gateway_observe_and_private_comment(self):
        gh = GitHubFake()
        gateway = Mock()
        gateway.observe.return_value = {"state": "OBSERVED", "pipeline": {"sha": SHA}}
        self.assertEqual(execute(EVENT, POLICY, ENV, gh, gateway), "OBSERVED")
        gateway.observe.assert_called_once_with("circleci", POLICY["project_slug"], "main", SHA, PIPE)
        self.assertEqual(len(gateway.mock_calls), 1)
        self.assertEqual(gh.posts[0][0], "/repos/owner/private/issues/8/comments")
        self.assertIn(SHA, gh.posts[0][1]["body"])

    def test_hostile_event_matrix_never_calls_gateway(self):
        cases = []
        for key, value in [("action", "edited"), ("sender", {"id": 99}), ("issue", {"number": 8, "pull_request": {}}),
                           ("repository", {"full_name": "owner/private", "private": False, "default_branch": "main"})]:
            e = copy.deepcopy(EVENT)
            e[key] = value
            cases.append(e)
        for field, value in [("body", BODY + "\nwhoami"), ("body", "/ci-broker dispatch --approve"),
                             ("body", BODY.replace("main", "other")), ("user", {"id": 9, "login": "owner"}),
                             ("author_association", "CONTRIBUTOR")]:
            e = copy.deepcopy(EVENT)
            e["comment"][field] = value
            cases.append(e)
        for e in cases:
            gateway = Mock()
            gh = GitHubFake()
            with self.assertRaises(BrokerError):
                execute(e, POLICY, ENV, gh, gateway)
            gateway.assert_not_called()
            self.assertEqual(gateway.mock_calls, [])
            self.assertEqual(gh.posts, [])

    def test_nondefault_workflow_ref_denied(self):
        with self.assertRaises(ControlError):
            parse_event(EVENT, POLICY, dict(ENV, GITHUB_REF="refs/pull/1/merge"))

    def test_changed_comment_public_repo_or_unrelated_commit_denied(self):
        for field, value in [("body", BODY + " "), ("private", False), ("comparison", "diverged")]:
            gh = GitHubFake()
            gh.head = "b" * 40
            setattr(gh, field, value)
            gateway = Mock()
            with self.assertRaises(ControlError):
                execute(EVENT, POLICY, ENV, gh, gateway)
            self.assertEqual(gateway.mock_calls, [])

    def test_auth_error_bodies_and_secrets_never_escape(self):
        gh = GitHubFake()
        gateway = Mock()
        gateway.observe.side_effect = BrokerError("CircleCI HTTP 403; secret=never-print-me")
        state = execute(EVENT, POLICY, ENV, gh, gateway)
        self.assertEqual(state, "BLOCKED")
        self.assertIn("CIRCLECI_HTTP_403", gh.posts[0][1]["body"])
        self.assertNotIn("never-print-me", gh.posts[0][1]["body"])

    def test_known_secret_redacted_and_json_fence_cannot_be_injected(self):
        gh = GitHubFake()
        gateway = Mock()
        gateway.observe.return_value = {"message": "secret-value ``` malicious"}
        execute(EVENT, POLICY, dict(ENV, CIRCLECI_TOKEN="secret-value"), gh, gateway)
        body = gh.posts[0][1]["body"]
        self.assertNotIn("secret-value", body)
        self.assertEqual(body.count("```"), 2)

    def test_invalid_policy_denied(self):
        for patch in [{"repository": "../bad"}, {"issue": 0}, {"branches": []}, {"project_slug": "https://evil.invalid"}]:
            with self.assertRaises(BrokerError):
                policy_from(dict(POLICY, **patch))


if __name__ == "__main__":
    unittest.main()
