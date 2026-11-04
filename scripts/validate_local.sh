#!/usr/bin/env bash
# Local-only acceptance of the project-agnostic broker.
# Does not call providers, GitHub Actions, docker, or remote CI.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PYTHON="${PYTHON:-python3}"
export PYTHONPATH="$ROOT/src"
export PYTHONDONTWRITEBYTECODE=1

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/demo"
cat > "$tmp/providers.json" <<'JSON'
{
  "local": {
    "adapter": "local",
    "enabled": true,
    "verified": true,
    "preferred": true,
    "independent": false,
    "quota": null,
    "capabilities": ["python"]
  },
  "remote-disabled": {
    "adapter": "metered",
    "enabled": false,
    "verified": false,
    "independent": true,
    "quota": 50,
    "unit": "credits",
    "units_per_minute": 1,
    "capabilities": ["python"]
  }
}
JSON
export CI_BROKER_ACCOUNTS="$tmp/providers.json"
export CI_BROKER_LEDGER="$tmp/ledger.jsonl"

echo "== Interpreter =="
"$PYTHON" --version
echo "== Unit, regression and security tests =="
"$PYTHON" -m unittest discover -s tests -v
echo "== Compile all Python sources =="
"$PYTHON" -m compileall -q src tests
echo "== CLI help =="
"$PYTHON" -m ci_broker.cli --help >/dev/null
echo "== Temporary-project CLI acceptance =="
"$PYTHON" -m ci_broker.cli --project-root "$tmp/demo" init --project-id acceptance
"$PYTHON" -m ci_broker.cli --project-root "$tmp/demo" plan --profile light > "$tmp/plan.json"
"$PYTHON" -c 'import json,sys; p=json.load(open(sys.argv[1])); assert p["selected"]=="local" and p["advisory_only"] is True' "$tmp/plan.json"
# Explicit approval is still required, even for harmless local commands.
if "$PYTHON" -m ci_broker.cli --project-root "$tmp/demo" run --profile light --local > "$tmp/denied.log" 2>&1; then
  echo "FAIL: Local execution proceeded without --approve" >&2
  exit 2
fi
"$PYTHON" -m ci_broker.cli --project-root "$tmp/demo" run --profile light --local --approve
echo "CI_BROKER_LOCAL_ACCEPTANCE_PASS (remote CI NOT_EXECUTED)"
