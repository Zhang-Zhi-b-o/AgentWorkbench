"""资源管理器插件：把「这台电脑」搬进浏览器。

本模块只做文件系统本身，不做渲染：列出目录、读写文本、上传下载、复制移动、
重命名删除（走回收站）、打包解包、搜索，以及把某个目录交给工作台里的 VS Code。

范围就是整台电脑——这个插件的用途就是远程操作自己的机器，所以不设沙箱，只做
两类防护：拒绝空路径/驱动器根这类「删掉整台机器」的操作；任何写操作都先写临时
文件再 ``os.replace``，断电或中断不会留下半个文件。

删除默认进回收站（Windows 的 ``SHFileOperation``），因此误删可以像在电脑上一样
从回收站恢复；显式传 ``permanent`` 才真正抹掉。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import errno
import json
import mimetypes
import os
import re
import shutil
import stat as stat_module
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from workbench import config
from workbench.errors import WorkbenchError
from workbench.folders import remote_query

from . import convert

PREFIX = "/api/explorer"
# How much text one editor tab will load into the browser.
READ_LIMIT = 4 * 1024 * 1024
# A recursive search is a foreground request: bound it by time and by hits.
SEARCH_LIMIT = 400
SEARCH_SECONDS = 6.0
SEARCH_VISIT_LIMIT = 60_000
# Zips are built on disk, then streamed: the browser needs a Content-Length.
ZIP_DIR = config.HOME / "cache" / "explorer"
ZIP_MAX_AGE = 2 * 60 * 60

# Extensions the explorer knows how to label (the browser only picks icons).
_KIND_BY_EXT = {
    ".doc": "word", ".docx": "word", ".docm": "word", ".odt": "word", ".rtf": "word",
    ".xls": "excel", ".xlsx": "excel", ".xlsm": "excel", ".csv": "excel", ".ods": "excel",
    ".ppt": "ppt", ".pptx": "ppt", ".pptm": "ppt", ".odp": "ppt",
    ".pdf": "pdf",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image", ".webp": "image",
    ".bmp": "image", ".svg": "image", ".ico": "image", ".avif": "image",
    ".mp4": "video", ".webm": "video", ".mkv": "video", ".mov": "video", ".avi": "video",
    ".mp3": "audio", ".wav": "audio", ".flac": "audio", ".m4a": "audio", ".ogg": "audio",
    ".zip": "archive", ".7z": "archive", ".rar": "archive", ".tar": "archive",
    ".gz": "archive", ".bz2": "archive", ".xz": "archive",
    ".md": "text", ".markdown": "text", ".txt": "text", ".log": "text", ".json": "text",
    ".yml": "text", ".yaml": "text", ".toml": "text", ".ini": "text", ".cfg": "text",
    ".xml": "text", ".htm": "text", ".html": "text", ".css": "text", ".js": "text",
    ".mjs": "text", ".cjs": "text", ".ts": "text", ".tsx": "text", ".jsx": "text",
    ".py": "text", ".ps1": "text", ".bat": "text", ".cmd": "text", ".sh": "text",
    ".c": "text", ".h": "text", ".cpp": "text", ".hpp": "text", ".rs": "text",
    ".go": "text", ".java": "text", ".sql": "text", ".tex": "text", ".bib": "text",
    ".cs": "text", ".rb": "text", ".php": "text", ".lua": "text", ".vue": "text",
}


# -- settings -----------------------------------------------------------
def _setting(key: str, default=None):
    return config.plugin_setting("explorer", key, default)


def _start_dir() -> str:
    """Directory the plugin opens on, falling back to the user profile."""
    configured = str(_setting("root", "") or "").strip()
    if configured:
        return configured
    return str(Path.home())


# -- path validation ----------------------------------------------------
def _resolve(value: str, *, must_exist: bool = True, want_dir: bool = False) -> Path:
    """Absolute path -> resolved Path, with a readable error for the browser."""
    text = str(value or "").strip()
    if not text:
        raise WorkbenchError("缺少路径", status=400, code="missing_path")
    if "\x00" in text:
        raise WorkbenchError("路径包含非法字符", status=400, code="bad_path")
    path = Path(text).expanduser()
    if not path.is_absolute():
        raise WorkbenchError("必须使用绝对路径：%s" % text, status=400, code="relative_path")
    try:
        path = path.resolve()
    except OSError as exc:
        raise WorkbenchError("路径无法解析：%s" % exc, status=400, code="bad_path") from exc
    if must_exist and not path.exists():
        raise WorkbenchError("路径不存在：%s" % path, status=404, code="path_not_found")
    if want_dir and not path.is_dir():
        raise WorkbenchError("这不是一个文件夹：%s" % path, status=400, code="not_a_directory")
    return path


def _guarded(path: Path) -> Path:
    """Refuse operations that would take the volume or the machine with them."""
    if path.parent == path or (os.name == "nt" and len(str(path)) <= 3):
        raise WorkbenchError("不能对驱动器根目录执行这个操作", status=400,
                             code="protected_path")
    home = Path.home()
    if path == home:
        raise WorkbenchError("不能对这个目录本身执行这个操作", status=400,
                             code="protected_path")
    return path


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def _unique(target: Path) -> Path:
    """`name.ext` -> `name (2).ext`, `name (3).ext`, ... until free."""
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    for index in range(2, 10_000):
        candidate = target.with_name("%s (%d)%s" % (stem, index, suffix))
        if not candidate.exists():
            return candidate
    raise WorkbenchError("同名文件太多，请先整理目录", status=409, code="too_many_copies")


# -- platform helpers ---------------------------------------------------
def _windows() -> bool:
    return os.name == "nt"


def _known_folders() -> list[dict]:
    """Explorer's own quick-access locations, resolved through the shell.

    OneDrive can redirect Desktop/Documents/Pictures off the profile; asking the
    shell for the folder ids keeps those entries pointing where Explorer does.
    """
    if not _windows():
        home = Path.home()
        picks = [("主目录", home), ("桌面", home / "Desktop"),
                 ("文档", home / "Documents"), ("下载", home / "Downloads")]
        return [{"name": name, "path": str(path)} for name, path in picks
                if path.is_dir()]

    class GUID(ctypes.Structure):
        _fields_ = (("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16),
                    ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8))

    def guid(text: str) -> GUID:
        raw = uuid.UUID(text)
        return GUID(raw.time_low, raw.time_mid, raw.time_hi_version,
                    (ctypes.c_ubyte * 8)(*raw.bytes[8:]))

    folders = [
        ("主目录", "5E6C858F-0E22-4760-9AFE-EA3317B67173"),
        ("桌面", "B4BFCC3A-DB2C-424C-B029-7FE99A87C641"),
        ("文档", "FDD39AD0-238F-46AF-ADB4-6C85480369C7"),
        ("下载", "374DE290-123F-4565-9164-39C4925E467B"),
        ("图片", "33E28130-4E1E-4676-835A-98395C3BC3BB"),
        ("视频", "18989B1D-99B5-455B-841C-AB7C74E4DDFC"),
        ("音乐", "4BD8D571-6D19-48D3-BE97-422220080E43"),
    ]
    found = []
    shell32 = ctypes.windll.shell32
    ole32 = ctypes.windll.ole32
    for name, text in folders:
        raw = ctypes.c_wchar_p()
        try:
            result = shell32.SHGetKnownFolderPath(ctypes.byref(guid(text)), 0, None,
                                                  ctypes.byref(raw))
        except OSError:
            continue
        if result != 0 or not raw.value:
            continue
        found.append({"name": name, "path": raw.value})
        ole32.CoTaskMemFree(raw)
    return found


_DRIVE_KIND = {2: "removable", 3: "fixed", 4: "network", 5: "cdrom", 6: "ram"}


def _drives() -> list[dict]:
    """Every mounted volume, labelled the way Explorer labels it."""
    if not _windows():
        return [{"name": "/", "path": "/", "kind": "fixed", "label": ""}]

    found = []
    bitmask = ctypes.windll.kernel32.GetLogicalDrives()
    kernel32 = ctypes.windll.kernel32
    for index in range(26):
        if not bitmask & (1 << index):
            continue
        root = "%s:\\" % chr(ord("A") + index)
        kind = _DRIVE_KIND.get(kernel32.GetDriveTypeW(root), "fixed")
        label = ""
        if kind != "cdrom":
            buffer = ctypes.create_unicode_buffer(261)
            try:
                kernel32.GetVolumeInformationW(root, buffer, 261, None, None, None,
                                               None, 0)
                label = str(buffer.value or "")
            except OSError:
                label = ""
        found.append({"name": root[:2], "path": root, "kind": kind, "label": label})
    return found


def _entry(path: Path, name: str | None = None) -> dict:
    """One directory entry, tolerant of entries that cannot be stat'ed."""
    try:
        info = path.stat()
        is_dir = path.is_dir()
        size = 0 if is_dir else info.st_size
        mtime = info.st_mtime
        readonly = not os.access(path, os.W_OK)
        attrs = getattr(info, "st_file_attributes", 0)
        hidden = bool(attrs & 0x2) or (name or path.name).startswith(".")
        link = path.is_symlink() or bool(attrs & 0x400)
    except OSError:
        is_dir, size, mtime, readonly, hidden, link = False, 0, 0.0, True, False, False
    label = name if name is not None else path.name
    ext = path.suffix.lower()
    return {
        "name": label,
        "path": str(path),
        "dir": bool(is_dir),
        "size": int(size),
        "mtime": float(mtime),
        "ext": ext,
        "kind": "dir" if is_dir else _KIND_BY_EXT.get(ext, "other"),
        "hidden": bool(hidden),
        "readonly": bool(readonly),
        "link": bool(link),
        "dirroot": bool(is_dir and path.parent == path),
    }


