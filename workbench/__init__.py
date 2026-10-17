"""AgentWorkbench host: a plugin host for a small personal remote workbench.

The host itself knows nothing about any specific plugin.  It validates the
request origin, answers two meta routes, hands declared paths to the plugin that
owns them, and otherwise serves ``web/``.  Plugins live under ``plugins/`` and
never import the host's internals beyond :mod:`workbench.config` and
:mod:`workbench.errors`.

Public entry point: ``python -m workbench``.
"""

__version__ = "0.1.0"
