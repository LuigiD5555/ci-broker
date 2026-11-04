"""Project-independent CI Gateway API; no network on import."""
from __future__ import annotations

from importlib.metadata import entry_points
import os
from pathlib import Path
from typing import Callable, Protocol
from urllib.parse import quote

from .model import BrokerError
from .remote import CircleCIClient, _receipt, dispatch as circle_dispatch
from .remote import status as circle_status, cancel as circle_cancel, SLUG


class RemoteProvider(Protocol):
    kind: str
    def dispatch(self, root: Path, profile: str, provider: str,
                 sha: str, branch: str, approve: bool) -> dict: ...
    def status(self, run_ref: str) -> dict: ...
    def jobs(self, run_ref: str) -> dict: ...
    def artifacts(self, run_ref: str, job_number: int) -> dict: ...
    def logs(self, run_ref: str, job_number: int) -> dict: ...
    def cancel(self, run_ref: str, approve: bool) -> dict: ...


class CircleCIProvider:
    kind = "circleci"

    def _client(self) -> CircleCIClient:
        return CircleCIClient(os.environ.get("CIRCLECI_TOKEN", ""))

    def _read_client(self) -> CircleCIClient:
        return CircleCIClient(os.environ.get("CIRCLECI_TOKEN", ""), read_only=True)

    def observe(self, project_slug: str, branch: str, sha: str,
                pipeline_id: str | None = None) -> dict:
        from .observation import observe
        return observe(self._read_client(), project_slug, branch, sha, pipeline_id)

    @staticmethod
    def _context(ref: str) -> tuple[dict, dict]:
        from .config import accounts
        start, receipt = _receipt(ref)
        spec = accounts().get(start["provider"], {})
        if spec.get("remote_kind") != "circleci":
            raise BrokerError("Reservation is not for CircleCI")
        return start, receipt

    @staticmethod
    def _slug() -> str:
        slug = os.environ.get("CI_BROKER_CIRCLECI_SLUG", "")
        if not SLUG.fullmatch(slug):
            raise BrokerError("CI_BROKER_CIRCLECI_SLUG required for job evidence")
        return slug

    def dispatch(self, root: Path, profile: str, provider: str,
                 sha: str, branch: str, approve: bool) -> dict:
        return circle_dispatch(root, profile, provider, sha, branch, approve)

    def status(self, run_ref: str) -> dict:
        return circle_status(run_ref, self._client())

    def jobs(self, run_ref: str) -> dict:
        _, receipt = self._context(run_ref)
        c = self._client()
        workflows = c.workflows(receipt["pipeline_id"])
        jobs = []
        for w in workflows:
            wid = w.get("id")
            if not isinstance(wid, str):
                raise BrokerError("CircleCI workflow missing ID")
            payload = c.call("GET", f"/workflow/{wid}/job")
            if payload.get("next_page_token"):
                raise BrokerError("CircleCI jobs paginated; incomplete evidence")
            items = payload.get("items")
            if not isinstance(items, list):
                raise BrokerError("CircleCI jobs missing")
            for j in items:
                if not isinstance(j, dict) or (j.get("job_number") is not None and type(j["job_number"]) is not int):
                    raise BrokerError("Invalid CircleCI job record")
                jobs.append({"number": j["job_number"], "name": j.get("name"),
                             "status": j.get("status"), "workflow": w.get("name"),
                             "workflow_id": wid})
        return {"provider_kind": self.kind, "run_ref": run_ref,
                "expected_sha": receipt["sha"], "jobs": jobs,
                "acceptance": "NOT_ATTESTED"}

    def _check_job(self, ref: str, number: int) -> None:
        if type(number) is not int or number <= 0:
            raise BrokerError("Positive job number required")
        if number not in {j["number"] for j in self.jobs(ref)["jobs"]}:
            raise BrokerError("Job does not belong to reserved pipeline")

    def artifacts(self, run_ref: str, job_number: int) -> dict:
        self._check_job(run_ref, job_number)
        slug = quote(self._slug(), safe="/")
        data = self._client().call("GET", f"/project/{slug}/job/{job_number}/artifacts")
        if data.get("next_page_token"):
            raise BrokerError("CircleCI artifacts paginated; incomplete evidence")
        items = data.get("items")
        if not isinstance(items, list):
            raise BrokerError("CircleCI artifacts missing")
        return {"provider_kind": self.kind, "run_ref": run_ref,
                "job_number": job_number,
                "artifacts": [{"path": x.get("path"), "node_index": x.get("node_index")}
                              for x in items if isinstance(x, dict)],
                "content": "NOT_DOWNLOADED", "acceptance": "NOT_ATTESTED"}

    def logs(self, run_ref: str, job_number: int) -> dict:
        self._check_job(run_ref, job_number)
        slug = self._slug().split("/")
        vcs = {"gh": "github", "github": "github", "bb": "bitbucket"}.get(slug[0])
        if vcs is None:
            return {"provider_kind": self.kind, "run_ref": run_ref,
                    "job_number": job_number, "steps": [],
                    "content": "UNAVAILABLE_FOR_PROJECT_TYPE",
                    "acceptance": "NOT_ATTESTED"}
        c = self._client()
        c.BASE = "https://circleci.com/api/v1.1"
        data = c.call("GET", "/project/" + quote("/".join([vcs, *slug[1:]]), safe="/") +
                      f"/{job_number}")
        steps = []
        for step in data.get("steps", []):
            if not isinstance(step, dict):
                continue
            for action in step.get("actions", []):
                if isinstance(action, dict):
                    steps.append({"name": action.get("name") or step.get("name"),
                                  "status": action.get("status"),
                                  "exit_code": action.get("exit_code"),
                                  "has_output": bool(action.get("output_url"))})
        # No presigned URLs or raw stdout in the public GitHub control channel.
        return {"provider_kind": self.kind, "run_ref": run_ref,
                "job_number": job_number, "steps": steps[:100],
                "content": "STEP_METADATA_ONLY_NO_RAW_LOGS",
                "acceptance": "NOT_ATTESTED"}

    def cancel(self, run_ref: str, approve: bool) -> dict:
        return circle_cancel(run_ref, approve, self._client())


