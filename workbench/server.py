"""HTTP host: origin guards, plugin dispatch, static files.

The host knows nothing about any specific plugin.  Request order is:

1. reject a foreign ``Host`` / ``Origin``,
2. answer the two meta routes (``/api/health``, ``/api/plugins``),
3. hand the path to the plugin whose manifest claims it (longest prefix wins),
4. serve ``web/`` as static files,
5. otherwise report 404.

Mutating requests need the shared header before any of that, so a hostile page
cannot drive a plugin through the user's own browser.
"""
from __future__ import annotations

import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from . import __version__, config, plugin, security
from .errors import WorkbenchError
from .http import Request

_TOO_LARGE = object()

# A content-hashed asset can be cached forever: its URL changes with its bytes.
_HASHED_ASSET = re.compile(
    r"[-.][A-Za-z0-9_]{8,}\.(?:js|css|mjs|map|woff2?|ttf|otf|png|jpe?g|gif|svg|webp|ico)$")


def cache_policy(name: str) -> str:
    """Long cache for hashed assets; revalidate everything else.

    An unhashed stylesheet that changes while a phone holds a five-minute copy is
    exactly the bug where "I fixed it but the device still shows the old look".
    A 304 costs a couple of hundred bytes, so the bandwidth argument does not
    apply to these few files.
    """
    lowered = name.lower()
    if lowered.endswith(".html"):
        return "no-store"
    if _HASHED_ASSET.search(name):
        return "public, max-age=31536000, immutable"
    return "no-cache"


def safe_static_path(root: Path, path: str) -> Path | None:
    """Resolve ``path`` under ``root``; None when it escapes the root."""
    path = unquote(path or "")
    if path in ("", "/"):
        path = "/index.html"
    if path.endswith("/"):
        path += "index.html"
    root = root.resolve()
    target = (root / path.lstrip("/")).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None
    return target


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "AgentWorkbench/" + __version__
    sys_version = ""
    # A HEAD response is the GET response without a body; the plugins do not need
    # to know the difference.
    head_only = False

    # -- entry points ----------------------------------------------------
    def log_message(self, *args):  # keep the console clean; no paths or secrets
        return

    def do_GET(self):
        self._handle("GET")

    def do_HEAD(self):
        self.head_only = True
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_OPTIONS(self):
        self._json(405, {"error": "method not allowed", "code": "method_not_allowed"})

    # -- core ------------------------------------------------------------
    def _handle(self, method):
        try:
            if not security.host_allowed(self.headers.get("Host")):
                return self._json(400, {"error": "invalid host", "code": "bad_host"})
            if not security.origin_allowed(self.headers.get("Origin")):
                return self._json(403, {"error": "forbidden origin",
                                        "code": "bad_origin"})
            parsed = urlsplit(self.path)
            path = unquote(parsed.path)
            query = parse_qs(parsed.query)
            body: dict = {}
            if method == "POST":
                if not security.mutation_authorized(self.headers):
                    return self._json(403, {"error": "missing %s header" % config.SHARED_HEADER,
                                            "code": "unauthorized_mutation"})
                body = self._read_json()
                if body is _TOO_LARGE:
                    return self._json(413, {"error": "body too large",
                                            "code": "payload_too_large"})
                if body is None:
                    return self._json(400, {"error": "invalid JSON body",
                                            "code": "bad_json"})

            request = Request(self, method, path, query, body, raw_query=parsed.query)
            if method == "GET" and path == "/api/health":
                return request.json(200, {"ok": True, "service": config.SERVICE_NAME,
                                          "version": __version__})
            if method == "GET" and path == "/api/plugins":
                return request.json(200, self.server.registry.manifests())

            owner = self.server.registry.match(path)
            if owner is not None and owner.handle(request):
                return
            if path.startswith("/api/"):
                return request.json(404, {"error": "not found", "code": "not_found"})
            return self._serve_static(request, path)
        except WorkbenchError as exc:
            return self._json(exc.status, {"error": exc.message, "code": exc.code})
        except (BrokenPipeError, ConnectionResetError):
            return
        except ValueError as exc:
            return self._json(400, {"error": str(exc), "code": "invalid_input"})
        except Exception as exc:  # never leak internals to the client
            return self._json(500, {"error": "internal error: %s" % type(exc).__name__,
                                    "code": "internal_error"})

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            return None
        if length < 0 or length > config.JSON_BODY_LIMIT:
            self.close_connection = True
            return _TOO_LARGE
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        return data if isinstance(data, dict) else None

    # -- responses -------------------------------------------------------
    def _json(self, status, payload):
        body = json.dumps(security.scrub(payload), ensure_ascii=False,
                          separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if not self.head_only:
            self.wfile.write(body)

    def _serve_static(self, request, path):
        target = safe_static_path(config.WEB_DIR, path)
        if target is None:
            return request.json(403, {"error": "forbidden", "code": "path_traversal"})
        if not target.is_file():
            return request.json(404, {"error": "not found", "code": "not_found"})
        try:
            request.file(target, cache=cache_policy(target.name))
        except OSError:
            return request.json(500, {"error": "read error", "code": "io_error"})


class WorkbenchServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, registry):
        self.registry = registry
        super().__init__(address, Handler)


def main(argv=None) -> int:
    # The checkout is the delivery unit: ``web/`` and ``plugins/`` are read from
    # beside this package, so a wheel installed into site-packages would serve no
    # pages and find no plugins.  Say so instead of starting a blank host.
    if not (config.WEB_DIR / "index.html").is_file():
        print("workbench: %s has no web/index.html" % config.WEB_DIR, file=sys.stderr)
        print("workbench: run it from the checkout, or install it there with "
              "pip install -e .", file=sys.stderr)
        return 2
    registry = plugin.load(config.PLUGIN_DIR)
    server = WorkbenchServer((config.HOST, config.PORT), registry)
    registry.start()
    print("AgentWorkbench listening on http://%s:%d" % (config.HOST, config.PORT))
    print("  data      %s" % config.HOME)
    print("  plugins   %d from %s" % (len(registry.plugins), config.PLUGIN_DIR))
    for item in registry.plugins:
        note = item.error or ("ready" if item.ready()
                              else (item.reason() or "not available"))
        print("  %-9s %-8s %-18s %s" % (item.id, item.kind, item.entry, note))
    # Launched from start.ps1 the banner goes to a log file; without this it
    # would sit in the block buffer for the whole life of the process.
    sys.stdout.flush()
    if not registry.plugins:
        print("workbench: no plugins found; the shell will show an empty list",
              file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        registry.stop()
        server.server_close()
    return 0
