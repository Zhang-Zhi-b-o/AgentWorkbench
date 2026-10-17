# AgentWorkbench

[English](README.md) · **简体中文**

一个自托管的随身工作台：把「远程用自己那台电脑」变成打开一个网页。宿主只做插件目录与
转发，每个工具一个独立插件。为**私有网络（Tailscale）**和**慢速、高延迟链路**而设计。

自带三个插件：

| 插件 | 做什么 | 需要电脑上有什么 |
|---|---|---|
| `agents` | 完整的 DSH 对话工作区，可接入 Codex 与 Claude Code | `node` + 一个 DeepSeek API key |
| `zotero` | 浏览 Zotero 书库、读 PDF、划词翻译 | Zotero 桌面端 |
| `vscode` | 在新标签页打开 VS Code Web | VS Code CLI（`code`） |

第四个插件就是一个放两个文件的文件夹。宿主从不按名字 import 插件、也不看插件内部，
所以加一个工具不需要改宿主。

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
  同一个反向代理给它加一条映射即可，另外两个插件什么都不用加。
- **靠清单，不靠 import。** 外壳渲染什么由 `GET /api/plugins` 决定，路由表就是
  `plugins/*/plugin.json`。

## 环境要求

- Python 3.10+（宿主和所有自带插件只用标准库）
- 按插件可选：`node` 与 DSH 运行时（agents）、Zotero 桌面端（zotero）、
  VS Code CLI（vscode）

## 启动

```bash
git clone <本仓库> AgentWorkbench
cd AgentWorkbench
python -m workbench
```

然后打开 <http://127.0.0.1:8768/>。启动时宿主会打印数据目录和每个插件的探测结果：

```
AgentWorkbench listening on http://127.0.0.1:8768
  data      /path/to/AgentWorkbench/data
  plugins   3 from /path/to/AgentWorkbench/plugins
  agents    iframe   /agents/           ready
  vscode    link     /vscode/           ready
  zotero    iframe   /plugins/zotero/   ready
```

没有别的准备工作：缺依赖的插件只会显示 `not available`、卡片置灰，其余照常工作。

Windows 下也可以直接跑 `start.ps1`：它复用已在运行的实例，必要时后台启动再打开浏览器。

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
- 插件的状态路由永不返回 DSH 的浏览器握手 token 或 API key。

## 开发

```bash
python -m pytest          # 34 个测试：安全闸门、路由、缓存策略、插件、端到端起服务
```

目录：

```
workbench/    宿主：config、errors、插件注册表、request 外观、server
plugins/      一个插件一个包，各自带 plugin.json
web/          外壳、插件页面，以及自带的 Zotero reader
web/zotero-reader/   官方 Zotero reader 的构建产物（见 NOTICE.md）
tests/
```

## 许可

AGPL-3.0-or-later。第三方组件见 [NOTICE.md](NOTICE.md)。
