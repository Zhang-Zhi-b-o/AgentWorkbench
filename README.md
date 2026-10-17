# AgentWorkbench

**English** · [简体中文](README.zh-CN.md)

A small, self-hosted workbench for reaching your own computer from anywhere: a
plugin host with one page per tool, designed for a private network (Tailscale)
and a slow, high-latency link.

![The workbench shell: every plugin ready](docs/screenshot.png)

In one sentence: **AI chat, file management, Office preview, Zotero reading and
VS Code, all behind one web page.** Open the same address from a phone, a tablet
or another computer; no remote desktop and no extra port per tool.

## Plugins at a glance

| Plugin | What it does | Needs on your machine |
|---|---|---|
| `agents` | The full DSH chat workspace, with Codex and Claude Code available as agents | `node` 18+, the DSH runtime (one `npm install`, see below) and a DeepSeek API key |
| `explorer` | Browse the whole computer: preview Office documents, edit text, upload and download, copy and move, zip and unzip, and open any folder in VS Code | nothing |
| `zotero` | Browse your Zotero library, read PDFs, translate a selection | the Zotero desktop app; selection translation additionally needs the *Translate for Zotero* plugin configured with a custom GPT / ChatGPT service |
| `vscode` | Edit code in VS Code Web, inside the shell (top-bar links stay available) | the VS Code CLI (`code`) |

