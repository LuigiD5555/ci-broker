"""Offline authorization and project/SHA binding for existing CI runs."""
import unittest
from unittest.mock import patch

from ci_broker.gateway import Gateway
from ci_broker.model import BrokerError

SHA = "a" * 40
PIPE = "12345678-1234-1234-1234-123456789abc"
WF = "12345678-1234-1234-1234-123456789abd"
SLUG = "circleci/team/project"


class Fake:
    def __init__(self):
        self.calls = []
        self.pipeline = {"id": PIPE, "project_slug": SLUG, "number": 4,
                         "vcs": {"revision": SHA, "branch": "main"}}
        self.more = False

    def call(self, method, path):
        assert method == "GET", method
        self.calls.append(path)
        if path == f"/project/{SLUG}/pipeline":
            return {"items": [self.pipeline], "next_page_token": "more"}
        if path == f"/pipeline/{PIPE}":
            return self.pipeline
        if path.endswith("/workflow"):
            return {"items": [{"id": WF, "name": "unit", "status": "failed"}]}
        if path.endswith("/job"):
            return {"items": [{"id": "job-id", "job_number": 7, "name": "test", "status": "failed"}],
                    "next_page_token": "more" if self.more else None}
        if path.endswith("/artifacts"):
            return {"items": [{"path": "junit.xml", "url": "https://signed.invalid/?secret=yes"}]}
        if path.endswith("/tests"):
            return {"items": [{"name": "test_x", "result": "failure", "message": "password=unsafe https://signed.invalid/x"}]}
        if path.endswith("/job/7"):
            return {"status": "failed", "duration": 12, "messages": [{"message": "oops"}]}
        raise AssertionError(path)


class ObservationTests(unittest.TestCase):
    def observe(self, fake, **overrides):
        args = dict(kind="circleci", project_slug=SLUG, branch="main", sha=SHA, pipeline_id=PIPE)
        args.update(overrides)
        with patch("ci_broker.gateway.CircleCIProvider._read_client", return_value=fake):
            return Gateway().observe(**args)

    def test_reads_existing_pipeline_without_receipt_or_ledger(self):
        fake = Fake()
        result = self.observe(fake)
        self.assertEqual(result["state"], "OBSERVED")
        self.assertEqual(result["pipeline"]["sha"], SHA)
        self.assertEqual(result["workflows"][0]["jobs"][0]["tests"]["items"][0]["result"], "failure")
        self.assertNotIn("signed.invalid", str(result))
        self.assertNotIn("unsafe", str(result))
        self.assertEqual(result["acceptance"], "NOT_ATTESTED")
        self.assertEqual(result["raw_logs"], "UNAVAILABLE_API_V2")

    def test_refuses_other_project_sha_or_branch_before_jobs(self):
        for key, val in [("project_slug", "circleci/other/private"), ("vcs", {"revision": "b"*40, "branch": "main"}),
                         ("vcs", {"revision": SHA, "branch": "unapproved"})]:
            fake = Fake()
            fake.pipeline[key] = val
            with self.assertRaises(BrokerError):
                self.observe(fake)
            self.assertEqual(len(fake.calls), 1)

    def test_latest_is_bounded_and_explicitly_partial(self):
        result = self.observe(Fake(), pipeline_id=None)
        self.assertTrue(result["pipeline_search_truncated"])

    def test_pagination_is_not_silent_complete_evidence(self):
        fake = Fake()
        fake.more = True
        with self.assertRaises(BrokerError):
            self.observe(fake)

    def test_invalid_inputs_never_call_api(self):
        for changes in [{"sha": "abc"}, {"project_slug": "https://evil.invalid"}, {"pipeline_id": "../me"},
                        {"branch": "../main"}, {"kind": "untrusted"}]:
            fake = Fake()
            with self.assertRaises(BrokerError):
                self.observe(fake, **changes)
            self.assertEqual(fake.calls, [])

    def test_read_only_client_rejects_writes_and_redirects(self):
        from ci_broker.remote import CircleCIClient
        client = CircleCIClient("unit-secret", read_only=True)
        for method in ("POST", "DELETE", "PATCH", "PUT"):
            with self.assertRaises(BrokerError):
                client.call(method, "/pipeline/" + PIPE)
        with patch("ci_broker.remote.request.build_opener") as opener:
            from urllib.error import HTTPError
            opener.return_value.open.side_effect = HTTPError("https://elsewhere", 302, "redirect", {}, None)
            with self.assertRaises(BrokerError):
                client.call("GET", "/pipeline/" + PIPE)


if __name__ == "__main__":
    unittest.main()
