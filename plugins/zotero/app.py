"""Start the Zotero desktop app when its local API is not up yet.

The reader reads a Zotero library through Zotero's official local API on
loopback (port 23119).  That server only exists while the desktop app runs, so
a request that arrives while Zotero is closed fails at the socket.  This module
turns that specific failure into an attempt to open the app: locate the
executable, launch it detached from the Hub, and wait until the socket answers.

Everything here is best effort and Windows-specific.  It never raises for a
"cannot start" outcome -- callers get ``(ok, detail)`` and decide what to tell
the user -- because a missing or half-configured Zotero must not break the
reader's error reporting.
"""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import threading
import time
from ctypes import wintypes
from pathlib import Path

PROCESS_NAME = 'zotero.exe'

# Cold start of a large library takes a while; the reader request waits this
# long at most before reporting that the app did not come up.
START_TIMEOUT = 40.0
# A running instance may still be opening its HTTP server (double-clicked just
# now, or started by another window), so give it a shorter grace window.
RUNNING_GRACE = 8.0
# A repeat click while the app is booting must not spawn a second process; the
# caller still waits out the same boot as the request that launched it, so two
# reader panels opening at once both succeed instead of one erroring out.
RELAUNCH_COOLDOWN = 30.0
POLL_INTERVAL = 0.4

# Install locations tried when the registry and PATH know nothing better.
CANDIDATES = (
    '%ProgramFiles%\\Zotero\\zotero.exe',
    '%ProgramFiles(x86)%\\Zotero\\zotero.exe',
    '%LOCALAPPDATA%\\Zotero\\zotero.exe',
    '%USERPROFILE%\\Zotero\\zotero.exe',
    '/opt/zotero/zotero',
    '/usr/bin/zotero',
)

_lifecycle = threading.Lock()
_last_start = 0.0
_last_detail = ''


class _ProcessEntry32W(ctypes.Structure):
    """PROCESSENTRY32W from the Toolhelp API (one running image)."""

    _fields_ = [
        ('dwSize', wintypes.DWORD),
        ('cntUsage', wintypes.DWORD),
        ('th32ProcessID', wintypes.DWORD),
        ('th32DefaultHeapID', ctypes.c_size_t),
        ('th32ModuleID', wintypes.DWORD),
        ('cntThreads', wintypes.DWORD),
        ('th32ParentProcessID', wintypes.DWORD),
        ('pcPriClassBase', ctypes.c_long),
        ('dwFlags', wintypes.DWORD),
        ('szExeFile', wintypes.WCHAR * 260),
    ]


def process_names() -> set:
    """Image names of the running processes, lower-cased; empty when unknown."""
    if os.name != 'nt':
        return set()
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry32W)]
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry32W)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    try:
        snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
        if not snapshot or snapshot == wintypes.HANDLE(-1).value:
            return set()
        try:
            entry = _ProcessEntry32W()
            entry.dwSize = ctypes.sizeof(entry)
            names = set()
            more = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
            while more:
                names.add(entry.szExeFile.lower())
                more = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
            return names
        finally:
            kernel32.CloseHandle(snapshot)
    except OSError:
        return set()


def running() -> bool:
    """True when a Zotero process is visible (unknown listings answer False)."""
    return PROCESS_NAME in process_names()


def _existing(candidate: str) -> Path | None:
    try:
        path = Path(os.path.expandvars(os.path.expanduser(candidate.strip().strip('"'))))
    except (OSError, ValueError):
        return None
    try:
        return path if path.is_file() else None
    except OSError:
        return None


def _registry_app_path() -> str:
    """The ``App Paths`` entry an installed Zotero registers (may be empty)."""
    if os.name != 'nt':
        return ''
    import winreg
    key = r'Software\Microsoft\Windows\CurrentVersion\App Paths\zotero.exe'
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(root, key) as handle:
                value, _ = winreg.QueryValueEx(handle, '')
                if isinstance(value, str) and value.strip():
                    return value
        except OSError:
            continue
    return ''


