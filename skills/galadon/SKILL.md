---
name: galadon
description: Initialize and run an agent-ready engineering workspace with the five-stage Galadon loop. Use when the user says Galadon/工程阵地/初始化工程/项目模板/一键部署/下一步做什么, or wants a new project scaffolded with spec, build, verify, ship and learn gates.
---

# Galadon

Galadon is a five-stage engineering loop:

```text
Spec -> Build -> Verify -> Ship -> Learn
```

The core depends on four ports: `RolePort`, `TemplatePort`, `DeployPort` and
`ToolPort`. The bundled implementations are references, not the framework.

## When to use

- The user wants to initialize a new engineering project.
- The user asks "下一步做什么" or wants an agent to follow a standard loop.
- The user wants a project that can build, verify, ship and keep evidence.
- The user wants to swap deployment style without changing the workflow.

## Workflow

1. Read `galadon.yaml`.
2. Run `galadon next`.
3. Do only the current phase; write its artifact.
4. Run `galadon check`.
5. Repeat until the loop is done.

## Running the CLI

Resolve it in this order and stop at the first hit:

1. `galadon` on `PATH`.
2. `../../bin/galadon` relative to this skill directory (the plugin ships the CLI).
3. `PYTHONPATH=<plugin>/src python3 -m galadon`.

The CLI only needs Python 3.10+ and `PyYAML`.

## Commands

```bash
galadon init --template web --role solo
galadon next
galadon check
galadon new
galadon bootstrap --env test
galadon ship --env test
galadon ship --env prod --yes-prod
galadon teardown --env test --yes
```

`galadon new` archives the finished loop's checks and release records under
`.galadon/history/` and returns to `build`, so the next change starts clean.
`bootstrap` provisions whatever the selected DeployPort needs (for `ssh`:
system user, release tree, systemd unit, reverse-proxy block) and is
idempotent; `teardown --yes` removes exactly that. Both adapters keep release
directories, `current`/`previous` links and `shared/deploy.lock`; for `local`
the root is `deploy.options.root` (default `.galadon/deploy`).

## Rules

- Never skip `Verify` or `Ship`.
- Never report ship success without a version probe match.
- A failed probe rolls back to the previous release and the run exits non-zero.
- Production release requires explicit confirmation in the current session.
- Never write secret values into `galadon.yaml` or git.
- If an adapter is not implemented, say so; do not fake a deployment.
- Keep `AGENTS.md` short. The current phase lives in the managed block.
- Every remote write prints its target first; `teardown` refuses a remote root
  that was not created by `bootstrap`.

## Ports

| Port | Reference implementation | Replace with |
|---|---|---|
| `RolePort` | `solo` | `six-ring`, `orchestrator` |
| `TemplatePort` | `web`, `api`, `static` | team templates |
| `DeployPort` | `local` command adapter, `ssh` systemd adapter | Kubernetes, platform deploys |
| `ToolPort` | local file tools | GitLab, Jira, notifications |