def _natural(text: str):
    return [int(part) if part.isdigit() else part.lower()
            for part in re.split(r"(\d+)", text or "")]


def _iso(mtime: float) -> str:
    if not mtime:
        return ""
    return datetime.fromtimestamp(mtime, tz=timezone.utc).astimezone().isoformat(timespec="seconds")


# -- read / list --------------------------------------------------------
def info() -> dict:
    return {
        "platform": "windows" if _windows() else os.name,
        "sep": os.sep,
        "home": str(Path.home()),
        "start": _start_dir(),
        "drives": _drives(),
        "quick": _known_folders(),
        "trash": _windows(),
        "limits": {"read": READ_LIMIT, "upload": config.RAW_BODY_LIMIT,
                   "search": SEARCH_LIMIT},
    }


def listing(value: str) -> dict:
    """One directory; an empty path is the synthetic 'This PC' root."""
    text = str(value or "").strip()
    if not text:
        entries = [{
            "name": (drive["label"] or drive["name"]) + " (" + drive["path"] + ")",
            "path": drive["path"], "dir": True, "size": 0, "mtime": 0.0,
            "ext": "", "kind": "drive", "hidden": False, "readonly": False,
            "link": False, "dirroot": True, "drive": drive["kind"],
        } for drive in _drives()]
        return {"path": "", "name": "此电脑", "parent": "", "isRoot": True,
                "writable": False, "entries": entries}

    path = _resolve(text, want_dir=True)
    try:
        with os.scandir(path) as stream:
            raw = list(stream)
    except PermissionError as exc:
        raise WorkbenchError("没有权限读取这个文件夹", status=403,
                             code="permission_denied") from exc
    except OSError as exc:
        raise WorkbenchError("无法读取这个文件夹：%s" % exc, status=400,
                             code="io_error") from exc
    entries = []
    for item in raw:
        try:
            entries.append(_entry(Path(item.path), item.name))
        except OSError:
            continue
    entries.sort(key=lambda entry: (not entry["dir"], _natural(entry["name"])))
    parent = "" if path.parent == path else str(path.parent)
    return {
        "path": str(path), "name": path.name or str(path),
        "parent": parent, "isRoot": False,
        "writable": os.access(path, os.W_OK),
        "entries": entries,
    }


