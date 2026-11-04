# CI Broker — remote CircleCI CLI and local agent tool (opt-in)

## Status

Experimental extension on `feature/circleci-remote-cli-20261104`. **Not merged, not release-certified.** No CircleCI token, account quota, pipeline definition, or running webhook receiver is installed by committing code. This feature does not change the stabilization result for v0.1.1: P5 remains TEST_PENDING until its offline harness actually passes.

## Contract

- CI Broker core remains project-agnostic: test commands, profiles, CircleCI pipeline parameter names and project slug are declared by each consumer.
- Read-only `remote plan` does not call any external API. `dispatch` requires `--approve`, a 40-character lowercase SHA, an eligible `verified=true` provider with protected worst-case budget, and an exact declared profile.
- The same logical `project_id / profile / SHA` can be dispatched **only once across all accounts** using an atomic ledger reservation. A successful response writes one durable receipt with pipeline ID; an ambiguous network/response failure leaves an unclosed reservation (UNKNOWN_DISPATCH_HOLD) to prevent double-spend.
- `remote status` observes CircleCI once. It requires the pipeline's observed Git SHA to equal the expected SHA. A reported workflow success is **not** formal acceptance: inspect required jobs, JUnit, physical witnesses and omissions before accepting a gate.
- The remote command never runs untrusted project commands on the broker host. It asks a configured provider to run predeclared CI YAML.
- `remote cancel` requires approval and does not release budget automatically. Reconcile actual provider usage with existing `ci-broker finish` / `record` only after verifying remote state.
- Credentials are only read from the operator environment, never from GitHub, chat messages, task JSON or CLI command arguments.

## Setup (one-time, operator machine)

1. Install the branch as an editable Python package: `python3 -m pip install -e .`. For clients supporting a **local stdio MCP server**, use `python3 -m pip install -e '.[mcp]'`.
2. Create `~/.config/ci-broker/providers.json` with your actual manually confirmed provider budgets. A dedicated example follows; values are intentionally **illustrative, not verified**. Do not enable until you've verified your own quota and pipeline compatibility.

```json
{
  "circleci-main": {
    "adapter": "metered",
    "remote_kind": "circleci",
    "enabled": false,
    "verified": false,
    "independent": true,
    "quota": 1000,
    "unit": "credits",
    "units_per_minute": 2,
    "billing_increment_seconds": 60,
    "reserve_fraction": 0.10,
    "capabilities": ["python", "linux", "docker"]
  }
}
```

Set `enabled=true`, `verified=true`, `quota` and `units_per_minute` **only after** checking the account, resource class and billing unit. On CircleCI, credits per minute depend on the resource class; numbers above are not a quote for a real machine.

3. In the consuming project's `.ci-broker/project.json`, add `remotes.circleci.profiles` mapping an existing project test profile to one boolean CircleCI pipeline parameter. Optionally set a project slug and definition ID; if omitted they **must** be set through `CI_BROKER_CIRCLECI_SLUG` and `CI_BROKER_CIRCLECI_DEFINITION_ID` in the operator's environment.

```json
"remotes": {
  "circleci": {
    "profiles": {"z01": "ci_broker_z01"},
    "sha_parameter": "ci_broker_sha"
  }
}
```

The consuming `.circleci/config.yml` must declare both pipeline parameters, use the boolean parameter to select the intended acceptance workflow, and compare the **actual checkout SHA** to `<< pipeline.parameters.ci_broker_sha >>` **before executing tests**:

```yaml
parameters:
  ci_broker_z01:
    type: boolean
    default: false
  ci_broker_sha:
    type: string
    default: ""
# Inside the intended CI job, after checkout:
#   test "$(git rev-parse HEAD)" = "<< pipeline.parameters.ci_broker_sha >>"
# Never approve a missing/mismatched SHA or a skipped gate.
```

The exact job must run the gate's complete physical witness suite and produce auditable JUnit/artifacts. The profile flag alone is **not** evidence of testing.

4. Obtain your own API token, project slug and **pipeline definition ID** from CircleCI's settings. Set the token privately (do not paste it into ChatGPT or commit it):

```bash
export CIRCLECI_TOKEN="<local-secret>"       # provide privately via your shell/secret manager
export CI_BROKER_CIRCLECI_SLUG="gh/<org>/<repo>"    # use your exact CircleCI project slug
export CI_BROKER_CIRCLECI_DEFINITION_ID="<uuid>"
```

