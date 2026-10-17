"""ONLYOFFICE 文档服务的接入层。

工作台不自己实现 Office：本机装一个 ONLYOFFICE Docs（官方 Windows 安装包），
Word/Excel/PPT 的渲染与编辑都在它里面完成，本模块负责四件事：

1. **探测** —— ``/healthcheck`` 有答复才算可用；
2. **会话** —— 为一个文件生成 DocsAPI 配置，并把文件地址、保存回调都签成短时票
   （``document.url`` 与 ``callbackUrl`` 由文档服务自己访问，所以走回环地址）；
3. **保存** —— 文档服务的保存回调把改好的文件下载下来，原子替换原文件；
4. **同源反代** —— 文档服务通常只监听一个回环端口，远程浏览器到不了它。这里把
   ``/office/`` 整个反代到工作台自己的 origin（含 WebSocket），手机端因此只需要
   工作台这一个入口，也不用再暴露第二个端口。

JWT 与文档服务同源配置里的密钥一致；密钥为空时照常可用，但保存回调不再验签。
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import hmac
import http.client
import json
import os
import secrets
import select
import shutil
import socket
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import quote, urlsplit

from workbench import config
from workbench.errors import WorkbenchError

# The editor's own script/iframe prefix on the workbench origin.
PREFIX = "/office"

DOCS_URL = str(config.plugin_setting("office", "docs_url", "http://127.0.0.1:8080")
               or "http://127.0.0.1:8080").rstrip("/")
# Where the document server reaches this workbench.  It runs on the same machine,
# so the loopback address is both reachable and free of TLS trust questions.
LOOPBACK = "http://127.0.0.1:%d" % config.PORT
SECRET_FILE = config.HOME / "onlyoffice" / "jwt-secret.txt"

# The document server answers "true" from /healthcheck once it is up.
HEALTH_TTL = 8.0
TOKEN_TTL = 12 * 3600
_TOKEN_LIMIT = 512

# Document extensions the editor understands, with their document type.
_DOC_TYPES = {
    "docx": "word", "doc": "word", "docm": "word", "dot": "word", "dotx": "word",
    "odt": "word", "ott": "word", "rtf": "word", "txt": "word", "html": "word",
    "xlsx": "cell", "xls": "cell", "xlsm": "cell", "xlt": "cell", "ods": "cell",
    "ots": "cell", "csv": "cell",
    "pptx": "slide", "ppt": "slide", "pptm": "slide", "pot": "slide", "potx": "slide",
    "odp": "slide", "otp": "slide",
}

_lock = threading.Lock()
_health = {"at": 0.0, "ok": False, "error": "", "docs": DOCS_URL, "version": ""}
_tokens: dict[str, dict] = {}
_rewrite_cache: dict[tuple[str, str], tuple[str, bytes]] = {}


# -- configuration ------------------------------------------------------
def _secret() -> str:
    """JWT secret shared with the document server's local.json."""
    value = str(config.plugin_setting("office", "jwt_secret", "") or "").strip()
    if value:
        return value
    try:
        return SECRET_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return os.environ.get("ONLYOFFICE_JWT_SECRET", "").strip()


def _upstream() -> tuple[str, str, int]:
    parsed = urlsplit(DOCS_URL)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return parsed.scheme or "http", host, port


def _origin(handler) -> str:
    """Absolute origin this request arrived on, scheme included."""
    host = handler.headers.get("Host") or ""
    remote = urlsplit(config.REMOTE_ORIGIN)
    if remote.scheme and host.lower() == remote.netloc.lower():
        return config.REMOTE_ORIGIN
    return "http://" + host


def _forwarded(handler) -> tuple[str, str]:
    """Headers that tell the document server which URL it is published under.

    The server builds absolute URLs of its own (``/cache/files/.../Editor.bin``
    is what the editor fetches to open a document) from ``scheme://X-Forwarded-Host``,
    so the sub-path has to travel inside that host value -- this is what the
    official virtual-path proxy does (document-server-proxy,
    ``nginx/proxy-to-virtual-path.conf``).  Without it the editor asks the
    document server directly on its own port, which the browser blocks as
    cross-origin and reports as a failed download.
    """
    origin = urlsplit(_origin(handler))
    host = (origin.netloc or handler.headers.get("Host") or "") + PREFIX
    return host, origin.scheme or "http"


