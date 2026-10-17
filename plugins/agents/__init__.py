"""AI 对话插件：拥有一个隔离的官方 DSH Web 进程。

DSH 跑在自己的 origin 上（本地 ``3080``，私有反代端口 ``8770``），因为这一版
不支持子路径部署。本插件只做三件事：按需拉起、给出**正确 origin** 的入口、
提供一条不含令牌的状态路由。

DeepSeek 凭据只注入子进程环境，从不落盘、从不返回给浏览器。取值顺序：
进程环境 → ``$WORKBENCH_HOME/agent-env.json``。
"""
from __future__ import annotations

import http.client
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import threading
import time
from urllib.parse import urlsplit, urlunsplit

from workbench import config
from workbench.errors import WorkbenchError
from workbench.processes import ProcessTree

# The DSH build this integration is written and tested against; reported for
# debugging, never enforced (a newer DSH is allowed to run).
DSH_VERSION = "0.1.5-rc.2"
# Version of the Codex / Claude Code link profile the plugin hands to DSH.
PLUGIN_VERSION = "0.2.4"
PORT = int(config.plugin_setting("agents", "port", 3080))
REMOTE_PORT = int(config.plugin_setting("agents", "remote_port", 8770))
RUNTIME = config.HOME / "dsh-runtime"
DSH_HOME = config.HOME / "dsh-home"
ENV_FILE = config.HOME / "agent-env.json"
BINARY = RUNTIME / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js"
# Installing the runtime is one command, and it has to land exactly where BINARY
# already looks -- so the same line is shown to whoever hits the missing piece.
INSTALL = 'npm install --prefix "%s" @deepseek-ai/dsh' % RUNTIME

_READY_TIMEOUT = 90.0
# Re-entrant: ensure() reports through status() while it still holds the lock.
_lock = threading.RLock()
_state = {"process": None, "ready": False, "error": "", "started": 0.0,
          "launch_url": ""}


