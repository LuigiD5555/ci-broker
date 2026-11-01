# Instructions for coding agents

Read `docs/LLM_CONTEXT.md` and `docs/ARCHITECTURE.md` before making architectural changes.

- **No consumer imports**: core must not depend on Extracta, BPFW, Guenda, Rust or any particular app.
- **Fail closed**: never silently run external CI or spend resources; keep remote dispatch unimplemented until explicitly authorized.
- **Contracts first**: preserve project schema v1, CLI surface, plugin protocol and account-shared ledger.
- **Tests**: `PYTHONPATH=src python3 -m unittest discover -s tests -v`.
- **Safety**: commands are trusted project-owned argv arrays; local execution requires `--approve`.
- **Evidence**: do not call YAML templates actual cloud integrations or mark unexecuted tests PASS.
- **Scope**: add project-specific behavior to examples/configuration, never inside the core.

## Stabilization acceptance

Read `docs/PLAN_ESTABILIZACION_0_1_1.md` before further development. Run `bash scripts/validate_local.sh` with Python 3.15.2 in an isolated environment. Record SHA, exact interpreter, test counts and exit status; do not claim PASS based only on committed tests. No external CI, no changes to Extracta or `main`.