def read_text(value: str) -> dict:
    path = _resolve(value)
    if path.is_dir():
        raise WorkbenchError("这是一个文件夹", status=400, code="not_a_file")
    size = path.stat().st_size
    if size > READ_LIMIT:
        raise WorkbenchError("文件太大（%s），请用 VS Code 打开" % _human(size),
                             status=413, code="file_too_large")
    data = path.read_bytes()
    if b"\x00" in data[:8192]:
        raise WorkbenchError("这不是文本文件，不能在这里编辑", status=415,
                             code="not_text")
    encoding = "utf-8"
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            encoding = "gb18030"
            content = data.decode("gb18030")
        except UnicodeDecodeError:
            encoding = "latin-1"
            content = data.decode("latin-1")
    return {"path": str(path), "content": content, "encoding": encoding,
            "size": size, "mtime": path.stat().st_mtime,
            "mtimeText": _iso(path.stat().st_mtime)}


def write_text(body: dict) -> dict:
    path = _resolve(str(body.get("path") or ""), must_exist=False)
    if path.is_dir():
        raise WorkbenchError("这是一个文件夹", status=400, code="not_a_file")
    content = body.get("content")
    if not isinstance(content, str):
        raise WorkbenchError("缺少文件内容", status=400, code="missing_content")
    encoding = str(body.get("encoding") or "utf-8").lower()
    if encoding in ("utf8", "utf-8-sig"):
        encoding = "utf-8"
    try:
        data = content.encode(encoding, errors="strict")
    except (LookupError, UnicodeEncodeError):
        encoding = "utf-8"
        data = content.encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name("." + path.name + ".wb-tmp")
    try:
        temp.write_bytes(data)
        os.replace(temp, path)
    except OSError as exc:
        try:
            temp.unlink()
        except OSError:
            pass
        raise WorkbenchError("保存失败：%s" % exc, status=400, code="io_error") from exc
    return {"path": str(path), "size": len(data), "encoding": encoding}


