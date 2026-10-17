"""Office 编辑器插件：把 Word/Excel/PPT 交给本机的 ONLYOFFICE 文档服务。

宿主只做转发；真正的文档引擎在 :mod:`.docs`。这里暴露的接口：

* ``GET  /api/office/status``            文档服务是否可用，以及它在哪里
* ``GET  /api/office/session?path=``     一个文件的 DocsAPI 配置
* ``GET  /api/office/file/<token>``      给文档服务下载原文件（票即授权）
* ``POST /api/office/callback/<token>``  文档服务的保存回调（JWT 验签）
* ``/office/...``                        同源反代到文档服务本身
"""
from __future__ import annotations

from pathlib import Path

from workbench.errors import WorkbenchError

from . import docs

PREFIX = "/api/office"


def _file(value: str) -> Path:
    text = str(value or "").strip()
    if not text:
        raise WorkbenchError("缺少文件路径", status=400, code="missing_path")
    path = Path(text).expanduser()
    if not path.is_absolute():
        raise WorkbenchError("必须使用绝对路径", status=400, code="relative_path")
    try:
        path = path.resolve()
    except OSError as exc:
        raise WorkbenchError("路径无法解析：%s" % exc, status=400, code="bad_path") from exc
    if not path.is_file():
        raise WorkbenchError("文件不存在：%s" % path, status=404, code="path_not_found")
    return path


def handle(request) -> bool:
    path = request.path
    if path == "/office" or path.startswith("/office/"):
        docs.proxy(request.raw, path, request.raw_query)
        return True
    if not (path == PREFIX or path.startswith(PREFIX + "/")):
        return False
    route = path[len(PREFIX):].strip("/")
    method = request.method

    if method == "GET" and route == "status":
        state = docs.health(force=request.arg("force") in ("1", "true"))
        request.json(200, {
            "available": state["ok"], "error": state["error"], "docs": state["docs"],
            "jwt": bool(docs._secret()), "hint": "" if state["ok"] else docs.install_hint(),
        })
        return True
    if method == "GET" and route == "session":
        state = docs.health()
        if not state["ok"]:
            raise WorkbenchError(state["error"] + "。" + docs.install_hint(), status=503,
                                 code="office_unavailable")
        target = _file(request.arg("path"))
        # "view" is an explicit read-only open; anything else edits.
        mode = "view" if request.arg("mode") == "view" else "edit"
        # The DocsAPI token is a signature over the editor configuration and must
        # reach the browser verbatim; the generic secret scrubber would eat it.
        request.json(200, docs.session(target, docs._origin(request.raw), mode=mode),
                     scrub=False)
        return True
    if method == "GET" and route.startswith("file/"):
        token = route[len("file/"):]
        target = docs.lookup(token)
        if target is None:
            raise WorkbenchError("文件票已失效，请重新打开文档", status=403,
                                 code="expired_ticket")
        request.file(target, cache="private, max-age=60")
        return True
    if method == "POST" and route.startswith("callback/"):
        token = route[len("callback/"):]
        request.json(200, docs.callback(token, request))
        return True
    raise WorkbenchError("未知的接口：%s" % route, status=404, code="not_found")


# -- plugin interface ---------------------------------------------------
def available() -> bool:
    """The capability exists on every machine; the editor page explains a missing server."""
    return True


def status() -> dict:
    state = docs.health()
    return {"available": state["ok"], "docs": state["docs"], "error": state["error"],
            "jwt": bool(docs._secret())}
