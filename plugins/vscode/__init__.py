"""编辑器插件：在独立 origin 上提供 VS Code Web。

早期版本把整个工作台反代到 ``/vscode`` 下（418 行，含 HTML 改写与 WebSocket
隧道），因为 ``serve-web`` 拒绝跨 origin 嵌入。改成插件后不再需要：本插件只
负责盯住子进程并给出入口地址，浏览器直接访问 ``serve-web`` 自己的 origin，
少一跳中转，也就少一条故障路径。

安全前提：``serve-web`` 只绑回环，远程访问靠同一个私有反代（例如 Tailscale
serve 把 ``8772`` 转到 ``8799``），和宿主一样不暴露公网。
"""
from __future__ import annotations

import http.client
import json
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from workbench import config
from workbench.errors import WorkbenchError
from workbench.processes import ProcessTree

PORT = int(config.plugin_setting("vscode", "port", 8799))
REMOTE_PORT = int(config.plugin_setting("vscode", "remote_port", 8772))
CLI_SETTING = str(config.plugin_setting("vscode", "cli", ""))
FOLDER_SETTING = str(config.plugin_setting("vscode", "folder", ""))
SERVER_DIR = config.HOME / "vscode-server"

_READY_TIMEOUT = 90.0
# Re-entrant: ensure() reports through status() while it still holds the lock.
_lock = threading.RLock()
_state = {"process": None, "port": 0, "error": "", "started": 0.0}


def _cli() -> Path | None:
    """Locate the VS Code CLI that provides ``serve-web``."""
    candidates = []
    override = os.environ.get("WORKBENCH_VSCODE_CLI", "").strip() or CLI_SETTING
    if override:
        candidates.append(Path(override))
    for name in ("code.cmd", "code"):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        candidates.append(Path(local) / "Programs" / "Microsoft VS Code" / "bin" / "code.cmd")
    for key in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(key, "")
        if base:
            candidates.append(Path(base) / "Microsoft VS Code" / "bin" / "code.cmd")
    for item in candidates:
        if item.is_file():
            return item
    return None


def available() -> bool:
    """Cheap probe: a CLI exists.  Starts nothing."""
    return _cli() is not None


def _configure_folder() -> None:
    """Point the picker at a default folder, but only when one is configured.

    The value lives in the user's own settings file, which may be JSONC: an
    unparseable file is left alone rather than silently rewritten.
    """
    if not FOLDER_SETTING:
        return
    settings = SERVER_DIR / "data" / "User" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    if settings.exists():
        try:
            values = json.loads(settings.read_text(encoding="utf-8-sig"))
        except (ValueError, OSError):
            return
    else:
        values = {}
    if not isinstance(values, dict):
        return
    values["files.dialog.defaultPath"] = FOLDER_SETTING
    settings.write_text(json.dumps(values, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")


def _ready(port: int, process) -> bool:
    deadline = time.time() + _READY_TIMEOUT
    while time.time() < deadline:
        if process.poll() is not None:
            return False
        try:
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            try:
                connection.request("GET", "/")
                response = connection.getresponse()
                response.read(4096)
                if response.status in (200, 301, 302):
                    return True
            finally:
                connection.close()
        except OSError:
            time.sleep(0.3)
    return False


def status() -> dict:
    with _lock:
        process = _state["process"]
        alive = process is not None and process.poll() is None
        cli = _cli()
        return {"ready": bool(alive and _state["port"]), "running": alive,
                "error": _state["error"], "started": _state["started"],
                "port": _state["port"], "installed": cli is not None,
                "cli": str(cli) if cli else ""}


def entry_url(host_header: str) -> str:
    """The origin the caller should open: loopback locally, private remote otherwise."""
    remote = urlsplit(config.REMOTE_ORIGIN)
    host = urlsplit("//" + (host_header or "")).hostname
    if remote.hostname and host == remote.hostname:
        return "https://%s:%d/" % (remote.hostname, REMOTE_PORT)
    with _lock:
        port = _state["port"] or PORT
    return "http://127.0.0.1:%d/" % port


def ensure() -> dict:
    """Start the workbench once; concurrent callers share the same process."""
    with _lock:
        process = _state["process"]
        if process is not None and process.poll() is None:
            return status()
        cli = _cli()
        if cli is None:
            _state.update(process=None, port=0, error="找不到 VS Code CLI，"
                          "可用 WORKBENCH_VSCODE_CLI 或 plugins.json 的 vscode.cli 指定")
            return status()
        port = PORT
        # A surviving old listener must not trap a newly started host.
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
        SERVER_DIR.mkdir(parents=True, exist_ok=True)
        _configure_folder()
        argv = [str(cli), "serve-web", "--host", "127.0.0.1", "--port", str(port),
                "--without-connection-token", "--accept-server-license-terms",
                "--server-data-dir", str(SERVER_DIR), "--disable-telemetry"]
        flags = ({"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
                 if os.name == "nt" else {"start_new_session": True})
        try:
            process = subprocess.Popen(argv, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, **flags)
            process.workbench_tree = ProcessTree(process)
        except OSError as exc:
            _state.update(process=None, port=0, error="无法启动 VS Code Web: %s" % exc)
            return status()
        _state.update(process=process, port=port, started=time.time(), error="")
    if not _ready(port, process):
        with _lock:
            if _state["process"] is process:
                _state.update(error="VS Code Web 未能就绪")
        return status()
    return status()


def stop() -> None:
    with _lock:
        process = _state["process"]
        _state.update(process=None, port=0, started=0.0)
    if process is None:
        return
    tree = getattr(process, "workbench_tree", None)
    if tree:
        tree.close()
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def handle(request) -> bool:
    path = request.path
    if request.method == "GET" and path in ("/vscode", "/vscode/"):
        state = ensure()
        if not state.get("ready"):
            raise WorkbenchError(state.get("error") or "VS Code Web 尚未就绪",
                                 status=503, code="vscode_unavailable")
        request.redirect(entry_url(request.host()))
        return True
    if request.method == "GET" and path == "/api/vscode/status":
        request.json(200, status())
        return True
    if request.method == "POST" and path == "/api/vscode/start":
        request.json(200, ensure())
        return True
    if request.method == "POST" and path == "/api/vscode/stop":
        stop()
        request.json(200, status())
        return True
    return False