# -- create / rename / copy / move -------------------------------------
def _safe_name(name: str) -> str:
    text = str(name or "").strip()
    if not text or text in (".", ".."):
        raise WorkbenchError("名称不能为空", status=400, code="bad_name")
    if any(char in text for char in "\\/:*?\"<>|\x00"):
        raise WorkbenchError("名称不能包含 \\ / : * ? \" < > |", status=400,
                             code="bad_name")
    return text


def make_dir(body: dict) -> dict:
    base = _resolve(str(body.get("path") or ""), want_dir=True)
    target = base / _safe_name(body.get("name"))
    try:
        target.mkdir()
    except FileExistsError as exc:
        raise WorkbenchError("同名项目已存在", status=409, code="exists") from exc
    except OSError as exc:
        raise WorkbenchError("新建文件夹失败：%s" % exc, status=400,
                             code="io_error") from exc
    return {"entry": _entry(target)}


# Office starter documents live with the office plugin, which owns them.
_TEMPLATE_DIR = config.PLUGIN_DIR / "office" / "templates"
_OFFICE_KIND_EXT = {"word": ".docx", "excel": ".xlsx", "ppt": ".pptx"}


def create_file(body: dict) -> dict:
    """New empty file: a template when the office plugin ships one, else nothing."""
    base = _resolve(str(body.get("path") or ""), want_dir=True)
    name = _safe_name(body.get("name"))
    kind = str(body.get("kind") or "text").lower()
    suffix = os.path.splitext(name)[1].lower()
    template = None
    if kind in _OFFICE_KIND_EXT:
        suffix = _OFFICE_KIND_EXT[kind]
        if not name.lower().endswith(suffix):
            name += suffix
        template = _TEMPLATE_DIR / ("blank" + suffix)
    elif not suffix:
        suffix = ".txt"
        if not name.lower().endswith(suffix):
            name += suffix
    target = _unique(base / name)
    try:
        if template is not None and template.is_file():
            shutil.copyfile(template, target)
        else:
            target.write_bytes(b"")
    except OSError as exc:
        raise WorkbenchError("新建文件失败：%s" % exc, status=400, code="io_error") from exc
    return {"entry": _entry(target)}


