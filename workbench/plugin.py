"""Plugin discovery and the routing table built from it.

A plugin is a directory under ``plugins/`` holding:

* ``plugin.json`` -- the manifest the host and the browser both read, and
* ``__init__.py`` -- an optional module that may define ``handle(request)``
  plus ``start()`` / ``stop()`` / ``status()`` / ``available()``.

The host never imports a plugin by name and never inspects its internals: it
reads manifests, then asks the owning module to handle the paths the manifest
declares.  Adding a plugin therefore requires no host change at all.
"""
from __future__ import annotations

import importlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

VALID_KINDS = ("iframe", "link", "redirect")
REQUIRED_KEYS = ("id", "name", "kind", "entry")
_ID = re.compile(r"^[a-z][a-z0-9-]{1,31}$")


@dataclass(frozen=True)
class Plugin:
    """One installed plugin, as declared by its manifest."""

    id: str
    name: str
    description: str
    icon: str
    kind: str
    entry: str
    routes: tuple[str, ...]
    module: object | None = None
    error: str = ""

    # -- manifest -------------------------------------------------------
    def manifest(self) -> dict:
        """The JSON the browser receives.  Never contains secrets."""
        payload = {
            "id": self.id, "name": self.name, "description": self.description,
            "icon": self.icon, "kind": self.kind, "entry": self.entry,
            "ready": self.ready(), "error": self.error, "reason": self.reason(),
        }
        return payload

    def reason(self) -> str:
        """Why a working plugin is unavailable, in words a user can act on.

        ``error`` is reserved for a broken manifest or a failed import; this is
        the normal, expected case of a missing dependency, so the two never mix.
        A plugin opts in by defining ``reason()`` in its module.
        """
        if self.error or self.ready():
            return ""
        hook = getattr(self.module, "reason", None) if self.module else None
        if hook is None:
            return ""
        try:
            return str(hook() or "")
        except Exception:
            return ""

    def ready(self) -> bool:
        """Cheap availability probe; must not start anything."""
        if self.module is None:
            return False
        probe = getattr(self.module, "available", None)
        if probe is None:
            return True
        try:
            return bool(probe())
        except Exception:
            return False

    # -- routing --------------------------------------------------------
    def owns(self, path: str) -> int:
        """Length of the longest declared prefix that owns ``path`` (0 = none)."""
        best = 0
        for prefix in self.routes:
            if path == prefix or path.startswith(prefix + "/"):
                best = max(best, len(prefix))
        return best

    def handle(self, request) -> bool:
        """True when the plugin produced a response."""
        handler = getattr(self.module, "handle", None) if self.module else None
        if handler is None:
            return False
        return bool(handler(request))

    # -- lifecycle ------------------------------------------------------
    def start(self) -> None:
        hook = getattr(self.module, "start", None) if self.module else None
        if hook is not None:
            hook()

    def stop(self) -> None:
        hook = getattr(self.module, "stop", None) if self.module else None
        if hook is not None:
            hook()

    def status(self) -> dict:
        hook = getattr(self.module, "status", None) if self.module else None
        return hook() if hook is not None else {}


class Registry:
    """The installed plugins, in display order."""

    def __init__(self, plugins):
        self.plugins = tuple(plugins)

    def match(self, path: str) -> Plugin | None:
        """Longest-prefix match, so ``/api/zotero`` beats a bare ``/api``."""
        best, best_len = None, 0
        for plugin in self.plugins:
            length = plugin.owns(path)
            if length > best_len:
                best, best_len = plugin, length
        return best

    def manifests(self) -> dict:
        return {"plugins": [plugin.manifest() for plugin in self.plugins]}

    def by_id(self, plugin_id: str) -> Plugin | None:
        return next((p for p in self.plugins if p.id == plugin_id), None)

    def start(self) -> None:
        for plugin in self.plugins:
            plugin.start()

    def stop(self) -> None:
        for plugin in reversed(self.plugins):
            try:
                plugin.stop()
            except Exception as exc:  # shutdown must not stop on one failure
                print("workbench: stopping %s failed: %s" % (plugin.id, exc),
                      file=sys.stderr)


def _load_one(manifest_path: Path) -> Plugin | None:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print("workbench: ignoring %s (%s)" % (manifest_path, exc), file=sys.stderr)
        return None
    if not isinstance(manifest, dict):
        print("workbench: ignoring %s (not an object)" % manifest_path, file=sys.stderr)
        return None

    missing = [key for key in REQUIRED_KEYS if not manifest.get(key)]
    if missing:
        print("workbench: %s is missing %s" % (manifest_path, ", ".join(missing)),
              file=sys.stderr)
        return None

    plugin_id = str(manifest["id"])
    kind = str(manifest["kind"])
    entry = str(manifest["entry"])
    problems = []
    if not _ID.match(plugin_id):
        problems.append("id must be lowercase letters, digits and dashes")
    if kind not in VALID_KINDS:
        problems.append("kind must be one of %s" % ", ".join(VALID_KINDS))
    if not entry.startswith("/"):
        problems.append("entry must start with '/'")
    routes = tuple(str(item) for item in manifest.get("routes") or ())
    if any(not route.startswith("/") for route in routes):
        problems.append("every route must start with '/'")

    module = None
    module_name = str(manifest.get("module") or plugin_id)
    if not problems:
        try:
            module = importlib.import_module("plugins." + module_name)
        except Exception as exc:  # a broken plugin must not take the host down
            problems.append("import failed: %s: %s" % (type(exc).__name__, exc))

    return Plugin(
        id=plugin_id,
        name=str(manifest["name"]),
        description=str(manifest.get("description") or ""),
        icon=str(manifest.get("icon") or "box"),
        kind=kind,
        entry=entry,
        routes=routes,
        module=module,
        error="; ".join(problems),
    )


def load(directory: Path) -> Registry:
    """Read every ``plugins/*/plugin.json``; a broken one is reported, not fatal."""
    plugins = []
    for manifest_path in sorted(directory.glob("*/plugin.json")):
        plugin = _load_one(manifest_path)
        if plugin is not None:
            plugins.append(plugin)
    return Registry(plugins)
