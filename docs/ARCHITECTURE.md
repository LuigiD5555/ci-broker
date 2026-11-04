# Architecture and behavioral contracts

## Design rule

**The engine must not import, identify or assume the structure of Extracta or any other consuming project.** All project-specific names and paths belong to the project's `.ci-broker/project.json`. BPFW-inspired design: install a reusable package, resolve an explicit project root and validate declared authority, rather than copying vendor logic into each codebase.

## Modules

| Module | Responsibility | Allowed dependencies |
|---|---|---|
| `model` | `BrokerError`, `Quote`, `ProviderAdapter` protocol | Python stdlib |
| `config` | Project profiles and provider account JSON loading | `model`, `providers` |
| `graph` | Deterministic task DAG resolution and critical path estimate | `model` |
| `providers` | Billing units, limits, optional explicitly trusted entry points | `model` |
| `ledger` | Append-only global cost records, locking, reservations | `model` |
| `planner` | Project tasks × provider constraints × budget | previous pure modules |
| `cli` | Explicit user commands, approved LOCAL execution | all public interfaces |
| `remote` | Optional CircleCI-only dispatch/observe/cancel, expected-SHA receipts | project config, planner, ledger, Python stdlib HTTPS |
| `mcp_server` | Optional local stdio agent facade (never an automatic ChatGPT connection) | `remote`, optional `mcp` package |

Pure planning and local execution never call GitHub/CircleCI/Buildkite or access tokens. Only the explicitly invoked `remote` module contacts CircleCI, and only with `--approve` for writes.

## Data boundaries

- `.ci-broker/project.json`: source-controlled **per project**; schema version 1, `project_id`, `tasks`, `profiles`.
- Provider account file: **per operator/account**, not shipped with a private token. Quota, units, capabilities, enabled/verified flags, vendor adapter type.
- Ledger: **per operator/account**, shared across local projects, not checked into Git.
- `--project-root`: explicit root, equivalent to a reusable installed tooling interface. No hard-coded cwd assumptions beyond default `.`.

## Admission policy

1. Resolve profile's transitive prerequisites in stable DFS order, fail on missing nodes/cycles.
2. Quote expected and timeout-bounded worst-case units in **each provider's own currency**.
3. Check declared capabilities, maximum job duration, enabled status and independence requirements.
4. Read provider's billing-period usage and unfinished reservations.
5. Keep a 10% protected quota margin by default.
6. Prefer configured LOCAL provider if eligible and no independent verification requested; otherwise select the largest normalized protected headroom.
7. Plan is advisory; local `run` first rechecks admission for a local provider; `reserve` rechecks quota **inside a file lock**.
8. Experimental CircleCI dispatch is opt-in, requires `--approve`, verifies the remote account as budget-eligible and atomically reserves worst-case units before the API call. A reservation alone is NOT execution; ambiguous network outcomes remain held.

Unit normalization: compare `remaining/quota` fractions, **never** compare raw CircleCI credits to vCPU minutes or monetary credits. Provider selection is policy guidance, not proof that pipelines exist.

## Test and execution safety

- Project commands must be arrays of explicit argv tokens. The CLI uses `subprocess.Popen(..., shell=False, start_new_session=True)`, with cwd set to project root; timeout terminates the isolated process group.
- Execution requires `--local --approve`; commands are **trusted code owned by the project**. Approval does not make arbitrary scripts safe.
- No implicit external cloud API calls or automatic paid overage.
- Unknown external adapters are rejected until the operator explicitly authorizes an installed entry point.
- The ledger is protected by POSIX `fcntl.flock` across processes, but is not a distributed coordinator across multiple hosts.
- Unfinished reservations remain held after an expired timeout and across month boundaries until explicitly closed.
- Protected usage is monotonic per run: the highest settled/observed value counts, without double counting. Reductions require future explicit audit support, not silent overwrite.
- Event timestamps and billing months are separate, allowing cross-month completion without misattribution.
- Explicitly missing account catalogs are rejected; a default local provider is used only without an override.
- A failed task stops downstream execution. The plan's critical path number is illustrative: concurrency is not implemented.

## Future evolution (not yet implemented)

1. Stable plugin APIs for `ProjectDetector`, `TaskRunner`, `PipelineRenderer`, `ProviderClient` (quote, fetch quotas, trigger, poll, cancel, settle).
2. Vendor templates generated from declared tasks, no manually copied per-provider YAML.
3. Historical durations and costs by test signature; changed-file impact graph and incremental test selection.
4. Parallel DAG scheduler with resource-constraint packing, critical-path and job-sharing optimization.
5. More remote provider adapters, provider-billing reconciliation, authenticated GitHub webhook receivers, automatic task-level cloud distribution, and verified CI gate attestation. The first CircleCI-only experimental adapter is separate and not yet live-accepted.
6. Optional central ledger backend when multiple machines must coordinate quotas.

Do not claim these roadmap items are already present.