def rename(body: dict) -> dict:
    path = _guarded(_resolve(str(body.get("path") or "")))
    name = _safe_name(body.get("name"))
    target = path.with_name(name)
    if target == path:
        return {"entry": _entry(path)}
    if target.exists() and not bool(body.get("overwrite")):
        raise WorkbenchError("同名项目已存在", status=409, code="exists")
    try:
        os.replace(path, target)
    except OSError as exc:
        raise WorkbenchError("重命名失败：%s" % exc, status=400, code="io_error") from exc
    return {"entry": _entry(target)}


def _targets(body: dict) -> list[Path]:
    items = body.get("paths")
    if not isinstance(items, list) or not items:
        raise WorkbenchError("没有选中任何项目", status=400, code="no_selection")
    found = []
    for item in items:
        found.append(_resolve(str(item or "")))
    return found


def _transfer(body: dict, *, move: bool) -> dict:
    dest = _resolve(str(body.get("dest") or ""), want_dir=True)
    done = []
    for source in _targets(body):
        _guarded(source)
        if source.is_dir() and (_inside(dest, source) or dest == source):
            raise WorkbenchError("不能把文件夹放进它自己里面", status=400,
                                 code="recursive_move")
        target = _unique(dest / source.name)
        try:
            if move:
                shutil.move(str(source), str(target))
            elif source.is_dir():
                shutil.copytree(source, target, symlinks=True)
            else:
                shutil.copy2(source, target)
        except (OSError, shutil.Error) as exc:
            raise WorkbenchError("%s失败：%s" % ("移动" if move else "复制", exc),
                                 status=400, code="io_error") from exc
        done.append(_entry(target))
    return {"entries": done}


def move(body: dict) -> dict:
    return _transfer(body, move=True)


def copy(body: dict) -> dict:
    return _transfer(body, move=False)


# -- delete -------------------------------------------------------------
class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = (("hwnd", ctypes.c_void_p), ("wFunc", ctypes.c_uint),
                ("pFrom", ctypes.c_wchar_p), ("pTo", ctypes.c_wchar_p),
                ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", ctypes.c_int),
                ("hNameMappings", ctypes.c_void_p),
                ("lpszProgressTitle", ctypes.c_wchar_p))


def _recycle(paths: list[Path]) -> None:
    """Move to the Recycle Bin through the shell's own delete verb."""
    joined = "\x00".join(str(path) for path in paths) + "\x00\x00"
    operation = _SHFILEOPSTRUCTW(
        None, 3,  # FO_DELETE
        joined, None,
        0x0040 | 0x0010 | 0x0004 | 0x0400,  # ALLOWUNDO | NOCONFIRMATION | SILENT | NOERRORUI
        0, None, None)
    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation))
    if result != 0 or operation.fAnyOperationsAborted:
        raise WorkbenchError("移入回收站失败（错误码 %d）" % result, status=400,
                             code="recycle_failed")


def delete(body: dict) -> dict:
    paths = [_guarded(path) for path in _targets(body)]
    permanent = bool(body.get("permanent"))
    deleted = []
    if _windows() and not permanent:
        _recycle(paths)
        deleted = [str(path) for path in paths]
    else:
        for path in paths:
            try:
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path)
                else:
                    path.unlink()
            except OSError as exc:
                raise WorkbenchError("删除失败：%s" % exc, status=400,
                                     code="io_error") from exc
            deleted.append(str(path))
    return {"deleted": deleted, "permanent": permanent}


