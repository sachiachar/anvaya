# Notes for coding agents

Read [CONTRIBUTING.md](CONTRIBUTING.md) first; everything there applies to you. In short:

## Never

- Commit on `main`, push to `main`, force-push, or skip git hooks (`--no-verify`).
- Deploy: no `gcloud run deploy`, `firebase deploy` or `infra/*.sh`. Deploys happen when a person merges to `main`, or through Actions → Deploy.
- Call Gemini outside the governance gateway, or add an agent that isn't in `agents/registry.yaml`.
- Put conversation text, names or emails in the platform admin portal, logs or test fixtures.
- Switch the prototype release to Vertex AI.

## Always

- Work on a branch (`feat/…`, `fix/…`, `chore/…`, `docs/…`), and open a pull request using the template.
- Run `cd agents && uv run --group dev pytest -q` and `cd web && npm run build` before opening the PR. Say honestly what you did and didn't check.
- Commit only the files your change needs. Leave other people's uncommitted work alone.
- Link the roadmap item (`PR-x.y`) or issue, and record progress on the issue.

## Where things are

| Area | Path |
| --- | --- |
| API, loops, live call | `agents/app/` |
| Governance layer: registry, gateway checks, ledger, guardian events, roles, audit, admin API | `agents/app/governance/`, `agents/registry.yaml` |
| Customer web app | `web/src/` (`index.html`) |
| Platform admin portal | `web/src/admin/` (`admin.html`, served as `anvaya-admin`) |
| Tests | `agents/tests/` |
| Design | `docs/architecture.md`, `docs/proposals/agent-governance.md` |
| Plan | GitHub issues: milestone "Prototype release" (PR-1 to PR-12), parent issue #43 for governance |
