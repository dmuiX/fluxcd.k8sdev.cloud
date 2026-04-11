# Renovate (self-hosted)

Renovate runs as a self-hosted GitHub Actions workflow instead of the hosted renovate.app service. Behavior is identical — same config format, same PR output.

## How it works

- Runs hourly via cron (`0 * * * *`)
- Can be triggered manually via **Actions → renovatebot → Run workflow**
- Triggers automatically when a checkbox in the **Dependency Dashboard** issue is edited — same behavior as the hosted version's "Trigger Renovate Run" button

## Config

`.github/renovate.json5` — standard Renovate config:

- `config:recommended` as base
- Dependency Dashboard enabled
- Semantic commits
- Flux HelmRelease versions tracked (`infra/`, `apps/`, `clusters/`)
- GitHub Actions versions tracked
- Packages grouped: monitoring stack, stable infra patches, FluxCD, stateful services

## Dependency Dashboard

The [Dependency Dashboard](../../issues?q=is%3Aissue+Dependency+Dashboard) issue is created automatically on first run. It shows:

- All detected dependencies and their current versions
- Pending updates and open PRs
- Checkboxes to re-trigger specific updates (e.g. after closing a PR)

Editing a checkbox in the dashboard triggers a new Renovate run automatically.

## Permissions

The workflow uses `GITHUB_TOKEN` — no extra secrets needed. Required permissions:

| Permission | Why |
| --- | --- |
| `contents: write` | Create/push branches for PRs |
| `pull-requests: write` | Open and update PRs |
| `issues: write` | Create and update the Dependency Dashboard issue |
