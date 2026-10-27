# cc-remote-windows

**cc-remote 的 Windows 移植分支。** 在本机跑一个 Wrapper，把 **DeepSeek Harness 桌面版 / Codex / Claude Code** 的会话、工具过程和文件改动带到手机或任意浏览器的 cc-remote 界面里。

> 上游项目与完整产品文档见 [`README.upstream.md`](README.upstream.md) 和 [`docs/`](docs/)。
> 本文件只讲**这个分支做了什么、Windows 上怎么跑起来**。

| | |
|---|---|
| 产品版本 | `4.0.29` |
| Wire protocol | **81** |
| Wrapper 支持平台 | Windows 10/11 · Linux · macOS |
| 引擎 | DSH（桌面版）· Codex CLI · Claude Code |
| 中继 | 自建 VPS（Caddy）或 Cloudflare 隧道 |

---

## 1. 链路长什么样

```
手机 / 浏览器
     │  HTTPS（或经 Cloudflare 隧道的 HTTPS）
     ▼
中继 relay（VPS: Caddy → 127.0.0.1:8765，或本机 + 隧道）
     │  WebSocket /ws，Bearer WRAPPER_TOKEN
     ▼
本机 Wrapper（Windows）
     ├── DSH 桌面版    http://127.0.0.1:<端口>?token=<43字符>
     ├── Codex CLI     app-server --stdio
     └── Claude Code   stream-json 子进程
```

要点：

