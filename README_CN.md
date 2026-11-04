# Galadon

面向 agent 工程的五阶段循环：

```text
Spec -> Build -> Verify -> Ship -> Learn
```

Galadon 初始化一个最小工程阵地，然后让 agent 每次只做当前阶段。内核只认阶段、
产物和门禁；其余全部是端口：

| 端口 | 作用 | 参考实现 |
|---|---|---|
| `RolePort` | 谁来做、怎么交接 | `solo` |
| `TemplatePort` | 生成什么工程骨架 | `web`、`api`、`static` |
| `DeployPort` | 构建、迁移、发布、探活、回滚 | `local`、`ssh` |
| `ToolPort` | 工单、CI、通知、密钥、观测 | 本地文件 |

内置实现只是例子。项目可以替换任意端口，不需要改五阶段循环。

## 安装

插件自带 skill 和 CLI，装插件即可，不需要额外步骤。

```bash
# DeepSeek Harness
dsh plugin --profile headless add <路径或仓库>

# ZCode
zcode plugins install galadon@<marketplace>

# Codex
codex plugin add galadon@<marketplace>
```

工作区里 agent 直接用插件自带的 `bin/galadon`。自己想用 CLI 时，可以调
插件目录下的 `bin/galadon`，或：

```bash
pip install <插件目录>   # 或 pipx install <插件目录>
```

依赖 Python 3.10+ 与 `PyYAML`。

## 快速开始

```bash
galadon init --template api --role solo   # 空目录；非空目录加 --force
galadon next                              # 当前阶段 + 一个下一步
```

之后交给 agent：它读 `galadon.yaml`，只做当前阶段，跑 `galadon check`，
直到循环走到 `learn`。

## 命令

```bash
galadon init --template web --role solo
galadon next
galadon check
galadon new
galadon bootstrap --env test
galadon ship --env test
galadon teardown --env test --yes
```

`ship` 没有真实探针就不算成功。生产发布必须在当前会话显式加 `--yes-prod`。
`new` 把上一轮的 checks 与 release 记录归档到 `.galadon/history/`，回到 `build`
开始下一轮。

## 远程部署

`deploy.adapter: ssh` 把产物发到一台跑 systemd + 反向代理的机器：多版本目录、
原子切换 `current`/`previous`、部署锁、按 commit 校验的 JSON 探针。

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

`bootstrap` 负责系统用户、发布目录、systemd unit 和带 marker 的反代 block
（改前备份、`caddy validate` 失败即恢复）。`teardown --yes` 只删除还带
bootstrap marker 的远端目录。

## 副作用

指向重要机器之前请先读这一段。

- `bootstrap` 会在远端写入：系统用户（`options.manage_user: false` 可关闭）、
  `<remote_root>/{releases,shared}`、systemd unit，以及反代配置里一个带 marker 的
  block。改反代配置前先备份，`caddy validate` 不过就恢复且不 reload。
- `ship` 上传产物、切换 `current`、重启服务并探活；探活失败自动回滚到
  `previous` 并以非零退出。
- `teardown --yes` 删除 unit、marker block、发布目录和系统用户；远端目录必须
  仍带 bootstrap 写的 marker 才会执行。
- 所有远端写操作先打印目标；`--env prod` 还必须同一次调用带 `--yes-prod`。

## 边界

- `DeployPort` 目前只有 `local` 与 `ssh`（systemd + Caddy）。`k8s` 只有声明，
  调用即 fail-loud。
- `ssh` 适配器假定目标机有 root 权限、`rsync`、`flock`、`systemctl`。
- 反代块依赖名为 `access_log` 的 Caddy snippet；没有这个 snippet 的机器需要自己
  实现一个 `DeployPort`。
- 探针必须返回带 `commit` 字段的 JSON，且与本次构建一致。

## 设计约束

- 一个阶段只有一个产物和一个门。
- 内核不 import SSH、Kubernetes、Jira 或通知 SDK。
- 六环是 `RolePort` 的预设，不是内核。
- git 内容不能给生产发布授权。
- 失败必须响亮；不静默兜底，不把部分成功说成成功。

## 开发

```bash
PYTHONPATH=src python3 -m galadon --help
python3 -m unittest discover -s tests -v
node --test tests/test_plugin.mjs
```
