"""Gateway offline contracts: never contact external CI."""
import unittest
from pathlib import Path
from unittest.mock import patch

from ci_broker.gateway import Gateway, CircleCIProvider, register_provider
from ci_broker.model import BrokerError

REF = "remote/" + "a" * 64
SHA = "a" * 40
PIPE = "12345678-1234-1234-1234-123456789abc"
WF = "12345678-1234-1234-1234-123456789abd"


class StubClient:
    def workflows(self, pipeline):
        assert pipeline == PIPE
        return [{"id": WF, "name": "tests", "status": "success"}]

    def call(self, method, path):
        if path.endswith("/job"):
            return {"items": [{"job_number": 7, "name": "pytest",
                                "status": "success"}], "next_page_token": None}
        if path.endswith("/artifacts"):
            return {"items": [{"path": "results/junit.xml", "node_index": 0,
                                "url": "https://sensitive-signed.example/?token=secret"}]}
        if path.endswith("/7"):
            return {"steps": [{"name": "unit",
                               "actions": [{"exit_code": 0, "status": "success",
                                            "output_url": "https://private.s3/log"}]}]}
        raise AssertionError(path)


class TestGateway(unittest.TestCase):
    def test_capabilities(self):
        result = Gateway().capabilities()
        self.assertIn("circleci", result["providers"])
        self.assertFalse(result["automatic_billing"])
        self.assertIn("logs", result["operations"])

    def test_explicit_dispatch_approval_and_unknown_adapter(self):
        with self.assertRaises(BrokerError):
            Gateway().dispatch("circleci", Path("."), "light", "auto", SHA, "main", False)
        with self.assertRaisesRegex(BrokerError, "No trusted"):
            Gateway().dispatch("missing", Path("."), "light", "auto", SHA, "main", True)

    @patch("ci_broker.gateway.CircleCIProvider._context",
           return_value=({"provider": "circle-main"}, {"pipeline_id": PIPE, "sha": SHA}))
    @patch("ci_broker.gateway.CircleCIProvider._slug", return_value="gh/org/project")
    @patch("ci_broker.gateway.CircleCIProvider._client", return_value=StubClient())
    def test_evidence_is_scoped_and_no_signed_urls_escape(self, *_):
        api = CircleCIProvider()
        self.assertEqual(api.jobs(REF)["jobs"][0]["number"], 7)
        artifacts = api.artifacts(REF, 7)
        self.assertEqual(artifacts["artifacts"][0]["path"], "results/junit.xml")
        self.assertNotIn("sensitive-signed", str(artifacts))
        logs = api.logs(REF, 7)
        self.assertEqual(logs["steps"][0]["exit_code"], 0)
        self.assertNotIn("private.s3", str(logs))
        self.assertEqual(logs["content"], "STEP_METADATA_ONLY_NO_RAW_LOGS")
        with self.assertRaisesRegex(BrokerError, "does not belong"):
            api.artifacts(REF, 8)

    @patch("ci_broker.gateway.CircleCIProvider._context",
           return_value=({"provider": "circle-main"}, {"pipeline_id": PIPE, "sha": SHA}))
    @patch("ci_broker.gateway.CircleCIProvider._client")
    def test_rejects_incomplete_job_pages(self, mock, *_):
        mock.return_value.workflows.return_value = [{"id": WF, "name": "tests"}]
        mock.return_value.call.return_value = {"items": [], "next_page_token": "more"}
        with self.assertRaisesRegex(BrokerError, "paginated"):
            CircleCIProvider().jobs(REF)


    def test_second_provider_is_not_coupled_to_circleci(self):
        from unittest.mock import patch
        class SampleProvider:
            kind = "stub-unit"
            def status(self, run_ref):
                return {"state": "SUCCESS_CI_ONLY", "run_ref": run_ref,
                        "acceptance": "NOT_ATTESTED"}
            def jobs(self, run_ref):
                return {"run_ref": run_ref, "jobs": [{"number": 42}]}
        register_provider("stub-unit", SampleProvider)
        with patch("ci_broker.gateway._receipt", return_value=({"provider": "stub-account"}, {})):
            with patch("ci_broker.config.accounts", return_value={
                "stub-account": {"remote_kind": "stub-unit"}
            }):
                self.assertEqual(Gateway().status(REF)["state"], "SUCCESS_CI_ONLY")
                self.assertEqual(Gateway().jobs(REF)["jobs"][0]["number"], 42)

    def test_provider_registration_requires_explicit_nonduplicate_factory(self):
        with self.assertRaises(BrokerError):
            register_provider("circleci", CircleCIProvider)


if __name__ == "__main__":
    unittest.main()