# -- credential ---------------------------------------------------------
def _credential() -> tuple[str, str]:
    """The DeepSeek key, from the process environment or the local env file."""
    for name in ("DEEPSEEK_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY"):
        value = os.environ.get(name, "").strip()
        if value:
            return value, os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    try:
        payload = json.loads(ENV_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    for name in ("DEEPSEEK_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY"):
        value = str(payload.get(name) or "").strip()
        if value:
            return value, str(payload.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com")
    raise WorkbenchError(
        "没有找到 DeepSeek API key。请设置环境变量 DEEPSEEK_API_KEY，"
        "或写入 %s（键名 DEEPSEEK_API_KEY）。" % ENV_FILE,
        status=503, code="agents_no_credential")


def _child_env() -> dict:
    """Resolve the key only into the owned child environment."""
    token, base_url = _credential()
    base = urlsplit(base_url.rstrip("/"))
    # This integration is specifically the official DeepSeek service. Never send
    # the credential to a guessed or redirected provider.
    if (base.scheme != "https" or base.hostname != "api.deepseek.com"
            or base.port not in (None, 443) or base.username or base.password
            or base.path not in ("", "/anthropic", "/v1", "/anthropic/v1")
            or base.query or base.fragment):
        raise WorkbenchError("DeepSeek 服务地址不是已确认的官方地址",
                             status=503, code="agents_bad_endpoint")
    env = dict(os.environ)
    for name in ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY",
                 "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL"):
        env.pop(name, None)
    env.update(DSH_HOME=str(DSH_HOME), DEEPSEEK_API_KEY=token,
               DEEPSEEK_BASE_URL="https://api.deepseek.com",
               DSH_TELEMETRY_DISABLED="1",
               RELAY_CLAUDE_LINK_PATH=str(DSH_HOME / "claude-dsh-links.json"),
               RELAY_CODEX_LINK_PATH=str(DSH_HOME / "codex-dsh-links.json"))
    return env


# -- process ------------------------------------------------------------
def available() -> bool:
    """Cheap probe: installed runtime plus a node on PATH.  Starts nothing."""
    return BINARY.is_file() and shutil.which("node") is not None


def reason() -> str:
    """The single line that fixes an unavailable plugin, for the shell to show."""
    if shutil.which("node") is None:
        return "未找到 Node.js：装好 18 以上版本，再执行 " + INSTALL
    if not BINARY.is_file():
        return "未安装 DSH 运行时：执行 " + INSTALL
    return ""


def _assert_port_free() -> None:
    """Fail with a readable message instead of a raw socket error."""
    try:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", PORT))
    except OSError as exc:
        raise WorkbenchError(
            "端口 %d 已被占用（多半是另一个 DSH 实例还在运行），"
            "请先停止它，或在 plugins.json 里改用别的 agents.port。" % PORT,
            status=503, code="agents_port_in_use") from exc


def _http_ready() -> bool:
    connection = http.client.HTTPConnection("127.0.0.1", PORT, timeout=2)
    try:
        connection.request("GET", "/")
        response = connection.getresponse()
        response.read(4096)
        return response.status in (200, 401)
    except OSError:
        return False
    finally:
        connection.close()


def _collect_startup(process) -> None:
    """Capture the browser handoff URL without persisting upstream output."""
    try:
        for line in process.stdout:
            clean = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", line)
            if "dsh web:" not in clean:
                continue
            match = re.search(r"https?://[^\s\x1b]+", clean)
            if not match:
                continue
            parsed = urlsplit(match.group(0))
            if parsed.hostname not in ("127.0.0.1", "localhost") or parsed.port != PORT:
                continue
            with _lock:
                if _state["process"] is process:
                    _state["launch_url"] = match.group(0)
    except (OSError, ValueError):
        pass


def status() -> dict:
    """Only public operational facts -- never the browser token or the key."""
    with _lock:
        process = _state["process"]
        alive = process is not None and process.poll() is None
        return {"ready": bool(alive and _state["ready"]), "running": alive,
                "error": _state["error"], "started": _state["started"],
                "version": DSH_VERSION, "claude_plugin": PLUGIN_VERSION,
                "codex_plugin": PLUGIN_VERSION, "port": PORT,
                "provider": "deepseek-official", "model": "deepseek-flash",
                "installed": available()}


def ensure() -> dict:
    """Start once on demand; concurrent callers share the same child."""
    if not available():
        with _lock:
            _state.update(process=None, ready=False,
                          error=reason() or "DSH 尚未安装到 %s" % RUNTIME)
        return status()
    with _lock:
        process = _state["process"]
        if process is None or process.poll() is not None:
            try:
                env = _child_env()
                _assert_port_free()
                argv = [str(Path(shutil.which("node")).resolve()), str(BINARY.resolve()),
                        "--profile", "web", "--host", "127.0.0.1", "--port", str(PORT),
                        "--no-open"]
                remote = urlsplit(config.REMOTE_ORIGIN)
                if remote.hostname:
                    argv += ["--trusted-host", "%s:%d" % (remote.hostname, REMOTE_PORT)]
                flags = ({"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
                         if os.name == "nt" else {"start_new_session": True})
                process = subprocess.Popen(argv, cwd=config.ROOT, env=env,
                                           stdin=subprocess.DEVNULL,
                                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                           text=True, encoding="utf-8", errors="replace",
                                           **flags)
                process.workbench_tree = ProcessTree(process)
                _state.update(process=process, ready=False, error="",
                              started=time.time(), launch_url="")
                threading.Thread(target=_collect_startup, args=(process,),
                                 daemon=True).start()
            except (OSError, ValueError, WorkbenchError) as exc:
                _state.update(process=None, ready=False,
                              error="DSH 启动失败：%s" % getattr(exc, "message", exc),
                              launch_url="")
                return status()
    deadline = time.monotonic() + _READY_TIMEOUT
    while time.monotonic() < deadline:
        with _lock:
            announced = _state["process"] is process and bool(_state["launch_url"])
        code = process.poll()
        if code is not None:
            with _lock:
                if _state["process"] is process:
                    _state.update(ready=False, error="DSH 启动后退出（exit %s）" % code)
            break
        if announced and _http_ready():
            with _lock:
                if _state["process"] is process:
                    _state.update(ready=True, error="")
            return status()
        time.sleep(0.2)
    else:
        with _lock:
            if _state["process"] is process:
                _state.update(ready=False, error="DSH 启动未在 90 秒内就绪")
    return status()


def entry_url(host_header: str) -> str:
    """The handoff URL on the origin the caller actually reached us on."""
    remote = urlsplit(config.REMOTE_ORIGIN)
    host = urlsplit("//" + (host_header or "")).hostname
    origin = "http://127.0.0.1:%d" % PORT
    if remote.hostname and host == remote.hostname:
        origin = "https://%s:%d" % (remote.hostname, REMOTE_PORT)
    with _lock:
        launch = urlsplit(_state["launch_url"])
    target = urlsplit(origin)
    return urlunsplit((target.scheme, target.netloc, launch.path or "/",
                       launch.query, launch.fragment))


def stop() -> None:
    with _lock:
        process = _state["process"]
        _state.update(process=None, ready=False, launch_url="", started=0.0)
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
    if process.stdout:
        process.stdout.close()


# -- HTTP ---------------------------------------------------------------
def handle(request) -> bool:
    """Serve the plugin's four routes; False lets the host keep looking."""
    path = request.path
    if request.method == "GET" and path in ("/agents", "/agents/"):
        state = ensure()
        if not state.get("ready"):
            raise WorkbenchError(state.get("error") or "DSH 尚未就绪",
                                 status=503, code="agents_unavailable")
        request.redirect(entry_url(request.host()))
        return True
    if request.method == "GET" and path == "/api/agents/status":
        request.json(200, status())
        return True
    if request.method == "POST" and path == "/api/agents/start":
        request.json(200, ensure())
        return True
    if request.method == "POST" and path == "/api/agents/stop":
        stop()
        request.json(200, status())
        return True
    return False
