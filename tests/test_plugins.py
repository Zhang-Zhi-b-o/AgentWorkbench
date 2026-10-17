"""Tests for the bundled plugins and for the host end to end."""
from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from workbench import config
from workbench.errors import WorkbenchError
from workbench.plugin import load
from workbench.server import WorkbenchServer

from plugins.zotero import client, translate


class FakeRequest:
    """Captures what a plugin tried to send, without a socket."""

    def __init__(self, method, path, query=None, body=None):
        self.method = method
        self.path = path
        self.query = query or {}
        self.body = body or {}
        self.sent = []

    def json(self, status, payload):
        self.sent.append(("json", status, payload))

    def file(self, path, **kwargs):
        self.sent.append(("file", str(path), kwargs))

    def redirect(self, location, status=302):
        self.sent.append(("redirect", location))

    def text(self, status, body, content_type="text/plain"):
        self.sent.append(("text", status, body))

    def arg(self, name, default=""):
        values = self.query.get(name) or []
        return str(values[0]) if values else default

    def host(self):
        return "127.0.0.1:%d" % config.PORT


def fake_fetch(payload, headers=None):
    """Replace client._fetch with a canned upstream answer."""
    def _fetch(url, text):
        if callable(payload):
            return payload(url, text)
        return payload, headers or {}
    return _fetch


ZOTERO_ITEM = {
    "key": "ABCD1234",
    "data": {"itemType": "journalArticle", "title": "一篇论文",
             "creators": [{"firstName": "Ada", "lastName": "Lovelace"}],
             "date": "2024", "publicationTitle": "Journal", "abstractNote": "摘要",
             "DOI": "10.1/x", "tags": [{"tag": "rag"}]},
}


class ZoteroPluginTest(unittest.TestCase):
    def setUp(self):
        self._real_fetch = client._fetch
        self.plugin = load(config.PLUGIN_DIR).by_id("zotero")

    def tearDown(self):
        client._fetch = self._real_fetch

    def test_collections_shape(self):
        client._fetch = fake_fetch([{"key": "COL00001", "data": {"name": "分子"}}])
        request = FakeRequest("GET", "/api/zotero/collections")
        self.assertTrue(self.plugin.handle(request))
        kind, status, payload = request.sent[0]
        self.assertEqual((kind, status), ("json", 200))
        self.assertEqual(payload, {"collections": [
            {"key": "COL00001", "name": "分子", "parent": None}]})

    def test_items_summarise_a_row(self):
        client._fetch = fake_fetch([ZOTERO_ITEM], {"Total-Results": "1"})
        request = FakeRequest("GET", "/api/zotero/items", {"start": ["0"]})
        self.assertTrue(self.plugin.handle(request))
        payload = request.sent[0][2]
        self.assertEqual(payload["total"], 1)
        row = payload["items"][0]
        self.assertEqual(row["authors"], "Ada Lovelace")
        self.assertEqual(row["tags"], ["rag"])

    def test_detail_lists_pdf_attachments(self):
        def payload(url, text):
            if "/children" in url:
                return ([{"key": "PDF12345",
                          "data": {"contentType": "application/pdf",
                                   "title": "Full Text"}}], {})
            return ZOTERO_ITEM, {}
        client._fetch = fake_fetch(payload)
        request = FakeRequest("GET", "/api/zotero/items/ABCD1234")
        self.assertTrue(self.plugin.handle(request))
        pdfs = request.sent[0][2]["pdfs"]
        self.assertEqual(pdfs, [{"key": "PDF12345", "title": "Full Text",
                                 "url": "/api/zotero/files/PDF12345"}])

    def test_invalid_item_key_is_a_client_error(self):
        request = FakeRequest("GET", "/api/zotero/items/not-a-key")
        with self.assertRaises(ValueError):
            self.plugin.handle(request)

    def test_pdf_route_streams_the_file(self):
        target = Path(self.id().replace(".", "_") + ".pdf")
        client._fetch = fake_fetch(lambda url, text: (
            ("file:///" + str(target).replace("\\", "/")) if text else (ZOTERO_ITEM, {})))
        client.pdf_path = lambda key: target  # bypass the byte check
        try:
            request = FakeRequest("GET", "/api/zotero/files/PDF12345")
            self.assertTrue(self.plugin.handle(request))
            kind, path, kwargs = request.sent[0]
            self.assertEqual(kind, "file")
            self.assertEqual(kwargs["content_type"], "application/pdf")
        finally:
            del client.pdf_path

    def test_unconfigured_translation_reports_instead_of_failing(self):
        def boom():
            raise WorkbenchError("尚未配置", status=503, code="translation_not_configured")
        real = translate.status
        translate.status = boom
        try:
            request = FakeRequest("GET", "/api/zotero/translation")
            self.assertTrue(self.plugin.handle(request))
            kind, status, payload = request.sent[0]
            self.assertEqual(status, 200)
            self.assertFalse(payload["configured"])
            self.assertIn("尚未配置", payload["error"])
        finally:
            translate.status = real

    def test_unknown_path_is_not_handled(self):
        request = FakeRequest("GET", "/api/zotero/nope")
        self.assertFalse(self.plugin.handle(request))