# -- health -------------------------------------------------------------
def health(force: bool = False) -> dict:
    """Cheap availability probe, cached for a few seconds."""
    with _lock:
        if not force and time.time() - _health["at"] < HEALTH_TTL:
            return dict(_health)
    ok, error = False, ""
    try:
        scheme, host, port = _upstream()
        conn = (http.client.HTTPSConnection(host, port, timeout=3)
                if scheme == "https" else http.client.HTTPConnection(host, port, timeout=3))
        try:
            conn.request("GET", "/healthcheck")
            response = conn.getresponse()
            body = response.read(64).decode("utf-8", "replace").strip().lower()
            ok = response.status == 200 and body.startswith("true")
            if not ok:
                error = "文档服务返回 HTTP %d" % response.status
        finally:
            conn.close()
    except OSError as exc:
        error = "连接不上文档服务 %s：%s" % (DOCS_URL, exc)
    with _lock:
        _health.update(at=time.time(), ok=ok, error="" if ok else error)
        return {"ok": ok, "error": "" if ok else error, "docs": DOCS_URL}


def install_hint() -> str:
    return ("未检测到 ONLYOFFICE 文档服务。在装有它的电脑上运行 "
            "https://download.onlyoffice.com/install/documentserver/windows/"
            "onlyoffice-documentserver.exe（或 Docker 的 onlyoffice/documentserver），"
            "装好后把地址写进 plugins.json 的 office.docs_url。")


# -- JWT ----------------------------------------------------------------
def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def sign(payload: dict, secret: str) -> str:
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"},
                             separators=(",", ":")).encode("utf-8"))
    body = _b64(json.dumps(payload, separators=(",", ":"),
                           ensure_ascii=False).encode("utf-8"))
    signing = ("%s.%s" % (header, body)).encode("ascii")
    mac = hmac.new(secret.encode("utf-8"), signing, hashlib.sha256).digest()
    return "%s.%s.%s" % (header, body, _b64(mac))


def verify(token: str, secret: str) -> dict | None:
    parts = str(token or "").split(".")
    if len(parts) != 3:
        return None
    signing = ("%s.%s" % (parts[0], parts[1])).encode("ascii")
    expected = hmac.new(secret.encode("utf-8"), signing, hashlib.sha256).digest()
    try:
        given = base64.urlsafe_b64decode(parts[2] + "=" * (-len(parts[2]) % 4))
        payload = json.loads(base64.urlsafe_b64decode(
            parts[1] + "=" * (-len(parts[1]) % 4)).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not hmac.compare_digest(expected, given) or not isinstance(payload, dict):
        return None
    return payload


# -- session ------------------------------------------------------------
def _prune() -> None:
    now = time.time()
    expired = [key for key, value in _tokens.items() if value["exp"] < now]
    for key in expired:
        _tokens.pop(key, None)
    if len(_tokens) > _TOKEN_LIMIT:
        for key, _value in sorted(_tokens.items(), key=lambda item: item[1]["exp"])[:64]:
            _tokens.pop(key, None)


def issue(path: Path) -> str:
    token = secrets.token_urlsafe(24)
    with _lock:
        _prune()
        _tokens[token] = {"path": str(path), "exp": time.time() + TOKEN_TTL}
    return token


def lookup(token: str) -> Path | None:
    with _lock:
        entry = _tokens.get(token)
    if not entry or entry["exp"] < time.time():
        return None
    return Path(entry["path"])


def _document_key(path: Path) -> str:
    """Changes whenever the file changes, so the server never serves a stale copy."""
    try:
        info = path.stat()
        seed = "%s|%d|%d" % (path, info.st_mtime_ns, info.st_size)
    except OSError:
        seed = str(path)
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:24]


