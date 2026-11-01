"""Offline invariants for process groups, graph dedup, config and state security."""
from __future__ import annotations
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, mock
import json
import os
import sys
import time

from ci_broker.cli import local_run
from ci_broker.config import accounts, project
from ci_broker.graph import order
from ci_broker.ledger import Ledger, active
from ci_broker.model import BrokerError


class ExecutionSafety(TestCase):
    def test_failed_dependency_never_runs_successor(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = {"tasks": {
                "bad": {"command": [sys.executable, "-c", "raise SystemExit(9)"],
                        "expected_minutes": 0.01, "timeout_minutes": 1},
                "after": {"needs": ["bad"], "command": [
                    sys.executable, "-c", "from pathlib import Path; Path('executed').write_text('yes')"],
                    "expected_minutes": 1, "timeout_minutes": 2}
            }, "profiles": {"light": ["after"]}}
            self.assertEqual(local_run(cfg, root, "light", True), 9)
            self.assertFalse((root / "executed").exists())

    def test_timeout_terminates_descendant_group(self):
        if os.name != "posix":
            self.skipTest("POSIX-only process group semantics")
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            grandchild = "import time; from pathlib import Path; time.sleep(0.5); Path('orphan').write_text('bad')"
            parent = ("import subprocess,sys,time; subprocess.Popen([sys.executable, '-c', " +
                      repr(grandchild) + "]); time.sleep(30)")
            cfg = {"tasks": {"spawn": {"command": [sys.executable, "-c", parent],
                                     "expected_minutes": 0.001, "timeout_minutes": 0.003}},
                   "profiles": {"light": ["spawn"]}}
            self.assertEqual(local_run(cfg, root, "light", True), 124)
            time.sleep(0.65)
            self.assertFalse((root / "orphan").exists(), "Timed out child survived")

    def test_diamond_dependency_runs_once(self):
        tasks = {"root": {}, "left": {"needs": ["root"]}, "right": {"needs": ["root"]},
                 "final": {"needs": ["left", "right"]}}
        self.assertEqual(order(tasks, ["final"]), ["root", "left", "right", "final"])


class StateSafety(TestCase):
    def test_symlink_ledger_is_not_followed(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            real = root / "real.jsonl"
            real.write_text("")
            link = root / "linked.jsonl"
            link.symlink_to(real)
            with self.assertRaises(BrokerError):
                Ledger(link).events()

    def test_valid_json_without_terminal_newline_is_rejected(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = root / "ledger.jsonl"
            ledger.write_text(json.dumps({
                "kind": "observed", "provider": "remote", "run_ref": "123",
                "units": 3, "at": "2026-11-01T00:00:00+00:00"}))
            with self.assertRaises(BrokerError):
                Ledger(ledger).events()

    def test_jsonl_world_readable_file_is_rejected(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.jsonl"
            path.write_text("")
            path.chmod(0o644)
            with self.assertRaises(BrokerError):
                Ledger(path).events()

    def test_no_implicit_reservation_expiry(self):
        from datetime import datetime, timezone
        with TemporaryDirectory() as tmp:
            led = Ledger(Path(tmp) / "ledger.jsonl")
            led.reserve(provider="local", spec={"adapter": "local", "enabled": True,
                        "verified": True, "quota": 100}, project="a",
                        run_ref="long-run", units=8,
                        now=datetime(2026, 10, 1, tzinfo=timezone.utc))
            self.assertEqual(len(active(led.events(), "2027-02")), 1)

    def test_account_catalog_explicit_symlink_fails(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            actual = root / "actual.json"
            actual.write_text(json.dumps({"local": {
                "adapter": "local", "enabled": True, "verified": True,
                "quota": None, "capabilities": ["python"]}}))
            symlink = root / "providers.json"
            symlink.symlink_to(actual)
            with mock.patch.dict(os.environ, {"CI_BROKER_ACCOUNTS": str(symlink)}):
                with self.assertRaises(BrokerError):
                    accounts()


class ProjectCompatibility(TestCase):
    def test_two_projects_no_consumer_specific_imports(self):
        from ci_broker import cli, config, graph, planner, providers
        for consumer in ("alpha", "beta"):
            with TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / ".ci-broker").mkdir()
                (root / ".ci-broker/project.json").write_text(json.dumps({
                    "schema": 1, "project_id": consumer,
                    "tasks": {"task": {"command": ["python", "--version"], "expected_minutes": 1}},
                    "profiles": {"light": ["task"]}}))
                self.assertEqual(project(root)["project_id"], consumer)
