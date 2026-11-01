# CI Broker

**Project-agnostic, local-first CI planning and execution.** CI Broker is an installable Python package that reads a project's validation profiles, selects compatible providers using their own quota units, protects shared compute budgets and optionally executes explicitly approved commands locally.

CI Broker does **not** know what Extracta, BPFW, a Python API, or a Rust application is. Each consumer supplies a small declarative file. Like BPFW, it operates on a target directory with `--project-root`.

> **Status: v0.1.0, early standalone core.** Local execution, planning, reservations and two illustrative CI renderers are implemented. Remote provider dispatch, billing API sync, and graph-based test deduplication are future work. No cloud account or daemon is necessary.

## 1. Install

Python 3.11+ on a POSIX system (uses `fcntl` for ledger file locks). No runtime third-party dependencies.

```bash
git clone https://github.com/LuigiD5555/ci-broker.git
cd ci-broker
python3 -m pip install -e .
ci-broker --help
```

## 2. Five-minute quick start

```bash
# From any project folder:
cd /path/to/a-project
ci-broker init
# Edit .ci-broker/project.toml to use THIS project's actual commands.
ci-broker validate
ci-broker providers
ci-broker plan --profile light
ci-broker run --profile light --yes
```

`init` never overwrites an existing configuration. `plan`, `validate`, `providers` and `render` do not run tests or dispatch CI. `run --yes` executes the commands in the local project configuration; only approve trusted files.

## 3. Project file

Create `.ci-broker/project.toml` in the consumer project:

```toml
schema = 1

[project]
id = "sample-api"

[profiles.light]
commands = ["python -m unittest discover -s tests"]
minutes = 3
timeout_minutes = 10
requires = ["python"]

[profiles.full]
commands = ["python -m unittest discover -s tests -v"]
minutes = 15
timeout_minutes = 30
requires = ["python"]
independent = true

[policy]
prefer_local = true
require_approval = true
allow_paid_overage = false
reserve_fraction = 0.10
```

For different languages, **only the commands and capabilities change**. For Rust, `commands = ["cargo test --locked"]` and `requires = ["rust"]`; for Go, `commands = ["go test ./..."]` and `requires = ["go"]`. There are ready-to-copy files under [`examples/`](examples/README.md).

## 4. Use CI Broker from outside the project

```bash
ci-broker --project-root /work/extracta validate
ci-broker --project-root /work/extracta plan --profile light
ci-broker --project-root /work/extracta run --profile light --yes
```

This is deliberately similar to BPFW's `--project-root` design. The package remains installed once; any number of projects can consume it.

## 5. Commands and effects

| CLI | Purpose | Side effects |
| --- | --- | --- |
| `init` | Create editable config | Writes `.ci-broker/project.toml` |
| `validate` | Check project and provider configuration | Read only |
| `providers` | Show configured pools, quotas and verified status | Read only |
| `plan --profile light` | Suggest eligible capacity, expected and worst-case spend | Read only |
| `plan --profile full --independent` | Require an independent verified provider | Read only |
| `run --profile light --yes` | Execute declared commands in target working directory | **Runs local commands** |
| `render --provider github --profile light` | Generate an illustrative manual CI configuration | Prints YAML only |
| `render --provider gitlab --profile light` | Generate an illustrative web-triggered configuration | Prints YAML only |
| `usage` | Display known usage and outstanding holds | Read only |
| `reserve --provider P --profile light --run-ref ID` | Lock worst-case capacity | Writes ledger; **never launches CI** |
| `settle --provider P --run-ref ID --units N` | Close a verified execution | Writes ledger |
| `cancel --provider P --run-ref ID` | Cancel a verified pending job | Writes ledger |

Run `ci-broker --help` and `ci-broker <subcommand> --help` for arguments.

## 6. How the budget planner works

Each provider has its own billing unit, such as minutes or credits. CI Broker calculates the *expected* cost and the *worst-case* cost from `minutes`, `timeout_minutes`, billing granularity and `rate_per_minute`. It checks capability requirements, the remaining quota, outstanding reservations and a protected fraction. When the project prefers local compute, a compatible local provider takes priority.

**Shared ledger:** `~/.local/state/ci-broker/ledger.jsonl`. Point `CI_BROKER_LEDGER` to an absolute shared ledger location when multiple projects use the same provider account. All projects must use the same provider ID for the same quota pool. File locks protect parallel reservations on a local POSIX filesystem.

Unverified external accounts are **ineligible** until the operator provides verified quotas. `plan` is advisory; it never uses cloud credentials, connects accounts or starts a job. Unmetered local is not an assertion of unlimited CPU resources.

## 7. Add providers without changing core

Operator-specific JSON configuration goes under `~/.config/ci-broker/providers.d/` (or the directory defined by `CI_BROKER_PROVIDERS_DIR`). For instance:

```json
{
  "my-ci": {
    "label": "Private hosted worker",
    "adapter": "metered",
    "enabled": false,
    "verified": false,
    "independent": true,
    "quota": 500,
    "unit": "credits",
    "rate_per_minute": 2,
    "billing_increment_seconds": 60,
    "capabilities": ["python", "rust"]
  }
}
```

By design, existing provider keys may be overridden in local operator JSON (e.g. to supply a *verified* account allowance) without editing packaged defaults. Don't place tokens or secrets in JSON. Invalid adapter references are rejected.

For non-linear billing, publish a trusted Python class implementing `ProviderAdapter.quote(provider, profile) -> Quote` and `eligible(provider, profile) -> (bool, reason)` and register an entry point in the `ci_broker.adapters` group. Load explicitly with `--trusted-adapter your-adapter`. Catalog JSON never loads arbitrary Python by itself.

## 8. Provider pipeline examples

```bash
ci-broker render --provider github --profile light > ci-broker-github-example.yml
ci-broker render --provider gitlab --profile light > ci-broker-gitlab-example.yml
```

These are templates to inspect, **not verified active pipelines**. They intentionally don't execute project tests until the operator reviews the runner, installs the package in the target environment and approves execution. Real-world CI services may require specific Python, Rust, Docker or database images, and their published free quotas are not guaranteed available in your account.

## 9. Example with Extracta

Extracta consumes the core like any other project. See [`examples/extracta/.ci-broker/project.toml`](examples/extracta/.ci-broker/project.toml): it declares its own Python 3.15.2 validation command. The engine has no reference to Extracta in its Python source. Keep the existing Extracta Q12 workflow separately while validating equivalence; do not silently migrate full acceptance or connect paid providers.

## 10. Run the tests

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q src tests
```

## 11. For future agents and LLMs

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) defines ownership boundaries, extension contracts, execution guards and limitations.
- [`docs/LLM_CONTEXT.md`](docs/LLM_CONTEXT.md) is a short handoff document with exact project state, file layout, tests and rules for safe continuation.
- [`examples/README.md`](examples/README.md) contains copy-and-paste examples for Python, Rust and Extracta.

The architecture is **core → providers/adapters + consumer project profiles**. Never put project-specific test commands into `src/ci_broker/`. Build remote capabilities as independent adapters, never into the budget scheduler.