def session(path: Path, origin: str, *, mode: str = "edit") -> dict:
    """One document: the DocsAPI config and the script it needs.

    ``mode`` is ``edit`` or ``view``; view clears the edit permissions as well,
    because the document server takes the mode from the permissions when the
    signed token is what it reads.

    The editor type is always ``desktop``.  ONLYOFFICE Docs only lets the free
    Community edition *view* documents in its mobile web editor -- editing there
    belongs to the paid editions, and an iPad that gets ``type: mobile`` therefore
    opens read-only however the permissions are set.  The desktop web editor is
    fully editable in the free edition, so tablets and phones get that one.
    """
    suffix = path.suffix.lower().lstrip(".")
    document_type = _DOC_TYPES.get(suffix)
    if document_type is None:
        raise WorkbenchError("ONLYOFFICE 不能编辑这种文件：.%s" % suffix,
                             status=415, code="unsupported_office_type")
    if not path.is_file():
        raise WorkbenchError("文件不存在", status=404, code="path_not_found")
    editable = mode != "view"
    token = issue(path)
    config_payload = {
        "document": {
            "fileType": suffix,
            "key": _document_key(path),
            "title": path.name,
            "url": "%s/api/office/file/%s" % (LOOPBACK, token),
            "permissions": {"edit": editable, "download": True, "print": True},
        },
        "documentType": document_type,
        "editorConfig": {
            "callbackUrl": "%s/api/office/callback/%s" % (LOOPBACK, token),
            "lang": "zh-CN",
            "mode": "edit" if editable else "view",
            "coEditing": {"mode": "fast", "change": False},
            "customization": {
                "autosave": editable,
                "forcesave": editable,
                "compactHeader": True,
                "help": False,
                "hideRightMenu": False,
                "uiTheme": "theme-dark" if _dark_theme() else "theme-light",
            },
            "user": {"id": "workbench", "name": "工作台"},
            "permissions": {"edit": editable, "download": True, "print": True,
                            "comment": editable, "fillForms": editable},
        },
        "type": "desktop",
        "width": "100%",
        "height": "100%",
    }
    secret = _secret()
    if secret:
        config_payload["token"] = sign(config_payload, secret)
    return {"config": config_payload, "script": PREFIX + "/web-apps/apps/api/documents/api.js",
            "title": path.name, "path": str(path), "origin": origin,
            "mode": "edit" if editable else "view", "editable": editable,
            "jwt": bool(secret), "docs": DOCS_URL}


def _dark_theme() -> bool:
    """The editor is rendered before the browser tells us; default to the light UI."""
    return False


# -- save callback ------------------------------------------------------
def _callback_payload(body: dict, handler) -> dict:
    """The trusted callback object: verified JWT payload when signing is on."""
    secret = _secret()
    if not secret:
        return body
    supplied = ""
    authorization = handler.headers.get("Authorization") or ""
    if authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    if not supplied:
        supplied = str(body.get("token") or "")
    payload = verify(supplied, secret)
    if payload is None:
        raise WorkbenchError("保存回调的签名无效", status=403, code="bad_jwt")
    if isinstance(payload.get("payload"), dict):
        payload = payload["payload"]
    return payload


def _download(url: str, target: Path) -> None:
    """Fetch the edited copy from the document server and swap it into place."""
    parsed = urlsplit(url)
    scheme, host, port = _upstream()
    if parsed.scheme not in ("http", "https") or parsed.hostname not in (
            host, "127.0.0.1", "localhost"):
        raise WorkbenchError("保存地址不是本机文档服务：%s" % url, status=400,
                             code="bad_callback_url")
    try:
        with urllib.request.urlopen(url, timeout=120) as response:
            temp = target.with_name("." + target.name + ".wb-save")
            with temp.open("wb") as stream:
                shutil.copyfileobj(response, stream, length=1024 * 1024)
        os.replace(temp, target)
    except OSError as exc:
        raise WorkbenchError("写回文件失败：%s" % exc, status=502,
                             code="save_failed") from exc


def callback(token: str, request) -> dict:
    target = lookup(token)
    if target is None:
        raise WorkbenchError("保存票已失效，请重新打开文档", status=403,
                             code="expired_ticket")
    payload = _callback_payload(request.body or {}, request.raw)
    try:
        status = int(payload.get("status") or 0)
    except (TypeError, ValueError):
        status = 0
    url = str(payload.get("url") or "")
    if status in (2, 6) and url:
        _download(url, target)
    return {"error": 0}


# -- reverse proxy ------------------------------------------------------
_HOP_HEADERS = {"host", "connection", "content-length", "transfer-encoding",
                "keep-alive", "proxy-connection", "upgrade", "accept-language"}
