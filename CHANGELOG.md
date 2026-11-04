# Changelog

## 0.2.0

- `ssh` DeployPort: release directories, atomic `current`/`previous` switch,
  remote deploy lock, commit-versioned JSON probe and automatic rollback.
- `bootstrap` / `teardown` are part of `DeployPort`; the CLI no longer reaches
  into adapter internals.
- `teardown` refuses a remote root that `bootstrap` did not create.
- `ship` fails before deploying when the installed systemd unit drifted from
  `galadon.yaml`.
- `galadon new` archives a finished loop and returns to `build`.
- Remote paths are charset-checked before reaching a shell or a unit file.
- Added a runnable `examples/minimal` workspace.

## 0.1.0

- Five-stage loop (`spec`, `build`, `verify`, `ship`, `learn`) with one artifact
  and one gate per phase.
- `RolePort` presets (`solo`, `six-ring`, `orchestrator`), `TemplatePort`
  profiles (`web`, `api`, `static`), local `DeployPort` reference and `ToolPort`
  interfaces.
- `init`, `next`, `check`, `ship` and the managed `AGENTS.md` block.
