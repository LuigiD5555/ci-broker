"""Minimal *illustrative*, manual-only CI examples; inspect before use."""
from __future__ import annotations

from .models import ConfigError


def render(provider: str, profile: str) -> str:
    if provider == "github":
        return f'''# Requires installing ci-broker in the target repository environment.
# Manual dispatch only. No push trigger.
name: CI Broker {profile}
on: workflow_dispatch
permissions:
  contents: read
jobs:
  verify:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.13'
      - run: python -m pip install project-ci-broker
      - run: ci-broker plan --profile {profile}
      # Approval required for executing project commands:
      # - run: ci-broker run --profile {profile} --yes
'''
    if provider == "gitlab":
        return f'''# Web-triggered only. Connect a private project in GitLab first.
workflow:
  rules:
    - if: '$CI_PIPELINE_SOURCE == "web"'
    - when: never
verify:
  image: python:3.13-slim
  script:
    - python -m pip install project-ci-broker
    - ci-broker plan --profile {profile}
    # Uncomment once approved: ci-broker run --profile {profile} --yes
'''
    raise ConfigError(f"no sample renderer registered for {provider!r}")