A fifth plugin, `office` (browser editing through a local ONLYOFFICE Docs server),
ships parked with `"hidden": true`: its mobile web editor is view-only in the free
edition. See [Office documents](#office-documents-preview-and-hand-off).

One more plugin is just a folder with two files. The host never imports a plugin
by name and never inspects its internals, so adding a tool does not mean editing
the host.

---

## Feature details

### AI chat (`agents`)

Brings the local DSH workspace into the workbench. The plugin starts and manages
a dedicated DSH web process, and wires Codex and Claude Code in as switchable
agents.

- **Multiple agents.** Switch between Codex and Claude Code inside the same
  workspace; DSH owns the conversation state.
- **Its own origin.** DSH has no base-path option, so `/agents/` answers with a
  `302` to its own loopback port. From the browser it still looks like one tab
  of the workbench.
- **Started on demand.** DSH is launched the first time you open the tab and
  stopped with the host. If the port is taken, the plugin says so instead of
  failing silently.
- **Workspace shortcuts.** The bundled `dsh-workbench-open` integration adds two
  links to the DSH session header: open the current directory in the workbench
  file manager, or in the workbench VS Code. Both jump to the other plugins with
  the path already filled in.
- **Key isolation.** The DeepSeek key is injected into the DSH child process
  only; it never reaches a page, a response or a log.

### Files (`explorer`)

A file manager in the browser, covering the whole computer.

- **Quick access.** The sidebar lists known folders (Desktop, Documents,
  Downloads…) and every drive; “This PC” goes to the roots.
- **Navigation.** Back, forward, up, refresh, clickable breadcrumbs, search
  within the current folder, name sorting, list and grid views.
- **Preview and edit.** Open a text file, read or change it, and save straight
  back to the original file (there is a size ceiling for reads).
- **File operations.** Create folders and text files, rename, copy, move, delete.
  On Windows deletes go to the Recycle Bin by default; permanent deletion is a
  separate choice.
- **Upload and download.** Upload from the browser into the current folder, or
  download any file; images and PDFs preview in place.
- **Zip and unzip.** Pack files or folders into a zip download, or extract a zip
  into a target directory.
- **Open elsewhere.** From the context menu, hand a file or folder to the built-in
  VS Code (`/vscode/?folder=/d:/…`), or open it with the desktop's default
  application on Windows. Office documents preview in place and download as the
  original file (see [Office documents](#office-documents-preview-and-hand-off)).
- **Path guard.** Every path is normalized and checked for escapes; `..` cannot
  climb out of the allowed roots.

### Office documents: preview and hand off

The workbench does not edit Office files in the browser. Clicking one in `explorer`
shows a **full-page preview** — the document is rendered to a read-only PDF by the
Microsoft Office installed on the same machine — and the button in the preview's
top-right corner **downloads the original file**, unchanged, for the editor on the
device you are holding (Word/Pages on a tablet, Word on a desktop, WPS anywhere).

- **Formats.** Word, Excel and PowerPoint (`.docx/.xlsx/.pptx`, plus the older
  `.doc/.xls/.ppt` and the OpenDocument equivalents).
- **Faithful preview, read-only.** Office exports the file to PDF;
  `data/preview-cache/` keeps the result keyed on path, size and mtime, so the
  second look is instant. A PDF cannot be edited in place, which is the point.
- **Download is the source file.** The preview PDF is never what you get — the
  button serves the original bytes with its original name.
- **Why not edit in the browser.** ONLYOFFICE Docs can do it for the desktop web
  editor, but its *mobile* web editor is view-only in the free Community edition,
  so on a tablet the editor loads and then ignores every keystroke. The `office`
  plugin is therefore parked (`"hidden": true` in its manifest); set that back to
  `false` to bring the editor tab and its `/office` proxy back.
- **Editing round trip.** Edit on the device with its own Office, then upload the
  file back through `explorer` (a browser cannot write to the host on its own).

### Zotero reading (`zotero`)

A read-only reading surface around Zotero's official local API (loopback
`23119`).

- **Library browsing.** Collections on the left, items on the right, plus an
  “All items” view.
- **Search.** Search by title, author and the other fields Zotero indexes;
  paginated server-side, 25 items per page, more on scroll.
- **Item details.** Authors, publication info, abstract and attachment list.
- **PDF reading.** Stream the PDF attachment inside the workbench, with seek
  support (Range requests).
- **Selection translation.** Select text and translate it. The translation
  service is the OpenAI-compatible one already configured in *Translate for
  Zotero*; no second key is needed.
- **Autostart.** If the local API does not answer, the plugin can start the
  Zotero desktop app; point `path` at the executable if autodetection fails.

### Editor (`vscode`)

VS Code Web (`code serve-web`) inside the shell — **relayed by the host, not
redirected**.

- **Same origin.** `/vscode/` is served through the workbench origin, so a phone
  needs this one entry and no second exposed port.
- **Full capabilities.** Workbench assets, webview frames, web sockets and gzip
  all pass through the host; editing, terminal, extensions and the Git panel
  keep working.
- **Opened from Files.** Click “VS Code” in `explorer` to open the current folder
  as `/vscode/?folder=/d:/…`: the web workbench builds
  `vscode-remote://<authority><path>` only for a value that starts with `/`, so a
  bare `D:\…` keeps URI scheme `d` and drops the drive letter, and a `file://`
  URI names a scheme the remote server does not serve — both leave the explorer
  empty.
- **Direct link.** `/vscode/` also works on its own, with the workbench plugin
  bar kept on top. The bare URL redirects to the plugin's `folder` setting
  (`plugins.json`), because stock serve-web answers it with the empty welcome
  window, i.e. nothing to edit.
- **Your desktop extensions.** The server keeps its own extension set, so
  `import-vscode-extensions.ps1` mirrors `%USERPROFILE%\.vscode\extensions` into
  `data/vscode-server/extensions` and lets the server rescan. It skips the
  desktop's `extensions.json` (whose entries point at the desktop folder) and the
  versions listed in `.obsolete`. Because the extension host runs in Node on the
  same machine, extensions with native binaries work — they are Windows builds,
  so this is a Windows-to-Windows import. Pass `-Restart` to restart the
  workbench afterwards.

---

## How to use it

### First run

```bash
git clone https://github.com/Zhang-Zhi-b-o/AgentWorkbench.git
cd AgentWorkbench
python -m workbench
```

Then open <http://127.0.0.1:8768/>. On startup the host prints where its data
lives and what it found for each plugin:

```
AgentWorkbench listening on http://127.0.0.1:8768
  data      /path/to/AgentWorkbench/data
  plugins   4 from /path/to/AgentWorkbench/plugins
  agents    iframe   /agents/           ready
  explorer  iframe   /plugins/explorer/ ready
  vscode    link     /vscode/           ready
  zotero    iframe   /plugins/zotero/   ready
```

A plugin whose dependency is missing is reported as `not available`; its card
stays disabled and says what is missing, and the rest keep working.

On Windows, `start.ps1` does the same thing from a shortcut: it reuses a healthy
running instance, starts one in the background if needed, then opens the browser.

### Opening the workbench

- The home page shows one card per plugin; the top bar switches between plugins
  directly.
- Every plugin has its own address, so it can be bookmarked or opened in a new
  tab: `/plugins/explorer/`, `/plugins/zotero/`, `/vscode/`, `/agents/`.
- Opening a plugin address directly wraps it in the workbench bar; opening it
  inside the shell does not nest a second one.
- The top bar collapses to a thin handle, giving the content the full height
  (handy on phones and tablets).

### AI chat

1. Install the DSH runtime and set the DeepSeek key first (next section).
2. Open the “AI 对话” tab; DSH starts on first use — wait until it is ready.
3. Create a session in DSH and pick Codex or Claude Code as the agent.
4. When you need to look at files or edit code, use the “files” / “VS Code”
   links in the session header; the current workspace path comes along.

### Browsing and managing files

1. Open the “文件” tab and pick a quick-access folder or a drive.
2. Click into folders; move around with `←`, `→`, `↑` and the breadcrumbs.
3. Select a file to download, rename, copy, move or delete. The context menu also
   offers “open in VS Code” and “open locally”.
4. “＋ 新建”, “上传” and “粘贴” act on the current folder; the search box searches
   only that folder.
5. Compress: select entries and download a zip. Extract: choose a zip and a
   target directory.

### Editing Office documents

1. Click the `.docx` / `.xlsx` / `.pptx` in “文件”. The workbench renders a
   read-only preview and offers “下载并打开” in the preview's top-right corner.
2. That button downloads the original file; open it with the editor on the device
   you are using (Word or Pages on a tablet, Word on a desktop).
3. When you are finished, upload the file back through “文件”. A browser cannot
   write to the host's disk on its own, so this step stays manual.
4. If the preview reports that it could not be rendered, Microsoft Office is
   missing or busy on the host; the download still works.

### Reading Zotero items

1. Make sure the Zotero desktop app is signed in and has a local library; the
   plugin tries to start it if it is closed.
2. Open “Zotero 阅读”, pick a collection (or “All items”) on the left, browse and
   search on the right.
3. Click an item for its metadata; click the PDF attachment to read it in the
   workbench, seeking included.
4. Select text in the PDF and translate it. The service comes from *Translate for
   Zotero*; if it is not configured the page says what is missing.

### Writing code in VS Code

- Go to the target folder in “文件” and click “VS Code”; the folder opens in the
  editor.
- Or open `/vscode/` directly and pick a folder in VS Code's own picker; the
  default folder is `vscode.folder` in `plugins.json`.
- The top bar stays available, so you can switch back to another plugin at any
  time.

### Using it from a phone or tablet

1. Expose the workbench with Tailscale on the computer (see “Remote access”).
2. Open the workbench address on the phone; it behaves like the desktop browser.
3. Adding the page to the home screen (PWA mode) is recommended.
4. The editor works on a phone, but **VS Code extension webviews do not work in
   iOS Safari** — see “Known limits”.

---

## How it is put together

```
browser ──HTTPS──▶ private reverse proxy ──▶ 127.0.0.1:8768  (host)
                                                     │
                           ┌─────────────────────────┼─────────────────────┐
                      static web/            /api/<plugin>          /<plugin>/
                           │                         │                     │
                     the shell + pages      plugin Python module     302 or link
```

- **One inbound surface.** The host binds `127.0.0.1` only. Remote access is a
  reverse proxy's job (Tailscale serve or any TLS terminator); nothing listens on
  a public interface.
- **The editor is relayed, not redirected.** `/vscode/` is proxied through the
  host — workbench assets, webview frames, web sockets and gzip included — so a
  phone reaches it on the same origin as everything else and needs no second
  exposed port.
- **`agents` needs its own origin.** DSH has no base-path option
  (`dsh web --help` has no such flag), so `/agents/` answers with a `302` to its
  own loopback port. Point the same reverse proxy at that port once; the other
  plugins need nothing extra.
- **Manifests, not imports.** `GET /api/plugins` returns what the shell renders.
  The routing table is `plugins/*/plugin.json`.

## Requirements

- Python 3.10+ (the host and all bundled plugins use the standard library only)
- Optional, per plugin: `node` 18+ and the DSH runtime (agents), the Zotero desktop
  app (zotero), the VS Code CLI (vscode). Office previews additionally use the
  Microsoft Office already installed on a Windows host, when there is one.
- `explorer` needs nothing extra

The host is plain Python and its test suite runs on both Windows and Linux in CI,
but day-to-day use is Windows: the launcher and remote-access helpers are
PowerShell scripts, and the `zotero` / `vscode` plugins look for Windows install
locations. On Linux or macOS the host and the shell work; those plugins will
report why they are unavailable.

## Run it

```bash
git clone https://github.com/Zhang-Zhi-b-o/AgentWorkbench.git
cd AgentWorkbench
python -m workbench
```

Then open <http://127.0.0.1:8768/>. See “First run” for the startup output.

On Windows, `start.ps1` does the same thing from a shortcut: it reuses a healthy
running instance, starts one in the background if needed, then opens the browser.

### Install the DSH runtime (the `agents` plugin)

`agents` drives a local DSH install; it does not bundle one. Install it once into
the data directory, from the checkout root:

```bash
# Windows PowerShell
New-Item -ItemType Directory -Force data | Out-Null
npm install --prefix "$PWD\data\dsh-runtime" @deepseek-ai/dsh
```

```bash
# macOS / Linux
mkdir -p data
npm install --prefix "$PWD/data/dsh-runtime" @deepseek-ai/dsh
```

Creating `data/` first also pins `WORKBENCH_HOME` to this checkout (see below), so
the runtime lands exactly where the plugin looks for it. If you keep your data
somewhere else, install into `$WORKBENCH_HOME/dsh-runtime` instead.

The plugin then needs a DeepSeek API key; see [The DeepSeek key](#the-deepseek-key).
If either piece is missing, the disabled card and the `503` from `/agents/` both
print the exact command for your machine — you do not have to guess the path.

### Office preview (optional)

Previews are produced by the Microsoft Office installed on the host, so there is
nothing to install while Office is present. If only the parked `office` plugin is
wanted back, install the Windows build from the
[ONLYOFFICE Docs download page](https://download.onlyoffice.com/install/documentserver/windows/onlyoffice-documentserver.exe)
or run the Docker image `onlyoffice/documentserver`, then set `"hidden": false` in
`plugins/office/plugin.json`; put the document server's JWT secret in
`ONLYOFFICE_JWT_SECRET`.

## Configuration

Everything machine-specific lives in the data directory, never in git.

| File | Purpose |
|---|---|
| `$WORKBENCH_HOME/plugins.json` | per-plugin settings (ports, CLI path, default folder, Zotero autostart) |
| `$WORKBENCH_HOME/agent-env.json` | the DeepSeek key for the `agents` plugin, `{"DEEPSEEK_API_KEY": "..."}` |
| `$WORKBENCH_HOME/remote-access.json` | the one private origin, `{"origin": "https://host:port"}` |

`WORKBENCH_HOME` defaults to a local `data/` directory when one exists, and to
the platform data directory (`%LOCALAPPDATA%\AgentWorkbench`,
`$XDG_DATA_HOME/AgentWorkbench`) otherwise. Move the checkout freely; only move
`data/` if you also set `WORKBENCH_HOME`.

Override the listen port with `WORKBENCH_PORT`.

Moving an existing installation on Windows? `sync-data.ps1` brings the DSH
conversations and runtime across, including the ~500 directory junctions that a
plain copy would either follow (and explode) or drop (and break the plugin
loader). A fresh install never needs it.

### `plugins.json`

```json
{
  "agents": { "port": 3080, "remote_port": 8770 },
  "vscode": { "port": 8799, "cli": "", "folder": "" },
  "zotero": { "autostart": true, "path": "" }
}
```

- `port` — loopback port the plugin owns.
- `agents.remote_port` — the port your reverse proxy exposes for DSH's own origin.
- `vscode.cli` — explicit `code.cmd` path; empty means "search `PATH` and the
  standard install locations".
- `vscode.folder` — default folder the remote file picker opens.
- `zotero.autostart` / `zotero.path` — start Zotero via its local API when it is
  closed, and where the executable is if autodetection fails.

### The DeepSeek key

The `agents` plugin injects a key into the DSH child process only. It is read
from `DEEPSEEK_API_KEY` (or `ANTHROPIC_AUTH_TOKEN`) in the environment first, then
from `agent-env.json`. It is never written to a response, a log, or the shell's
HTML. The endpoint is pinned to `https://api.deepseek.com`, and a redirect to any
other host is refused.

## Remote access

The host is loopback-only by design. To reach it from a phone or tablet, put a
private reverse proxy in front of it. With Tailscale:

```bash
# the workbench, including the proxied editor — this is the only entry a phone needs
tailscale serve --bg --https=8769 http://127.0.0.1:8768
# the agents plugin's own origin (DSH cannot be served under a path)
tailscale serve --bg --https=8770 http://127.0.0.1:3080
```

`setup-remote.ps1` runs both and writes `remote-access.json` for you.

Then write the shell's origin into `remote-access.json`:

```json
{ "origin": "https://your-machine.your-tailnet.ts.net:8769" }
```

The host accepts a request whose `Host` matches that origin, or a loopback host on
its own port. Anything else is rejected with `400`, and a foreign `Origin` header
gets `403`. That is what lets the page work from a phone while remaining
unreachable to a random web page on the same machine.

## Known limits

- **One person, one machine.** No accounts, no per-user isolation. Do not expose
  the host to the open internet; the reverse proxy is the security boundary.
- **The shell's interface is Simplified Chinese only.** The README is bilingual;
  the UI is not.
- **Pre-1.0.** Interfaces between plugins and the host, and the HTTP routes, may
  change in minor releases.
- **The bundled Zotero reader is a build artifact** (`web/zotero-reader/`,
  ~10 MB). See [NOTICE.md](NOTICE.md) for its provenance and build steps.
- **VS Code extension webviews do not work on iPad.** The UI of extensions such
  as Claude Code and Codex runs inside a webview iframe; iOS Safari's restrictions
  on cross-origin iframes, service workers and the hostname check leave the panel
  blank or unable to load. Editing, the terminal and file operations are
  unaffected; use the workbench's own “AI 对话” plugin for AI chat instead.
- **Office previews need Office on the host.** Only the PDF preview uses it; the
  download button and every other plugin keep working without it.

## Adding a plugin

Create `plugins/mytool/` with two files.

`plugin.json`:

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

`kind` is one of:

- `iframe` — the shell embeds `entry` in a panel;
- `link` — the shell opens `entry` in a new tab;
- `redirect` — the host itself answers the path with a redirect.

`__init__.py` may define any of:

| Function | Role |
|---|---|
| `handle(request) -> bool` | serve a path you declared in `routes`; return `False` to fall through |
| `available() -> bool` | cheap probe used for the card's enabled state; must not start anything |
| `reason() -> str` | optional: one line explaining why `available()` is `False`, shown on the disabled card |
| `start()` / `stop()` | lifecycle, called with the host |
| `status() -> dict` | optional operational facts for your own debug endpoint |

`request` gives you `method`, `path`, `query`, `body`, `headers`, and four
responders: `json(status, payload)`, `text(...)`, `redirect(location)`,
`file(path, content_type=..., support_range=True)`. Raise
`workbench.errors.WorkbenchError(message, status, code)` for an expected failure
and the host formats it for the browser.

**No host change is needed.** Drop the folder in, restart, and the shell lists it.

## Security model

- Loopback bind only; remote access is delegated to a TLS-terminating proxy.
- `Host` and `Origin` allow-lists.
- Every mutating request must carry `X-Workbench: 1`
  (`X-Agent-Hub: 1` is still accepted for older scripts).
- JSON responses are recursively scrubbed: keys such as `api_key` and values that
  look like bearer tokens are replaced with `[REDACTED]`.
- Static paths are resolved under `web/`; traversal is rejected.
- The files plugin normalizes and guards every path; Office callbacks use a
  one-time file ticket and JWT verification.
- Plugin status routes never return the browser hand-off token or the API key.

## Development

```bash
python -m pytest          # 40 tests: guards, routing, cache policy, plugins, end to end
```

CI runs the same suite on Windows and Linux for Python 3.10 and 3.12
([workflow](.github/workflows/tests.yml)); run it locally before pushing, since
the workflow is currently manual-triggered.

Layout:

```
workbench/     host: config, errors, plugin registry, request facade, server
plugins/       one package per plugin, each with plugin.json
web/           shell, plugin pages, and the bundled Zotero reader
web/plugins/   pages for the bundled plugins (explorer / office / zotero)
web/zotero-reader/   official Zotero reader build (see NOTICE.md)
integrations/ dsh-workbench-open: the DSH session-header “files / VS Code” links
docs/          screenshot used above
tests/
```

## License

AGPL-3.0-or-later. Third-party components are described in [NOTICE.md](NOTICE.md).
