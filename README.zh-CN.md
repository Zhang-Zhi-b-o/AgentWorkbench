# AgentWorkbench

[English](README.md) · **简体中文**

一个自托管的随身工作台：把「远程用自己那台电脑」变成打开一个网页。宿主只做插件目录与
转发，每个工具一个独立插件。为**私有网络（Tailscale）**和**慢速、高延迟链路**而设计。

![工作台外壳：插件全部就绪](docs/screenshot.png)

一句话概括：**AI 对话、文件管理、Office 预览、Zotero 阅读、VS Code，全部收在同一个网页里。**
手机、平板、另一台电脑打开同一个地址就能用，不需要远程桌面，也不需要为每个工具单独开端口。

## 插件一览

| 插件 | 做什么 | 需要电脑上有什么 |
|---|---|---|
| `agents` | 完整的 DSH 对话工作区，可接入 Codex 与 Claude Code | `node` 18+、DSH 运行时（一条 `npm install`，见下文）、一个 DeepSeek API key |
| `explorer` | 浏览整台电脑：Office 文档预览、文本编辑、上传下载、复制移动、压缩解压，并能从任意目录直接打开 VS Code | 无 |
| `zotero` | 浏览 Zotero 书库、搜索文献、继续阅读 PDF，并对选中段落划词翻译 | Zotero 桌面端；划词翻译还要装 *Translate for Zotero* 插件并配好自定义 GPT / ChatGPT 服务 |
| `vscode` | 在壳内用 VS Code Web 写代码，顶栏导航一直可用 | VS Code CLI（`code`） |

