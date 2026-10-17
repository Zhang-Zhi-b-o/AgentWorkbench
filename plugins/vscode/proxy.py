"""Relay the VS Code web workbench under the host's own origin.

``code serve-web`` refuses framing from another origin, so a bare iframe to its
port cannot work, and a phone on a private network must not need a second
exposed port.  This module owns the child process and proxies the whole
workbench under ``/vscode``: the server is started with that same base path, so
its own URLs already match and requests need no rewriting beyond pointing the
baked-in authority at the host.  Web sockets are tunnelled byte for byte.

Webview frames are the subtle part.  They are normally served from
``<origin-hash>.vscode-cdn.net``, which a tablet on a tailnet cannot resolve, so
the endpoint template is repointed at this host and the frame's own hostname
guard is patched to accept it -- together with re-hashing the inline bootstrap
that the page's CSP pins.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import http.client
import json
import os
import select
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from workbench import config
from workbench.processes import ProcessTree

PREFIX = "/vscode"
# Webview frames get their own path prefix: the workbench puts a per-webview
# origin hash where ``{{uuid}}`` sits in the endpoint template, and everything
# after it is the stock ``pre/`` directory.
WEBVIEW_PREFIX = "/vscode-webview"
# Loopback port the workbench listens on; the host proxies it under PREFIX.
PORT = int(config.plugin_setting("vscode", "port", 8799))
SERVER_DIR = config.HOME / "vscode-server"
# Only written when configured, so a fresh install leaves the user's own
# settings file alone.
DEFAULT_FOLDER = str(config.plugin_setting("vscode", "folder", "") or "")
_READY_TIMEOUT = 90.0
_UPSTREAM_TIMEOUT = 120

_state = {"process": None, "port": 0, "error": "", "started": 0.0}
_lock = threading.Lock()


def _cli() -> Path | None:
    """Locate the VS Code CLI that provides ``serve-web``."""
    candidates = []
    override = os.environ.get("WORKBENCH_VSCODE_CLI", "").strip()
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


def _ready(port: int, process) -> bool:
    deadline = time.time() + _READY_TIMEOUT
    while time.time() < deadline:
        if process.poll() is not None:
            return False
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            try:
                conn.request("GET", PREFIX + "/")
                response = conn.getresponse()
                response.read()
                if response.status == 200:
                    return True
            finally:
                conn.close()
        except OSError:
            time.sleep(0.3)
    return False


def _configure_dialog() -> None:
    """The picker uses remote user settings, independently of default-folder."""
    settings = SERVER_DIR / "data" / "User" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    if settings.exists():
        # Preserve manually maintained JSONC rather than silently discarding it.
        try:
            values = json.loads(settings.read_text(encoding="utf-8-sig"))
        except (ValueError, OSError):
            return
    else:
        values = {}
    values["files.dialog.defaultPath"] = DEFAULT_FOLDER
    settings.write_text(json.dumps(values, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")


def start() -> dict:
    """Start the workbench once; concurrent callers share the same process."""
    with _lock:
        process = _state["process"]
        if process is not None and process.poll() is None:
            return dict(_state)
        cli = _cli()
        if cli is None:
            _state.update(process=None, port=0, started=0.0,
                          error="找不到 VS Code CLI，可用 WORKBENCH_VSCODE_CLI 指定路径")
            return dict(_state)
        port = PORT
        # A surviving old listener must not trap a newly started Hub.
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
        SERVER_DIR.mkdir(parents=True, exist_ok=True)
        _configure_dialog()
        argv = [str(cli), "serve-web", "--host", "127.0.0.1", "--port", str(port),
                "--server-base-path", PREFIX,
                "--without-connection-token", "--accept-server-license-terms",
                "--server-data-dir", str(SERVER_DIR),
                "--disable-telemetry"]
        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = (getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                                      | getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            kwargs["start_new_session"] = True
        try:
            process = subprocess.Popen(argv, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, **kwargs)
            process.workbench_tree = ProcessTree(process)
        except OSError as exc:
            _state.update(process=None, port=0, started=0.0,
                          error="无法启动 VS Code Web: %s" % exc)
            return dict(_state)
        _state.update(process=process, port=port, started=time.time(), error="")
    if not _ready(port, process):
        with _lock:
            if _state["process"] is process:
                _state.update(error="VS Code Web 未能在 %d 秒内就绪" % _READY_TIMEOUT)
        return dict(_state)
    return dict(_state)


def stop() -> None:
    """Terminate the workbench and everything it spawned."""
    with _lock:
        process = _state.get("process")
        _state.update(process=None, port=0, started=0.0)
    if process is None:
        return
    tree = getattr(process, "workbench_tree", None)
    if tree is not None:
        tree.close()  # the job object kills the whole tree
    if process.poll() is None:
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


_COMPRESSIBLE = ("text/", "application/javascript", "application/json",
                 "application/xml", "image/svg+xml")


def _compressible(content_type: str) -> bool:
    return content_type.lower().startswith(_COMPRESSIBLE)


_PRODUCT_ANCHOR = "&quot;productConfiguration&quot;:{"
# The frame refuses to start unless its own hostname is the base32 sha256 of
# (parentOrigin, salt), and serves nothing else.
_HOSTNAME_GUARD = "throw new Error(`Expected '${parentOriginHash}' as hostname or subdomain!`);"
_GUARD_MARK = "// validation succeeded!"
def _origin(handler) -> str:
    """Absolute origin this request arrived on, scheme included."""
    host = handler.headers.get("Host") or ""
    remote = urlsplit(config.REMOTE_ORIGIN)
    if remote.scheme and host.lower() == remote.netloc.lower():
        return config.REMOTE_ORIGIN
    return "http://" + host


def _inject_webview_endpoint(text: str, handler) -> str:
    """Serve webview frames from the Hub instead of the public CDN.

    ``{{uuid}}`` is the hash of the parent origin and a fresh random salt, so
    the frame can only ever be embedded from ``<hash>.vscode-cdn.net``.  Clients
    that cannot resolve that name -- a tablet on the tailnet, say -- get a blank
    panel.  The Hub answers for it instead; the workbench still substitutes the
    same hash, it just lands in the path rather than in the host.
    """
    if _PRODUCT_ANCHOR not in text:
        return text
    endpoint = "%s%s/{{uuid}}/{{quality}}/{{commit}}/out/vs/workbench/contrib/webview/browser/pre/" % (
        _origin(handler), WEBVIEW_PREFIX)
    return text.replace(
        _PRODUCT_ANCHOR,
        _PRODUCT_ANCHOR + "&quot;webviewContentExternalBaseUrlTemplate&quot;:&quot;"
        + endpoint + "&quot;,", 1)


def _patch_webview_pre(text: str) -> str:
    """Drop the hostname check the Hub's own path prefix cannot satisfy.

    The check exists to keep the frame from being embedded by a foreign origin;
    here the Hub *is* the parent origin, and it is the only origin allowed to
    serve this document, so the rejection branch is pointed at the same
    acceptance path the check itself takes.  The page pins its inline bootstrap
    with a CSP hash, so the script is re-hashed afterwards.
    """
    if _HOSTNAME_GUARD not in text:
        return text
    text = text.replace(_HOSTNAME_GUARD, "return start(parentOrigin);", 1)
    text = text.replace(_CSP_ANCHOR, _CSP_ANCHOR + " connect-src 'self';", 1)
    at = text.index(_GUARD_MARK)
    code = text[text.index(">", text.rindex("<script", 0, at)) + 1:text.index("</script>", at)]
    digest = base64.b64encode(
        hashlib.sha256(code.replace("\r\n", "\n").encode("utf-8")).digest()).decode("ascii")
    key = "script-src 'sha256-"
    start = text.index(key) + len(key)
    end = text.index("'", start)
    return text[:start] + digest + text[end:]


def _rewrite(text: str, host: str, port: int) -> str:
    """Point the workbench's own authority at the Hub.

    The page bakes in ``127.0.0.1:<port>`` for its management socket and its
    extension-resource URLs; leaving it would send the browser straight past
    this proxy whenever the Hub is reached from another machine.
    """
    if not host:
        return text
    return text.replace("127.0.0.1:%d" % port, host)


def _webview_target(path: str) -> str | None:
    """Map a webview frame URL onto the stock ``pre/`` directory it mirrors."""
    parts = path[len(WEBVIEW_PREFIX):].lstrip("/").split("/", 3)
    if len(parts) != 4 or not parts[3]:
        return None
    return "%s/%s-%s/static/%s" % (PREFIX, parts[1], parts[2], parts[3])


def proxy(handler, path: str, query: str = "") -> None:
    """Relay one Hub request to the workbench."""
    state = start()
    if state["error"]:
        return handler._json(503, {"error": state["error"], "code": "vscode_unavailable"})
    # The server was started on the same base path, so the path goes through as is.
    if path.startswith(WEBVIEW_PREFIX + "/"):
        target = _webview_target(path)
        if target is None:
            return handler._json(404, {"error": "unknown webview path",
                                       "code": "not_found"})
    else:
        target = path
    if query:
        target += "?" + query
    if (handler.headers.get("Upgrade") or "").lower() == "websocket":
        return _tunnel(handler, target, state["port"])
    return _relay(handler, target, state["port"])


def _relay(handler, target: str, port: int) -> None:
    length = int(handler.headers.get("Content-Length") or 0)
    body = handler.rfile.read(length) if length else None
    headers = {key: value for key, value in handler.headers.items()
               if key.lower() not in ("host", "connection",
                                      "content-length", "transfer-encoding",
                                      "accept-language")}
    headers["Host"] = "127.0.0.1:%d" % port
    # The workbench is served in the requested display language, and only the English
    # message table matches this build: unpkg's zh-cn table belongs to another release,
    # so a Chinese browser gets every string shifted onto the wrong index.
    headers["Accept-Language"] = "en-US,en;q=0.9"
    # The locale cookie takes precedence over Accept-Language in serve-web.
    if "Cookie" in headers:
        headers["Cookie"] = "; ".join(
            item.strip() for item in headers["Cookie"].split(";")
            if item.strip().partition("=")[0] != "vscode.nls.locale")
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=_UPSTREAM_TIMEOUT)
    try:
        conn.request(handler.command, target, body=body, headers=headers)
        response = conn.getresponse()
        data = response.read()
    except OSError as exc:
        conn.close()
        return handler._json(502, {"error": "VS Code Web 无响应: %s" % exc,
                                   "code": "vscode_unreachable"})
    content_type = response.getheader("Content-Type") or ""
    encoding = (response.getheader("Content-Encoding") or "").strip().lower()
    document = target.partition("?")[0]
    if content_type.startswith("text/html"):
        if encoding == "gzip":
            data = gzip.decompress(data)
            encoding = ""
        text = _rewrite(data.decode("utf-8", "replace"),
                        handler.headers.get("Host") or "", port)
        text = _inject_webview_endpoint(text, handler)
        if document.endswith("/webview/browser/pre/index.html"):
            text = _patch_webview_pre(text)
        data = text.encode("utf-8")
    # serve-web ships the ~20 MB workbench bundle uncompressed and ignores
    # Accept-Encoding, so compressing here is the only thing that makes the
    # editor open quickly on a phone.
    if (not encoding and len(data) > 1024
            and "gzip" in (handler.headers.get("Accept-Encoding") or "").lower()
            and _compressible(content_type)):
        data = gzip.compress(data, 6)
        encoding = "gzip"
    handler.send_response(response.status)
    for key, value in response.getheaders():
        if key.lower() in ("content-length", "connection", "transfer-encoding",
                           "content-encoding"):
            continue
        handler.send_header(key, value)
    if encoding:
        handler.send_header("Content-Encoding", encoding)
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    conn.close()
    if handler.command == "HEAD":
        return
    try:
        handler.wfile.write(data)
    except (BrokenPipeError, ConnectionResetError):
        pass


def _tunnel(handler, target: str, port: int) -> None:
    """Carry a web socket through untouched; it is what keeps the workbench alive."""
    try:
        upstream = socket.create_connection(("127.0.0.1", port), timeout=30)
    except OSError as exc:
        return handler._json(502, {"error": "VS Code Web 无响应: %s" % exc,
                                   "code": "vscode_unreachable"})
    request = "%s %s HTTP/1.1\r\n" % (handler.command, target)
    for key, value in handler.headers.items():
        # WebKit can advertise this legacy extension but reject serve-web's
        # negotiated response. Leave compression unnegotiated for that offer;
        # the browser and server then exchange ordinary WebSocket frames.
        if (key.lower() == "sec-websocket-extensions"
                and "x-webkit-deflate-frame" in value.lower()):
            continue
        if key.lower() != "host":
            request += "%s: %s\r\n" % (key, value)
    request += "Host: 127.0.0.1:%d\r\n\r\n" % port
    try:
        upstream.sendall(request.encode("latin-1"))
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = upstream.recv(4096)
            if not chunk:
                raise ConnectionResetError
            head += chunk
        head, _, rest = head.partition(b"\r\n\r\n")
        handler.connection.sendall(head + b"\r\n\r\n" + rest)
        _pump(handler.connection, upstream)
    except OSError:
        pass
    finally:
        try:
            upstream.close()
        except OSError:
            pass
        handler.close_connection = True


def _pump(client: socket.socket, upstream: socket.socket) -> None:
    while True:
        ready, _, _ = select.select([client, upstream], [], [], 300)
        if not ready:
            return
        for source in ready:
            data = source.recv(65536)
            if not data:
                return
            peer = upstream if source is client else client
            peer.sendall(data)


# -- plugin interface ---------------------------------------------------
def available() -> bool:
    """Cheap probe: a CLI exists.  Starts nothing."""
    return _cli() is not None


def status() -> dict:
    cli = _cli()
    with _lock:
        process = _state["process"]
        alive = process is not None and process.poll() is None
        return {"ready": bool(alive and _state["port"]), "running": alive,
                "error": _state["error"], "started": _state["started"],
                "port": _state["port"], "installed": cli is not None,
                "cli": str(cli) if cli else "", "mode": "same-origin proxy"}


def handle(request) -> bool:
    """Serve /vscode and /vscode-webview through the proxy; False otherwise."""
    path = request.path
    if request.method == "GET" and path == "/api/vscode/status":
        request.json(200, status())
        return True
    if request.method == "POST" and path == "/api/vscode/start":
        request.json(200, start())
        return True
    if request.method == "POST" and path == "/api/vscode/stop":
        stop()
        request.json(200, status())
        return True
    if path == PREFIX or path.startswith((PREFIX + "/", WEBVIEW_PREFIX + "/")):
        proxy(request.raw, path, request.raw_query)
        return True
    return False