# -- search -------------------------------------------------------------
def search(value: str, term: str) -> dict:
    base = _resolve(value, want_dir=True)
    needle = str(term or "").strip().lower()
    if not needle:
        return {"path": str(base), "query": "", "entries": [], "truncated": False}
    deadline = time.monotonic() + SEARCH_SECONDS
    found, visited, truncated = [], 0, False
    for root, dirs, files in os.walk(base):
        if time.monotonic() > deadline or visited >= SEARCH_VISIT_LIMIT:
            truncated = True
            break
        visited += len(dirs) + len(files)
        for name in dirs + files:
            if needle in name.lower():
                try:
                    found.append(_entry(Path(root) / name, name))
                except OSError:
                    continue
                if len(found) >= SEARCH_LIMIT:
                    truncated = True
                    break
        if truncated:
            break
    found.sort(key=lambda entry: (not entry["dir"], _natural(entry["name"])))
    return {"path": str(base), "query": term, "entries": found, "truncated": truncated}


# -- archive ------------------------------------------------------------
def _purge_zips() -> None:
    try:
        ZIP_DIR.mkdir(parents=True, exist_ok=True)
        cutoff = time.time() - ZIP_MAX_AGE
        for item in ZIP_DIR.iterdir():
            try:
                if item.is_file() and item.stat().st_mtime < cutoff:
                    item.unlink()
            except OSError:
                continue
    except OSError:
        pass


def _human(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return ("%d %s" % (size, unit)) if unit == "B" else ("%.1f %s" % (size, unit))
        size /= 1024.0
    return "%d B" % size


def zip_path(value: str) -> Path:
    """Zip a file or folder on disk and return the archive to stream."""
    source = _resolve(value)
    _purge_zips()
    ZIP_DIR.mkdir(parents=True, exist_ok=True)
    archive = ZIP_DIR / (uuid.uuid4().hex + ".zip")
    base = source if source.is_dir() else source.parent
    try:
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED,
                             compresslevel=6, allowZip64=True) as bundle:
            if source.is_dir():
                for root, _dirs, files in os.walk(source):
                    for name in files:
                        item = Path(root) / name
                        try:
                            bundle.write(item, item.relative_to(base))
                        except (OSError, ValueError):
                            continue
            else:
                bundle.write(source, source.name)
    except OSError as exc:
        try:
            archive.unlink()
        except OSError:
            pass
        raise WorkbenchError("打包失败：%s" % exc, status=400, code="io_error") from exc
    return archive


def unzip(body: dict) -> dict:
    source = _resolve(str(body.get("path") or ""))
    if not zipfile.is_zipfile(source):
        raise WorkbenchError("不是有效的 zip 文件", status=400, code="not_zip")
    dest_text = str(body.get("dest") or "").strip()
    dest = _resolve(dest_text, want_dir=True) if dest_text else _unique(
        source.with_suffix(""))
    dest.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(source) as bundle:
            for member in bundle.infolist():
                target = (dest / member.filename).resolve()
                if not _inside(target, dest):
                    raise WorkbenchError("压缩包包含越界路径，已拒绝解压", status=400,
                                         code="zip_slip")
            bundle.extractall(dest)
    except (OSError, zipfile.BadZipFile) as exc:
        raise WorkbenchError("解压失败：%s" % exc, status=400, code="io_error") from exc
    return {"dest": str(dest)}


# -- upload -------------------------------------------------------------
def upload(request, body: dict) -> dict:
    """Stream one upload into a directory; the body never sits in memory."""
    directory = _resolve(str(request.arg("dir") or body.get("dir") or ""), want_dir=True)
    name = _safe_name(request.arg("name") or body.get("name"))
    target = _unique(directory / name)
    temp = target.with_name("." + target.name + ".wb-upload")
    written = request.stream_to(temp, config.RAW_BODY_LIMIT)
    try:
        os.replace(temp, target)
    except OSError as exc:
        try:
            temp.unlink()
        except OSError:
            pass
        raise WorkbenchError("保存上传文件失败：%s" % exc, status=400,
                             code="io_error") from exc
    return {"entry": _entry(target), "size": written}


