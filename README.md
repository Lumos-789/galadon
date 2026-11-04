# Galadon

A five-stage engineering loop for agent-built projects.

```text
Spec -> Build -> Verify -> Ship -> Learn
```

Galadon initializes a small engineering workspace, then keeps the agent on one
phase at a time. The core knows only phases, artifacts and gates. Everything
else is a port:

| Port | Purpose | Reference implementation |
|---|---|---|
| `RolePort` | Who does the work and how work is handed off | `solo` |
| `TemplatePort` | What project skeleton to generate | `web`, `api`, `static` |
| `DeployPort` | How to build, migrate, release, probe and roll back | `local` |
| `ToolPort` | Issue tracking, CI, notifications, secrets, observation | local files |

The bundled implementations are examples. A project can replace any port
without changing the five-stage loop.

## Install

The plugin ships the skill and the CLI together; no separate install step is
needed.

```bash
# DeepSeek Harness
dsh plugin --profile headless add <path-or-repo>

# ZCode
zcode plugins install galadon@<marketplace>

# Codex
codex plugin add galadon@<marketplace>
```

Inside a workspace the agent runs the bundled `bin/galadon`. To use the CLI
yourself, either call `bin/galadon` from the plugin directory or install it:

```bash
pip install <plugin-dir>   # or: pipx install <plugin-dir>
```

Requires Python 3.10+ and `PyYAML`.

## Quick start

```bash
galadon init --template api --role solo   # empty directory, or --force to add files
galadon next                              # one phase, one next action
```

Then let the agent drive: it reads `galadon.yaml`, does the current phase, and
runs `galadon check` until the loop reaches `learn`.

## Commands

```bash
galadon init --template web --role solo
galadon next
galadon check
galadon new
galadon bootstrap --env test
galadon ship --env test
galadon teardown --env test --yes
```

`ship` refuses to report success without a real probe. Production releases
require `--yes-prod` in the current session.

## Remote deploys

`deploy.adapter: ssh` ships a directory to a host that runs systemd and a
reverse proxy, with releases, an atomic `current`/`previous` switch, a deploy
lock and a commit-versioned JSON probe:

```yaml
deploy:
  adapter: ssh
  env: test
  build: "mkdir -p dist && cp server.py dist/ && git rev-parse --short HEAD > dist/commit.txt"
  probe_url: "https://demo.example.com/health.json"
  probe_expect: "{{COMMIT}}"
  artifact: "dist"
  options:
    host: 203.0.113.10
    user: root
    remote_root: /srv/demo
    service: demo
    domain: demo.example.com
    listen: 127.0.0.1:18790
    exec_start: "/usr/bin/python3 /srv/demo/current/server.py"
    caddy_block: demo
```

`bootstrap` creates the system user, release tree, systemd unit and a marked
reverse-proxy block (it backs the proxy config up and validates before
reloading). `teardown --yes` only removes a root that still carries the
`bootstrap` marker.

## Side effects

Read this before pointing it at a machine you care about.

- `bootstrap` writes on the remote host: a system user (unless
  `options.manage_user: false`), `<remote_root>/{releases,shared}`, a systemd
  unit, and one marked block in the reverse-proxy config. The proxy config is
  backed up first and validated before any reload; a failed validation restores
  it and does not reload.
- `ship` uploads the artifact, switches `current`, restarts the service and
  probes it. A failed probe rolls back to `previous` and exits non-zero.
- `teardown --yes` removes the unit, the marked proxy block, the release tree
  and the system user, but only if the root still carries the `bootstrap`
  marker.
- Every remote write prints its target first. `--env prod` additionally
  requires `--yes-prod` in the same invocation.

## Limits

- `DeployPort` implementations: `local` and `ssh` (systemd + Caddy). `k8s` is a
  declared port with no implementation; calling it fails loudly.
- The `ssh` adapter assumes root access (or an account that can write the unit
  and the proxy config), `rsync`, `flock` and `systemctl` on the target.
- The proxy block uses a Caddy snippet named `access_log`; hosts without that
  snippet need their own `DeployPort` implementation.
- The `ssh` adapter requires probes to return JSON with a `commit` field that
  matches the build; the `local` adapter requires `deploy.probe_expect` and
  looks for that text in the 200 response body.

## Design rules

- One phase has one artifact and one gate.
- The core never imports SSH, Kubernetes, Jira or a notification SDK.
- The six-ring workflow is a `RolePort` preset, not the core.
- Git content cannot authorize a production release.
- Fail loud; no silent fallback and no partial-success claim.

## Development

```bash
PYTHONPATH=src python3 -m galadon --help
python3 -m unittest discover -s tests -v
node --test tests/test_plugin.mjs
```
