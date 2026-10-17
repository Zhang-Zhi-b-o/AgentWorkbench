"""Office 插件：把 Word/Excel/PPT 的编辑交给本机的 ONLYOFFICE 文档服务。

宿主只转发；接口在 :mod:`.app`，文档引擎在 :mod:`.docs`。
"""
from __future__ import annotations

# Re-exported as the plugin's interface: the loader looks these up by name.
from .app import available, handle, status  # noqa: F401