def _protocol_command() -> str:
    """The executable from Zotero's ``zotero://`` handler, when registered."""
    if os.name != 'nt':
        return ''
    import winreg
    key = r'Software\Classes\zotero\shell\open\command'
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_CLASSES_ROOT):
        try:
            with winreg.OpenKey(root, key) as handle:
                value, _ = winreg.QueryValueEx(handle, '')
        except OSError:
            continue
        if not isinstance(value, str):
            continue
        text = value.strip()
        if text.startswith('"'):
            end = text.find('"', 1)
            return text[1:end] if end > 0 else ''
        return text.split(' ')[0]
    return ''


def locate(explicit: str = '') -> Path | None:
    """Best available path to the Zotero executable, or None.

    The explicitly configured path wins so a portable or relocated install
    works; the discovery order below then tries the registry an installed copy
    writes, the conventional install directories, PATH, and finally the
    ``zotero://`` protocol handler.
    """
    for candidate in (explicit, _registry_app_path()):
        if not candidate:
            continue
        found = _existing(candidate)
        if found is not None:
            return found
    for candidate in CANDIDATES:
        found = _existing(candidate)
        if found is not None:
            return found
    for name in ('zotero', 'zotero.exe'):
        found = shutil.which(name)
        if found:
            resolved = _existing(found)
            if resolved is not None:
                return resolved
    return _existing(_protocol_command())


def launch(executable: Path) -> None:
    """Open Zotero detached from the Hub, so it outlives a Hub restart."""
    flags = 0
    if os.name == 'nt':
        flags = getattr(subprocess, 'DETACHED_PROCESS', 0) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
    subprocess.Popen(
        [str(executable)], cwd=str(executable.parent),
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        close_fds=True, creationflags=flags)


def wait_until(ready, timeout: float) -> bool:
    """Poll ``ready`` until it answers True or the timeout expires."""
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        try:
            if ready():
                return True
        except Exception:
            pass  # a probe that fails is simply "not ready yet"
        if time.monotonic() >= deadline:
            return False
        time.sleep(POLL_INTERVAL)


def ensure(ready, enabled: bool = True, explicit: str = '',
           timeout: float = START_TIMEOUT) -> tuple:
    """Try to make ``ready()`` true by starting Zotero.

    ``ready`` is the caller's cheap liveness probe for the local API.  The
    returned detail says what happened so the caller can word the failure:

    ``already-running`` / ``ready`` / ``started`` are successes.
    ``disabled`` / ``not-found`` / ``launch-failed`` / ``start-timeout`` /
    ``api-disabled`` are failures with distinct fixes.
    """
    global _last_start, _last_detail
    if not enabled:
        return False, _remember('disabled')
    if wait_until(ready, 0):
        return True, _remember('already-running')
    if running():
        if wait_until(ready, RUNNING_GRACE):
            return True, _remember('ready')
        return False, _remember('api-disabled')
    executable = locate(explicit)
    if executable is None:
        return False, _remember('not-found')
    with _lifecycle:
        waited = time.monotonic() - _last_start
        if waited < RELAUNCH_COOLDOWN:
            # An earlier request already opened the app: wait for that boot
            # instead of spawning a second process.
            pending = True
        else:
            pending = False
            try:
                launch(executable)
            except OSError:
                return False, _remember('launch-failed')
            _last_start = time.monotonic()
    if pending:
        if wait_until(ready, timeout):
            return True, _remember('ready')
        return False, _remember('start-timeout')
    if wait_until(ready, timeout):
        return True, _remember('started')
    return False, _remember('start-timeout')


def _remember(detail: str) -> str:
    """Record the last outcome (diagnostics only) and return it."""
    global _last_detail
    _last_detail = detail
    return detail


def last_detail() -> str:
    """The most recent outcome detail, for diagnostics."""
    return _last_detail


def reset() -> None:
    """Forget the cooldown and the last outcome (used by tests and diagnostics)."""
    global _last_start, _last_detail
    with _lifecycle:
        _last_start = 0.0
        _last_detail = ''