# -- open elsewhere -----------------------------------------------------
def open_with(body: dict) -> dict:
    """Hand a path to another surface: VS Code, the office editor, or the desktop."""
    path = _resolve(str(body.get("path") or ""))
    app = str(body.get("app") or "").strip().lower()
    folder = path if path.is_dir() else path.parent
    if app == "vscode":
        # ``?folder=`` takes a remote path (``/d:/dir``), never a URI: see
        # ``workbench.folders`` for why a file URI leaves the editor empty.
        return {"app": "vscode", "url": "/vscode/?folder=" + remote_query(folder),
                "folder": str(folder)}
    if app == "office":
        from urllib.parse import quote
        return {"app": "office", "url": "/plugins/office/?path=" + quote(str(path)),
                "path": str(path)}
    if app == "local":
        if not _windows():
            raise WorkbenchError("只有 Windows 能在这里打开本机应用", status=400,
                                 code="unsupported")
        try:
            os.startfile(str(path))  # type: ignore[attr-defined]
        except OSError as exc:
            raise WorkbenchError("本机打开失败：%s" % exc, status=400,
                                 code="open_failed") from exc
        return {"app": "local", "path": str(path)}
    raise WorkbenchError("未知的打开方式：%s" % app, status=400, code="bad_app")


# -- HTTP ---------------------------------------------------------------
def handle(request) -> bool:
    """Serve the explorer's routes; False leaves the path to someone else."""
    path = request.path
    if not (path == PREFIX or path.startswith(PREFIX + "/")):
        return False
    route = path[len(PREFIX):].strip("/")
    method = request.method

    if method == "GET" and route == "info":
        request.json(200, info())
        return True
    if method == "GET" and route == "list":
        request.json(200, listing(request.arg("path")))
        return True
    if method == "GET" and route == "read":
        request.json(200, read_text(request.arg("path")))
        return True
    if method == "GET" and route == "raw":
        target = _resolve(request.arg("path"))
        if target.is_dir():
            raise WorkbenchError("这是一个文件夹", status=400, code="not_a_file")
        mime = mimetypes.guess_type(str(target))[0]
        download = request.arg("download") in ("1", "true", "yes")
        request.file(target, content_type=mime, filename=target.name,
                     cache="private, max-age=60", inline=not download)
        return True
    if method == "GET" and route == "preview":
        target = _resolve(request.arg("path"))
        if target.is_dir():
            raise WorkbenchError("这是一个文件夹", status=400, code="not_a_file")
        try:
            rendered = convert.to_pdf(target, config.HOME)
        except convert.ConvertError as exc:
            raise WorkbenchError(str(exc), status=415,
                                 code="preview_failed") from exc
        request.file(rendered, content_type="application/pdf",
                     filename=target.stem + ".pdf",
                     cache="private, max-age=300", inline=True)
        return True
    if method == "GET" and route == "search":
        request.json(200, search(request.arg("path"), request.arg("q")))
        return True
    if method == "GET" and route == "zip":
        archive = zip_path(request.arg("path"))
        name = Path(request.arg("path")).name or "打包"
        request.file(archive, content_type="application/zip",
                     filename=name + ".zip", cache="no-store", inline=False)
        return True

    if method != "POST":
        raise WorkbenchError("不支持的方法", status=405, code="method_not_allowed")

    if route == "write":
        request.json(200, write_text(request.body))
        return True
    if route == "mkdir":
        request.json(200, make_dir(request.body))
        return True
    if route == "new":
        request.json(200, create_file(request.body))
        return True
    if route == "rename":
        request.json(200, rename(request.body))
        return True
    if route == "move":
        request.json(200, move(request.body))
        return True
    if route == "copy":
        request.json(200, copy(request.body))
        return True
    if route == "delete":
        request.json(200, delete(request.body))
        return True
    if route == "unzip":
        request.json(200, unzip(request.body))
        return True
    if route == "open":
        request.json(200, open_with(request.body))
        return True
    if route == "upload":
        request.json(200, upload(request, request.body))
        return True
    raise WorkbenchError("未知的接口：%s" % route, status=404, code="not_found")


# -- plugin interface ---------------------------------------------------
def available() -> bool:
    return True


def status() -> dict:
    return {"start": _start_dir(), "platform": "windows" if _windows() else os.name,
            "trash": _windows(), "readLimit": READ_LIMIT}
