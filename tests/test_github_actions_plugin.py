"""GitHub Actions reader: offline tests with `gh` mocked; no network, no dispatch."""
from __future__ import annotations
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from ci_broker import providers
from ci_broker.cli import main
from ci_broker.model import BrokerError
from ci_broker.plugins.github_actions import GitHubActionsReader, github_units

RUN = {
    "databaseId": 4242,
    "conclusion": "success",
    "jobs": [
        {"startedAt": "2026-11-01T10:00:00Z", "completedAt": "2026-11-01T10:01:30Z"},
        {"startedAt": "2026-11-01T10:00:00Z", "completedAt": None},
    ],
}


class GitHubUnitsTests(unittest.TestCase):
    def test_rounds_each_finished_job_up_to_whole_minutes_and_skips_unfinished_jobs(self):
        self.assertEqual(github_units(RUN), 2)

    def test_run_without_jobs_costs_nothing(self):
        self.assertEqual(github_units({"jobs": []}), 0)


class GitHubReaderTests(unittest.TestCase):
    def test_read_run_maps_gh_output_to_observation(self):
        reader = GitHubActionsReader()
        with mock.patch.object(reader, "fetch_run", return_value=RUN) as fetch:
            observation = reader.read_run("4242")
        fetch.assert_called_once_with("4242")
        self.assertEqual(observation, {"run_ref": "4242", "units": 2, "status": "success"})

    def test_rejects_non_numeric_run_id_before_calling_gh(self):
        reader = GitHubActionsReader()
        with mock.patch("subprocess.run") as run:
            with self.assertRaises(BrokerError):
                reader.read_run("4242; rm -rf /")
        run.assert_not_called()

    def test_gh_failure_is_reported_without_crashing(self):
        reader = GitHubActionsReader()
        failed = mock.Mock(returncode=1, stdout="", stderr="HTTP 404")
        with mock.patch("subprocess.run", return_value=failed):
            with self.assertRaises(BrokerError):
                reader.fetch_run("4242")


class ImportRunCLITests(unittest.TestCase):
    def test_import_run_records_measured_units_once(self):
        with TemporaryDirectory() as t:
            accounts = Path(t) / "providers.json"
            accounts.write_text(json.dumps({"github": {
                "adapter": "github-actions", "enabled": True, "verified": True,
                "units_per_minute": 1, "quota": 2000, "capabilities": ["python"]}}))
            env = {"CI_BROKER_ACCOUNTS": str(accounts), "CI_BROKER_LEDGER": str(Path(t) / "ledger.jsonl")}
            argv = ["--project-root", t, "--plugin", "github-actions",
                    "import-run", "--provider", "github", "--run-id", "4242"]
            # The plugin registry is process-global; each CLI run gets its own copy so the second run can register again.
            with mock.patch.dict(os.environ, env), mock.patch.dict(providers._REGISTRY), \
                    mock.patch.object(GitHubActionsReader, "fetch_run", return_value=RUN), \
                    redirect_stdout(StringIO()) as first:
                self.assertEqual(main(argv), 0)
            self.assertIn('"created": true', first.getvalue())
            with mock.patch.dict(os.environ, env), mock.patch.dict(providers._REGISTRY), \
                    mock.patch.object(GitHubActionsReader, "fetch_run", return_value=RUN), \
                    redirect_stdout(StringIO()) as second:
                self.assertEqual(main(argv), 0)
            self.assertIn('"created": false', second.getvalue())

    def test_import_run_refuses_provider_without_reader(self):
        with TemporaryDirectory() as t:
            accounts = Path(t) / "providers.json"
            accounts.write_text(json.dumps({"local": {"adapter": "local", "enabled": True,
                "verified": True, "quota": None, "capabilities": ["python"]}}))
            env = {"CI_BROKER_ACCOUNTS": str(accounts), "CI_BROKER_LEDGER": str(Path(t) / "ledger.jsonl")}
            with mock.patch.dict(os.environ, env), redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                self.assertEqual(main(["--project-root", t, "import-run", "--provider", "local",
                                       "--run-id", "1"]), 2)


if __name__ == "__main__":
    unittest.main()
