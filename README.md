# CI Broker

**Project-agnostic, local-first CI planning and budget orchestration.** Install it once and reuse it across Python, Rust, Go, Java, mixed-language, or custom projects. Your project supplies **what** to test. CI Broker decides **which tests and in what order**, **where resources are available**, and **whether budget permits the run**.

> **Status: v0.1.0 pre-release prototype.** Local execution and advisory provider planning are implemented. Cloud accounts, remote dispatch, auto-generated vendor pipelines, hosted quotas, and native test discovery are **not** connected yet. Never interpret a configuration file as proof that a cloud runner is active.

## Quick start (5 minutes)

Requires **Python 3.11+** on Linux, macOS or POSIX systems supporting `fcntl`. CI Broker itself uses only the Python standard library; your test commands can use any runtime.

```bash
git clone https://github.com/LuigiD5555/ci-broker.git
cd ci-broker
python3 -m pip install -e .
ci-broker --help
```

In **another project**:

```bash
cd /path/to/my-project
ci-broker init
# Edit .ci-broker/project.json to select your real test commands
ci-broker plan --profile light
ci-broker run --profile light --local --approve
```

`plan` **never executes tests or spends credits**. `run` only executes project-defined commands **locally**, with no shell interpolation (`shell=False`), and requires `--approve`. The default generated project runs `python --version` as a safe placeholder, **not** as a real test suite.

### How it works

```text
any project directory
  └── .ci-broker/project.json    ← project-specific adapter/configuration
                │
                ▼
        ci_broker core package
        ├── config + validation
        ├── dependency graph / deterministic test order
        ├── provider adapter registry
        ├── local-first budget-aware planner
        └── append-only shared ledger with atomic reservations
                │
       ┌────────┴────────────┐
       ▼                     ▼
local execution        cloud advice + budget reserve
(explicit approval)    (NO remote dispatch)
```

The core has **no Extracta-specific imports**, no fixed Python version for test projects, no hard-coded paths inside consumers, no account tokens, and no always-running service.

## Project configuration

Put `.ci-broker/project.json` at the root of **any** repository. Example:

```json
{
  "schema": 1,
  "project_id": "my-service",
  "tasks": {
    "lint": {
      "command": ["python", "-m", "compileall", "-q", "src"],
      "expected_minutes": 1,
      "timeout_minutes": 3,
      "capabilities": ["python"]
    },
    "unit": {
      "needs": ["lint"],
      "command": ["python", "-m", "unittest", "discover", "-s", "tests"],
      "expected_minutes": 3,
      "timeout_minutes": 10,
      "capabilities": ["python"]
    }
  },
  "profiles": {
    "light": ["unit"],
    "full": ["unit"]
  }
}
```

Selecting `unit` automatically includes its transitive dependency `lint`, **once**. Cycles and missing dependencies fail closed. Execution is sequential in v0.1.0; the reported **critical path** is informational and does not imply concurrency.

Try:

```bash
ci-broker plan --profile light
ci-broker run --profile light --local --approve
ci-broker --project-root /path/to/another-project plan --profile full
```

Commands are argument arrays. **Do not** use `"command": "curl ... | bash"`; string commands are rejected. For a project-owned shell script, explicitly use `["sh", "scripts/test.sh"]` and review the script.

## Shared CI budgets across projects

All projects on one machine use the **same provider-account catalog and ledger**:

- Provider accounts: `~/.config/ci-broker/providers.json` or `CI_BROKER_ACCOUNTS=/absolute/path/providers.json`
- State: `~/.local/state/ci-broker/ledger.jsonl` or `CI_BROKER_LEDGER=/absolute/path/ledger.jsonl`
- Project declaration: `.ci-broker/project.json` (**in each project**, version controlled)

Without a provider-account file, the broker offers a single **local** provider by default. Example optional account catalog (illustrative quota, **NOT account verified**):

```json
{
  "local": {
    "adapter": "local",
    "enabled": true,
    "verified": true,
    "independent": false,
    "preferred": true,
    "quota": null,
    "capabilities": ["python", "rust", "go", "node", "java", "docker", "linux"]
  },
  "example-cloud": {
    "adapter": "metered",
    "enabled": false,
    "verified": false,
    "independent": true,
    "quota": 100,
    "unit": "credits",
    "units_per_minute": 2,
    "billing_increment_seconds": 60,
    "reserve_fraction": 0.10,
    "capabilities": ["python"]
  }
}
```