_TEXTUAL = ("text/", "application/javascript", "application/json", "application/xml",
            "image/svg+xml", "application/x-javascript")
_UPSTREAM_TIMEOUT = 120


def _textual(content_type: str) -> bool:
    return content_type.lower().startswith(_TEXTUAL)


def _rewrite(text: str, origin: str) -> str:
    """Point every absolute document-server URL at the workbench origin."""
    parsed = urlsplit(DOCS_URL)
    host = parsed.netloc
    replacements = {
        DOCS_URL: origin + PREFIX,
        "http://" + host: origin + PREFIX,
        "//" + host: "//" + urlsplit(origin).netloc + PREFIX,
    }
    for needle, value in replacements.items():
        if needle and needle != value:
            text = text.replace(needle, value)
    return text


# The editor registers its own caching service worker.  That worker assumes it is
# served from the document server's root: it derives URLs from its own location and
# keeps a FIFO copy of the document binary per document id.  Under ``/office`` those
# assumptions do not hold, and a browser that registered it during an earlier,
# broken build keeps being served stale pieces of the editor -- which shows up as an
# editor that opens but will not edit.  We answer that one URL with a worker that
# registers, claims and then never intercepts anything, so every asset comes
# straight from the document server.
_PASS_THROUGH_WORKER = """
/* AgentWorkbench: replaces the document server's caching worker under /office. */
self.addEventListener('install', function () { self.skipWaiting(); });
self.addEventListener('activate', function (event) {
  event.waitUntil(
    caches.keys()
      .then(function (names) {
        return Promise.all(names.map(function (name) { return caches.delete(name); }));
      })
      .then(function () { return self.clients.claim(); })
      .catch(function () {})
  );
});
""".lstrip()


def _service_worker(handler, target: str) -> bool:
    """Answer the editor's service-worker URL with the pass-through worker."""
    if not target.split("?", 1)[0].endswith("/document_editor_service_worker.js"):
        return False
    body = _PASS_THROUGH_WORKER.encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "application/javascript; charset=utf-8")
    handler.send_header("Cache-Control", "no-cache, must-revalidate")
    handler.send_header("Service-Worker-Allowed", "/")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    if handler.command != "HEAD":
        try:
            handler.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            handler.close_connection = True
    return True


def proxy(handler, path: str, query: str = "") -> None:
    """Relay one request under ``/office`` to the document server."""
    origin = _origin(handler)
    if (handler.headers.get("Upgrade") or "").lower() == "websocket":
        return _tunnel(handler, path, query)
    scheme, host, port = _upstream()
    target = path[len(PREFIX):] or "/"
    if query:
        target += "?" + query
    if _service_worker(handler, target):
        return
    length = int(handler.headers.get("Content-Length") or 0)
    body = handler.rfile.read(length) if length else None
    headers = {key: value for key, value in handler.headers.items()
               if key.lower() not in _HOP_HEADERS
               and key.lower() != "accept-encoding"
               and not key.lower().startswith("x-forwarded-")}
    headers["Host"] = "%s:%d" % (host, port)
    headers["Accept-Language"] = "zh-CN,zh;q=0.9,en;q=0.8"
    forwarded_host, forwarded_proto = _forwarded(handler)
    headers["X-Forwarded-Host"] = forwarded_host
    headers["X-Forwarded-Proto"] = forwarded_proto
    headers["X-Forwarded-For"] = handler.client_address[0]
    # Always ask for an identity body.  When the browser advertises gzip, nginx
    # answers some static files (the editor's .template bundle) chunked and
    # compressed, i.e. with no Content-Length we could pass on -- and an unframed
    # response leaves the browser waiting forever, holding one of its six
    # connections per origin.  Textual bodies are compressed for the client below.
    headers["Accept-Encoding"] = "identity"
    conn = (http.client.HTTPSConnection(host, port, timeout=_UPSTREAM_TIMEOUT)
            if scheme == "https" else http.client.HTTPConnection(host, port,
                                                                 timeout=_UPSTREAM_TIMEOUT))
    try:
        conn.request(handler.command, target, body=body, headers=headers)
        response = conn.getresponse()
    except OSError as exc:
        conn.close()
        return _fail(handler, "ONLYOFFICE 文档服务无响应：%s" % exc)
    content_type = response.getheader("Content-Type") or ""
    if _textual(content_type):
        data = response.read()
        encoding = (response.getheader("Content-Encoding") or "").strip().lower()
        if encoding == "gzip":
            try:
                data = gzip.decompress(data)
            except OSError:
                pass
            encoding = ""
        data = _rewritten(handler.command, target, origin, data)
        if (len(data) > 1024 and "gzip" in (handler.headers.get("Accept-Encoding") or "").lower()
                and _textual(content_type)):
            data = gzip.compress(data, 6)
            encoding = "gzip"
        handler.send_response(response.status)
        for key, value in response.getheaders():
            if key.lower() in ("content-length", "connection", "transfer-encoding",
                               "content-encoding", "content-security-policy"):
                continue
            handler.send_header(key, value)
        if encoding:
            handler.send_header("Content-Encoding", encoding)
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        if handler.command != "HEAD":
            try:
                handler.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass
    else:
        _stream(handler, response, target)
    conn.close()


