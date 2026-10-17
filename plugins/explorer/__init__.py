"""文件插件：把这台电脑的目录树搬进工作台。

实现在 :mod:`.app`；本文件只把插件接口暴露给宿主，和 ``vscode`` 插件同样的写法。
"""
from __future__ import annotations

# Re-exported as the plugin's interface: the loader looks these up by name.
from .app import available, handle, status  # noqa: F401
