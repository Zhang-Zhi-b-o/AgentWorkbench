"""The one exception type a plugin raises to produce an HTTP error response.

It lives here rather than next to the host's own code so that a plugin does not
need to import the server, the dispatcher, or anything else, just to report
"not found" or "unavailable".
"""
from __future__ import annotations


class WorkbenchError(Exception):
    """An expected failure with a user-facing message and an HTTP status."""

    def __init__(self, message: str, status: int = 400, code: str = "error"):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