第五个插件 `office`（经本机 ONLYOFFICE Docs 在浏览器里编辑）以 `"hidden": true` 停用随包附带：
它的移动端 Web 编辑器在免费版里只能查看。见 [Office 文档](#office-文档预览--交给设备打开)。

再加一个插件就是一个放两个文件的文件夹。宿主从不按名字 import 插件、也不看插件内部，
所以加一个工具不需要改宿主。

---

## 功能详解

### AI 对话（`agents`）

把本机的 DSH 对话工作区整个搬进工作台。插件负责启动和管理一个独立的 DSH Web 进程，
并把 Codex、Claude Code 作为可切换的 agent 接进来。

- **多 agent**：同一个工作区里可以在 Codex 与 Claude Code 之间切换，对话上下文由 DSH 管理。
- **独立 origin**：DSH 没有 base-path 选项，所以 `/agents/` 用 `302` 跳到它自己的回环端口；
  从浏览器看仍然是「工作台里的一个标签」，实际是 DSH 自己的页面。
- **自动启停**：第一次打开时按需拉起 DSH；宿主退出时一起结束。端口被占用时会直接说明。
- **工作区入口**：配套的 `dsh-workbench-open` 集成会在 DSH 会话头部放两个链接，
  分别是「用工作台的文件管理器打开当前目录」和「用工作台的 VS Code 打开当前目录」，
  都是从 DSH 跳到工作台的另一个插件，不用手动找路径。
- **密钥隔离**：DeepSeek key 只注入 DSH 子进程，不会出现在页面、响应或日志里。

### 文件（`explorer`）

一个跑在浏览器里的资源管理器，范围是整台电脑。

- **快速访问**：侧栏列出桌面、文档、下载等已知目录和所有盘符，可以随时切到「此电脑」。
- **浏览**：前进 / 后退 / 上一级 / 刷新、可点击的面包屑、当前文件夹内搜索、名称排序、
  列表与网格两种视图。
- **预览与编辑**：直接打开文本文件查看和修改，保存即写回原文件（大文件有读取上限）。
- **文件操作**：新建文件夹、新建文本文件、重命名、复制、移动、删除。
  Windows 上删除默认进回收站，也可以选择永久删除。
- **上传与下载**：从浏览器上传到当前目录；任意文件可以直接下载，图片、PDF 等在原位预览。
- **压缩与解压**：把文件或文件夹打包成 zip 下载，也能把 zip 解压到指定目录。
- **打开方式**：右键可以把文件或文件夹交给内置 VS Code（`/vscode/?folder=/d:/...`），
  或在 Windows 上用本机默认程序打开；Office 文档在弹窗里预览，并可下载源文件
  （见 [Office 文档](#office-文档预览--交给设备打开)）。
- **安全边界**：所有路径都做规范化与越界检查，不允许通过 `..` 跳出允许的范围。

### Office 文档：预览 + 交给设备打开

工作台不在浏览器里编辑 Office 文件。在「文件」里点开一个 Office 文档，会弹出**整页预览**：
由同机安装的 Microsoft Office 把它渲染成只读 PDF；预览右上角的「下载并打开」按钮
**下载源文件本体**（原样、原名），交给手上这台设备自带的编辑器（iPad 上的 Word / Pages、
桌面的 Word、或者 WPS）。

- **支持的格式**：Word、Excel、PPT（`.docx/.xlsx/.pptx`，以及旧版 `.doc/.xls/.ppt` 与 OpenDocument 系列）。
- **保真预览、且只读**：由 Office 本身导出 PDF；`data/preview-cache/` 按路径、大小、修改时间
  缓存结果，第二次打开是瞬时的。PDF 无法原地编辑，这正是我们要的。
- **下载拿到的是源文件**：预览用的 PDF 不会成为下载内容，按钮给的是原始字节与原始文件名。
- **为什么不在浏览器里编辑**：ONLYOFFICE Docs 的**桌面** Web 编辑器是免费可编辑的，但它的
  **移动端** Web 编辑器在免费社区版里只能查看 —— 平板上编辑器能打开，却不接受任何输入。
  因此 `office` 插件已停用（清单里 `"hidden": true`），改回 `false` 就能恢复编辑器标签页与
  `/office` 反代。
- **编辑往返**：用设备自带的 Office 改完，再通过「文件」把文件上传回来
  （网页本身没有权限直接写宿主机磁盘）。

### Zotero 阅读（`zotero`）

围绕 Zotero 官方本地 API（回环 `23119`）做的一个只读阅读界面。

- **书库浏览**：按集合和全部条目浏览，左侧分类，右侧条目列表。
- **搜索**：按标题、作者等字段搜索条目，服务端分页，每次 25 条，滚动到底继续加载。
- **文献详情**：作者、出版信息、摘要、附件列表。
- **PDF 阅读**：直接在工作台里流式打开条目的 PDF 附件，支持拖动进度条（Range 请求）。
- **划词翻译**：选中段落即可翻译，翻译服务复用 *Translate for Zotero* 里已经配置好的
  OpenAI 兼容服务，不需要在工作台里再填一遍 key。
- **自动启动 Zotero**：本地 API 没响应时，可以按配置自动把 Zotero 桌面端拉起来；
  自动找不到可执行文件时，在 `plugins.json` 里指定路径。

### 编辑器（`vscode`）

在壳内使用 VS Code Web（`code serve-web`），并且是由宿主**反向代理**进来的，不是跳转。

- **同源**：`/vscode/` 走工作台自己的 origin，手机和平板只需要工作台这一个入口，
  不需要为编辑器再开第二个对外端口。
- **完整能力**：工作台资源、webview 帧、WebSocket、gzip 压缩都由宿主转发，
  编辑、终端、扩展、Git 面板照常工作。
- **从文件插件进入**：在「文件」里点「VS Code」，会带着当前文件夹打开：
  `/vscode/?folder=/d:/...`。这里的值必须是「远端路径」——浏览器端的 VS Code 只有
  看到以 `/` 开头的值才拼 `vscode-remote://<authority><path>`；直接给 `D:\...` 会被
  当成 scheme `d` 而丢掉盘符，`file:///D:/...` 则是远端服务不提供的 scheme，
  两种都会留下一个空资源管理器。
- **独立打开**：直接访问 `/vscode/` 也可以，页面顶部仍带工作台的插件导航；没带文件夹
  参数时会跳到插件配置里的 `folder`（`plugins.json`），否则原生 serve-web 只会给一个
  空的欢迎窗口，没有文件可编辑。
- **导入桌面扩展**：服务器有自己的一套扩展，`import-vscode-extensions.ps1` 会把
  `%USERPROFILE%\.vscode\extensions` 同步到 `data/vscode-server/extensions`，再由服务器
  自己重扫。脚本不复制桌面那份 `extensions.json`（它的条目指向桌面目录），也不复制
  `.obsolete` 里列出的旧版本。因为扩展宿主就在本机以 Node 运行，带原生二进制的扩展也能用
  —— 它们是 Windows 构建，所以这是 Windows 到 Windows 的导入。加 `-Restart` 可在导入后
  顺带重启工作台。

---

## 使用方法

### 第一次运行

```bash
git clone https://github.com/Zhang-Zhi-b-o/AgentWorkbench.git
cd AgentWorkbench
python -m workbench
```

然后打开 <http://127.0.0.1:8768/>。启动时宿主会打印数据目录和每个插件的探测结果：

```
AgentWorkbench listening on http://127.0.0.1:8768
  data      /path/to/AgentWorkbench/data
  plugins   5 from /path/to/AgentWorkbench/plugins
  agents    iframe   /agents/           ready
  explorer  iframe   /plugins/explorer/ ready
  vscode    link     /vscode/           ready
  zotero    iframe   /plugins/zotero/   ready
```

缺依赖的插件显示 `not available`：卡片置灰，并直接说明缺什么，其余插件照常工作。
Windows 下也可以直接跑 `start.ps1`：它复用已在运行的实例，必要时后台启动再打开浏览器。

### 打开工作台

- 首页是插件卡片，点任意卡片进入对应插件；顶部导航栏可以在插件之间直接切换。
- 每个插件都有自己的地址，可以直接收藏或用新标签页打开：
  `/plugins/explorer/`、`/plugins/zotero/`、`/vscode/`、`/agents/`。
- 单独打开插件地址时，页面顶部会自动套上工作台的导航栏；壳内打开时不会重复嵌套。
- 顶部导航可以收起，收起后只留一个细手柄，给内容腾出高度（手机和平板上很有用）。

### AI 对话

1. 先按下一节装好 DSH 运行时和 DeepSeek key。
2. 打开「AI 对话」标签，第一次会自动启动 DSH，等它就绪。
3. 在 DSH 里新建会话，按需要选择 Codex 或 Claude Code 作为 agent。
4. 在会话里需要看文件或改代码时，用会话头部的「文件」「VS Code」入口跳到对应插件，
   路径会自动带上当前工作区。

### 浏览和操作文件

1. 打开「文件」标签，侧栏选一个快速访问目录或盘符。
2. 点文件夹进入下一级；用 `←` `→` `↑` 和面包屑在目录间移动。
3. 选中文件后可以下载、重命名、复制、移动、删除；右键菜单里还有「用 VS Code 打开」
   「在本机打开」。
4. 工具栏的「＋ 新建」「上传」「粘贴」作用于当前目录；搜索框只搜索当前文件夹。
5. 压缩：选中若干条目生成 zip 下载；解压：对 zip 文件选择解压目标目录。

### 编辑 Office 文档

1. 在「文件」里点开 `.docx` / `.xlsx` / `.pptx`，工作台弹出只读预览，右上角有「下载并打开」。
2. 那个按钮下载的是源文件本体；用它自带编辑器打开（平板上是 Word / Pages，桌面是 Word）。
3. 改完再通过「文件」上传回来 —— 网页没有权限直接写宿主机磁盘，这一步需要你操作。
4. 如果预览提示无法渲染，说明宿主机上的 Microsoft Office 缺失或被占用；下载仍然可用。

### 阅读 Zotero 文献

1. 确保 Zotero 桌面端已登录并至少有本地书库；没开的话插件会尝试自动拉起。
2. 打开「Zotero 阅读」，左侧选集合或「全部条目」，右侧浏览和搜索。
3. 点条目查看元数据；点附件里的 PDF 直接在工作台内阅读，支持进度跳转。
4. 在 PDF 里选中文字，点翻译即可得到译文。翻译服务来自 *Translate for Zotero* 的配置；
   没配置时页面会说明缺什么。

### 用 VS Code 写代码

- 从「文件」里进入目标文件夹，点工具栏的「VS Code」，会带着该文件夹打开编辑器。
- 也可以直接打开 `/vscode/`，在 VS Code 自己的文件选择器里选目录；
  默认目录由 `plugins.json` 的 `vscode.folder` 决定。
- 编辑器里的顶栏导航仍然可用，可以随时切回其他插件。

### 从手机或平板使用

1. 在电脑上用 Tailscale 暴露工作台（见「远程访问」）。
2. 手机上打开工作台地址，用法与桌面浏览器一致。
3. 建议把页面添加到主屏幕，按 PWA 方式使用。
4. 编辑器在手机上可用；但 **VS Code 扩展的 webview 面板在 iOS Safari 上不可用**，
   详见「已知限制」。

---

## 结构

```
浏览器 ──HTTPS──▶ 私有反向代理 ──▶ 127.0.0.1:8768（宿主）
                                        │
                 ┌──────────────────────┼──────────────────────┐
           静态 web/            /api/<插件>              /<插件>/
                 │                      │                      │
           外壳与插件页面          插件 Python 模块        反代 / 302 / 链接
```

- **只有一个入站面。** 宿主只绑 `127.0.0.1`。对外是反向代理的事（Tailscale serve 或
  任何 TLS 终结器），宿主自己从不监听公网。
- **编辑器是反代，不是跳转。** `/vscode/` 经宿主转发——工作台资源、webview 帧、
  WebSocket、gzip 压缩都走这里——所以手机和别的功能同一个 origin，**不需要第二个对外端口**。
- **`agents` 必须独占一个 origin。** DSH 没有 base-path 选项
  （`dsh web --help` 里没有这类参数），所以 `/agents/` 用 `302` 跳到它自己的回环端口。
  同一个反向代理给它加一条映射即可，其它插件什么都不用加。
- **靠清单，不靠 import。** 外壳渲染什么由 `GET /api/plugins` 决定，路由表就是
  `plugins/*/plugin.json`。

## 环境要求

- Python 3.10+（宿主和所有自带插件只用标准库）
- 按插件可选：`node` 18+ 与 DSH 运行时（agents）、Zotero 桌面端（zotero）、
  VS Code CLI（vscode）；Office 预览另外用上宿主机已装的 Microsoft Office（有就用）
- `explorer` 没有额外依赖

宿主是纯 Python，测试在 CI 上同时跑 Windows 与 Linux；但日常使用是 Windows：启动器与
远程访问脚本是 PowerShell，`zotero` / `vscode` 两个插件也按 Windows 的安装位置查找。
Linux 或 macOS 上宿主和外壳能跑，这些插件会说明自己缺什么。

## 启动

```bash
git clone https://github.com/Zhang-Zhi-b-o/AgentWorkbench.git
cd AgentWorkbench
python -m workbench
```

然后打开 <http://127.0.0.1:8768/>。启动输出见「第一次运行」。

Windows 下也可以直接跑 `start.ps1`：它复用已在运行的实例，必要时后台启动再打开浏览器。

### 安装 DSH 运行时（`agents` 插件）

`agents` 驱动本机的 DSH，不捆绑运行时。在仓库根目录装一次：

```powershell
# Windows PowerShell
New-Item -ItemType Directory -Force data | Out-Null
npm install --prefix "$PWD\data\dsh-runtime" @deepseek-ai/dsh
```

```bash
# macOS / Linux
mkdir -p data
npm install --prefix "$PWD/data/dsh-runtime" @deepseek-ai/dsh
```

先建 `data/` 同时也把 `WORKBENCH_HOME` 钉在这个仓库上（见下文），运行时就会落在插件
查找的那个位置。数据目录放在别处的话，装到 `$WORKBENCH_HOME/dsh-runtime` 即可。

之后还需要一个 DeepSeek API key，见 [DeepSeek key](#deepseek-key)。这两样缺任何一个，
置灰的卡片和 `/agents/` 返回的 `503` 都会打印你这台机器上该跑的那条命令，不用自己猜路径。

### Office 预览（可选）

预览由宿主机上已装的 Microsoft Office 生成，装着就无需任何额外安装。如果还想把停用的
`office` 插件恢复回来，从
[ONLYOFFICE Docs 官方下载页](https://download.onlyoffice.com/install/documentserver/windows/onlyoffice-documentserver.exe)
安装 Windows 版，或运行 Docker 镜像 `onlyoffice/documentserver`，再把
`plugins/office/plugin.json` 里的 `"hidden"` 改成 `false`；需要保存回调验签时，把文档服务的
JWT 密钥写进环境变量 `ONLYOFFICE_JWT_SECRET`。

## 配置

所有和这台机器相关的东西都放在数据目录，永远不进 git。

| 文件 | 用途 |
|---|---|
| `$WORKBENCH_HOME/plugins.json` | 每个插件的设置（端口、CLI 路径、默认目录、Zotero 自动启动） |
| `$WORKBENCH_HOME/agent-env.json` | `agents` 插件用的 DeepSeek key，`{"DEEPSEEK_API_KEY": "..."}` |
| `$WORKBENCH_HOME/remote-access.json` | 唯一那个私有 origin，`{"origin": "https://host:port"}` |

`WORKBENCH_HOME` 的取值顺序：环境变量 → 存在本地 `data/` 就用它 → 平台数据目录
（`%LOCALAPPDATA%\AgentWorkbench`、`$XDG_DATA_HOME/AgentWorkbench`）。
所以代码目录可以随便搬：只有同时搬 `data/` 时才需要设 `WORKBENCH_HOME`。

监听端口用 `WORKBENCH_PORT` 覆盖。

Windows 下从旧版本迁过来？跑 `sync-data.ps1`：它把 DSH 的对话与运行时搬过来，并重建
几百个目录联接——直接复制要么顺着联接把 690 MB 展开成几个 G，要么丢掉联接让 DSH 的
插件加载失败。全新安装用不到它。

### `plugins.json`

```json
{
  "agents": { "port": 3080, "remote_port": 8770 },
  "vscode": { "port": 8799, "cli": "", "folder": "" },
  "zotero": { "autostart": true, "path": "" }
}
```

- `port` —— 插件独占的回环端口。
- `agents.remote_port` —— 反向代理为 DSH 自己的 origin 暴露的端口。
- `vscode.cli` —— 显式的 `code.cmd` 路径；留空表示按 `PATH` 与标准安装位置搜索。
- `vscode.folder` —— 远程文件选择器的默认目录。
- `zotero.autostart` / `zotero.path` —— Zotero 没开时按本地 API 把它拉起来；
  自动找不到时用 `path` 指定可执行文件。

### DeepSeek key

`agents` 插件只把 key 注入 DSH 子进程。取值顺序：进程环境里的 `DEEPSEEK_API_KEY`
（或 `ANTHROPIC_AUTH_TOKEN`）→ `agent-env.json`。它不会出现在响应、日志或外壳 HTML 里。
服务地址被钉死在 `https://api.deepseek.com`，指向其他主机的跳转一律拒绝。

## 远程访问

宿主设计上只监听回环。要从手机/平板访问，在它前面放一个私有反向代理。用 Tailscale：

```bash
# 工作台本体，含反代的编辑器——手机只需要这一个入口
tailscale serve --bg --https=8769 http://127.0.0.1:8768
# agents 插件自己的 origin（DSH 不能挂在子路径下）
tailscale serve --bg --https=8770 http://127.0.0.1:3080
```

`setup-remote.ps1` 会一次做完这两条，并按 `tailscale status` 自动写好
`remote-access.json`，不需要你手抄地址（需要管理员权限，`tailscale serve` 改的是全机设置）。

`remote-access.json` 里写的就是外壳的 origin：

```json
{ "origin": "https://your-machine.your-tailnet.ts.net:8769" }
```

宿主接受两种请求：`Host` 等于上面的 origin，或是本机端口上的回环名。其余一律 `400`，
外来 `Origin` 头拿 `403`。这就是「手机能用，但同一个浏览器里随便一个网页用不了」的实现方式。

## 已知限制

- **一个人、一台机器。** 没有账号体系、没有用户隔离。不要直接暴露到公网，反向代理才是
  安全边界。
- **外壳界面只有简体中文。** README 双语，UI 不是。
- **还没到 1.0。** 插件与宿主之间的接口、HTTP 路由都可能在小版本里变。
- **自带的 Zotero reader 是构建产物**（`web/zotero-reader/`，约 10 MB）。来源与构建方法见
  [NOTICE.md](NOTICE.md)。
- **VS Code 扩展的 webview 在 iPad 上不可用。** Claude Code、Codex 这类扩展的界面全部
  跑在 webview iframe 里，iOS Safari 对跨源 iframe、Service Worker 和宿主机名校验的
  限制会让面板空白或无法加载。编辑器本身的编辑、终端、文件操作不受影响；需要 AI 对话时
  用工作台自带的「AI 对话」插件。
- **Office 预览依赖宿主机上的 Office。** 只有生成预览那一步用到它；下载按钮与其余插件
  在没装 Office 的机器上照常工作。

## 加一个插件

新建 `plugins/mytool/`，两个文件。

`plugin.json`：

```json
{
  "id": "mytool",
  "name": "我的工具",
  "description": "一句话说明",
  "icon": "box",
  "kind": "iframe",
  "entry": "/mytool/",
  "routes": ["/mytool", "/api/mytool"],
  "module": "mytool"
}
```

`kind` 三选一：

- `iframe` —— 外壳在面板里嵌入 `entry`；
- `link` —— 外壳在新标签页打开 `entry`；
- `redirect` —— 宿主自己对这个路径回一个跳转。

`__init__.py` 里可以定义任意几个：

| 函数 | 作用 |
|---|---|
| `handle(request) -> bool` | 处理你声明的路径；返回 `False` 表示继续往下找 |
| `available() -> bool` | 卡片是否可用的轻量探测，**不允许**在里面启动任何东西 |
| `reason() -> str` | 可选：一行说明 `available()` 为什么是 `False`，显示在置灰的卡片上 |
| `start()` / `stop()` | 生命周期，随宿主一起 |
| `status() -> dict` | 可选，给你自己的调试端点用 |

`request` 提供 `method`、`path`、`query`、`body`、`headers`、`raw_query`，以及四个响应方法：
`json(status, payload)`、`text(...)`、`redirect(location)`、
`file(path, content_type=..., support_range=True)`。预期内的失败直接抛
`workbench.errors.WorkbenchError(message, status, code)`，宿主会替你格式化。

**不需要改宿主。** 文件夹丢进去、重启，外壳就把它列出来了。

## 安全模型

- 只绑回环；对外访问交给会做 TLS 的反向代理。
- `Host` 与 `Origin` 白名单。
- 每个写请求必须带 `X-Workbench: 1`（老脚本的 `X-Agent-Hub: 1` 仍然接受）。
- JSON 响应递归脱敏：`api_key` 这类键名、以及长得像 bearer token 的值，一律替换成
  `[REDACTED]`。
- 静态路径一律解析在 `web/` 之下，路径穿越直接拒绝。
- 文件插件对每个路径做规范化与越界检查；Office 回调使用一次性文件票和 JWT 验签。
- 插件的状态路由永不返回 DSH 的浏览器握手 token 或 API key。

## 开发

```bash
python -m pytest          # 40 个测试：安全闸门、路由、缓存策略、插件、端到端起服务
```

同一套测试在 Windows 和 Linux、Python 3.10 与 3.12 上跑
（[workflow](.github/workflows/tests.yml)）；因为该 workflow 目前是手动触发，push 前
请本地跑一遍。

目录：

```
workbench/    宿主：config、errors、插件注册表、request 外观、server
plugins/      一个插件一个包，各自带 plugin.json
web/          外壳、插件页面，以及自带的 Zotero reader
web/plugins/  自带插件的页面（explorer / office / zotero）
web/zotero-reader/   官方 Zotero reader 的构建产物（见 NOTICE.md）
integrations/ dsh-workbench-open：DSH 会话头部的「打开文件 / VS Code」入口
docs/         上面那张截图
tests/
```

## 许可

AGPL-3.0-or-later。第三方组件见 [NOTICE.md](NOTICE.md)。
