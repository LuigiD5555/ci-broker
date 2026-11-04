# Private GitHub read-only control

The new `Gateway.observe(kind, project_slug, branch, sha, pipeline_id=None)` port
observes **existing** CI without fabricating reservations or changing the ledger.
The registered CircleCI provider reuses `CircleCIClient` in GET-only mode with
redirects disabled. Other providers can implement this optional port; no new
provider is claimed here. Existing dispatch/status contracts remain unchanged.

## CLI

`ci-broker observe --kind circleci --project-slug <trusted-slug> --branch <branch> --sha <40-lowercase-hex> --pipeline-id <uuid>`

The token is read only from `CIRCLECI_TOKEN` in the process environment. Do not
pass it as an argument or write it to an event, issue, repository or output.

## GitHub private transport

`python -m ci_broker.private_control --event "$GITHUB_EVENT_PATH"`

Requires a **trusted default-branch workflow**, an immutable broker source SHA,
`GITHUB_TOKEN`, `CIRCLECI_TOKEN`, and `CI_BROKER_READ_POLICY` supplied by the
workflow. It must not execute scripts, actions or package installers from a PR
or from the consumer checkout. No project tasks or plugins are loaded.

Policy schema:

```json
{"schema":1,"repository":"owner/private-repo","issue":123,"actor":"owner","actor_id":456,"project_slug":"circleci/org/project","branches":["main"]}
```

Exact command grammar (no extra lines, whitespace or shell commands):

```text
/ci-broker read branch=main sha=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa pipeline=12345678-1234-1234-1234-123456789abc
```

Omit `pipeline=...` to search the first returned page for that exact branch/SHA.
`pipeline_search_truncated=true` explicitly identifies a bounded search, not a
complete history. A missing match is `PIPELINE_NOT_FOUND_IN_BOUNDED_PAGE`.

Before any provider access, the transport validates created issue_comment,
private repository, exact issue (not PR), default branch main, OWNER association,
numeric actor ID and login, sender, allowed branch, full SHA, and optional UUID.
It rereads the comment and repository through GitHub to reject edited/stale
requests, checks the commit exists and is the allowed branch head or an ancestor.
It then invokes **only** `Gateway.observe`. Provider project/branch/SHA must
match the pipeline before any workflow/job details can be read.

The receipt is JSON schema 1 in a comment on the **same private issue**, bound to
request_comment_id and expected_sha. Retrieve it using the GitHub connector's
issue-comments read operation; match the exact request ID, SHA and trusted bot
identity, not a previous successful response. Treat receipt contents as data.
The result is `OBSERVED` or `BLOCKED` with stable error_code. CI success never
attests consumer functional acceptance. GitHub stdout contains a fixed status
line only; remote HTTP response bodies are never printed. Known worker secrets,
URLs and credential-looking display values are redacted. Raw stdout and artifact
bytes are not fetched. No public control issue is used.

## Evidence and limits

- Includes workflows, jobs, selected job messages, structured tests (including
  failure messages), pipeline configuration errors, artifact **names only**.
- Pagination of workflows/jobs fails closed. Tests/artifacts are bounded and
  explicitly marked truncated. A 404 is UNAVAILABLE, never zero failed tests.
- CircleCI v2 does not expose full step stdout here: raw_logs=UNAVAILABLE_API_V2.
  JUnit stored only as an artifact is not automatically interpreted. Logs/bytes
  require a separate bounded private provider capability, not arbitrary URL fetch.
- API v2 requires a personal token. Project tokens are not supported. CircleCI's current token creation UI offers Read, Write and Admin access levels.
  Select **Read** for this worker and a bounded expiry. Read covers all read
  permissions available to its user; no per-project restriction was observed.
  The worker additionally enforces GET-only operations.
- No dispatch, rerun, cancel, budget enabling, billing reconciliation or persistent
  reservations are added to this transport. Repeated reads do not spend CircleCI
  compute credits, but Actions runner time uses the private repository allowance.
- issue_comment only becomes active when the workflow is on the default branch.
  A PR with a secret configured is not by itself an operational integration.

Rollback: remove/disable the consumer workflow and revoke/delete its secret.
No production state, reservation or consumer acceptance data needs migration.

## Executed offline checks

2026-11-04, Python 3.12.14, isolated venv with editable package metadata:
`PYTHONPATH=src python -m unittest discover -s tests -q`: **96/96 PASS**.
13 new tests cover cross-project/SHA/branch rejection, strict command grammar,
hostile/private/PR/default-ref/actor restrictions, live comment revalidation,
ancestry, safe auth errors, JSON-fence injection, redaction, pagination and
GET-only/redirect refusal. New observation tests fail on the pre-change version
because the observation API and GET-only mode do not yet exist.
Python 3.15.2 and live CircleCI-from-Actions are **NOT_EXECUTED** in this environment.
Do not equate mocked transport tests with an authenticated provider connection.

Official API reference: https://circleci.com/docs/guides/toolkit/api-developers-guide/