class AgentsAndVscodeTest(unittest.TestCase):
    def setUp(self):
        self.registry = load(config.PLUGIN_DIR)

    def test_status_routes_never_leak_secrets(self):
        for plugin_id, path in (("agents", "/api/agents/status"),
                                ("vscode", "/api/vscode/status")):
            plugin = self.registry.by_id(plugin_id)
            request = FakeRequest("GET", path)
            self.assertTrue(plugin.handle(request), path)
            payload = request.sent[0][2]
            for key in ("api_key", "token", "DEEPSEEK_API_KEY"):
                self.assertNotIn(key, payload)

    def test_unknown_paths_fall_through(self):
        for plugin_id in ("agents", "vscode"):
            plugin = self.registry.by_id(plugin_id)
            self.assertFalse(plugin.handle(FakeRequest("GET", "/api/other")))

    def test_first_agents_visit_starts_dsh_and_returns_handoff(self):
        """首次打开 AI 页必须走完整 argv 构造，不能仅验证状态接口。"""
        from plugins import agents

        process = Mock()
        process.poll.return_value = None
        state = {"process": None, "ready": False, "error": "", "started": 0.0,
                 "launch_url": ""}

        def announce():
            state["launch_url"] = "http://127.0.0.1:3080/?token=test-bootstrap"

        request = FakeRequest("GET", "/agents/")
        with patch.object(agents, "_state", state), \
                patch.object(agents, "available", return_value=True), \
                patch.object(agents, "_child_env", return_value={}), \
                patch.object(agents, "_assert_port_free"), \
                patch.object(agents, "ProcessTree"), \
                patch.object(agents, "_http_ready", return_value=True), \
                patch.object(agents.threading, "Thread", return_value=SimpleNamespace(start=announce)), \
                patch.object(agents.subprocess, "Popen", return_value=process) as launch:
            self.assertTrue(agents.handle(request))
        argv = launch.call_args.args[0]
        self.assertTrue(Path(argv[0]).is_absolute())
        self.assertEqual(argv[1], str(agents.BINARY.resolve()))
        self.assertIn("--no-open", argv)
        self.assertEqual(request.sent[0][0], "redirect")
        self.assertTrue(state["ready"])

    def test_ensure_returns_when_its_port_is_taken(self):
        """Regression: ensure() reported through status() while holding the lock.

        With a non-reentrant lock that deadlocked the request thread, so the
        browser saw a connection that never answered.
        """
        import socket
        import threading

        from plugins import agents

        real_port = agents.PORT
        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen(1)
            agents.PORT = occupied.getsockname()[1]
            result = {}
            worker = threading.Thread(target=lambda: result.update(agents.ensure()),
                                      daemon=True)
            worker.start()
            worker.join(10)
            agents.PORT = real_port
        self.assertFalse(worker.is_alive(), "agents.ensure() deadlocked")
        self.assertTrue(result.get("error"), result)


class HostEndToEndTest(unittest.TestCase):
    """Boot the real server on an ephemeral port and talk HTTP to it."""

    @classmethod
    def setUpClass(cls):
        cls.registry = load(config.PLUGIN_DIR)
        cls.server = WorkbenchServer(("127.0.0.1", 0), cls.registry)
        cls.port = cls.server.server_address[1]
        cls._real_port = config.PORT
        config.PORT = cls.port          # the host guard checks the configured port
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        config.PORT = cls._real_port

    def _get(self, path, headers=None, method="GET"):
        request = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (self.port, path), method=method,
            headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, response.read(), dict(response.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), dict(exc.headers)

    def test_health(self):
        status, body, _ = self._get("/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["service"], "workbench")

    def test_plugin_manifest(self):
        status, body, _ = self._get("/api/plugins")
        self.assertEqual(status, 200)
        ids = sorted(item["id"] for item in json.loads(body)["plugins"])
        self.assertEqual(ids, ["agents", "vscode", "zotero"])

    def test_foreign_host_is_rejected(self):
        status, _, _ = self._get("/api/health", {"Host": "evil.example.com"})
        self.assertEqual(status, 400)

    def test_mutation_without_header_is_rejected(self):
        status, _, _ = self._get("/api/vscode/stop", method="POST")
        self.assertEqual(status, 403)

    def test_unknown_api_path_is_404(self):
        status, body, _ = self._get("/api/does-not-exist")
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(body)["code"], "not_found")

    def test_static_index_is_served_uncached(self):
        status, body, headers = self._get("/")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        self.assertIn(b"<html", body.lower())

    def test_traversal_is_refused(self):
        status, _, _ = self._get("/../LICENSE")
        self.assertIn(status, (403, 404))


if __name__ == "__main__":
    unittest.main()
