"""Isolated CLI tests; local commands are harmless Python version checks."""
from __future__ import annotations
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock
from ci_broker.cli import main

class CLITests(unittest.TestCase):
    def test_init_plan_requires_approval_to_run(self):
        with TemporaryDirectory() as t:
            providers = Path(t)/"providers.json"
            providers.write_text(json.dumps({"local": {"adapter": "local", "enabled": True,
                "verified": True, "preferred": True, "quota": None,
                "capabilities": ["python"]}}))
            env = {"CI_BROKER_LEDGER": str(Path(t)/"ledger.jsonl"),
                   "CI_BROKER_ACCOUNTS": str(providers)}
            with mock.patch.dict(os.environ, env), redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                self.assertEqual(main(["--project-root", t, "init"]), 0)
                self.assertEqual(main(["--project-root", t, "plan", "--profile", "light"]), 0)
                self.assertEqual(main(["--project-root", t, "run", "--profile", "light", "--local"]), 2)
                self.assertEqual(main(["--project-root", t, "run", "--profile", "light",
                                       "--local", "--approve"]), 0)

    def test_invalid_profile_fails_without_running(self):
        with TemporaryDirectory() as t:
            providers = Path(t)/"providers.json"
            providers.write_text(json.dumps({"local": {"adapter": "local", "enabled": True,
                "verified": True, "preferred": True, "quota": None,
                "capabilities": ["python"]}}))
            with mock.patch.dict(os.environ, {"CI_BROKER_ACCOUNTS": str(providers)}), redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                main(["--project-root", t, "init"])
                self.assertEqual(main(["--project-root", t, "plan", "--profile", "nonexistent"]), 2)

if __name__ == "__main__":
    unittest.main()