- **中继只负责转发**，不碰模型 API。模型登录、API Key、网关地址全部留在本机。
- **Wrapper 与中继必须同协议版本**（当前 v81）。跨版本会在握手后被 `4406 protocol upgrade required` 踢掉。
- 中继可以部署在 VPS，也可以用 Cloudflare 隧道从本机暴露，见 [§5](#5-中继部署)。

---

## 2. Windows 上跑起来

### 2.1 前置

| 依赖 | 说明 |
|---|---|
| Python 3.12 | 本分支验证用的是便携版。它带 `python312._pth`，会禁用 `PYTHONPATH` 和 site 发现，所以依赖装在仓库内的 `.pydeps`，用 `site.addsitedir` 加载 |
| Node.js + npm | 只有需要自己构建前端时才要（`web/`） |
| Git | 拉代码 |
| 引擎可执行文件 | `codex.exe` / `claude.exe` 的真实路径，见 [§3](#3-引擎配置) |

### 2.2 装依赖

```powershell
cd <仓库目录>
python -m pip install --target .pydeps -r requirements-wrapper.txt
```

> `.pydeps` 约 280 MB，已在 `.gitignore` 中排除。

### 2.3 准备 `.env`

从 [`.env.example`](.env.example) 复制一份 `.env`（**`.env` 不入库**）。Windows 上至少要设这几项：

```ini
# ── 中继 ─────────────────────────────────────────────
RELAY_URL=wss://<你的中继域名>/ws
WRAPPER_TOKEN=<64 位十六进制，必须和中继端一致>
CC_REMOTE_MACHINE_ID=windows-local

# ── 引擎可执行文件（绝对路径）────────────────────────
CODEX_BIN=C:\...\@openai\codex\...\bin\codex.exe
CLAUDE_BIN=C:\...\@anthropic-ai\claude-code\bin\claude.exe

# ── Codex 共享守护进程 ───────────────────────────────
# 共享 daemon 依赖 Unix socket，Windows 上走 stdio 回退
CC_REMOTE_CODEX_DAEMON=off

# ── DSH 桌面版配对文件（可选，配了才启用 DSH 引擎）───
CC_REMOTE_DSH_CONNECTION_FILE=C:\Users\<你>\.config\cc-remote\dsh-connection.json
```

⚠️ **`.env` 必须是 UTF-8 无 BOM、LF 换行。** 带 BOM 时第一行的键会变成 `\ufeffRELAY_URL`，dotenv 认不出来，于是静默回落到默认值 —— 表现为「改了配置却不生效」。

### 2.4 启动

```powershell
# 启动 Wrapper（首次会自动配对）
.\scripts\run-wrapper.ps1

# 只做 DSH 桌面版配对
.\scripts\pair-desktop.ps1
```

`run-wrapper.ps1` 会：

1. 检查 `.pydeps` 与 `.env` 是否存在；
2. 若桌面版导出的状态文件比配对文件新，**自动重新配对**（桌面版每次启动都会换端口和 token）；
3. 启动 `python -m cc_remote.wrapper`。

开机自启：把 `scripts\run-wrapper.ps1` 的快捷方式放进
`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup`。

### 2.5 代理策略（Windows 特有，已内置）

部分外部域名在本机直连会被 TLS 重置，只有本地 Clash（`127.0.0.1:7897`）能通。因此启动脚本对**两类子进程用相反的代理策略**：

| 谁 | 代理 | 原因 |
|---|---|---|
| Wrapper 自身（连中继） | **走代理** `HTTPS_PROXY=http://127.0.0.1:7897` | 直连 Cloudflare 隧道域名会被重置 |
| Claude 子进程 | **强制直连**（代理变量置空，`NO_PROXY` 含网关域名） | Claude Code 用的是自建网关，直连可达、走代理超时 |

这两条写在 `scripts/run-wrapper.ps1` 和 `cc_remote/wrapper/child_env.py` 里，无需手工设置。若你的网络环境不同，改这两处即可。

---

## 3. 引擎配置

### 3.1 DSH 桌面版（推荐）

DSH 桌面版的 API 只绑回环，且 **launch token 每次启动新生成、只存在进程内存里**，外部拿不到。所以需要一个小扩展把 `{pid, port, origin, token}` 导出成私有文件。

**一次性配置**：在桌面版 profile 的 patch 层里加两条插件
（`%USERPROFILE%\.dsh\profiles\desktop\cordis.patch.yml`）：

```yaml
- insert:
    - id: cc-remote-desktop-url
      name: <仓库绝对路径>/integrations/dsh/cc-remote-desktop-url.mjs
    - id: cc-remote-metadata
      name: <仓库绝对路径>/integrations/dsh/cc-remote.mjs
```

- `cc-remote-desktop-url` —— 启动后把 `{schema,pid,port,origin,token}` 原子写入
  `%USERPROFILE%\.dsh\cc-remote-desktop.json`（权限 0600）。全程 try/catch，
  **导出失败也不会影响 App 启动**。
- `cc-remote-metadata` —— cc-remote 需要的只读元数据端点。

**配对**：

```powershell
.\scripts\pair-desktop.ps1
```

它读取上面的状态文件、校验 pid 存活，再对着活着的 DSH API 做只读校验
（`agentPresets/list`、`session/modelCatalog`、`list_sessions`、`draft_catalog`），然后写配对文件。
桌面版换端口/换 token 后配对自动失效，`run-wrapper.ps1` 会重新配对。

### 3.2 Codex

- `CODEX_BIN` 指向**真实可执行文件**（不是 `.cmd` 包装器）。
- `CC_REMOTE_CODEX_DAEMON=off`：共享 daemon 走 Unix socket，Windows 上不可用；`off` 让 Wrapper 走 stdio 回退，功能等价。
- 一个 thread **同时只能有一个写入者**。若同一会话在官方 Codex App 里开着，cc-remote 会得到
  `already has an active writer`，只能看不能写 —— 关掉那一边即可。

### 3.3 Claude Code

两个前提：

1. **`claude-agent-sdk` 必须是 `0.2.157`**。仓库对它做严格相等校验，版本不符会在 preflight 直接拒绝整个引擎。
   PyPI 上 `0.2.157` 只有源码包，便携版 Python 构建不了（缺 hatchling 后端）。
   解法是直接从 sdist 里取出纯 Python 包目录复制进 `.pydeps`：

   ```powershell
   python -m pip download --no-deps --no-binary :all: claude-agent-sdk==0.2.157 -d .dl
   # 解包 .dl/*.tar.gz，把 src/claude_agent_sdk/ 整个目录复制到 .pydeps/claude_agent_sdk/
   ```

2. **`mcp==1.28.1`**。更新的 `mcp` 移除了 `Server.list_tools`，启动时会
   `AttributeError: 'Server' object has no attribute 'list_tools'`。

**关于网关**：Claude Code 读 `~/.claude/settings.json` 里的 `env`，其**优先级高于**父进程环境变量：

```json
{ "env": { "ANTHROPIC_AUTH_TOKEN": "…", "ANTHROPIC_BASE_URL": "https://<你的网关>" } }
```

这个网关通常**只在内网/直连可达**。因此 Wrapper 给 Claude 子进程显式**关掉代理**
（`HTTPS_PROXY=""`、`NO_PROXY` 含网关域名），`ANTHROPIC_BASE_URL` 留给 settings.json 决定。

---

## 4. 服务器端（中继）

中继跑在能对外提供 HTTPS 的机器上。核心是让 `/healthz` 返回**与 Wrapper 相同的 protocol**：

```bash
curl -s http://127.0.0.1:8765/healthz
# {"ok":true,"protocol":81,"version":"4.0.29","wrapper_connected":false,"machines":[]}
```

升级中继（新 release 目录，保留旧版本）：

```bash
sudo mkdir -p /opt/cc-remote/releases/release-<ts>.v81
sudo tar -xzf ccremote.tar.gz -C /opt/cc-remote/releases/release-<ts>.v81
sudo cp -a /opt/cc-remote/current/.venv /opt/cc-remote/releases/release-<ts>.v81/.venv
# 复制来的 venv 里 bin/pip 仍指向旧解释器，必须用 python -m pip 安装依赖
sudo /opt/cc-remote/releases/release-<ts>.v81/.venv/bin/python -m pip install -r requirements-relay.txt
sudo ln -sfn /opt/cc-remote/releases/release-<ts>.v81 /opt/cc-remote/current
sudo systemctl restart cc-remote-relay
```

服务端 `.env` 关键项：

```ini
RELAY_HOST=127.0.0.1
RELAY_PORT=8765
PUBLIC_ORIGIN=https://<你的域名>      # 浏览器实际访问的 origin，必须精确匹配
LOGIN_PASSWORD=<≥16 位强口令>
SESSION_SECRET=<openssl rand -hex 32>
WRAPPER_TOKEN=<与 Wrapper 一致>
WEB_STATIC_DIR=/opt/cc-remote/current/web/dist
ALLOW_INSECURE_HTTP=1                 # 仅明文 HTTP 部署时为 1
```

> 前端 `web/dist` 必须在**能构建的机器**上打好再上传。protocol 变了必须重建前端；
> 产物里的 `cc-remote-build.json` 会写明 `{"version":…,"protocol":81}`。

---

## 5. 中继部署

### 方案 A：VPS + Caddy（推荐，域名固定）

见 [§4](#4-服务器端中继)。Caddy 占 80/443 反代到 `127.0.0.1:8765`，relay 只绑回环。
记得在云控制台的**安全组**放行 80/443 —— 默认往往只放行 22。

### 方案 B：本机 relay + Cloudflare 隧道（无 VPS）

```powershell
cloudflared tunnel --url http://127.0.0.1:8765
# → https://<随机名>.trycloudflare.com
```

然后把中继的 `PUBLIC_ORIGIN` 改成该地址并重启 relay。

⚠️ 快速隧道**每次重启换域名**，且没有可用性保证。域名变了 `PUBLIC_ORIGIN` 不跟着变，
浏览器 origin 校验就会失败。长期使用请用**命名隧道 + 自有域名**。

---

## 6. Windows 适配做了什么

| 层 | 问题 | 处理 |
|---|---|---|
| `os_compat.py` | 缺少 `getuid`、`O_NOFOLLOW`、`fchmod`、`pread/pwrite` | 一次性 shim，语义对齐 Unix |
| | `st_mode` 恒为 `0o666`，导致所有「私有文件」校验误杀（真正访问控制靠 ACL） | `S_IMODE` 只暴露 owner 位 |
| | 目录 `open()` + `fsync`（POSIX 专有） | 目录 fsync 跳过；「为 flush rename 而打开目录」返回无害句柄 |
| | 没有 `*at()` 家族，`dir_fd=` 直接 `NotImplementedError` | 记录每个目录描述符对应的真实路径，把 `open/stat/unlink/mkdir/rename/replace` 的 `dir_fd` 翻译成路径拼接 |
| | `terminate()` 只杀直接子进程，Codex 的 node 孙进程残留并持有 thread 锁 | `kill_process_tree()`（`taskkill /F /T`） |
| `file_lock_compat.py` | 没有 `fcntl.flock` | 用 `msvcrt.locking` 实现 |
| `resource_compat.py` | 没有 `resource` 模块 | rlimit 空实现 |
| `session.py` | cwd 带盘符与反斜杠，`pathlib` 把状态目录整个丢弃 | cwd 净化为扁平安全文件名 |
| `history_store.py` | 时间戳单位不一（秒/毫秒/微秒/纳秒），乘 1000 后溢出 SQLite | 按量级归一 + 夹取到有符号 64 位 |
| | Windows 的 `st_dev` 是 64 位**无符号**卷序列号，超过 SQLite 上限 | 指纹字段折叠进 64 位 |
| `process_scan.py` / `claude_external.py` | 进程归属扫描只实现了 macOS 和 Linux，Windows 永远「扫描不完整」，于是**所有 Claude 会话被判只读** | 新增 psutil 版 Windows 快照 + cwd 读取，并正确排除 Wrapper 自己的子进程 |
| `machine.py` | Claude PTY broker 依赖 Unix socket | 改为可选导入；但**转录解析函数保持可用**（否则 Claude 回合推不动） |

`dir_fd`、进程树、`S_IMODE` 这三处踩坑最深：它们不报业务错误，只表现为「会话只读」「回合卡住」「历史对不上」。

---

## 7. 故障排查

日志在 `wrapper.log.err`（JSON Lines，每行一条）。

| 现象 / 日志 | 含义 | 处理 |
|---|---|---|
| `4406 protocol upgrade required` | Wrapper 与中继协议版本不一致 | 升级中继到同版本；`/healthz` 看 `protocol` |
| `already has an active writer` | 同一 thread 被另一个进程（官方 App / 上一轮残留）持有 | 关掉那一边；清掉残留 `codex.exe` |
| `did not receive a valid HTTP response` | 到中继的链路被中途掐断 | 确认该走代理的走了代理；用 `Test-NetConnection` 对比其他站点 |
| `API Error: Connection dropped (ECONNRESET)` | 引擎连不上自己的 API / 网关 | Claude：确认**没有**把代理变量传给它；确认 settings.json 的网关直连可达 |
| `dir_fd unavailable on this platform` | 有 `dir_fd=` 调用绕过了兼容层 | 检查该描述符是否由 `os.open` 打开 |
| `Codex control store is not a private bounded file` | `S_IMODE` shim 未生效 | 确认 `import cc_remote` 已装 shim |
| 界面「外部 CLI 控制中 · Web 只读」 | 进程归属扫描判定外部占用 | Windows 上需 `psutil`；`Claude process owner scan incomplete` 是同一问题的前兆 |
| 改了 `.env` 不生效 | UTF-8 BOM | 重写为无 BOM、LF |
| 历史对不上 / 条目重复 | 历史索引读写失败（SQLite 溢出） | 看是否还有 `history index ... failed` |

**判断「请求到底有没有发出去」**：在引擎侧看进程有没有连到上游，或临时加一条回环中转打印请求行。
例如排查 Claude 时，中转日志里只要出现 `POST /v1/messages -> 401` 就说明链路已通。

---

## 8. 安全

- **`.env` 绝不入库**（已在 `.gitignore`）。里面有 `WRAPPER_TOKEN`、`LOGIN_PASSWORD`、`SESSION_SECRET`。
- `LOGIN_PASSWORD` 至少 16 位；`SESSION_SECRET` / `WRAPPER_TOKEN` 用 `openssl rand -hex 32` 生成。
- 中继的 `PUBLIC_ORIGIN` 必须与浏览器实际访问的 origin **完全一致**，否则 cookie 鉴权的 WebSocket 会被拒。
- 明文 HTTP 部署（`ALLOW_INSECURE_HTTP=1`）时登录口令和全部流量都是明文。能上 HTTPS 就上。
- 这个界面可以驱动本机 agent 执行命令。暴露到公网前请确认你接受该风险范围。

---

## 9. 已知限制

| 项 | 状态 |
|---|---|
| **Viewer（文件预览）** | Windows 上未移植。依赖 Unix socket 与目录 `dir_fd` 语义，需单独实现。日志会持续出现 `Viewer channel unavailable`，**不影响对话** |
| **Claude PTY broker** | 仅 Unix（`experimental_claude_broker`，默认关闭） |
| **Codex 共享 daemon** | 仅 Unix；Windows 用 stdio 回退（`CC_REMOTE_CODEX_DAEMON=off`） |
| **自建网关 + 代理** | 若你的 Claude 网关需要走代理才通，需改 `child_env.py` 的代理策略 |
| **UAC / 服务安装** | 非管理员账户下无法安装 Clash 服务或开启 TUN |

---

## 10. 目录速览

```
cc_remote/
  relay/                  中继（FastAPI）：/ws 双角色、鉴权、静态前端
  wrapper/
    machine.py            核心：会话池、回合消费、控制面
    os_compat.py          ★ Windows 平台 shim（dir_fd 模拟、进程树回收等）
    file_lock_compat.py   ★ flock 的 msvcrt 实现
    resource_compat.py    ★ rlimit 空实现
    child_env.py          引擎子进程环境（代理策略、账号隔离）
    codex_*.py            Codex：handle / daemon / models / sessions
    claude_external.py    Claude 进程归属扫描（含 Windows 快照）
    dsh_desktop.py        ★ DSH 桌面版配对文件校验
    dsh_pair.py           配对 CLI（含 --desktop）
  protocol.py             Wire protocol（v81）
integrations/dsh/
  cc-remote.mjs           只读元数据端点
  cc-remote-desktop-url.mjs  ★ 导出桌面版 origin + token
scripts/
  run-wrapper.ps1         ★ Windows 启动器（自动重配对 + 代理策略）
  pair-desktop.ps1        ★ 桌面版配对
web/                      前端（Vue + Vite）
docs/                     上游文档
deploy/                   中继部署脚本与 systemd / Caddy 模板
```

★ = 本分支新增或重写。

---

## License

见 [LICENSE](LICENSE)。本分支为上游 cc-remote 的 Windows 移植，遵循同一许可。
