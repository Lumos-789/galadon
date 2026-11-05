# Changelog

## 0.3.3

- The deploy probe now waits for a service that was just restarted.
  `systemctl restart` returns once the process is spawned, not once it is
  listening: measured on a real host, the port refused connections for ~150 ms
  afterwards. The probe fired immediately and failed, so every shipment of a
  real project failed its probe and rolled back (2 out of 2 on the host used for
  the relay dogfood). Connection-level failures are now retried inside a bounded
  readiness window (15s); an HTTP answer is final and immediately subject to the
  commit check, and a target that never answers still fails loudly, naming the
  attempt count and the budget. The same zero tolerance made a rollback that had
  actually restored service report itself as failed, because the post-rollback
  re-check used the same probe.
- The probe fetch moved into `adapters/probe.py`, so the `local` and `ssh`
  adapters share one readiness policy instead of carrying a copy each.
- `bootstrap` refuses a `remote_root` it does not own. It used to `install -d`
  whatever path it was given and write its ownership marker into it, so a
  directory with someone else's data became a galadon root; `teardown` then
  deleted it, because the marker it checks was planted by that same bootstrap.
  It now refuses a non-empty unmarked directory, and a root that carries another
  slot's marker, before touching the user, the unit or Caddy.
- `teardown` no longer fails when the run user is shared. `userdel` on the
  default `galadon` user fails while any other slot runs as it — which is the
  normal state on a host with two deployments — and the teardown aborted
  *after* removing the root. It now leaves the user in place, says so, and
  reports `user_skip_reason`, while still failing loudly if the user cannot be
  removed for any other reason.
- `ship` refuses to publish a dirty working tree. The commit stamped on the
  release comes from `HEAD`, but the build copies the working tree, so
  uncommitted changes shipped under a commit they are not part of and a later
  rollback would restore a version that never existed. The loop's own files
  (evidence, the release record, `.galadon/`, the phase block in `AGENTS.md`)
  are exempt.
- `ship --dry-run` no longer demands `--yes-prod`. Previewing a production
  release required the flag that authorises one; the plan now prints
  `requires: --yes-prod` instead, and the guard still applies to a real run.

## 0.3.2

- `pytest` no longer walks into `examples/`. Running it from the repo root used
  to collect `examples/minimal/tests/test_health.py`, import the example's
  `server.py`, and leave `__pycache__` inside the directory that ships in the
  npm tarball. `files` is an allowlist and overrides `.npmignore`, so those
  bytecode files ride into the package and turn the "npm tarball excludes
  Python bytecode" test red for anyone who ran pytest before `node --test`.
  `npm test` was unaffected because unittest discovery already stays in
  `tests/`, which is why the failure surfaced only on the pytest path. The
  example is still covered: it is exercised from a scratch copy.
- `prepack` deletes `__pycache__` and `*.pyc` before packing, so the tarball is
  bytecode-free regardless of what ran in the tree beforehand.

## 0.3.1

- `galadon.yaml` rejects unknown top-level keys instead of ignoring them. The
  shipped schema already declared `additionalProperties: false`, so a block that
  had stopped meaning anything — the retired 0.1.0 `tools:` port — could sit in
  a real config doing nothing. The error names every unknown key and the allowed
  set.
- The documented `check` behaviour with the default `none` layout now matches
  the code: the first failure is the spec gate ("spec/spec.md still contains
  TODO"), and "no checks configured" only appears once the spec is complete.
  `README.md`, `README_CN.md` and `galadon init --list-layouts` had the checks
  failure first.
- Tests: a standing matrix covers all 36 layout × stack × template
  combinations, asserting each one is either green on a fresh scaffold or fails
  loudly with the reason it is expected to produce. `examples/minimal` — shipped
  in the npm tarball and previously covered by nothing — must pass `galadon
  check` from a scratch copy.

## 0.3.0

- `galadon init --layout {none,webapp,service,plugin}` (default `none`) and
  `--stack {python,node,none}` (default `python`) generate the nine layers that
  three unrelated real projects already share: entry, plan, truth, knowledge,
  context, code, run, verify, output. `galadon init --list-layouts` prints the
  shipped layouts and stacks.
- `layout=none` keeps 0.2.x behaviour: the base template only, the same
  directories, and the same artifact paths.
- Real stack checks: `python3 -m compileall -f -q <layout source roots>` and
  `python3 -m unittest discover -s tests -v`, or `node --check <entry>` plus
  `npm test` (`node --test tests/`). The plugin layout also runs
  `bash -n bin/<project>` on both stacks. Every layout ships passing tests, so
  `galadon check` is genuinely green right after `init`; `--stack none` still
  fails loudly with "no checks configured".
- `galadon check` enforces the layout contract for non-`none` layouts:
  `AGENTS.md`, `openspec/config.yaml`, `spec/INDEX.md`, `spec/domains/`,
  `spec/TESTING.md`, `spec/CONSTITUTION.md`,
  `wiki/{knowledge,progress,runbook}/`, the six `.agents/` slots, non-empty
  checks, and `out/` + `.agents/` in `.gitignore`. A missing item fails the gate
  and is named.
- Every non-`none` layout scaffolds the spec truth layer under `spec/`:
  `INDEX.md` (living feature index with `F-<DOMAIN>-01` IDs, `1w`/`2w` gates
  and `主落点` pointers), `domains/README.md` (behaviour truth and `impl`
  pointers), `TESTING.md` (change-to-check trigger matrix) and
  `CONSTITUTION.md` (hard rules). The files live once in
  `templates/layouts/_shared/` and render before the layout's `common/` layer,
  so a layout can override them; `none` is unchanged.
- New `paths:` block in `galadon.yaml` (`spec`, `evidence`, `retro`, `release`)
  read by the loop instead of hardcoded constants. Defaults are the 0.2.x
  paths; layouts put `retro` under `wiki/progress/`.
- Layout templates are organised as `templates/layouts/<layout>/{common,python,node}`,
  with the spec truth layer stored once in `templates/layouts/_shared/` and
  rendered before each layout's `common/` layer. `.agents/` is stored as
  `__agents__/` because that directory is gitignored in this repository, and the
  renderer maps it back, substituting placeholders in paths as well as content.
- `webapp` and `service` shipped two byte-identical copies of the app skeleton
  (`server/app.py`, `src/<pkg>/`, `tests/`, `scripts/dev.sh`, for both stacks —
  10 files, 206 lines). A fix to one would have left the other silently stale,
  which is exactly the failure mode nobody notices until a user picks that
  layout, so `service` now renders `webapp`'s stack templates through
  `LayoutSpec.stack_templates`. Generated output is unchanged across all 36
  profile × layout × stack combinations, verified against a pre-change baseline.

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
