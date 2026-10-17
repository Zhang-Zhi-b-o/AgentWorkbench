"""Tests for the host: guards, dispatch, cache policy, static safety."""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from workbench import config, security
from workbench.plugin import Plugin, Registry, load
from workbench.server import cache_policy, safe_static_path


class HostGuardTest(unittest.TestCase):
    def test_loopback_hosts_are_accepted(self):
        for host in ("127.0.0.1:%d" % config.PORT, "localhost:%d" % config.PORT,
                     "127.0.0.1", "[::1]:%d" % config.PORT):
            self.assertTrue(security.host_allowed(host), host)

    def test_foreign_hosts_are_rejected(self):
        for host in ("evil.example.com", "", None, "127.0.0.1:1",
                     "attacker.test:%d" % config.PORT):
            self.assertFalse(security.host_allowed(host), host)

    def test_origin(self):
        self.assertTrue(security.origin_allowed(None))
        self.assertTrue(security.origin_allowed(""))
        self.assertTrue(security.origin_allowed("http://127.0.0.1:%d" % config.PORT))
        self.assertFalse(security.origin_allowed("null"))
        self.assertFalse(security.origin_allowed("https://evil.example.com"))

    def test_mutations_need_the_shared_header(self):
        self.assertFalse(security.mutation_authorized({}))
        self.assertFalse(security.mutation_authorized(None))
        self.assertTrue(security.mutation_authorized({config.SHARED_HEADER: "1"}))
        # The header name the previous build used is still honoured.
        self.assertTrue(security.mutation_authorized({"X-Agent-Hub": "1"}))


class ScrubTest(unittest.TestCase):
    def test_keys_are_redacted(self):
        payload = {"api_key": "value", "nested": {"deepseek_api_key": "value"}}
        self.assertEqual(security.scrub(payload),
                         {"api_key": security.REDACTED,
                          "nested": {"deepseek_api_key": security.REDACTED}})

    def test_token_shapes_are_redacted_inside_text(self):
        # Built at runtime so no scanner mistakes this fixture for a credential.
        token = "sk-" + "abcdefghijklmnopqrstuvwxyz"
        self.assertIn(security.REDACTED, security.scrub_text("failed with " + token))


class CachePolicyTest(unittest.TestCase):
    def test_html_is_never_cached(self):
        self.assertEqual(cache_policy("index.html"), "no-store")
        self.assertEqual(cache_policy("reader.html"), "no-store")

    def test_hashed_assets_are_immutable(self):
        self.assertIn("immutable", cache_policy("index-D1ERvYDZ.js"))
        self.assertIn("immutable", cache_policy("vendor-react-vfD4mCvE.css"))

    def test_unhashed_assets_revalidate(self):
        self.assertEqual(cache_policy("app.js"), "no-cache")
        self.assertEqual(cache_policy("reader.js"), "no-cache")


class StaticPathTest(unittest.TestCase):
    def test_root_maps_to_index(self):
        root = Path(config.WEB_DIR)
        self.assertEqual(safe_static_path(root, "/").name, "index.html")
        self.assertEqual(safe_static_path(root, "/app.js").name, "app.js")

    def test_directory_maps_to_index(self):
        self.assertEqual(safe_static_path(Path(config.WEB_DIR), "/plugins/zotero/").name,
                         "index.html")

    def test_traversal_is_refused(self):
        root = Path(config.WEB_DIR)
        for path in ("/../LICENSE", "/plugins/../../etc/passwd", "/..%2f..%2fLICENSE"):
            self.assertIsNone(safe_static_path(root, path), path)


class RegistryTest(unittest.TestCase):
    def _plugin(self, plugin_id, routes, module=None):
        return Plugin(id=plugin_id, name=plugin_id, description="", icon="box",
                      kind="iframe", entry="/%s/" % plugin_id, routes=tuple(routes),
                      module=module)

    def test_longest_prefix_wins(self):
        registry = Registry([self._plugin("a", ["/api"]),
                             self._plugin("b", ["/api/zotero"])])
        self.assertEqual(registry.match("/api/zotero/items").id, "b")
        self.assertEqual(registry.match("/api/other").id, "a")
        self.assertIsNone(registry.match("/static/file.js"))

    def test_owning_paths_respect_boundaries(self):
        plugin = self._plugin("v", ["/vscode"])
        self.assertEqual(plugin.owns("/vscode"), 7)
        self.assertEqual(plugin.owns("/vscode/"), 7)
        self.assertEqual(plugin.owns("/vscode-webview/x"), 0)

    def test_manifest_never_contains_a_secret(self):
        payload = self._plugin("a", ["/api"]).manifest()
        self.assertEqual(set(payload), {"id", "name", "description", "icon", "kind",
                                        "entry", "ready", "error", "reason"})

    def test_real_plugins_load(self):
        registry = load(config.PLUGIN_DIR)
        ids = sorted(plugin.id for plugin in registry.plugins)
        self.assertEqual(ids, ["agents", "vscode", "zotero"])
        for plugin in registry.plugins:
            self.assertEqual(plugin.error, "", plugin.id)

    def test_registry_serialises_to_json(self):
        payload = load(config.PLUGIN_DIR).manifests()
        self.assertEqual(json.loads(json.dumps(payload))["plugins"][0]["kind"], "iframe")


class UnavailableReasonTest(unittest.TestCase):
    """A disabled plugin has to explain itself without becoming an error."""

    def _plugin(self, module, error=""):
        return Plugin(id="a", name="a", description="", icon="box", kind="iframe",
                      entry="/a/", routes=("/a",), module=module, error=error)

    def test_the_reason_reaches_the_manifest(self):
        module = SimpleNamespace(available=lambda: False,
                                 reason=lambda: "install the runtime")
        payload = self._plugin(module).manifest()
        self.assertFalse(payload["ready"])
        self.assertEqual(payload["reason"], "install the runtime")
        self.assertEqual(payload["error"], "")

    def test_a_ready_plugin_reports_no_reason(self):
        module = SimpleNamespace(available=lambda: True,
                                 reason=lambda: "must not be shown")
        self.assertEqual(self._plugin(module).manifest()["reason"], "")

    def test_a_broken_manifest_keeps_using_error(self):
        module = SimpleNamespace(available=lambda: False, reason=lambda: "ignored")
        payload = self._plugin(module, error="import failed: ValueError").manifest()
        self.assertEqual(payload["error"], "import failed: ValueError")
        self.assertEqual(payload["reason"], "")

    def test_a_plugin_without_a_hook_simply_has_no_reason(self):
        module = SimpleNamespace(available=lambda: False)
        self.assertEqual(self._plugin(module).manifest()["reason"], "")

    def test_a_raising_hook_does_not_break_the_manifest(self):
        def boom():
            raise RuntimeError("broken probe")
        module = SimpleNamespace(available=lambda: False, reason=boom)
        self.assertEqual(self._plugin(module).manifest()["reason"], "")


if __name__ == "__main__":
    unittest.main()
