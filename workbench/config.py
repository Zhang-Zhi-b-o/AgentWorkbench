"""Static configuration and path resolution for the workbench host.

Nothing machine-specific is hard-coded.  The repository root comes from this
file's own location, and user data lives under ``WORKBENCH_HOME``.  A checkout
that still has a ``data/`` directory keeps using it, so moving the code to a new
directory never strands an existing installation -- and a fresh clone needs no
configuration at all.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

# Repository root: one level above this package.
ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = ROOT / "web"
PLUGIN_DIR = ROOT / "plugins"

# The host binds loopback only.  Remote access is the reverse proxy's job
# (Tailscale serve or any other TLS terminator), never a public bind.
HOST = "127.0.0.1"
PORT = int(os.environ.get("WORKBENCH_PORT", "8768"))

SERVICE_NAME = "workbench"

# Every mutating request must carry this header.  It stops a hostile page from
# driving the local API through a browser the user already has open.
SHARED_HEADER = "X-Workbench"
SHARED_HEADER_VALUE = "1"
# The header name the earlier single-purpose build used.  Accepted during the
# transition so existing local scripts keep working.
LEGACY_SHARED_HEADERS = ("X-Agent-Hub",)

# Upper bound for a JSON request body (attachments are not handled by the host).
JSON_BODY_LIMIT = 8_000_000
# Upper bound for a raw, non-JSON request body (a plugin's own upload stream).
# Sized for "one file copied from a phone", streamed to disk rather than buffered.
RAW_BODY_LIMIT = int(os.environ.get("WORKBENCH_RAW_LIMIT", str(2 * 1024 ** 3)))

_MISSING = object()


def _resolve_home() -> Path:
    """User-data directory: explicit env, then in-repo ``data/``, then the OS.

    The in-repo ``data/`` branch is what keeps an existing installation working
    unchanged when the code is moved: the directory travels with the checkout.
    """
    explicit = os.environ.get("WORKBENCH_HOME", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    local = ROOT / "data"
    if local.is_dir():
        return local
    return default_home()


def default_home() -> Path:
    """Platform data directory used by a fresh install with no local ``data/``."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / "AgentWorkbench"
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        if base:
            return Path(base) / "AgentWorkbench"
    return Path.home() / ".agentworkbench"


HOME = _resolve_home()


def remote_origin() -> str:
    """The single private reverse-proxy origin (for example a Tailscale URL).

    Empty when the file is missing, which is the normal case for a fresh clone:
    the host then behaves as loopback-only.
    """
    try:
        payload = json.loads((HOME / "remote-access.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    origin = payload.get("origin") if isinstance(payload, dict) else ""
    return str(origin or "").rstrip("/")


REMOTE_ORIGIN = remote_origin()


def plugin_setting(plugin_id: str, key: str, default=_MISSING):
    """Read one optional per-plugin value from ``HOME/plugins.json``.

    Plugins that need a machine-specific path (a CLI location, a Tailscale port)
    read it here instead of hard-coding it.
    """
    try:
        payload = json.loads((HOME / "plugins.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        payload = {}
    section = payload.get(plugin_id) if isinstance(payload, dict) else None
    if isinstance(section, dict) and key in section:
        return section[key]
    if default is _MISSING:
        raise KeyError("%s.%s" % (plugin_id, key))
    return default
