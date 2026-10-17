"""Zotero 阅读插件：本地库浏览、PDF 流、划词翻译。

三个内部模块各管一件事：

* :mod:`.client` -- 只读访问 Zotero 官方本地 API（回环 23119）；
* :mod:`.app`    -- 本地 API 没响应时按需拉起桌面端；
* :mod:`.translate` -- 复用 Zotero 翻译插件已配置的 OpenAI 兼容服务。

前端页面是纯静态的 ``web/plugins/zotero/``，由宿主的静态文件服务提供。
"""
from __future__ import annotations

from workbench.errors import WorkbenchError

from . import client, translate

# The UI paginates by asking for the next window; the size lives here so the
# server, not the browser, decides how much one request may pull.
PAGE_SIZE = 25


def available() -> bool:
    """Cheap probe: nothing is required up front.  Starts nothing."""
    return True


def handle(request) -> bool:
    """Serve every ``/api/zotero/*`` path; False lets the host keep looking."""
    path = request.path
    method = request.method

    if method == "GET" and path == "/api/zotero/collections":
        request.json(200, client.collections())
        return True
    if method == "GET" and path == "/api/zotero/items":
        request.json(200, client.items(request.query))
        return True
    if method == "GET" and path.startswith("/api/zotero/items/"):
        request.json(200, client.detail(path.rsplit("/", 1)[-1]))
        return True
    if method == "GET" and path.startswith("/api/zotero/files/"):
        item_key = path.rsplit("/", 1)[-1]
        target = client.pdf_path(item_key)
        request.file(target, content_type="application/pdf",
                     filename="%s.pdf" % item_key, support_range=True)
        return True
    if method == "GET" and path == "/api/zotero/translation":
        # A missing translation configuration is a normal state, not a failure:
        # report it so the page can explain what to configure.
        try:
            request.json(200, translate.status())
        except WorkbenchError as exc:
            request.json(200, {"configured": False, "provider": "", "model": "",
                               "error": exc.message})
        return True
    if method == "POST" and path == "/api/zotero/translate":
        request.json(200, translate.translate(request.body))
        return True
    return False
