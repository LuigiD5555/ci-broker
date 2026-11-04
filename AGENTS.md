# Instructions for coding agents

Read `docs/LLM_CONTEXT.md` and `docs/ARCHITECTURE.md` before making architectural changes.

- **No consumer imports**: core must not depend on Extracta, BPFW, Guenda, Rust or any particular app.
- **Fail closed**: never silently run external CI or spend resources. Experimental CircleCI dispatch is separately opt-in (`ci-broker remote dispatch ... --approve`), only with verified budgets, a pinned SHA and atomic duplicate protection. It has no live CI acceptance yet.
- **Contracts first**: preserve project schema v1, CLI surface, plugin protocol and account-shared ledger.
- **Tests**: `PYTHONPATH=src python3 -m unittest discover -s tests -v`.
- **Safety**: commands are trusted project-owned argv arrays; local execution requires `--approve`.
- **Evidence**: do not call YAML templates actual cloud integrations or mark unexecuted tests PASS.
- **Scope**: add project-specific behavior to examples/configuration, never inside the core.

## Stabilization acceptance

Read `docs/PLAN_ESTABILIZACION_0_1_1.md` before further development. Run `bash scripts/validate_local.sh` with Python 3.15.2 in an isolated environment. Record SHA, exact interpreter, test counts and exit status; do not claim PASS based only on committed tests. No external CI, no changes to Extracta or `main`.

## Experimental remote extension (not part of v0.1.1 certification)

Read `docs/REMOTE_CI.md` before touching `src/ci_broker/remote.py`, `src/ci_broker/mcp_server.py` or remote ledger receipts. Never put provider tokens in source files, task JSON, chat, logs or issues. A reserved ambiguous dispatch is **UNKNOWN**, not safe to retry; remote `SUCCESS_CI_ONLY` is not release acceptance. The local stdio MCP server is not automatically a ChatGPT plugin. All real remote dispatches require explicit approval and account credentials controlled by the operator.
