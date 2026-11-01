from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from ci_broker.config import initialize, load_project, load_providers
from ci_broker.cli import main
from ci_broker.ledger import Ledger
from ci_broker.models import ConfigError, Profile, Quote, adapter_for, register_adapter
from ci_broker.scheduler import plan


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name)
        initialize(self.root)
        self.ledger = Ledger(self.root / "ledger.jsonl")
        self.env = mock.patch.dict(os.environ, {"CI_BROKER_LEDGER": str(self.ledger.path), "CI_BROKER_PROVIDERS_DIR": str(self.root / "no-providers")})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_project_contract_and_do_not_overwrite(self):
        project = load_project(self.root)
        self.assertEqual(project.identifier, "my-project")
        self.assertEqual(project.profiles["light"].timeout_minutes, 10)
        with self.assertRaises(FileExistsError):
            initialize(self.root)

    def test_local_first_plan_without_network(self):
        project = load_project(self.root)
        decision = plan(project, project.profiles["light"], load_providers(), [])
        self.assertEqual(decision["selected"], "local")
        self.assertEqual(decision["advisory_only"], True)
        self.assertEqual(decision["options"][0]["provider"], "circleci")

    def test_external_requires_verification(self):
        project = load_project(self.root)
        result = plan(project, project.profiles["light"], load_providers(), [], independent=True)
        self.assertIsNone(result["selected"])

    def test_provider_json_override(self):
        folder = self.root / "operator"
        folder.mkdir()
        (folder / "extra.json").write_text(json.dumps({"github": {"enabled": True, "verified": True},
                                                       "custom": {"adapter": "metered", "enabled": False, "verified": False,
                                                                  "quota": 100, "rate_per_minute": 1, "unit": "credits", "capabilities": ["python"]}}))
        with mock.patch.dict(os.environ, {"CI_BROKER_PROVIDERS_DIR": str(folder)}):
            data = load_providers()
        self.assertTrue(data["github"]["verified"])
        self.assertIn("custom", data)

    def test_quote_different_billing_increment(self):
        profile = Profile("light", ("true",), 1.5, 2.25, frozenset(["python"]))
        quote = adapter_for({"adapter": "metered"}).quote({"rate_per_minute": 2, "billing_increment_seconds": 1, "unit": "vcpu-min"}, profile)
        self.assertEqual((quote.expected, quote.worst), (3, 4.5))

    def test_cli_refuses_run_without_explicit_approval(self):
        self.assertEqual(main(["--project-root", str(self.root), "run", "--profile", "light"]), 2)

    def test_cli_executes_only_declared_local_command(self):
        path = self.root / ".ci-broker" / "project.toml"
        content = path.read_text().replace("python -m unittest discover -s tests", "printf pass > output.txt")
        path.write_text(content)
        self.assertEqual(main(["--project-root", str(self.root), "run", "--profile", "light", "--yes"]), 0)
        self.assertEqual((self.root / "output.txt").read_text(), "pass")

    def test_run_from_any_project_root(self):
        self.assertEqual(main(["--project-root", str(self.root), "validate"]), 0)
        self.assertEqual(main(["--project-root", str(self.root), "plan", "--profile", "light"]), 0)

    def test_ledger_reservation_idempotency_and_completion(self):
        first = self.ledger.reserve("cloud", "one", "light", "same", 20, 100, 0.1, True, False)
        second = self.ledger.reserve("cloud", "one", "light", "same", 20, 100, 0.1, True, False)
        self.assertEqual(first, second)
        with self.assertRaises(ConfigError):
            self.ledger.reserve("cloud", "two", "light", "same", 20, 100, 0.1, True, False)
        self.ledger.finish("cloud", "same", 3)
        with self.assertRaises(ConfigError):
            self.ledger.finish("cloud", "same", 3)
        self.assertEqual(Ledger.usage(self.ledger.entries(), "cloud", first["month"]), 3)

    def test_ledger_quota_protection(self):
        self.ledger.reserve("cloud", "x", "light", "a", 30, 50, 0.1, True, False)
        with self.assertRaises(ConfigError):
            self.ledger.reserve("cloud", "x", "light", "b", 30, 50, 0.1, True, False)

    def test_ledger_unverified_external(self):
        with self.assertRaises(ConfigError):
            self.ledger.reserve("cloud", "x", "light", "a", 10, 100, 0.1, False, False)

    def test_unknown_plugin_denied(self):
        with self.assertRaises(ConfigError):
            adapter_for({"adapter": "import.os.system"})

    def test_render_github_and_gitlab_only(self):
        self.assertEqual(main(["--project-root", str(self.root), "render", "--profile", "light", "--provider", "github"]), 0)
        self.assertEqual(main(["--project-root", str(self.root), "render", "--profile", "light", "--provider", "gitlab"]), 0)
        self.assertEqual(main(["--project-root", str(self.root), "render", "--profile", "light", "--provider", "no-renderer"]), 2)

    def test_invalid_profile_duration(self):
        p = self.root / ".ci-broker" / "project.toml"
        p.write_text(p.read_text().replace("timeout_minutes = 10", "timeout_minutes = 2"))
        with self.assertRaises(ConfigError):
            load_project(self.root)


if __name__ == "__main__":
    unittest.main()