def _rewritten(command: str, target: str, origin: str, data: bytes) -> bytes:
    key = (target, origin)
    with _lock:
        cached = _rewrite_cache.get(key)
    if cached is not None:
        return cached[1]
    text = _rewrite(data.decode("utf-8", "replace"), origin)
    payload = text.encode("utf-8")
    # Bound the cache: the editor's bundle is a few MB, and there are not many.
    with _lock:
        if len(_rewrite_cache) > 32:
            _rewrite_cache.clear()
        _rewrite_cache[key] = (command, payload)
    return payload


def _stream(handler, response, target: str) -> None:
    """Relay a binary body through unchanged, keeping the client-side framing valid.

    Upstream does not always declare a length, so when it does not we frame the
    body as chunked ourselves instead of just forwarding it and leaving the
    connection open: a body with neither a length nor chunk framing never ends for
    the browser, which then blocks on that connection until it gives up.
    """
    length = response.getheader("Content-Length")
    chunked = length is None and handler.command != "HEAD"
    handler.send_response(response.status)
    for key, value in response.getheaders():
        if key.lower() in ("connection", "transfer-encoding", "content-length"):
            continue
        handler.send_header(key, value)
    if chunked:
        handler.send_header("Transfer-Encoding", "chunked")
    elif length is not None:
        handler.send_header("Content-Length", length)
    handler.end_headers()
    if handler.command == "HEAD":
        return
    try:
        while True:
            chunk = response.read(65536)
            if not chunk:
                break
            if chunked:
                handler.wfile.write(b"%x\r\n" % len(chunk) + chunk + b"\r\n")
            else:
                handler.wfile.write(chunk)
        if chunked:
            handler.wfile.write(b"0\r\n\r\n")
    except (BrokenPipeError, ConnectionResetError):
        handler.close_connection = True


def _fail(handler, message: str) -> None:
    handler._json(503, {"error": message, "code": "office_unavailable"})


def _tunnel(handler, path: str, query: str) -> None:
    """Carry the editor's websocket through untouched (co-editing lives on it)."""
    _scheme, host, port = _upstream()
    target = path[len(PREFIX):] or "/"
    if query:
        target += "?" + query
    try:
        upstream = socket.create_connection((host, port), timeout=30)
    except OSError as exc:
        return _fail(handler, "ONLYOFFICE 文档服务无响应：%s" % exc)
    forwarded_host, forwarded_proto = _forwarded(handler)
    request = "%s %s HTTP/1.1\r\n" % (handler.command, target)
    for key, value in handler.headers.items():
        if key.lower() in ("host", "connection") or key.lower().startswith("x-forwarded-"):
            continue
        request += "%s: %s\r\n" % (key, value)
    request += "Host: %s:%d\r\n" % (host, port)
    # The session's absolute URLs come back over this socket, so the same
    # forwarding has to be announced here as on the plain HTTP path.
    request += "X-Forwarded-Host: %s\r\n" % forwarded_host
    request += "X-Forwarded-Proto: %s\r\n" % forwarded_proto
    request += "X-Forwarded-For: %s\r\n" % handler.client_address[0]
    request += "Connection: Upgrade\r\n\r\n"
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
