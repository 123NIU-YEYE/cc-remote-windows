# Windows 入门：先在本机使用 Claude Code

[English](windows-quickstart_en.md) · [返回首页](../README.md) · [Windows 适配范围](windows-upstream-migration.md)

本指南适用于第一次从本仓库源码安装的用户。先让同一台 Windows 电脑上的浏览器、Relay 和 Wrapper 连通，再考虑手机和公网访问。它不会安装系统服务，也不会自动启用 Work。

**已有正在运行的安装时，请使用原安装的升级流程。** 不要重复创建配置或启动第二个 Wrapper。

## 1. 准备环境

在 Windows 的 **PowerShell** 中操作。需要：

- [Git for Windows](https://git-scm.com/downloads/win)。
- [Python 3.13](https://www.python.org/downloads/)，安装时启用 PATH。
- [Node.js 24](https://nodejs.org/en/download)。
- 已安装、登录，并能在本机正常对话的 [Claude Code](https://code.claude.com/docs/en/setup)。最低 CLI 版本为 `2.1.263`，建议使用当前稳定版。

安装后重新打开 PowerShell，检查：

```powershell
git --version
python --version
node --version
npm.cmd --version
claude --version
```

确认 Python 显示 `3.13.x`、Node 显示 `v24.x`。先在另一个终端运行 `claude`，确认能正常回复，再继续。

## 2. 下载与安装

在你希望保存项目的目录执行：

```powershell
git clone https://github.com/123NIU-YEYE/cc-remote-windows.git
Set-Location cc-remote-windows

python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes --only-binary=:all: --no-binary=http-ece -r requirements.lock

npm.cmd --prefix web ci
npm.cmd --prefix web run build
```

每条命令成功后再执行下一条。使用 `npm.cmd`，无需修改 PowerShell 的脚本执行策略。依赖从锁文件安装；`http-ece` 需要从源码包构建，其余依赖优先使用二进制包。

## 3. 生成本机配置

在同一个项目目录执行以下完整代码块。它为本地体验生成独立的网页登录口令、签名密钥和 Wrapper 凭据，**遇到已有 `.env` 会停止，不会覆盖它**。

```powershell
if (Test-Path -LiteralPath .env) {
    throw '.env already exists. Edit the existing configuration instead of replacing it.'
}

$projectPath = (Get-Location).Path.Replace('\', '/')
$loginPassword = & .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(24))"
if ($LASTEXITCODE -ne 0) { throw 'Could not generate login password.' }
$sessionSecret = & .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
if ($LASTEXITCODE -ne 0) { throw 'Could not generate session secret.' }
$wrapperToken = & .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
if ($LASTEXITCODE -ne 0) { throw 'Could not generate wrapper token.' }

$configuration = @"
RELAY_HOST=127.0.0.1
RELAY_PORT=8765
PUBLIC_ORIGIN=http://127.0.0.1:8765
RELAY_URL=ws://127.0.0.1:8765/ws
LOGIN_PASSWORD=$loginPassword
SESSION_SECRET=$sessionSecret
WRAPPER_TOKEN=$wrapperToken
WEB_STATIC_DIR=web/dist
CC_REMOTE_MACHINE_ID=windows-pc
CC_CWD="$projectPath"
CLAUDE_BIN=
ALLOW_PRIVATE_ORIGINS=0
ALLOW_INSECURE_HTTP=0
"@
[System.IO.File]::WriteAllText((Join-Path (Get-Location).Path '.env'), $configuration, [System.Text.UTF8Encoding]::new($false))

Write-Host "Browser login password: $loginPassword"
```

保存终端最后显示的 **Browser login password**，它就是稍后网页登录的口令。不要把配置文件、密钥或口令截图上传到仓库。

`CC_CWD` 默认指向当前项目目录；新建 Code 会话时也可以选择自己的项目。`CLAUDE_BIN` 留空使用当前用户目录中的 `.local/bin/claude.exe`。如果你的 CLI 安装在别处，编辑 `.env`，将其改为实际可执行文件的完整路径，例如 `CLAUDE_BIN=C:/Tools/Claude/claude.exe`。

模型账号仍由本机 Claude 管理。这里的网页登录口令与模型 API Key 是两回事，不要互相替代。

## 4. 启动 Relay 和 Wrapper

在项目目录中打开两个 PowerShell 终端，分别运行以下命令，并保持两个终端开启。

**终端一：运行网页和中继**

```powershell
.\.venv\Scripts\python.exe -X utf8 -m cc_remote.relay
```

**终端二：连接本机 Claude**

```powershell
.\.venv\Scripts\python.exe -X utf8 -m cc_remote.wrapper
```

这两个命令必须从包含 `.env` 的项目目录运行。`-X utf8` 用于避免中文 Windows 的编码差异，不改变系统设置。

## 5. 在浏览器里发送第一条消息

1. 在这台电脑打开 [http://127.0.0.1:8765](http://127.0.0.1:8765)。
2. 使用第 3 步生成的口令登录。
3. 确认设备已连接，选择 **Claude → Code**。
4. 新建会话，发送一条简单消息，确认能够收到回复。这一步会使用你的模型额度。
5. 通过附件按钮添加图片或文件；通过「会话文件」浏览当前目录。

`127.0.0.1` 只指向正在使用的这台设备。手机打开这个地址不会连接到你的电脑。

## 6. 需要手机或公网访问

先按 [源码部署指南](installation.md#source-install) 在 Linux 服务器部署 Relay 和同一版本的网页，并为其配置 HTTPS。然后让 Windows Wrapper 连接该服务。

已有本地配置时，结束当前任务和排队消息，再停止本地测试进程，按实际部署修改：

| 配置项 | 公网部署的含义 |
|---|---|
| Relay 的 `PUBLIC_ORIGIN` | 浏览器实际访问的 HTTPS 地址，例如 `https://remote.example.com` |
| Wrapper 的 `RELAY_URL` | 对应 WebSocket 地址，例如 `wss://remote.example.com/ws` |
| Wrapper 的设备凭据 | 在网页设备中心配对生成；旧式手工配置须与 Relay 授权一致 |

以上域名只是格式示例，必须替换为自己的入口。**不要把本地生成的 `.env` 整份上传到服务器，也不要直接覆盖已有生产配置。** 设备配对见 [安装指南](installation.md#source-install)。

改用设备中心配对时，删除 Windows 本地测试 `.env` 中的 `WRAPPER_TOKEN` 和 `CC_REMOTE_MACHINE_ID` 两行，并检查终端环境中没有旧值，让 Wrapper 使用配对生成的凭据。仅留空仍会覆盖配对值；不要把本地测试 token 当成服务器的设备凭据。

固定域名是常见方式；使用公网 IP 的 HTTPS 需要证书覆盖该 IP。临时隧道的地址可能变化，不能将旧地址当成永久入口。源码模式的启动与托管部署的服务安装是不同步骤，后台常驻、升级与回滚遵循 [部署流程](../deploy/README.md)。

## 常见问题

<a id="claude-work"></a>

### 为什么 Claude Code 可以用，Claude Work 却创建失败

Claude Work 必须启动系统沙箱。Claude 官方目前支持 macOS、Linux 和 WSL2 的沙箱，原生 Windows 不支持；启动日志可能出现 `Sandbox required but unavailable`。

请在现有 Windows Wrapper 上选择 **Code**。需要 Work 时，在受支持的 macOS、Linux 或 WSL2 环境中单独安装、配置并运行 Wrapper；仅打开一个 WSL 终端不会改变已经运行在 Windows 上的 Wrapper。不要通过关闭 Work 的沙箱要求来处理这个错误。

参考：[Claude 官方沙箱说明](https://code.claude.com/docs/en/sandboxing)。

<a id="connection-troubleshooting"></a>

### 设备离线或一直等待重连

按下面的顺序检查：

1. 电脑是否休眠，Wrapper 终端是否已经关闭。
2. Relay 终端是否仍运行，浏览器能否打开入口地址。
3. 本机体验时是否使用精确的 `http://127.0.0.1:8765`；改成 `localhost` 后，浏览器 Origin 也会改变。
4. 公网入口是否变化；`PUBLIC_ORIGIN`、`RELAY_URL` 与实际入口是否一致。
5. Relay、网页和 Wrapper 是否来自同一份兼容代码；代理或 VPN 是否阻断 WebSocket。

排查前先核对任务状态，避免反复重发已经被接收的消息。

### 找不到 Python 或 Claude

重新打开终端再检查版本。`python` 若打开 Microsoft Store，请检查 Windows 的应用执行别名和 Python 安装路径。Claude 终端命令可用但网页仍提示找不到时，核对 `.env` 中的 `CLAUDE_BIN` 是否指向实际的 `claude.exe`。

### 端口 8765 已被占用

先检查是否已经启动 Relay，不要重复运行。如果需要另一个端口，必须同时修改 `RELAY_PORT`、`PUBLIC_ORIGIN`、`RELAY_URL` 和浏览器访问地址。

### 怎样接入 Codex

先安装并登录官方 Codex CLI。与本地终端共享会话需要 Wrapper 和终端连接同一个官方 app-server；Windows 支持显式的 loopback WebSocket 接入。完整的三引擎区别见 [桌面端与网页协同](../README.md#桌面端与网页一起使用)。

<a id="codex-shared-entry"></a>

### Windows 上的 Codex 共享入口

在已完成共享入口配置的电脑上，**Codex Shared** 用于 App，**Codex CLI Shared** 用于终端。网页中的 Codex Code 也要连接同一账号的同一个 daemon。这两个名称是本地快捷方式，不是 Codex 官方命令，也不会随着 `git clone` 自动安装。

首次配置先完成 [共享控制验收](../deploy/README.md#codex-code-shared-control-plane-acceptance)，再使用仓库的 [`cc_remote.codex_windows_shared`](../cc_remote/codex_windows_shared.py) 模块。先让旧 App 的任务完成并正常退出；不要在运行中强行关闭 App、终端或 daemon。

下面的示例从本仓库目录运行，要求官方 Codex App 已通过 Windows 应用包安装，并且当前账号的官方 daemon 已处于 `running` 状态。它在当前 PowerShell 中选择匹配的官方 CLI，通过仅监听本机的桥接入口启动 App：

```powershell
$codexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE '.codex' }
$daemonInfo = codex app-server daemon version | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or $daemonInfo.status -ne 'running') {
    throw 'Start the selected account daemon normally before configuring sharing.'
}
$package = Get-AppxPackage -Name OpenAI.Codex | Sort-Object Version -Descending | Select-Object -First 1
if (-not $package) { throw 'The official Codex App package is not installed.' }
$manifest = Get-AppxPackageManifest -Package $package.PackageFullName
$relativeApp = $manifest.Package.Applications.Application | Select-Object -First 1 -ExpandProperty Executable
$appPath = Join-Path $package.InstallLocation $relativeApp
$env:PYTHON_DOTENV_DISABLED = '1'
$env:CODEX_HOME = $codexHome
$env:CODEX_BIN = $daemonInfo.managedCodexPath
$selectedVersion = & $env:CODEX_BIN --version
if ($LASTEXITCODE -ne 0 -or $selectedVersion -ne ('codex-cli ' + $daemonInfo.appServerVersion)) {
    throw 'CLI and running daemon versions must match; finish active work before updating.'
}
.\.venv\Scripts\python.exe -X utf8 -m cc_remote.codex_windows_shared app --home $codexHome --port 8766 --app $appPath
```

这些变量仅影响当前终端及其子进程。使用同一个 PowerShell，可读取 App 的实际连接验收结果：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m cc_remote.codex_windows_shared check --home $codexHome --port 8766 --app $appPath
```

`state: verified` 表示该 App 当前连到了所选 daemon 的本机桥接。再用匹配的 CLI 显式连接同一入口：

```powershell
& $env:CODEX_BIN --remote ws://127.0.0.1:8766
```

部署者可以把这两种已验证的启动方式保存为 **Codex Shared** 与 **Codex CLI Shared** 快捷方式。安装位置、Python 路径和账号目录应保存在本机；App、CLI 或 Wrapper 更新后重新核对连接，不要向公网开放这个本机端口。

App 与终端的连接通过后，还要确认 Wrapper 对应账号连接的是同一个官方服务。仅能看到相同的历史文件不足以证明三端共享；只读连接验收也不等于已经验证真实模型回复。此方式适用于 **Codex Code**，不改变 Work 的独立后端。

### 没有桌面通知

在浏览器中允许当前站点发送通知，再到网页设置中开启提醒并发送测试通知。公网使用 HTTPS，并检查操作系统的专注模式。网页关闭后的通知还需要服务端 Web Push 配置，见 [配置说明](configuration.md)。
