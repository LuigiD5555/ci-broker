"""Optional local MCP facade for CI Broker. Requires pip install '.[mcp]'.

Expose only the project root explicitly configured on the operator's machine.
The server uses stdio by default. Do NOT expose this process over public HTTP
without authentication, authorization, request limits and HTTPS termination.
"""
from __future__ import annotations

import os
from pathlib import Path

from .config import accounts, project
from .ledger import Ledger
from .model import BrokerError
from .remote import _choose, _settings, dispatch, status, cancel
from .gateway import Gateway

try:
    # MCP Python SDK >= 2 renamed FastMCP to MCPServer.
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:
    try:
        # MCP Python SDK 1.x, retained for existing local clients.
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError("Install optional dependency: pip install '.[mcp]'") from exc

mcp = FastMCP("ci-broker")


def _root() -> Path:
    configured = os.environ.get("CI_BROKER_PROJECT_ROOT")
    if not configured:
        raise BrokerError("Set CI_BROKER_PROJECT_ROOT to an approved project checkout")
    root = Path(configured).expanduser().resolve(strict=True)
    if not (root / ".ci-broker" / "project.json").is_file():
        raise BrokerError("Approved project has no CI Broker configuration")
    return root


@mcp.tool()
def ci_plan(profile: str, provider: str = "auto") -> dict:
    """Read-only provider/cost plan; never sends jobs or spends credits."""
    cfg = project(_root())
    _settings(cfg, profile)
    _, _, decision = _choose(cfg, accounts(), profile, provider, Ledger())
    return {"state": "ADVISORY_ONLY", **decision}


@mcp.tool()
def ci_dispatch(profile: str, sha: str, branch: str,
                provider: str = "auto", approve: bool = False) -> dict:
    """Dispatch one CircleCI pipeline with a reserved budget.

    approve=True must be authorized for this exact commit/profile. A retry of
    the same request is refused, including after ambiguous network failures.
    """
    return dispatch(_root(), profile, provider, sha, branch, approve)


@mcp.tool()
def ci_status(run_ref: str) -> dict:
    """One-shot CircleCI status. CI success is not formal gate acceptance."""
    _root()
    return status(run_ref)


@mcp.tool()
def ci_cancel(run_ref: str, approve: bool = False) -> dict:
    """Request cancel; actual provider usage must be reconciled separately."""
    _root()
    return cancel(run_ref, approve)

@mcp.tool()
def ci_jobs(run_ref: str) -> dict:
    """List jobs belonging to an observed SHA-bound pipeline."""
    _root()
    return Gateway().jobs(run_ref)


@mcp.tool()
def ci_artifacts(run_ref: str, job_number: int) -> dict:
    """List artifact metadata; never return presigned private URLs."""
    _root()
    return Gateway().artifacts(run_ref, job_number)


@mcp.tool()
def ci_logs(run_ref: str, job_number: int) -> dict:
    """Read step/error metadata, not raw console output."""
    _root()
    return Gateway().logs(run_ref, job_number)



def main() -> None:
    # Stdio only. Remote ChatGPT MCP requires separately secured hosting.
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
