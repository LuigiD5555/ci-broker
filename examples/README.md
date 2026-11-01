# Project integration examples

Copy one `.ci-broker/project.toml` into the **target project's** `.ci-broker/` directory. Do not copy the Python source of CI Broker.

| Example | Primary runtime | Sample command | Status |
| --- | --- | --- | --- |
| `python-project/` | Python | `python -m unittest discover -s tests` | Generic sample |
| `rust-project/` | Rust | `cargo test --locked` | Generic sample |
| `extracta/` | Python + mixed stack | `python tools/update_manager_validation.py light` | Example only; requires Extracta Python 3.15.2 environment |

## Quick copy

```bash
mkdir -p /work/my-app/.ci-broker
cp examples/python-project/.ci-broker/project.toml /work/my-app/.ci-broker/project.toml
ci-broker --project-root /work/my-app validate
ci-broker --project-root /work/my-app plan --profile light
```

The Extracta example intentionally does not package or run Q12. Full acceptance remains controlled by Extracta's own CI gate, not this generic package. A profile marked `independent = true` is not a guarantee that `run` will provide independent verification: `run` always executes locally; use `plan --independent` for provider eligibility.
