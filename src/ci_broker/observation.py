"""Bounded GET-only observation of existing runs; never creates ledger receipts."""
from __future__ import annotations

import os
import re
from urllib.parse import quote

from .model import BrokerError
from .remote import SLUG, _uuid, _validate_identity


class ObservationError(BrokerError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def clean(value, limit=800):
    """Only selected display fields, no signed URLs or known worker credentials."""
    text = str(value or "")[:4000]
    for key in ("CIRCLECI_TOKEN", "GITHUB_TOKEN"):
        secret = os.environ.get(key)
        if secret:
            text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"https?://\S+", "[URL_REDACTED]", text)
    text = re.sub(r"(?i)(authorization|token|password|secret|api[_-]?key)\s*[:=]\s*\S+",
                  r"\1=[REDACTED]", text)
    return "".join(c for c in text if c >= " " or c == "\n")[:limit]


def _items(payload, label, maximum=30):
    if payload.get("next_page_token"):
        raise ObservationError("INCOMPLETE_" + label)
    items = payload.get("items")
    if not isinstance(items, list) or len(items) > maximum or any(not isinstance(x, dict) for x in items):
        raise ObservationError("INVALID_" + label)
    return items


def observe(client, project_slug: str, branch: str, sha: str,
            pipeline_id: str | None = None) -> dict:
    if not isinstance(project_slug, str) or not SLUG.fullmatch(project_slug):
        raise ObservationError("INVALID_PROJECT")
    _validate_identity(branch, sha)
    truncated = False
    if pipeline_id is None:
        page = client.call("GET", f"/project/{quote(project_slug, safe='/')}/pipeline")
        rows = page.get("items")
        if not isinstance(rows, list) or len(rows) > 100 or any(not isinstance(x, dict) for x in rows):
            raise ObservationError("INVALID_PIPELINES")
        truncated = bool(page.get("next_page_token"))
        matches = [p for p in rows if isinstance(p.get("vcs"), dict)
                   and p["vcs"].get("revision") == sha and p["vcs"].get("branch") == branch]
        if not matches:
            raise ObservationError("PIPELINE_NOT_FOUND_IN_BOUNDED_PAGE")
        pipeline_id = matches[0].get("id")
    pipeline_id = _uuid(pipeline_id, "pipeline_id")
    p = client.call("GET", f"/pipeline/{pipeline_id}")
    vcs = p.get("vcs") or {}
    if (p.get("id") != pipeline_id or p.get("project_slug") != project_slug
            or not isinstance(vcs, dict) or vcs.get("revision") != sha or vcs.get("branch") != branch):
        raise ObservationError("PIPELINE_IDENTITY_MISMATCH")
    workflows = _items(client.call("GET", f"/pipeline/{pipeline_id}/workflow"), "WORKFLOWS", 10)
    output = []
    total_jobs = 0
    for w in workflows:
        wid = _uuid(w.get("id"), "workflow_id")
        jobs = _items(client.call("GET", f"/workflow/{wid}/job"), "JOBS", 20)
        total_jobs += len(jobs)
        if total_jobs > 20:
            raise ObservationError("JOB_LIMIT_EXCEEDED")
        normalized = []
        for j in jobs:
            number = j.get("job_number")
            item = {"name": clean(j.get("name")), "status": clean(j.get("status")), "number": number}
            if number is None:  # approval nodes have no test or artifact endpoint
                item["evidence"] = "NO_BUILD_NUMBER"
            elif type(number) is not int or number <= 0:
                raise ObservationError("INVALID_JOB_NUMBER")
            else:
                base = f"/project/{quote(project_slug, safe='/')}"
                detail = client.call("GET", f"{base}/job/{number}")
                item["status"] = clean(detail.get("status") or j.get("status"))
                item["messages"] = [clean(x.get("message")) for x in detail.get("messages", [])[:10]
                                    if isinstance(x, dict)]
                for label, endpoint, fields in [
                    ("tests", f"{base}/{number}/tests", ("name", "classname", "result", "message")),
                    ("artifacts", f"{base}/job/{number}/artifacts", ("path",))]:
                    try:
                        data = client.call("GET", endpoint)
                        rows = data.get("items")
                        if not isinstance(rows, list) or any(not isinstance(x, dict) for x in rows):
                            raise ObservationError("INVALID_EVIDENCE")
                        item[label] = {"items": [{k: clean(x.get(k)) for k in fields} for x in rows[:50]],
                                       "truncated": bool(data.get("next_page_token")) or len(rows) > 50}
                    except BrokerError as exc:
                        # 404 means not exposed, NOT zero failing tests or no artifacts.
                        if "HTTP 404" not in str(exc):
                            raise
                        item[label] = {"state": "UNAVAILABLE", "items": [], "complete": False}
            normalized.append(item)
        output.append({"id": wid, "name": clean(w.get("name")), "status": clean(w.get("status")),
                       "jobs": normalized})
    return {"state": "OBSERVED", "provider_kind": "circleci", "read_only": True,
            "pipeline": {"id": pipeline_id, "number": p.get("number"), "project_slug": project_slug,
                         "sha": sha, "branch": branch,
                         "errors": [{"type": clean(x.get("type")), "message": clean(x.get("message"))}
                                    for x in p.get("errors", [])[:10] if isinstance(x, dict)]},
            "pipeline_search_truncated": truncated, "workflows": output,
            "raw_logs": "UNAVAILABLE_API_V2", "artifact_bytes": "NOT_DOWNLOADED",
            "acceptance": "NOT_ATTESTED"}
