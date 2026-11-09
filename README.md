# cc-remote-windows

**在手机或浏览器中接续本机的 Claude Code、Codex 和 DeepSeek Harness 会话。**

[English](README_en.md) · 产品版本：v4.0.29 · protocol v81

同一原生会话 · 多设备与多会话 · 实时工具过程 · 协作房间 · Web / PWA / TUI

本分支整合了 Windows 适配、DSH 桌面接入与协作房间。模型登录、工具执行和原生历史保留在本机；cc-remote 提供远程控制、历史投影与界面。

[Windows 快速开始](#windows-快速开始) · [会话共享](#网页与本地如何共享会话) · [引擎配置](#引擎配置) · [部署与升级](#部署与升级) · [故障排查](#故障排查) · [开发与验证](#开发与验证)

![界面示例：多会话列表与工具过程](assets/readme-claude-multisession.jpg)

## 能做什么

- **接续本机任务**：查看回复、工具过程、文件改动，处理原生提问与审批，再回到官方客户端继续。
- **管理多个会话与设备**：按目录组织、搜索、固定与切换会话；切换视图不会停止后台任务。
- **查看一致的历史**：读取本机 transcript、rollout 或 DSH 原生日志，分页加载历史和详细过程。
- **使用原生能力**：按引擎提供模型选择、推理级别、计划、Goal、引导、排队、子代理与扩展。
- **组织协作房间**：用 `@成员` 派发任务、引用结果、转交消息，并查看成员的完整会话。
- **从终端访问**：TUI 支持 Claude/Codex 的 Code 与 Work；按 `Space e` 打开会话树，见 [TUI 使用指南](docs/tui_zh.md)。

能力取决于所选设备上的原生引擎、账号与版本。DSH 当前接入 Code；Claude/Codex 的 Work 使用独立项目与控制面。

## 网页与本地如何共享会话

“共享”指同一引擎在网页与官方本地客户端之间使用同一个原生会话；不同引擎保留各自的记录格式和会话 ID。

| 引擎 | 共享的身份与历史 | 控制方式 |
|---|---|---|
| Claude Code | 相同 UUID、原 cwd 与 transcript | 官方终端持有会话时网页只读镜像；终端退出或用户明确移交后，网页续接同一 UUID |
| Codex Code | 相同 thread ID、账号目录、官方 daemon 与 rollout | Wrapper 与普通官方终端连接同一 daemon；Codex App 使用共享入口并单独核对实际连接 |
| DSH | 网页 `dsh@<原生ID>` 对应桌面 Host 的同一会话 | 通过官方本机控制 API 操作，模型执行和日志仍由 DSH 管理 |

网页的 **“会话信息与本地续接”** 展示完整原生 ID、原 cwd、设备、账号与控制权，并提供适用的续接命令。手机上可从“更多设置”进入。

Claude 在原 cwd 使用 `claude --resume <UUID>`；Codex 使用 `codex resume <thread-ID>`；DSH 在已配对桌面端选择对应原生会话。控制权无法确认时保持只读，不通过结束未知进程来解锁。

## 链路与数据位置

```text
手机 / 浏览器 / PWA
        │ HTTPS + Cookie / WebSocket
        ▼
Relay + Web（VPS，前置 Caddy 或隧道）
        │ /ws，Wrapper 使用 Bearer 凭据连接
        ▼
本机 Wrapper
        ├─ Claude Code：日常官方 CLI + 固定版本 Agent SDK
        ├─ Codex：官方 app-server daemon / proxy
        └─ DSH：经过配对的回环控制 API

本机引擎 ── 自身的登录与配置 ── 模型服务
```

Relay 转发设备范围内的控制消息，不代理模型 API。原生日志留在设备上；浏览器投影与本地 SQLite 历史索引可重建。登录、设备授权和推送等控制状态需要随对应服务保留。

## Windows 快速开始

以下流程连接**已有 Relay**。尚未部署 Relay 时，先看 [部署与升级](#部署与升级)。

### 1. 准备环境

| 依赖 | 要求与用途 |
|---|---|
| Python | 推荐标准或 uv 管理的 Python 3.13；部署运行时见 [python-version.txt](deploy/python-version.txt) |
| Git | 获取源码 |
| Node.js | 构建 Web 时使用 Node 24，见 [.nvmrc](.nvmrc)；仅运行 Wrapper 无需构建 Web |
| Claude Code | 已登录的日常官方 CLI，版本 `>=2.1.263` |
| Codex | 已登录的官方 CLI，CLI 与 daemon 版本一致；已验收基线为 `0.166.0` |
| DSH（可选） | 桌面版 `0.2.1-rc.2` / `0.2.1-rc.5`，配对步骤见下文 |

```powershell
git clone https://github.com/123NIU-YEYE/cc-remote-windows.git
Set-Location cc-remote-windows
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-wrapper.lock
Copy-Item .env.example .env
```

已有 `.env` 时保留原文件。使用仓库自己的 `.venv`，不要把便携 Python 的 `.pydeps` 或旧环境当作当前安装入口。

### 2. 配置并登记设备

编辑本机 `.env`，使用 UTF-8 无 BOM。下面是**通过设备中心配对**时的示例，CLI 路径需替换为实际绝对路径：

```ini
# 留空时，从私有设备凭据文件读取这三项
RELAY_URL=
WRAPPER_TOKEN=
CC_REMOTE_MACHINE_ID=

CLAUDE_BIN=C:/path/to/claude.exe
CODEX_BIN=C:/path/to/codex.exe
CC_REMOTE_CODEX_DAEMON=auto

# 可选：默认工作目录，必须已经存在
# CC_CWD=C:/projects/workspace
```

在 Relay 网页的设备中心启用添加设备，取得一次性配对码，然后运行：

```powershell
.\.venv\Scripts\python.exe -m cc_remote.device pair https://relay.example.com XXXXX-XXXXX-XXXXX-XXXXX --name "Windows PC"
```

凭据保存在用户目录的私有设备文件中。非空环境变量优先于该文件：使用配对方式时，也要移除启动环境中遗留的 `RELAY_URL`、`WRAPPER_TOKEN` 和 `CC_REMOTE_MACHINE_ID` 覆盖值。

已有手工安装可继续显式配置这三项，其中 `RELAY_URL` 必须以 `/ws` 结尾，设备 ID 与凭据须匹配 Relay 授权。配对码、长期 Token 和真实部署配置不入库。

### 3. 启动与检查

```powershell
.\scripts\run-wrapper.ps1
# 也可双击 start-cc-remote.cmd
```

启动器检查 `.venv` 与 `.env`；存在私有 `.runtime/active-wrapper.json` 时，使用其中的冻结源码版本，否则使用当前检出。DSH 桌面状态更新后，启动器会尝试刷新配对。

在网页选择设备与引擎，核对原生 ID、cwd、控制权和历史。仅有 `/healthz` 正常或能列出模型，不能代替实际会话验收。

## 引擎配置

### Claude Code

Wrapper 使用日常官方 `claude`，通过 `CLAUDE_BIN` 指定真实绝对路径，并使用锁定的 `claude-agent-sdk==0.2.157`。启动前检查 CLI 与 SDK 版本。

模型账号和服务地址使用原生配置。当前 Claude SDK 子进程策略会清空继承的代理变量；仅修改 Wrapper 的 `HTTPS_PROXY` 不会改变这条模型线路。模型服务必须经代理访问时，应先核对 [child_env.py](cc_remote/wrapper/child_env.py) 的现行策略与原生配置。

原生终端拥有 transcript 时遵守单写入者保护。不要用 fork、新 cwd 或复制日志冒充续接。

### Codex 与 Codex App

常规安装使用 `CC_REMOTE_CODEX_DAEMON=auto`，让普通 CLI 与 Wrapper 使用同一账号的官方 daemon。显式 `off` 使用私有 stdio，不能算作共享验收。已有受控回环 WebSocket 安装可配置 `CODEX_APP_SERVER_WS_URL`；不接受非回环地址。

```powershell
codex app-server daemon version
codex resume <thread-ID>
```

检查实际 daemon、账号目录和 CLI 版本。后台升级后通过原安装渠道对齐版本，保留正在运行的任务。

需要桌面 App 共享时，先完成 App 任务并正常退出，再使用独立入口：

```powershell
.\scripts\start-codex-shared-app.ps1
.\scripts\start-codex-shared-app.ps1 -Action check
```

返回 `verified` 才表示 App 的实际共享连接通过。入口不修改官方程序或默认账号，也不会强制结束现有 App。升级后重新核对，详见 [Windows App 共享验收](docs/codex-desktop-windows.md)。

### DSH 桌面配对

在桌面 profile 的 patch 配置中加载两个扩展，路径指向实际检出或活动发布目录：

```yaml
- insert:
    - id: cc-remote-desktop-url
      name: C:/path/to/cc-remote-windows/integrations/dsh/cc-remote-desktop-url.mjs
    - id: cc-remote-metadata
      name: C:/path/to/cc-remote-windows/integrations/dsh/cc-remote.mjs
```

默认 patch 文件位于 `%USERPROFILE%\.dsh\profiles\desktop\cordis.patch.yml`；自定义 `DSH_HOME` 或 profile 时按原生配置定位。完成现有任务后正常重启 DSH，再配对：

```powershell
.\scripts\pair-desktop.ps1
```

扩展导出私有连接状态，配对工具核对进程与原生契约并保存控制凭据。端口或凭据轮换后，等任务结束再刷新配对、重启 Wrapper，保留桌面 Agent 的正常生命周期。

不要把 Token、连接文件或带 Token 的登录地址粘贴进 Git、聊天或浏览器参数。能力与版本边界见 [DSH 集成说明](docs/dsh-integration.md)。

## 协作房间

房间绑定一个目录与若干成员。使用 `@成员`、`@all` / `@全体` 派发任务，可引用消息、单条转交、选择成员模型并查看完整过程。

**成员各有自己的原生会话。** 房间通过受限的最近上下文与显式引用传递信息。当前房间适配器中的 DSH 成员需绑定已有 `dsh@<原生ID>`；这不限制 DSH 普通聊天页的新建会话能力。

默认附带最近 6 条记录，字符预算 6000，由 `CC_REMOTE_ROOM_CONTEXT_DEPTH` 和 `CC_REMOTE_ROOM_CONTEXT_BUDGET` 调整。成员工具流在完整会话中查看；自动顾问/执行流转尚未提供。详见 [房间设计](docs/collaboration-rooms.md) 与 [优化路线](docs/collaboration-rooms-optimization.md)。

## 部署与升级

统一遵循 [deploy/README.md](deploy/README.md)，安装步骤见 [安装指南](docs/installation.md) / [Installation](docs/installation_en.md)。Windows Wrapper 使用本页源码入口；Linux/macOS 与上游 Release 包按对应文档操作，并核对是否包含本分支功能。

Linux Relay 的不可变版本位于 `/opt/cc-remote/releases/`，通过 `/opt/cc-remote/current` 原子激活。为每个 release 创建自己的环境，不复制仍指向旧解释器的 venv；保留外部配置、私有状态与完整回滚集合。

Relay、Web 和所有 Wrapper 使用同一提交的源码快照或协调产物。相同产品版本号不保证功能相同；协议变化时必须协调更新三层。

```text
检查协议与构建 → 暂存所有目标 → 等待任务与队列排空
→ 保存状态快照 → 原子激活 → 验证公网、实际会话与共享连接
```

VPS + Caddy 适合固定 HTTPS 入口。Cloudflare 快速隧道适合临时验证，重启可能更换地址；长期使用按部署文档管理域名、来源与凭据。浏览器来源须与 `PUBLIC_ORIGIN` 精确匹配。

## 自助修改登录密码

1. 登录控制台，打开右上角 **更多设置 → 修改密码**。
2. 输入当前密码，再输入并确认 12 至 256 个字符的新密码，点击 **保存新密码**。
3. 用新密码重新登录。改密会撤销该账号的旧浏览器会话，同一个浏览器重新登录后仍按原来的有效期保留登录。

网页改密即时生效，中继会在设备授权数据库里保存加盐哈希，重启后继续有效。多账号部署中，每个账号只能修改自己的密码。

忘记当前密码时，用管理员账号 SSH 登录中继，在服务配置里把 `LOGIN_PASSWORD` 改成一个不同于原配置值的新密码。使用 `LOGIN_USERS_JSON` 的部署则修改对应账号的 `password`。重启中继后，新的配置密码会取代该账号的网页改密记录。配置路径和重启方式见 [部署文档](deploy/README.md)。

## 故障排查

先区分**控制链路**（浏览器 ⇄ Relay ⇄ Wrapper）与**模型链路**（本机引擎 ⇄ 模型服务）。启动器输出到当前终端，日志文件位置由实际服务或启动方式决定。

| 现象 | 检查与处理 |
|---|---|
| `4406 protocol upgrade required` | 对照 Relay `/healthz`、Web 构建和 Wrapper 的 protocol，协调更新三层 |
| WebSocket 401/403 | 核对登录、设备授权和精确 Origin；Relay 重启后重新登录，禁止把 Token 放进 URL |
| 建立连接后立刻断开 | 查看双方关闭原因与协议校验；自写客户端不要发送未定义字段 |
| `Connection dropped` / `ECONNRESET` | 确定是哪条链路；原生引擎也失败时检查模型地址、端口、直连/代理与 `NO_PROXY` |
| 只读或“归属不可确认” | 保留任务，检查原生进程可见性、私有文件 ACL 与依赖；检查不完整时不会解除保护 |
| `already has an active writer` | 核对原生 ID、账号、cwd 与客户端；等任务完成后正常退出或明确移交，避免强杀未知进程 |
| 配置修改没有生效 | 检查 BOM、环境变量覆盖、激活源码目录与私有设备配置 |
| 历史缺失或重复 | 核对原生 ID、原 cwd、账号与原始日志，再检查历史索引和 SQLite 日志；保留原始记录 |
| DSH 配对失效 | 确认桌面进程与扩展，完成任务后重新配对并重启 Wrapper |

读取历史不会创建模型回合。真实模型验收单独发起；结果不明确时先核对原生回执，避免重复发送已接受的请求。

## 支持边界与安全

- Windows 私有文件校验使用实际所有者与 DACL；Codex 占用检测核对进程代际、文件身份和写权限，检查不完整时保持只读。
- Windows 独立 Viewer 资源通道尚未作为完整平台能力验收，与普通文件预览分属不同路径；可设 `CC_REMOTE_VIEWER_CHANNEL=off`，见 [Viewer 边界](docs/remote-viewer.md)。
- DSH 当前没有 Work、会话删除、worktree 迁移和回滚适配；界面按原生能力呈现。
- `.env`、私有设备/配对文件、账号目录和 `.runtime` 不入库。浏览器使用 Cookie，Wrapper 使用升级请求中的 Bearer 凭据；设备授权按 `machine_id` 隔离。
- 公网使用 HTTPS/WSS。显式明文 HTTP 会暴露口令、Cookie 与会话流量，须按部署文档单独配置。

## 开发与验证

使用 Python 3.13、Node 24 与仓库依赖锁。开发环境安装完整依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements.lock
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
npm --prefix web ci
.\.venv\Scripts\python.exe -m pytest
npm --prefix web run build
npm --prefix web run test:reliability
npm --prefix web run lint
```

完整门禁（含 Ruff、ShellCheck 与 Bash 检查）见 [AGENTS.md](AGENTS.md#commit-and-pr-gate)。GitHub CI 在 PR 和 `main` / `master` 推送时执行 Web 构建及 Windows/Linux pytest。单元测试不调用模型；[live probes](scripts/live/) 单独运行，可能消耗模型用量。

2026-11-05 的功能验收基线 `dec6bbb`：Windows 5685 项、Linux 6710 项通过，平台条件跳过分别为 594、14 项。已核对三引擎原生历史、Codex App 共享传输、公网实时回复与重连，以及桌面/移动端会话信息。此记录说明已验证范围，不替代新部署的实际验收。

## 文档导航

| 主题 | 入口 |
|---|---|
| 安装、升级、回滚与验收 | [安装指南](docs/installation.md) · [部署合同](deploy/README.md) |
| Windows Codex App | [共享入口与验收](docs/codex-desktop-windows.md) |
| DSH 原生能力 | [集成说明](docs/dsh-integration.md) |
| 终端工作区 | [TUI 中文指南](docs/tui_zh.md) · [English](docs/tui.md) |
| 协作房间 | [设计](docs/collaboration-rooms.md) · [优化路线](docs/collaboration-rooms-optimization.md) |
| Claude 独立会话服务 | [生命周期与迁移](docs/claude-session-service.md) |
| 上游产品说明 | [README.upstream.md](README.upstream.md) |
| 更新记录与许可文本 | [CHANGELOG.md](CHANGELOG.md) · [LICENSE](LICENSE) |
