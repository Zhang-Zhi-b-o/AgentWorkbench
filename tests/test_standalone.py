"""Tests for the standalone wrapper: a plugin's own URL opened in a tab.

``/vscode/`` and ``/plugins/zotero/`` are the plugins' own entry URLs.  A browser
tab that opens one of them is served the workbench bar around the plugin instead
of the bare plugin document; the shell's iframes keep getting the bare document,
because they are already inside a bar.
"""
from __future__ import annotations

import unittest

from workbench import config
from workbench.plugin import load
from workbench.server import wants_standalone

ENTRIES = ("/agents/", "/vscode/", "/plugins/zotero/")


class StandaloneTest(unittest.TestCase):
    def test_a_tab_gets_the_bar(self):
        for path in ("/vscode/", "/vscode", "/plugins/zotero/", "/plugins/zotero",
                     "/agents/", "/agents"):
            self.assertTrue(wants_standalone("document", path, ENTRIES), path)

    def test_frames_and_plain_clients_keep_the_bare_plugin(self):
        # The shell's iframes say "iframe", and anything that sends no header at
        # all (curl, an older browser) keeps the old, unwrapped behaviour.
        for dest in ("iframe", "empty", "script", "image", "", None, " Document "):
            expected = (dest or "").strip().lower() == "document"
            self.assertEqual(wants_standalone(dest, "/vscode/", ENTRIES), expected, dest)

    def test_sub_paths_and_other_pages_are_left_alone(self):
        for path in ("/", "", "/index.html", "/standalone.html",
                     "/vscode/out/vs/workbench/workbench.js",
                     "/plugins/zotero/app.js"):
            self.assertFalse(wants_standalone("document", path, ENTRIES), path)

    def test_a_real_registry_names_its_entries(self):
        entries = [plugin.entry for plugin in load(config.PLUGIN_DIR).plugins]
        self.assertTrue(wants_standalone("document", "/vscode/", entries))
        self.assertTrue(wants_standalone("document", "/plugins/zotero/", entries))
        self.assertFalse(wants_standalone("document", "/", entries))

    def test_the_wrapper_page_exists(self):
        page = config.WEB_DIR / "standalone.html"
        self.assertTrue(page.is_file(), page)
        text = page.read_text(encoding="utf-8")
        self.assertIn("/standalone.js", text)
        # Served under a plugin's own path, so every asset must be origin absolute.
        self.assertNotIn('href="./', text)
        self.assertNotIn('src="./', text)


if __name__ == "__main__":
    unittest.main()
