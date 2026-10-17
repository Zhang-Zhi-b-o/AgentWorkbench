"""Request host/origin validation and recursive secret scrubbing.

The host binds loopback, but a browser on the same machine can still be lured
into calling it.  Two guards close that hole: the ``Host`` header must name a
host we accept, and the ``Origin`` header (when present) must match too.  A
foreign origin cannot read the response it never gets.

These helpers are pure so they can be unit-tested without a socket.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from . import config

# Field names whose values must never be persisted or returned.
SENSITIVE_KEYS = frozenset({
    "authorization", "auth", "token", "access_token", "refresh_token",
    "id_token", "api_key", "apikey", "api-key", "secret", "client_secret",
    "password", "passwd", "private_key", "access_key", "secret_key",
    "cookie", "set-cookie", "bearer", "oauth", "anthropic_api_key",
    "anthropic_auth_token", "openai_api_key", "deepseek_api_key",
    "x-api-key", "proxy-authorization",
})

# Value shapes that look like bearer credentials regardless of key name.
_VALUE_PATTERNS = (
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{12,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{8,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{8,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{8,}"),
    # JWT-ish: three dot-separated base64url chunks.
    re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{6,}"),
    re.compile(r"(?i)(?:authorization|api[-_]?key)\s*[:=]\s*\S+"),
)

REDACTED = "[REDACTED]"

_LOOPBACK_NAMES = frozenset({"127.0.0.1", "localhost", "::1"})


def scrub_text(text: str) -> str:
    """Replace token-shaped substrings inside a free-form string."""
    if not isinstance(text, str):
        return text
    out = text
    for pattern in _VALUE_PATTERNS:
        out = pattern.sub(REDACTED, out)
    return out


def scrub(obj: Any) -> Any:
    """Recursively redact sensitive keys and token-shaped values."""
    if isinstance(obj, dict):
        return {key: (REDACTED if isinstance(key, str) and key.strip().lower() in SENSITIVE_KEYS
                      else scrub(value))
                for key, value in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [scrub(item) for item in obj]
    if isinstance(obj, str):
        return scrub_text(obj)
    return obj


def host_allowed(host_header: str) -> bool:
    """Accept loopback hosts on our port, or the one configured private origin."""
    if not host_header or not isinstance(host_header, str):
        return False
    remote = urlsplit(config.REMOTE_ORIGIN)
    if remote.scheme in ("http", "https") and host_header.strip().lower() == remote.netloc.lower():
        return True
    host = host_header.strip()
    if host.startswith("["):  # IPv6 literal form [::1]:port
        name, _, port = host.partition("]")
        name = name[1:]
        port = port.lstrip(":")
    elif ":" in host:
        name, _, port = host.rpartition(":")
    else:
        name, port = host, ""
    if port and port != str(config.PORT):
        return False
    return name.lower() in _LOOPBACK_NAMES


def origin_allowed(origin) -> bool:
    """Missing Origin (curl, same-origin tooling) is allowed; foreign is not."""
    if origin is None:
        return True
    origin = origin.strip()
    if origin == "":
        return True
    if config.REMOTE_ORIGIN and origin == config.REMOTE_ORIGIN:
        return True
    if origin.lower() == "null":
        return False
    try:
        parts = urlsplit(origin)
    except ValueError:
        return False
    if parts.scheme not in ("http", "https"):
        return False
    host = (parts.hostname or "").lower()
    if host not in _LOOPBACK_NAMES:
        return False
    try:
        port = parts.port
    except ValueError:
        return False
    if port is not None and port != config.PORT:
        return False
    return True


def mutation_authorized(headers) -> bool:
    """Every mutating request must carry the shared header the UI sets."""
    if headers is None:
        return False
    if headers.get(config.SHARED_HEADER) == config.SHARED_HEADER_VALUE:
        return True
    return any(headers.get(name) == config.SHARED_HEADER_VALUE
               for name in config.LEGACY_SHARED_HEADERS)
