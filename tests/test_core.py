"""No network, cloud credentials, persistent user files or remote execution."""
from __future__ import annotations
from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from ci_broker.config import project, accounts
from ci_broker.graph import order, estimate
from ci_broker.ledger import Ledger, active, usage
from ci_broker.model import BrokerError
from ci_broker.planner import plan
from ci_broker.providers import MeteredProvider, resolve

def sample():
    return {"schema": 1, "project_id": "sample", "tasks": {
        "format": {"command": ["python", "--version"], "expected_minutes": 1, "capabilities": ["python"]},
        "tests": {"needs": ["format"], "command": ["python", "--version"],
                  "expected_minutes": 2, "timeout_minutes": 5, "capabilities": ["python"]}
    }, "profiles": {"light": ["tests"]}}

def catalog():
    return {
        "local": {"adapter": "local", "enabled": True, "verified": True, "preferred": True,
                  "independent": False, "quota": None, "capabilities": ["python"]},
        "cloud": {"adapter": "metered", "enabled": True, "verified": True,
                  "independent": True, "quota": 30, "unit": "credits", "units_per_minute": 2,
                  "reserve_fraction": .1, "capabilities": ["python"]}
    }

class GraphTests(unittest.TestCase):
    def test_transitive_inclusion_once(self):
        cfg = sample()
        self.assertEqual(order(cfg["tasks"], ["tests", "format"]), ["format", "tests"])

    def test_cycle_is_rejected(self):
        cfg = sample()
        cfg["tasks"]["format"]["needs"] = ["tests"]
        with self.assertRaisesRegex(BrokerError, "cycle"):
            order(cfg["tasks"], ["tests"])

    def test_missing_dependency_is_rejected(self):
        with self.assertRaisesRegex(BrokerError, "Unknown task"):
            order({"x": {"needs": ["not-there"]}}, ["x"])

    def test_estimate_distinguishes_serial_and_critical_path(self):
        cfg = sample()
        result = estimate(cfg["tasks"], ["format", "tests"])
        self.assertEqual(result["serial_expected_minutes"], 3)
        self.assertEqual(result["critical_path_minutes"], 3)

class ProviderTests(unittest.TestCase):
    def test_ceil_billing_is_per_provider(self):
        q = MeteredProvider().quote({"units_per_minute": 2, "unit": "credits",
                                      "billing_increment_seconds": 60},
                                     {"expected_minutes": 1.1, "timeout_minutes": 3.1})
        self.assertEqual((q.expected_units, q.worst_units, q.unit), (4, 8, "credits"))

    def test_unknown_adapter_never_loads_project_supplied_python(self):
        with self.assertRaisesRegex(BrokerError, "Unknown adapter"):
            resolve({"adapter": "fake.module:arbitrary_code"})

    def test_disabled_provider_not_eligible(self):
        ok, why = MeteredProvider().eligible({"enabled": False}, {"expected_minutes": 1})
        self.assertFalse(ok)
        self.assertEqual(why, "provider-disabled")

class PlannerTests(unittest.TestCase):
    def test_local_first(self):
        p = plan(sample(), catalog(), "light")
        self.assertEqual(p["selected"], "local")
        self.assertEqual(p["tasks"], ["format", "tests"])
        self.assertTrue(p["advisory_only"])

    def test_independent_requires_external(self):
        self.assertEqual(plan(sample(), catalog(), "light", independent=True)["selected"], "cloud")

    def test_no_budget_does_not_admit_run(self):
        c = catalog()
        c["cloud"]["quota"] = 5
        self.assertIsNone(plan(sample(), c, "light", independent=True)["selected"])

    def test_capability_restriction(self):
        c = catalog()
        c["cloud"]["capabilities"] = ["rust"]
        self.assertIsNone(plan(sample(), c, "light", independent=True)["selected"])

class LedgerTests(unittest.TestCase):
    def test_concurrent_reservation_no_overspend(self):
        from concurrent.futures import ThreadPoolExecutor
        with TemporaryDirectory() as t:
            led = Ledger(Path(t)/"ledger.jsonl")
            spec = {"adapter": "metered", "enabled": True, "verified": True,
                    "quota": 50, "reserve_fraction": .1}
            def reserve(ref):
                try:
                    led.reserve(provider="cloud", spec=spec, project="a", run_ref=ref,
                                units=30, now=datetime(2026, 11, 1, tzinfo=timezone.utc))
                    return "ok"
                except BrokerError:
                    return "denied"
            with ThreadPoolExecutor(max_workers=2) as pool:
                self.assertEqual(sorted(pool.map(reserve, ["1", "2"])), ["denied", "ok"])

    def test_cross_month_reservation_does_not_expire(self):
        with TemporaryDirectory() as t:
            led = Ledger(Path(t)/"ledger.jsonl")
            spec = {"adapter": "metered", "enabled": True, "verified": True, "quota": 100}
            led.reserve(provider="cloud", spec=spec, project="a", run_ref="job",
                        units=30, now=datetime(2026, 11, 30, tzinfo=timezone.utc))
            self.assertEqual(len(active(led.events(), "2026-12")), 1)
            led.finish("cloud", "job", units=12, now=datetime(2026, 12, 1, tzinfo=timezone.utc))
            self.assertFalse(active(led.events(), "2026-12"))
            self.assertEqual(usage(led.events(), "2026-11", "cloud"), 12)

    def test_duplicate_run_is_idempotent(self):
        with TemporaryDirectory() as t:
            led = Ledger(Path(t)/"ledger.jsonl")
            self.assertTrue(led.record("cloud", "run-1", 5))
            self.assertFalse(led.record("cloud", "run-1", 5))
            self.assertEqual(len(led.events()), 1)

    def test_unverified_remote_rejected(self):
        with TemporaryDirectory() as t:
            led = Ledger(Path(t)/"ledger.jsonl")
            with self.assertRaisesRegex(BrokerError, "unverified"):
                led.reserve(provider="cloud", spec={"adapter": "metered", "verified": False,
                            "enabled": True, "quota": 100}, project="a", run_ref="run", units=5)

class ConfigTests(unittest.TestCase):
    def test_project_config_does_not_require_extracta(self):
        with TemporaryDirectory() as t:
            root = Path(t)
            (root/".ci-broker").mkdir()
            (root/".ci-broker"/"project.json").write_text(json.dumps(sample()))
            self.assertEqual(project(root)["project_id"], "sample")

    def test_shell_command_string_rejected(self):
        with TemporaryDirectory() as t:
            root = Path(t); (root/".ci-broker").mkdir()
            c = sample(); c["tasks"]["format"]["command"] = "rm -rf /"
            (root/".ci-broker"/"project.json").write_text(json.dumps(c))
            with self.assertRaisesRegex(BrokerError, "argv array"):
                project(root)

    def test_default_accounts_local_only(self):
        with mock.patch.dict("os.environ", {"CI_BROKER_ACCOUNTS": "/this/path/does/not/exist.json"}):
            self.assertEqual(list(accounts()), ["local"])

if __name__ == "__main__":
    unittest.main()
