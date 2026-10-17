"""编辑器插件：在工作台自己的 origin 下反代 VS Code Web。

``code serve-web`` 拒绝被别的 origin 嵌入，所以编辑器不能简单地 iframe 到自己
的端口；而手机在私有网络里也不该再占用一个对外端口。实现在 :mod:`.proxy`，
它同时持有子进程与同源反代，本文件只把它们暴露成插件接口。
"""
from __future__ import annotations

# Re-exported as the plugin's interface: the loader looks these up by name.
from .proxy import (PREFIX, WEBVIEW_PREFIX, available, handle, start,  # noqa: F401
                    status, stop)