## Commands

```bash
# Always use an actual checkout containing .ci-broker/project.json
ci-broker remote --project-root /path/to/extracta plan --profile z01

# BILLABLE network operation: explicit approval, one exact SHA
ci-broker remote --project-root /path/to/extracta dispatch \
  --profile z01 --provider auto \
  --sha f2905babf8cb85436afc47b77a72bff8956c23d1 \
  --branch integracion/pipecraft-z01-g5b-episodes-20261104 --approve

# Copy run_ref from SUBMITTED_UNVERIFIED response (not a pipeline URL)
ci-broker remote status --run-ref remote/<sha256-request-key>

# Explicit cancellation (may still consume credits)
ci-broker remote cancel --run-ref remote/<sha256-request-key> --approve

# Ledger usage settlement requires provider-verified actual units
ci-broker finish --provider circleci-main --run-ref remote/<sha256-request-key> --units <measured>
```

`ci-broker-remote` is an equivalent console entry point for the remote subcommands. No background polling is performed.

## Local MCP interface for tool-enabled coding agents

`ci-broker-mcp` runs a **stdio** MCP server exposing four explicit tools: `ci_plan`, `ci_dispatch`, `ci_status` and `ci_cancel`. Configure these environment variables **on the host running the server**:

```bash
export CI_BROKER_PROJECT_ROOT=/absolute/path/to/approved/project
export CI_BROKER_ACCOUNTS=/absolute/path/to/private/providers.json
export CI_BROKER_LEDGER=/absolute/path/to/private/ledger.jsonl
export CIRCLECI_TOKEN=...                      # local secret, never in prompts
export CI_BROKER_CIRCLECI_SLUG=...
export CI_BROKER_CIRCLECI_DEFINITION_ID=...
ci-broker-mcp
```

The MCP server requires `approve=true` on dispatch and cancel calls, and restricts tool execution to one configured checkout. It does **not** auto-install as a ChatGPT plugin or expose an authenticated public endpoint. To use it from an assistant, connect it in a compatible MCP client or deploy it behind a separately secured HTTPS/OAuth remote MCP bridge. A GitHub comment is **not** currently a command trigger and no always-on server is installed.

## Testing, safety and limitations

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
bash scripts/validate_local.sh
```

The new CircleCI tests mock the HTTP client and never call a remote API. Record exact interpreter, source SHA and test result before certification. No external dispatch or account quota has been validated yet.

Current scheduling uses the existing **whole-profile** provider selector; task-by-task parallel distribution and other provider adapters remain future work. A status of `SUCCESS_CI_ONLY` is not enough to pass G5b/G5c. For duplicate or UNKNOWN dispatches, inspect CircleCI manually before reconciling the held reservation; never blindly reissue the same request.

## GitHub PR comment bridge (optional, no GitHub Actions credits)

For a ChatGPT session with GitHub access, `ci-broker-webhook` is an operator-hosted receiver. It listens **on localhost only**. To receive GitHub deliveries, set up a trusted HTTPS forwarding tunnel to `http://127.0.0.1:8791/github` and configure GitHub Repository Settings > Webhooks with **issue_comment** events only, content type JSON, and a strong secret.

```bash
export CI_BROKER_WEBHOOK_SECRET="<random secret at least 32 chars>"
export CI_BROKER_GITHUB_TOKEN="<least-privilege token to read PR and comment>"
# All CircleCI, provider accounts and ledger variables from Setup still apply.
ci-broker-webhook --project-root /absolute/path/to/extracta \
  --repo LuigiD5555/extracta --author LuigiD5555 --host 127.0.0.1 --port 8791
```

A GitHub-connected assistant can then create a **top-level PR comment** containing exactly:

```text
/ci-broker run z01 sha=f2905babf8cb85436afc47b77a72bff8956c23d1 --approve
```

The bridge verifies GitHub's HMAC-SHA256 signature **before** JSON parsing; requires action `created`, exact trusted repo and commenter, a PR (not an issue), same-repository head (not a fork), and a live GitHub API match of the PR's current head SHA. It then uses the standard broker budget and duplicate fences. The service posts the remote result back to the PR when permitted. If a reply fails, it **must not** repeat the remote dispatch.

No webhook server, GitHub webhook secret, tunnel, CircleCI token or CircleCI API connection has been deployed by the PR. Do not paste them into ChatGPT. An exposed unauthenticated MCP/HTTP endpoint is **not** part of this design.
