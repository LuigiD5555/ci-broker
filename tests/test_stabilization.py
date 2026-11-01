"""Regression tests for v0.1.1 safety contracts. Never touch real cloud resources."""
from __future__ import annotations
from datetime import datetime, timezone
from io import StringIO
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, mock
from concurrent.futures import ProcessPoolExecutor
import json
import os
import sys
import time

from ci_broker.cli import main
from ci_broker.config import accounts
from ci_broker.ledger import Ledger, active, usage
from ci_broker.model import BrokerError
from ci_broker.planner import plan

NOW = datetime(2026, 11, 20, tzinfo=timezone.utc)

def sample(capability="python", command=None):
    return {"schema": 1, "project_id": "sample", "tasks": {
        "t": {"command": command or [sys.executable, "-c", "pass"],
              "expected_minutes": 1, "timeout_minutes": 2,
              "capabilities": [capability]}}, "profiles": {"light": ["t"]}}

def remote(verified=True, quota=100):
    return {"adapter": "metered", "enabled": True, "verified": verified,
            "independent": True, "quota": quota, "units_per_minute": 1,
            "reserve_fraction": 0.1, "capabilities": ["python"]}

def _competing_reserve(args):
    path, run_ref = args
    ledger = Ledger(Path(path))
    try:
        ledger.reserve(provider="cloud", spec=remote(quota=50),
                       project="p", run_ref=run_ref, units=30, now=NOW)
        return True
    except BrokerError:
        return False

class AdmissionRegression(TestCase):
    def test_unverified_remote_remains_informative_not_eligible(self):
        result = plan(sample(), {"cloud": remote(verified=False)}, "light", independent=True, now=NOW)
        self.assertIsNone(result["selected"])
        self.assertFalse(result["candidates"][0]["eligible"])
        self.assertFalse(result["candidates"][0]["verified"])

    def test_local_run_cannot_bypass_disabled_provider_or_capability(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".ci-broker").mkdir()
            marker = root / "executed"
            cfg = sample("rust", [sys.executable, "-c", "from pathlib import Path; Path('executed').write_text('yes')"])
            (root / ".ci-broker/project.json").write_text(json.dumps(cfg))
            provider_file = root / "providers.json"
            provider_file.write_text(json.dumps({"local": {
                "adapter": "local", "enabled": False, "verified": True, "preferred": True,
                "independent": False, "quota": None, "capabilities": ["python"]}}))
            with mock.patch.dict(os.environ, {"CI_BROKER_ACCOUNTS": str(provider_file),
                                              "CI_BROKER_LEDGER": str(root / "ledger.jsonl")}), redirect_stderr(StringIO()):
                self.assertEqual(main(["--project-root", str(root), "run", "--profile", "light",
                                       "--local", "--approve"]), 2)
            self.assertFalse(marker.exists())

    def test_local_run_missing_capability_blocked_even_if_enabled(self):
        with TemporaryDirectory() as directory:
            root = Path(directory); (root / ".ci-broker").mkdir()
            cfg = sample("rust")
            (root / ".ci-broker/project.json").write_text(json.dumps(cfg))
            provider_file = root / "providers.json"
            provider_file.write_text(json.dumps({"local": {
                "adapter": "local", "enabled": True, "verified": True, "preferred": True,
                "independent": False, "quota": None, "capabilities": ["python"]}}))
            with mock.patch.dict(os.environ, {"CI_BROKER_ACCOUNTS": str(provider_file),
                                              "CI_BROKER_LEDGER": str(root / "ledger.jsonl")}), redirect_stderr(StringIO()):
                self.assertEqual(main(["--project-root", str(root), "run", "--profile", "light",
                                       "--local", "--approve"]), 2)

