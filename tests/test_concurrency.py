from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from ci_broker.ledger import Ledger
from ci_broker.models import ConfigError


class ConcurrentBudgetTests(unittest.TestCase):
    def test_two_parallel_jobs_cannot_overreserve_same_account(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(Path(tmp) / "events.jsonl")
            def apply(i):
                try:
                    ledger.reserve("github", f"project-{i}", "light", str(i), 30, 50, .10, True, False)
                    return "accepted"
                except ConfigError:
                    return "denied"
            with ThreadPoolExecutor(max_workers=2) as pool:
                self.assertEqual(sorted(pool.map(apply, (1, 2))), ["accepted", "denied"])

    def test_earlier_month_hold_still_blocks_after_rollover(self):
        before = "2026-10"
        after = "2026-11"
        events = [{"kind": "reserve", "month": before, "provider": "ci", "run_ref": "old", "units": 20}]
        self.assertEqual(Ledger.active(events, after)[("ci", "old")]["units"], 20)
        events.append({"kind": "settle", "month": before, "provider": "ci", "run_ref": "old", "units": 5})
        self.assertFalse(Ledger.active(events, after))


if __name__ == "__main__":
    unittest.main()
