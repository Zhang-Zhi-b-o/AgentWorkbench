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
from urllib.parse import unquote

from workbench import config
from workbench.errors import WorkbenchError
from workbench.plugin import load
from workbench.server import WorkbenchServer

from plugins.zotero import client, translate


class FakeRequest:
    """Captures what a plugin tried to send, without a socket."""

    def __init__(self, method, path, query=None, body=None, raw_query=""):
        self.method = method
        self.path = path
        self.query = query or {}
        self.body = body or {}
        self.raw_query = raw_query
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


class ExplorerOpenTest(unittest.TestCase):
    """The VS Code hand-off must hand over a remote path, not a URI.

    ``code serve-web`` turns only a value that starts with ``/`` into
    ``vscode-remote://<authority><path>``; anything else is parsed as a
    standalone URI, which is how "Workspace does not exist" happens.
    """

    def setUp(self):
        from plugins.explorer import app
        self.explorer = app

    def _folder_value(self, payload):
        prefix = "/vscode/?folder="
        self.assertTrue(payload["url"].startswith(prefix), payload["url"])
        return unquote(payload["url"][len(prefix):])

    def _remote(self, folder):
        """The remote spelling the workbench itself emits for a local folder."""
        text = Path(folder).resolve().as_posix()
        if len(text) > 1 and text[1] == ":":
            return "/" + text[0].lower() + text[1:]
        return text if text.startswith("/") else "/" + text

    def test_windows_drive_is_spelled_as_a_remote_path(self):
        # /d:/dir -- forward slashes, leading slash, lower-case drive letter.
        from workbench import folders
        self.assertEqual(folders.remote_query(Path("D:/Files/Work")),
                         "/d%3A/Files/Work")
        self.assertEqual(folders.remote_query(Path("/srv/data")), "/srv/data")

    def test_vscode_url_is_a_remote_folder_path(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            payload = self.explorer.open_with({"path": folder, "app": "vscode"})
            self.assertEqual(payload["folder"], str(Path(folder).resolve()))
            value = self._folder_value(payload)
        self.assertEqual(value, self._remote(folder))
        # No backslashes (the workbench reads them as part of a scheme) and a
        # leading slash, or the editor never enters the remote workspace.
        self.assertNotIn("\\", value)
        self.assertTrue(value.startswith("/"), value)
        self.assertNotIn("file:", value)

    def test_opening_a_file_hands_over_its_folder(self):
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root) / "with space"
            folder.mkdir()
            target = folder / "a b.txt"
            target.write_text("x", encoding="utf-8")
            payload = self.explorer.open_with({"path": str(target), "app": "vscode"})
            value = self._folder_value(payload)
            expected = self._remote(folder)
        # One decode of the query gives the raw path back, so a space stays a space.
        self.assertEqual(value, expected)
        self.assertIn("with space", value)


class VscodeStartFolderTest(unittest.TestCase):
    """The bare editor tab must open a workspace; stock serve-web shows nothing."""

    def setUp(self):
        self.registry = load(config.PLUGIN_DIR)

    def test_bare_editor_tab_redirects_to_the_start_folder(self):
        import tempfile

        from plugins.vscode import proxy
        from workbench import folders

        plugin = self.registry.by_id("vscode")
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(proxy, "DEFAULT_FOLDER", folder):
                request = FakeRequest("GET", "/vscode/")
                self.assertTrue(plugin.handle(request))
                expected = "folder=" + folders.remote_query(folder)
        self.assertEqual(request.sent[0], ("redirect", "/vscode/?" + expected))

    def test_a_folder_query_is_proxied_instead_of_redirected(self):
        from plugins.vscode import proxy

        plugin = self.registry.by_id("vscode")
        request = FakeRequest("GET", "/vscode/", query={"folder": ["/d%3A/x"]},
                              raw_query="folder=%2Fd%3A%2Fx")
        request.raw = object()  # what the proxy relays to
        with patch.object(proxy, "proxy") as forwarded:
            self.assertTrue(plugin.handle(request))
        forwarded.assert_called_once()
        # No redirect: the browser asked for a specific folder, so proxy as is.
        self.assertEqual(request.sent, [])



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
        self.assertEqual(ids, ["agents", "explorer", "vscode", "zotero"])

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
