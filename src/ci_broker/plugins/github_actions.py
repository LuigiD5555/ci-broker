"""Trusted GitHub Actions reader: read-only run accounting through `gh`, never dispatches.

Loaded only with an explicit `--plugin github-actions`. Requires `gh` authenticated
with permission to read Actions runs.
"""
from __future__ import annotations

import json
import math
import subprocess
from datetime import datetime, timezone
from typing import Any, Mapping

from ..model import BrokerError
from ..providers import MeteredProvider

RUN_FIELDS = "databaseId,headBranch,headSha,createdAt,conclusion,name,jobs"


def github_units(run: Mapping[str, Any]) -> float:
    """Billable job minutes: each job rounded up to whole minutes, unfinished jobs ignored."""
    total = 0
    for job in run.get("jobs", []):
        started, completed = job.get("startedAt"), job.get("completedAt")
        if not started or not completed:
            continue
        start = datetime.fromisoformat(started.replace("Z", "+00:00"))
        end = datetime.fromisoformat(completed.replace("Z", "+00:00"))
        total += math.ceil((end - start).total_seconds() / 60)
    return total


class GitHubActionsReader(MeteredProvider):
    """Quotes inherited from MeteredProvider; adds `read_run` for `ci-broker import-run`."""

    def fetch_run(self, run_id: str) -> dict[str, Any]:
        proc = subprocess.run(["gh", "run", "view", run_id, "--json", RUN_FIELDS],
                              text=True, capture_output=True, check=False)
        if proc.returncode:
            raise BrokerError("GitHub Actions run lookup failed: " + proc.stderr.strip()[:350])
        return json.loads(proc.stdout)

    def read_run(self, run_id: str) -> dict[str, Any]:
        if not run_id.isdigit():
            raise BrokerError("GitHub Actions run id must be numeric")
        run = self.fetch_run(run_id)
        return {"run_ref": str(run.get("databaseId", run_id)), "units": github_units(run),
                "status": run.get("conclusion") or "unknown"}
