"""A tiny request/response facade handed to plugins.

Plugins never touch ``BaseHTTPRequestHandler`` directly; they receive a
:class:`Request` and call one of :meth:`Request.json`, :meth:`Request.redirect`,
:meth:`Request.text` or :meth:`Request.file`.  That keeps a plugin testable with
a stub handler and keeps header-level concerns in one place.

Most requests carry a small JSON object, but a file upload is a byte stream:
when the content type is not JSON the host leaves the body on the socket and
hands the plugin :meth:`Request.stream_to` / :meth:`Request.read_raw` instead.
The host drains whatever a plugin did not consume before the connection is
reused, so an ignored body can never desynchronise the next request.
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
_UPLOAD_CHUNK = 1024 * 1024


class Request:
    """One HTTP exchange, as seen by a plugin."""

    def __init__(self, handler, method: str, path: str, query: dict, body: dict,
                 raw_query: str = "", raw_length: int = 0):
        self._handler = handler
        self.method = method
        self.path = path
        self.query = query or {}
        self.body = body if isinstance(body, dict) else {}
        self.raw_query = raw_query
        # Bytes still sitting on the socket for a non-JSON POST body.
        self._raw_remaining = max(0, int(raw_length or 0))

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

    # -- request body ----------------------------------------------------
    @property
    def raw_pending(self) -> bool:
        """True for a body the host deliberately did not parse (an upload)."""
        return self._raw_remaining > 0

    def stream_to(self, target: Path, limit: int) -> int:
        """Copy the request body straight into ``target``; not held in memory.

        ``limit`` is enforced before the first byte so an oversized upload is
        refused rather than half written.  Returns the number of bytes written.
        """
        from .errors import WorkbenchError

        if self._raw_remaining > limit:
            self._raw_remaining = 0
            raise WorkbenchError("上传内容超过大小上限", status=413,
                                 code="upload_too_large")
        written = 0
        with target.open("wb") as stream:
            while self._raw_remaining > 0:
                chunk = self._handler.rfile.read(min(_UPLOAD_CHUNK, self._raw_remaining))
                if not chunk:
                    break
                stream.write(chunk)
                written += len(chunk)
                self._raw_remaining -= len(chunk)
        if written == 0:
            raise WorkbenchError("上传内容为空", status=400, code="empty_upload")
        return written

    def read_raw(self, limit: int = 16 * 1024 * 1024) -> bytes:
        """The whole body as bytes, bounded by ``limit``."""
        from .errors import WorkbenchError

        if self._raw_remaining > limit:
            self._raw_remaining = 0
            raise WorkbenchError("请求体超过大小上限", status=413,
                                 code="payload_too_large")
        chunks = []
        while self._raw_remaining > 0:
            chunk = self._handler.rfile.read(min(_UPLOAD_CHUNK, self._raw_remaining))
            if not chunk:
                break
            chunks.append(chunk)
            self._raw_remaining -= len(chunk)
        return b"".join(chunks)

    def drain_raw(self) -> None:
        """Discard an unconsumed body so keep-alive can reuse the socket."""
        remaining = self._raw_remaining
        self._raw_remaining = 0
        if remaining <= 0:
            return
        # A body nobody asked for is either a bug or an attack; past a few
        # megabytes the cheapest correct move is to hang up instead of reading.
        if remaining > 8 * 1024 * 1024:
            self._handler.close_connection = True
            return
        while remaining > 0:
            chunk = self._handler.rfile.read(min(_CHUNK, remaining))
            if not chunk:
                break
            remaining -= len(chunk)

    # -- outputs --------------------------------------------------------
    def json(self, status: int, payload, scrub: bool = True) -> None:
        """Send JSON; ``scrub=False`` is for a payload whose token is a signature.

        The default redacts secret-shaped keys and values.  A plugin that must
        hand the browser a signed configuration (ONLYOFFICE's DocsAPI token is a
        signature over a public config, not a stored credential) opts out here,
        and the call site explains why.
        """
        data = security.scrub(payload) if scrub else payload
        body = json.dumps(data, ensure_ascii=False,
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
             support_range: bool = True, inline: bool = True) -> None:
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
        # Office formats are the most likely download target, and a browser that
        # guesses will hand them to the local suite instead of the workbench.
        if filename:
            handler.send_header(
                "Content-Disposition",
                "%s; filename*=UTF-8''%s" % ("inline" if inline else "attachment",
                                             quote(filename)))
        if ranged:
            handler.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
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
