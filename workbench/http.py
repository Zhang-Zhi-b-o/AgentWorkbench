"""A tiny request/response facade handed to plugins.

Plugins never touch ``BaseHTTPRequestHandler`` directly; they receive a
:class:`Request` and call one of :meth:`Request.json`, :meth:`Request.redirect`,
:meth:`Request.text` or :meth:`Request.file`.  That keeps a plugin testable with
a stub handler and keeps header-level concerns in one place.
"""
from __future__ import annotations

import json
import mimetypes
import re
from pathlib import Path
from urllib.parse import quote

from . import security

_RANGE = re.compile(r"bytes=(\d+)-(\d*)")
_CHUNK = 65536


class Request:
    """One HTTP exchange, as seen by a plugin."""

    def __init__(self, handler, method: str, path: str, query: dict, body: dict,
                 raw_query: str = ""):
        self._handler = handler
        self.method = method
        self.path = path
        self.query = query or {}
        self.body = body if isinstance(body, dict) else {}
        self.raw_query = raw_query

    # -- raw access ------------------------------------------------------
    @property
    def raw(self):
        """The underlying handler, for plugins that relay bytes (proxies, WS)."""
        return self._handler

    # -- inputs ---------------------------------------------------------
    @property
    def headers(self):
        return self._handler.headers

    def arg(self, name: str, default: str = "") -> str:
        """First value of a query-string parameter."""
        values = self.query.get(name) or []
        return str(values[0]) if values else default

    def host(self) -> str:
        return self.headers.get("Host") or ""

    # -- outputs --------------------------------------------------------
    def json(self, status: int, payload) -> None:
        body = json.dumps(security.scrub(payload), ensure_ascii=False,
                          separators=(",", ":")).encode("utf-8")
        handler = self._handler
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("X-Content-Type-Options", "nosniff")
        handler.end_headers()
        if not getattr(handler, "head_only", False):
            handler.wfile.write(body)

    def text(self, status: int, body: str, content_type: str = "text/plain; charset=utf-8") -> None:
        raw = body.encode("utf-8")
        handler = self._handler
        handler.send_response(status)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(raw)))
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
        if not getattr(handler, "head_only", False):
            handler.wfile.write(raw)

    def redirect(self, location: str, status: int = 302) -> None:
        handler = self._handler
        handler.send_response(status)
        handler.send_header("Location", location)
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Content-Length", "0")
        handler.end_headers()

    def file(self, path: Path, content_type: str | None = None,
             filename: str | None = None, cache: str = "no-store",
             support_range: bool = True) -> None:
        """Stream a file, honouring a single byte range when asked to."""
        handler = self._handler
        size = path.stat().st_size
        start, end = 0, size - 1
        ranged = False
        requested = handler.headers.get("Range") if support_range else None
        if requested:
            match = _RANGE.fullmatch(requested.strip())
            if (not match or int(match.group(1)) >= size
                    or (match.group(2) and int(match.group(2)) < int(match.group(1)))):
                handler.send_response(416)
                handler.send_header("Content-Range", "bytes */%d" % size)
                handler.send_header("Content-Length", "0")
                handler.end_headers()
                return
            start = int(match.group(1))
            end = min(int(match.group(2)) if match.group(2) else end, end)
            ranged = True
        ctype = content_type or mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        handler.send_response(206 if ranged else 200)
        handler.send_header("Content-Type", ctype)
        handler.send_header("Content-Length", str(end - start + 1))
        handler.send_header("Accept-Ranges", "bytes")
        handler.send_header("Cache-Control", cache)
        handler.send_header("X-Content-Type-Options", "nosniff")
        if ranged:
            handler.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        if filename:
            handler.send_header("Content-Disposition",
                                'inline; filename*=UTF-8\'\'%s' % quote(filename))
        handler.end_headers()
        if getattr(handler, "head_only", False):
            return
        with path.open("rb") as stream:
            stream.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = stream.read(min(_CHUNK, remaining))
                if not chunk:
                    break
                handler.wfile.write(chunk)
                remaining -= len(chunk)