`enabled=false` keeps a provider out of recommendations. `verified=false` prevents remote **reservations**, even if someone enables the provider. Verify real quotas, actual account status, runtime capability, pricing and billing before changing either. A reservation never starts a workflow.

```bash
ci-broker providers
ci-broker plan --profile light --independent
ci-broker reserve --profile light --provider example-cloud --run-ref project/job-123
ci-broker finish --provider example-cloud --run-ref project/job-123 --units 8
# Or omit --units to cancel a reservation AFTER confirming no job is running.
ci-broker record --provider example-cloud --run-ref external-job-456 --units 7
ci-broker status
```

The last four commands operate on the **shared ledger**. `record` is idempotent by provider/job ID. Reservations lock the file across processes, guard the worst-case time budget, and protect a configurable quota fraction (default 10%). **An unfinished job continues to hold quota across month boundaries.** Billing adjustments and remote-state reconciliation are not automated.

A per-machine ledger is **not** a distributed database. Multiple separate machines must use an explicitly coordinated shared service to enforce truly global limits; do not mount one JSONL over unreliable network filesystems and assume correctness.

## Provider plugins

Two built-in models: `local` and `metered`. A trusted external package can add additional billing and capability rules through the `ci_broker.providers` entry-point group:

```toml
[project.entry-points."ci_broker.providers"]
my-provider = "some_package.provider:MyProvider"
```

Implement `quote(spec, task) -> Quote` and `eligible(spec, task) -> (bool, reason)`. Install and **explicitly** enable the trusted package:

```bash
ci-broker --plugin my-provider plan --profile light
```

No plugin is imported just because a JSON configuration names it. Plugins are trusted Python code and should be reviewed before installation. Remote-dispatch adapters are not implemented yet: plugin registration does not grant execution or cloud billing permissions.

## Copyable project examples

- [Python + unittest](examples/python/.ci-broker/project.json)
- [Rust + Cargo](examples/rust/.ci-broker/project.json)
- [Go + go test](examples/go/.ci-broker/project.json)
- [Node.js + npm](examples/node/.ci-broker/project.json)
- [Extracta consumer integration](examples/extracta/.ci-broker/project.json)
- [Provider account catalog](examples/providers.json)
- [Quick-start guide in Spanish](docs/GUIA_RAPIDA_ES.md)
- [LLM quick context / machine-readable interface](docs/LLM_CONTEXT.md)
- [Architecture, contracts, security and roadmap](docs/ARCHITECTURE.md)

To try any example against its own project, copy the example's `.ci-broker/project.json` into that project's root and adapt the paths and commands. The Rust and Extracta examples refer to **files of their respective projects**, not files inside this broker repository.

## Development and tests

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q src tests
# Optional on-demand GitHub Actions: Actions → Core tests (manual only) → Run workflow
python3 -m pip install -e .
ci-broker --help
```

The package ships without automatic cloud webhooks, provider credentials or background jobs. No `main` merge in the original Extracta repository is required to start using CI Broker independently.

## Current limitations

- **Local-only execution.** Cloud provider planning/reservation is advisory; no job trigger, polling, cloud pipeline rendering or authenticated billing synchronization yet.
- **No automatic language detection** or changed-file test selection yet. Projects declare tasks, commands, capabilities and their dependency DAG explicitly.
- **No parallel test execution**, historical test-cost learning or mathematical optimization yet. Deterministic topological order and a critical-path estimate are implemented.
- **No guaranteed free cloud compute.** Limits are account-specific; the broker refuses unverified remote reservations.
- **POSIX file locks only.** Windows needs a platform-specific ledger backend.

For implementation decisions and extension boundaries, read `docs/ARCHITECTURE.md`. For a compact context file to load into an LLM, use `docs/LLM_CONTEXT.md`.

## License

MIT; see [LICENSE](LICENSE).

## Stabilization plan (v0.1.1)

Before expanding remote integrations or enabling cloud CI, follow the bounded [v0.1.1 stabilization plan](docs/PLAN_ESTABILIZACION_0_1_1.md) (Spanish). It records 16 safety/behavior invariants, six closed work packages (P0–P5), offline tests, release gates and explicit non-goals. **This is a plan, not an implementation or evidence of passing tests.**
