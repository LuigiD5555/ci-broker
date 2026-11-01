# CI Broker — portable LLM handoff

Read this file first when continuing implementation from another chat, agent or LLM.

## Identity

Repository: `LuigiD5555/ci-broker`. Package distribution name: `project-ci-broker`. Import package: `ci_broker`. CLI: `ci-broker`. This repository is **separate from** `LuigiD5555/extracta` and is intended for any programming language or project.

## Goal

Project-agnostic, local-first CI execution and quota planning. Make provider plugins and project descriptors independent of the core. Shared budgets must remain coherent when multiple projects share a provider account. Extracta is a consumer, not a compile-time dependency.

## Actual implemented scope (v0.1.0)

- Python 3.11+ package using standard library runtime and a `pyproject.toml` CLI entry point.
- Project TOML: `.ci-broker/project.toml` in the target project, or `--project-root /path`.
- `init`, `validate`, `providers`, `plan`, `run --yes`, `render`, `usage`, `reserve`, `settle`, `cancel`.
- Metered/unmetered adapter contract, opt-in trusted Python entry points, local account-specific JSON overrides.
- POSIX file-lock append-only reservation/settlement ledger; protected quotas and conservative cross-month holds.
- GitHub/GitLab illustrative **manual** YAML renderers.
- Offline unit tests and examples under `examples/`.

## NOT implemented (do not claim otherwise)

No remote submission, cloud API synchronization, confirmed free cloud minutes, remote run cancellation, actual Q12 execution, graph-based test deduplication, free-tier quota fetching, dashboard, always-running service or PyPI publication.

## Commands to verify

```bash
python3 -m pip install -e .
python3 -m unittest discover -s tests -v
python3 -m compileall -q src tests
ci-broker --project-root examples/python-project validate
ci-broker --project-root examples/python-project plan --profile light
```

## Architecture map

- `src/ci_broker/models.py`: contracts, registry, quote calculation.
- `src/ci_broker/config.py`: strict project TOML and operator provider JSON.
- `src/ci_broker/scheduler.py`: planning and budget constraints.
- `src/ci_broker/ledger.py`: append-only local state, concurrency.
- `src/ci_broker/cli.py`: commands, local runtime.
- `src/ci_broker/render.py`: illustrative CI templates.
- `tests/`: offline verification.
- `examples/`: consumer project profiles including Extracta.

## Non-negotiable development rules

1. Do NOT add Extracta-specific imports or command names inside `src/ci_broker`.
2. Do NOT automatically trigger cloud services, pay for compute or enable cloud providers based solely on published free-tier figures.
3. Do NOT merge or mutate Extracta `main` during work in this repository.
4. Preserve `--project-root` support and offline standard-library functionality.
5. Add tests for each new adapter and any ledger/concurrency policy change.
6. Keep README and this handoff accurate; never claim unexecuted tests passed.

## Recommended next steps

1. Add graph-based test node abstraction and deterministic dependency scheduler with caching by content hash.
2. Add a project plugin interface for detecting runtime/tests, only when user opts in.
3. Implement real read-only quota and run-status adapters per provider.
4. Add explicit, separately permissioned remote dispatcher interfaces with approval and idempotency.
5. Migrate Extracta's `tools/ci_broker` into a compatibility shim after parity tests, retaining its Q12 gate and historical ledger.
