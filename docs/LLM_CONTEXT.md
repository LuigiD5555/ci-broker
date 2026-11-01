# CI Broker — handoff context for AI coding assistants

**Repository:** https://github.com/LuigiD5555/ci-broker  
**Version:** 0.1.1 stabilization candidate (P5 runtime acceptance pending)  
**Purpose:** a reusable, project-agnostic, local-first CI planner and budget ledger. Extracta is a consumer, not a dependency.

## Before modifying anything

1. Read `README.md` and `docs/ARCHITECTURE.md`.
2. Inspect `src/ci_broker/model.py` and the public CLI surface in `src/ci_broker/cli.py`.
3. Run `python3 -m unittest discover -s tests -v`.
4. Preserve the project-agnostic boundary: **do not add imports of Extracta, BPFW or Guenda into the core**.
5. Maintain existing CLI commands and schema version unless a migration is explicitly approved.
6. Never add automatic CI dispatch, paid usage, account tokens, or merges without authorization.

## Public interface

```text
ci-broker [--project-root PATH] [--plugin NAME] init [--project-id ID]
ci-broker [--project-root PATH] [--plugin NAME] plan --profile NAME [--independent]
ci-broker [--project-root PATH] run --profile NAME --local --approve
ci-broker providers
ci-broker reserve --profile NAME --provider PROVIDER --run-ref REF
ci-broker finish --provider PROVIDER --run-ref REF [--units REAL]
ci-broker record --provider PROVIDER --run-ref REF --units REAL
ci-broker status
```

## Inputs and output

`project.json`: `schema=1`, `project_id`, `tasks[id]={command: argv[], needs?:id[], expected_minutes:number, timeout_minutes?:number, capabilities?:string[]}`, `profiles[profile]=[task-id]`.

`providers.json` in operator config: `{provider_id: {adapter, enabled, verified, independent, preferred?, quota?, unit?, units_per_minute?, billing_increment_seconds?, reserve_fraction?, capabilities?:[], max_job_minutes?}}`.

`plan` prints JSON with `schema`, `project`, `profile`, `selected`, `tasks`, `estimate`, `advisory_only`, `candidates`. The ledger is JSONL, with `reserve/settle/cancel/observed` events and provider-scoped run IDs.

## Readable examples

- `examples/python/.ci-broker/project.json` — Python syntax then unittest.
- `examples/rust/.ci-broker/project.json` — Cargo fmt/test/clippy.
- `examples/extracta/.ci-broker/project.json` — real consumer mapping.
- `examples/providers.json` — local account + inactive external account.

## Capability status

**Implemented (stabilization branch, acceptance pending):** project root resolution, explicit task profiles, transitive DAG ordering, cycle detection, estimate, local approval-gated execution, per-provider metered quotes, protected budget, account-shared atomic reservations, idempotent observations, explicit trusted plugin registration, JSON CLI.

**Not implemented:** automatic language discovery, pipeline generation, remote job dispatch, quota synchronization, actual cost predictions based on history, parallel execution, remote ledger backend. Never claim any of these works now.

## Common usage

```bash
python3 -m pip install -e .
ci-broker --project-root /path/to/project plan --profile light
ci-broker --project-root /path/to/project run --profile light --local --approve
CI_BROKER_ACCOUNTS=/path/to/providers.json ci-broker --project-root /path/to/project plan --profile light --independent
```

## Acceptance criteria for changes

A code change passes only if unit tests and CLI checks pass for a Python >=3.11 environment; the project-agnostic import boundary remains intact; no cloud consumption occurs during unit tests; and docs/examples match behavior. If remote CI has not run, state `NOT_EXECUTED` instead of implying provider acceptance.

## Current priority: stabilization, not new CI providers

Read [PLAN_ESTABILIZACION_0_1_1.md](PLAN_ESTABILIZACION_0_1_1.md) **before changing code**. It enumerates invariants INV-01..INV-16, work packages P0..P5, regression evidence and fail-closed acceptance. The audit baseline is `43f57333ebcceab081f566ee5d86d445410e4eaf`, with 20 passing tests under Python 3.15.2 but known safety/accounting defects. P0-P4 regression code is in `fix/stabilize-0.1.1`; P5 requires executed tests and a recorded Python 3.15.2 result. No GitHub Actions or other external CI credit expenditure, no changes to Extracta or `main`.

## v0.1.1 protected behavior

- `plan`, `run` and `reserve` use consistent eligibility criteria. A rejected local plan cannot become an approved run.
- Explicit missing `CI_BROKER_ACCOUNTS` paths fail. With no override, a local default is permissible.
- Usage for one execution is the maximum of settlement and observations. Conflicting repeated observations are rejected pending an explicit audited reconciliation policy.
- Cross-month reservations remain pending until their terminal event; no TTL automatically releases quota.
- `init` writes portable `["python", "--version"]`. Local timeout terminates a process group.
- Local acceptance command: `bash scripts/validate_local.sh` with Python 3.15.2. GitHub Actions is not required or authorized for this validation.
