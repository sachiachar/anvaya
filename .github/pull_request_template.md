## What and why

<!-- What changes for a user, and why. Link the roadmap item (PR-x.y) or issue. -->

## How I checked it

- [ ] `cd agents && uv run --group dev ruff check . && uv run --group dev pytest -q`
- [ ] `cd web && npm run lint && npm run build`
- [ ] Tried it in the app (say which role and flow):

## Risk

- [ ] Touches the vault, handover, sign-in, governance, `firestore.rules` or deploy (owner review needed)
- [ ] Adds or upgrades a dependency (latest stable, licence checked, `docs/dependencies.md` updated), environment variable or registry entry (say which)
- [ ] Release notes: an entry under "Not yet released", or the `no release note` label because nobody can notice this change (say why)
- [ ] Other docs updated: architecture, README, `docs/openapi.yaml` (or not needed)

## Coding agent

<!-- Which agent, if any, and what it did. "None" is fine. -->
