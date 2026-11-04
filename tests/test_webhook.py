"""GitHub comment bridge contract tests: never access GitHub or CI."""
from __future__ import annotations

import hashlib
import hmac
from pathlib import Path
import unittest
from unittest import mock

from ci_broker.model import BrokerError
from ci_broker.webhook import process_event, verify_signature

SHA = "f2905babf8cb85436afc47b77a72bff8956c23d1"


class FakeGitHub:
    def __init__(self, sha=SHA, repo="LuigiD5555/extracta"):
        self.sha = sha
        self.repo = repo
        self.comments = []

    def pull_request(self, number):
        return {"head": {"sha": self.sha, "ref": "feature/z01",
                         "repo": {"full_name": self.repo}}}

    def reply(self, number, body):
        self.comments.append((number, body))
        return {"id": 123}


def event(author="LuigiD5555", repo="LuigiD5555/extracta", sha=SHA):
    return {"action": "created", "repository": {"full_name": repo},
            "issue": {"number": 65, "pull_request": {"url": "https://api.github.com/x"}},
            "comment": {"user": {"login": author},
                        "body": f"/ci-broker run z01 sha={sha} --approve"}}


class WebhookTests(unittest.TestCase):
    def test_signature_good_and_bad(self):
        body, secret = b'{"action":"created"}', "a"*40
        sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        self.assertTrue(verify_signature(body, sig, secret))
        self.assertFalse(verify_signature(body+b"x", sig, secret))
        self.assertFalse(verify_signature(body, "sha1="+sig[7:], secret))
        self.assertFalse(verify_signature(body, sig, ""))

    def test_only_owner_created_pr_comment(self):
        gh = FakeGitHub()
        with mock.patch("ci_broker.webhook.dispatch") as run:
            self.assertEqual(process_event(event(author="unknown"), gh.repo,
                                           "LuigiD5555", Path("."), gh)["state"],
                             "IGNORED_AUTHOR")
            e = event(); e["action"] = "edited"
            self.assertEqual(process_event(e, gh.repo, "LuigiD5555",
                                           Path("."), gh)["state"], "IGNORED_EVENT")
            e = event(); e["issue"].pop("pull_request")
            self.assertEqual(process_event(e, gh.repo, "LuigiD5555",
                                           Path("."), gh)["state"], "IGNORED_NON_PR")
            e = event(); e["comment"]["body"] = "/ci-broker run z01"
            self.assertEqual(process_event(e, gh.repo, "LuigiD5555",
                                           Path("."), gh)["state"], "IGNORED_COMMAND")
        run.assert_not_called()

    def test_exact_head_sha_and_same_repo_required(self):
        with self.assertRaisesRegex(BrokerError, "current PR head"):
            process_event(event(), "LuigiD5555/extracta", "LuigiD5555",
                          Path("."), FakeGitHub(sha="0"*40))
        with self.assertRaisesRegex(BrokerError, "Forked PR"):
            process_event(event(), "LuigiD5555/extracta", "LuigiD5555",
                          Path("."), FakeGitHub(repo="other/fork"))

    def test_signed_authorized_command_dispatches_declared_profile_once(self):
        gh = FakeGitHub()
        response = {"state": "SUBMITTED_UNVERIFIED", "run_ref": "remote/key"}
        with mock.patch("ci_broker.webhook.dispatch", return_value=response) as run:
            actual = process_event(event(), gh.repo, "LuigiD5555", Path("/tmp/project"), gh)
        self.assertEqual(actual, response)
        run.assert_called_once_with(Path("/tmp/project"), "z01", "auto",
                                    SHA, "feature/z01", approve=True)
        self.assertEqual(len(gh.comments), 1)
        self.assertIn("NOT_ATTESTED", gh.comments[0][1])

    def test_failed_github_reply_does_not_repeat_dispatch(self):
        gh = FakeGitHub()
        gh.reply = mock.Mock(side_effect=BrokerError("no permission"))
        with mock.patch("ci_broker.webhook.dispatch",
                        return_value={"state": "UNKNOWN_DISPATCH_HOLD", "run_ref": "remote/x"}) as run:
            result = process_event(event(), gh.repo, "LuigiD5555", Path("."), gh)
        self.assertEqual(result["comment_state"], "FAILED_NO_RETRY")
        run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
