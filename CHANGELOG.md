# Changelog

## 0.2.2

- The `local` DeployPort now uses the same release contract as `ssh`: immutable
  `releases/<timestamp>-<version>` directories under `deploy.options.root`
  (default `.galadon/deploy`), an atomic `current` switch, a retained
  `previous`, and an exclusive `shared/deploy.lock`. A held lock refuses the
  ship with a non-zero exit instead of interleaving releases.
- A failed local probe rolls back to `previous`, re-probes the restored
  release, and names the release that is live again.
- The local probe is exact: a JSON response must carry a `commit` field equal
  to `deploy.probe_expect`, and a non-JSON response must equal it exactly. The
  recorded `actual_commit` now comes from the probe response, not build
  metadata.
- `galadon init` no longer scaffolds the do-nothing `checks: {build: "true",
  test: "true"}` gate. Empty checks make `galadon check` fail loudly with
  "no checks configured — declare checks.build/checks.test" until real commands
  are declared.
- `npm pack` no longer ships `__pycache__/` or `*.pyc` files from the working
  tree.

## 0.2.1

- The `local` DeployPort hashes an artifact directory the same way the `ssh`
  adapter does, so `artifact: dist` works with either adapter; an unreadable
  artifact reports a `galadon:` error instead of a Python traceback.
- An invalid `galadon.yaml` reports the parser message instead of a Python
  traceback.
- The `local` DeployPort requires `deploy.probe_expect`: a bare HTTP 200 is not
  a version probe, so it no longer counts as a successful ship.
- A missing PyYAML reports `galadon: PyYAML is required (pip install pyyaml)`
  and exits 69 instead of an import traceback.

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