class LedgerRegression(TestCase):
    def test_lower_observation_must_not_erase_settlement(self):
        with TemporaryDirectory() as directory:
            led = Ledger(Path(directory) / "ledger.jsonl")
            led.reserve(provider="cloud", spec=remote(), project="p", run_ref="job", units=10, now=NOW)
            led.finish("cloud", "job", units=8, now=NOW)
            led.record("cloud", "job", 3, now=NOW)
            self.assertGreaterEqual(usage(led.events(), "2026-11", "cloud"), 8)

    def test_higher_observation_must_not_be_erased_by_settle(self):
        with TemporaryDirectory() as directory:
            led = Ledger(Path(directory) / "ledger.jsonl")
            led.reserve(provider="cloud", spec=remote(), project="p", run_ref="job", units=10, now=NOW)
            led.record("cloud", "job", 12, now=NOW)
            led.finish("cloud", "job", units=8, now=NOW)
            self.assertGreaterEqual(usage(led.events(), "2026-11", "cloud"), 12)

    def test_conflicting_observation_must_not_be_silently_accepted(self):
        with TemporaryDirectory() as directory:
            led = Ledger(Path(directory) / "ledger.jsonl")
            self.assertTrue(led.record("cloud", "job", 3, now=NOW))
            with self.assertRaises(BrokerError):
                led.record("cloud", "job", 9, now=NOW)
            self.assertEqual(usage(led.events(), "2026-11", "cloud"), 3)

    def test_observation_after_cross_month_settlement_counts_once(self):
        with TemporaryDirectory() as directory:
            led = Ledger(Path(directory) / "ledger.jsonl")
            led.reserve(provider="cloud", spec=remote(), project="p", run_ref="job", units=10,
                        now=datetime(2026, 11, 30, tzinfo=timezone.utc))
            led.finish("cloud", "job", units=8, now=datetime(2026, 12, 1, tzinfo=timezone.utc))
            led.record("cloud", "job", 3, now=datetime(2026, 12, 2, tzinfo=timezone.utc))
            self.assertEqual(usage(led.events(), "2026-11", "cloud"), 8)
            self.assertEqual(usage(led.events(), "2026-12", "cloud"), 0)

    def test_duplicate_settlement_does_not_add_usage(self):
        with TemporaryDirectory() as directory:
            led = Ledger(Path(directory) / "ledger.jsonl")
            led.reserve(provider="cloud", spec=remote(), project="p", run_ref="job", units=20, now=NOW)
            led.finish("cloud", "job", units=5, now=NOW)
            try:
                led.finish("cloud", "job", units=5, now=NOW)
            except BrokerError:
                pass
            self.assertEqual(usage(led.events(), "2026-11", "cloud"), 5)

    def test_real_multiprocess_budget_reservation(self):
        with TemporaryDirectory() as directory:
            path = str(Path(directory) / "ledger.jsonl")
            with ProcessPoolExecutor(max_workers=3) as pool:
                results = list(pool.map(_competing_reserve, [(path, str(i)) for i in range(3)]))
            self.assertEqual(sum(results), 1)

    def test_truncated_ledger_fail_closed(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.jsonl"
            path.write_text('{"kind": "reserve"')
            with self.assertRaises(BrokerError):
                Ledger(path).events()

    def test_nonfinite_values_rejected(self):
        with TemporaryDirectory() as directory:
            led = Ledger(Path(directory) / "ledger.jsonl")
            for units in [float("nan"), float("inf"), -1]:
                with self.assertRaises(BrokerError):
                    led.record("cloud", "job", units)

class ConfigRegression(TestCase):
    def test_explicit_missing_accounts_fails_closed(self):
        with TemporaryDirectory() as directory:
            with mock.patch.dict(os.environ, {"CI_BROKER_ACCOUNTS": str(Path(directory) / "missing.json")}):
                with self.assertRaises(BrokerError):
                    accounts()

    def test_init_generates_portable_interpreter(self):
        with TemporaryDirectory() as directory:
            with redirect_stdout(StringIO()):
                self.assertEqual(main(["--project-root", directory, "init"]), 0)
            cfg = json.loads((Path(directory) / ".ci-broker/project.json").read_text())
            self.assertEqual(cfg["tasks"]["check"]["command"], ["python", "--version"])
