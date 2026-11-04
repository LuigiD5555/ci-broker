"""Remote CircleCI contract tests: offline only; no API token or paid CI."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock
from uuid import UUID

from ci_broker.ledger import Ledger
from ci_broker.model import BrokerError
from ci_broker.remote import (
    CircleCIClient, _reference, dispatch, main, status, cancel,
)

SHA = "f2905babf8cb85436afc47b77a72bff8956c23d1"
PIPE = "9c0045b8-9e21-4b71-86d2-d5c1d4614ee8"
WORKFLOW = "05d432d1-bf8f-4123-8d57-2fb419dffde4"
DEFINITION = "d423d3a7-8f7b-4c9b-87bf-c7e2e1ea26c2"


class FakeCircle:
    def __init__(self, *, revision=SHA, workflows=None, explode=False):
        self.revision = revision
        self.items = ([{"id": WORKFLOW, "status": "success", "name": "z01"}]
                      if workflows is None else workflows)
        self.explode = explode
        self.calls = []

    def trigger(self, settings, branch, sha):
        self.calls.append(("trigger", settings["flag"], branch, sha))
        if self.explode:
            raise BrokerError("Network timeout; remote state unknown")
        return {"id": PIPE}

    def pipeline(self, pipeline_id):
        self.calls.append(("pipeline", pipeline_id))
        return {"id": pipeline_id, "vcs": {"revision": self.revision}}

    def workflows(self, pipeline_id):
        self.calls.append(("workflows", pipeline_id))
        return self.items

    def cancel(self, workflow_id):
        self.calls.append(("cancel", workflow_id))
        return {}


class RemoteTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / ".ci-broker").mkdir()
        self.catalog = self.root / "providers.json"
        self.catalog.write_text(json.dumps({
            "circle-main": {
                "adapter": "metered", "remote_kind": "circleci",
                "enabled": True, "verified": True, "independent": True,
                "quota": 100, "units_per_minute": 2,
                "capabilities": ["python"], "reserve_fraction": 0.1
            },
            "circle-backup": {
                "adapter": "metered", "remote_kind": "circleci",
                "enabled": False, "verified": True, "independent": True,
                "quota": 100, "units_per_minute": 2,
                "capabilities": ["python"], "reserve_fraction": 0.1
            },
        }))
        self.project_file = self.root / ".ci-broker/project.json"
        self.project_file.write_text(json.dumps({
            "schema": 1, "project_id": "sample",
            "tasks": {"z01": {
                "command": ["python", "--version"], "capabilities": ["python"],
                "expected_minutes": 2, "timeout_minutes": 4}},
            "profiles": {"z01": ["z01"]},
            "remotes": {"circleci": {
                "project_slug": "gh/owner/sample", "definition_id": DEFINITION,
                "sha_parameter": "ci_broker_sha",
                "profiles": {"z01": "ci_broker_z01"}
            }}
        }))
        self.env = mock.patch.dict(os.environ, {
            "CI_BROKER_ACCOUNTS": str(self.catalog),
            "CI_BROKER_LEDGER": str(self.root / "ledger.jsonl"),
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_plan_no_token_or_network(self):
        with redirect_stdout(StringIO()) as out:
            self.assertEqual(main(["--project-root", str(self.root), "plan", "--profile", "z01"]), 0)
        data = json.loads(out.getvalue())
        self.assertEqual(data["selected"], "circle-main")
        self.assertEqual(data["state"], "ADVISORY_ONLY")

    def test_explicit_approval_and_exact_sha_required(self):
        with self.assertRaisesRegex(BrokerError, "approve"):
            dispatch(self.root, "z01", "auto", SHA, "feature/test", False, FakeCircle())
        with self.assertRaisesRegex(BrokerError, "SHA"):
            dispatch(self.root, "z01", "auto", "abc", "feature/test", True, FakeCircle())
        with self.assertRaisesRegex(BrokerError, "branch"):
            dispatch(self.root, "z01", "auto", SHA, "../bad", True, FakeCircle())
        self.assertEqual(Ledger().events(), [])

    def test_reserve_once_receipt_success_and_duplicate_refused(self):
        fake = FakeCircle()
        result = dispatch(self.root, "z01", "auto", SHA, "feature/test", True, fake)
        self.assertEqual(result["state"], "SUBMITTED_UNVERIFIED")
        self.assertEqual(result["pipeline_id"], PIPE)
        events = Ledger().events()
        self.assertEqual([e["kind"] for e in events], ["reserve", "receipt"])
        self.assertEqual(events[0]["units"], 8)
        self.assertEqual(result["run_ref"], _reference("sample", "z01", SHA))
        with self.assertRaisesRegex(BrokerError, "already reserved"):
            dispatch(self.root, "z01", "circle-main", SHA, "feature/test", True, fake)
        self.assertEqual(len(fake.calls), 1)

    def test_duplicate_ref_blocked_across_different_remote_account(self):
        first = dispatch(self.root, "z01", "circle-main", SHA, "feature/test", True, FakeCircle())
        self.assertEqual(first["state"], "SUBMITTED_UNVERIFIED")
        catalog = json.loads(self.catalog.read_text())
        catalog["circle-backup"]["enabled"] = True
        self.catalog.write_text(json.dumps(catalog))
        with self.assertRaisesRegex(BrokerError, "already reserved"):
            dispatch(self.root, "z01", "circle-backup", SHA, "feature/test", True, FakeCircle())

    def test_failed_network_keeps_budget_reserved(self):
        result = dispatch(self.root, "z01", "auto", SHA, "feature/test", True,
                          FakeCircle(explode=True))
        self.assertEqual(result["state"], "UNKNOWN_DISPATCH_HOLD")
        self.assertEqual([e["kind"] for e in Ledger().events()], ["reserve"])
        with self.assertRaisesRegex(BrokerError, "already reserved"):
            dispatch(self.root, "z01", "auto", SHA, "feature/test", True, FakeCircle())

    def test_status_is_not_formal_acceptance(self):
        submit = dispatch(self.root, "z01", "auto", SHA, "feature/test", True, FakeCircle())
        good = status(submit["run_ref"], FakeCircle())
        self.assertEqual(good["state"], "SUCCESS_CI_ONLY")
        self.assertIn("NOT_ATTESTED", good["acceptance"])
        bad_sha = status(submit["run_ref"], FakeCircle(revision="0"*40))
        self.assertEqual(bad_sha["state"], "UNVERIFIED_SHA")
        fail = status(submit["run_ref"], FakeCircle(workflows=[
            {"id": WORKFLOW, "status": "failed", "name": "z01"}]))
        self.assertEqual(fail["state"], "FAILED")
        pending = status(submit["run_ref"], FakeCircle(workflows=[]))
        self.assertEqual(pending["state"], "PENDING")

    def test_cancel_needs_approval_preserves_reservation(self):
        submit = dispatch(self.root, "z01", "auto", SHA, "feature/test", True, FakeCircle())
        with self.assertRaisesRegex(BrokerError, "approve"):
            cancel(submit["run_ref"], False, FakeCircle())
        fake = FakeCircle(workflows=[{"id": WORKFLOW, "status": "running"}])
        result = cancel(submit["run_ref"], True, fake)
        self.assertEqual(result["state"], "CANCEL_REQUESTED")
        self.assertEqual(fake.calls[-1], ("cancel", WORKFLOW))
        self.assertEqual([e["kind"] for e in Ledger().events()], ["reserve", "receipt"])

    def test_unverified_cloud_is_not_eligible(self):
        catalog = json.loads(self.catalog.read_text())
        catalog["circle-main"]["verified"] = False
        self.catalog.write_text(json.dumps(catalog))
        with self.assertRaisesRegex(BrokerError, "No eligible"):
            dispatch(self.root, "z01", "auto", SHA, "feature/test", True, FakeCircle())
        self.assertEqual(Ledger().events(), [])

    def test_cli_rejects_dispatch_without_approval_and_does_not_reserve(self):
        with redirect_stderr(StringIO()):
            rc = main(["--project-root", str(self.root), "dispatch", "--profile",
                       "z01", "--sha", SHA, "--branch", "feature/test"])
        self.assertEqual(rc, 2)
        self.assertEqual(Ledger().events(), [])


class APIProtocolTests(unittest.TestCase):
    def test_circleci_pipeline_run_endpoint_and_expected_sha(self):
        calls = []
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, n): return json.dumps({"id": PIPE}).encode()
        def fake_open(req, timeout):
            calls.append(req)
            return Response()
        with mock.patch("ci_broker.remote.request.urlopen", side_effect=fake_open):
            client = CircleCIClient("test-token-only")
            settings = {"project_slug": "gh/owner/repo", "definition_id": DEFINITION,
                        "flag": "ci_broker_z01", "sha_parameter": "ci_broker_sha"}
            reply = client.trigger(settings, "feature/test", SHA)
        self.assertEqual(reply["id"], PIPE)
        req = calls[0]
        self.assertEqual(req.full_url,
                         "https://circleci.com/api/v2/project/gh/owner/repo/pipeline/run")
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.get_header("Circle-token"), "test-token-only")
        body = json.loads(req.data)
        self.assertEqual(body["parameters"], {"ci_broker_z01": True, "ci_broker_sha": SHA})
        self.assertEqual(body["checkout"], {"branch": "feature/test"})

if __name__ == "__main__":
    unittest.main()