_FACTORIES: dict[str, Callable[[], RemoteProvider]] = {"circleci": CircleCIProvider}


def register_provider(kind: str, factory: Callable[[], RemoteProvider]) -> None:
    if not isinstance(kind, str) or not kind or kind in _FACTORIES or not callable(factory):
        raise BrokerError("Invalid or duplicate provider factory")
    _FACTORIES[kind] = factory


def load_trusted_providers(names: list[str]) -> None:
    available = {e.name: e for e in entry_points(group="ci_broker.remote_providers")}
    for name in names:
        if name not in available:
            raise BrokerError("Unknown trusted remote provider: " + name)
        register_provider(name, available[name].load())


def _provider(kind: str) -> RemoteProvider:
    if kind not in _FACTORIES:
        raise BrokerError(f"No trusted remote adapter for {kind!r}")
    obj = _FACTORIES[kind]()
    if obj.kind != kind:
        raise BrokerError("Remote provider kind mismatch")
    return obj


def _from_ref(ref: str) -> RemoteProvider:
    from .config import accounts
    start, _ = _receipt(ref)
    return _provider(accounts()[start["provider"]].get("remote_kind"))


class Gateway:
    """Stable application service shared by CLI, agent MCP and hooks."""

    def capabilities(self) -> dict:
        return {"gateway_schema": 1, "providers": sorted(_FACTORIES),
                "operations": ["dispatch", "status", "jobs", "artifacts", "logs", "cancel", "observe"],
                "raw_logs": "PRIVATE_CHANNEL_REQUIRED",
                "automatic_billing": False, "acceptance": "NOT_ATTESTED"}

    def observe(self, kind: str, project_slug: str, branch: str, sha: str,
                pipeline_id: str | None = None) -> dict:
        provider = _provider(kind)
        observe = getattr(provider, "observe", None)
        if observe is None:
            raise BrokerError("Provider does not support read-only observation")
        return {"gateway_schema": 1, **observe(project_slug, branch, sha, pipeline_id)}

    def dispatch(self, kind: str, root: Path, profile: str, provider: str,
                 sha: str, branch: str, approve: bool) -> dict:
        if not approve:
            raise BrokerError("Explicit remote dispatch approval required")
        return _provider(kind).dispatch(root, profile, provider, sha, branch, approve)

    def status(self, ref: str) -> dict:
        return {"gateway_schema": 1, **_from_ref(ref).status(ref)}

    def jobs(self, ref: str) -> dict:
        return {"gateway_schema": 1, **_from_ref(ref).jobs(ref)}

    def artifacts(self, ref: str, number: int) -> dict:
        return {"gateway_schema": 1, **_from_ref(ref).artifacts(ref, number)}

    def logs(self, ref: str, number: int) -> dict:
        return {"gateway_schema": 1, **_from_ref(ref).logs(ref, number)}

    def cancel(self, ref: str, approve: bool) -> dict:
        if not approve:
            raise BrokerError("Explicit cancel approval required")
        return {"gateway_schema": 1, **_from_ref(ref).cancel(ref, approve)}
