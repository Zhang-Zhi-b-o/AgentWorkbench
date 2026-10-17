"""Spell a local folder the way ``code serve-web`` reads ``?folder=``.

The web workbench resolves that query value against its *remote authority*: only
a value starting with ``/`` becomes ``vscode-remote://<authority><path>``, while
anything else is parsed as a standalone URI.  So a bare ``D:/dir`` keeps scheme
``d`` and drops the drive letter, and ``file:///D:/dir`` names a scheme the
remote server never serves -- both leave the editor with an empty explorer and
the "Workspace does not exist" dialog, which is to say nothing to edit.

The remote spelling of a Windows drive is ``/d:/dir``: leading slash, forward
slashes, lower-case letter.  A POSIX path only needs its leading slash.  The
value is percent-encoded once, because the browser decodes the query string
before the workbench builds the URI.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote


def remote_path(folder: Path | str) -> str:
    """``D:/dir`` -> ``/d:/dir``; the path half of a remote folder URI."""
    text = Path(folder).as_posix()
    if len(text) > 1 and text[1] == ":":
        text = "/" + text[0].lower() + text[1:]
    elif not text.startswith("/"):
        text = "/" + text
    return text


def remote_query(folder: Path | str) -> str:
    """The ``?folder=`` value: ``remote_path`` percent-encoded once."""
    return quote(remote_path(folder), safe="/")
