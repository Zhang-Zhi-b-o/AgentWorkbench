"""Terminate a child process together with everything it spawned.

``serve-web`` and the DSH server both fan out into helper processes.  Killing
only the direct child leaves listeners behind that then block the next start, so
each plugin wraps its child in a :class:`ProcessTree` and closes it on shutdown.
"""
from __future__ import annotations

import os
import signal
import subprocess
from contextlib import suppress


class ProcessTree:
    """Owns the process group started for one plugin."""

    def __init__(self, process: subprocess.Popen):
        self.process = process

    def close(self, timeout: float = 10.0) -> None:
        process = self.process
        if process is None or process.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           check=False)
        else:
            with suppress(ProcessLookupError, PermissionError):
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            with suppress(subprocess.TimeoutExpired):
                process.wait(timeout=5)
