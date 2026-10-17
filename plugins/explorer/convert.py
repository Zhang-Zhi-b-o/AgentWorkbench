"""Render a Word/Excel/PowerPoint file to PDF so the browser can show it whole.

A text outline is not a preview: the point is to see the document as it looks.
The workbench runs on the same Windows machine as Microsoft Office, so each
document is opened with its own application and exported to PDF, then shown
read-only in the browser's PDF viewer.  Nothing about the original file changes,
and downloading still hands over the untouched source.

The PDF is cached under ``data/preview-cache`` keyed on path, size and mtime.
Conversions run on a worker thread with a deadline, because Office automation can
block on a dialog in a document someone already has open.
"""
from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path

WORD = {".doc", ".docx", ".docm", ".dot", ".dotx", ".odt", ".rtf"}
EXCEL = {".xls", ".xlsx", ".xlsm", ".xlt", ".xltx", ".ods", ".csv"}
POWERPOINT = {".ppt", ".pptx", ".pptm", ".pot", ".potx", ".odp"}

# Export format constants from the Office object models.
_WD_EXPORT_PDF = 17
_XL_TYPE_PDF = 0
_PP_SAVE_AS_PDF = 32

_CACHE_LIMIT = 512 * 1024 * 1024
_DEADLINE = 75.0
_lock = threading.Lock()


class ConvertError(RuntimeError):
    """The document could not be rendered to PDF."""


def supported(path: Path) -> bool:
    suffix = path.suffix.lower()
    return suffix in WORD or suffix in EXCEL or suffix in POWERPOINT


def _cache_dir(home: Path) -> Path:
    directory = home / "preview-cache"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _cache_key(source: Path) -> str:
    info = source.stat()
    seed = "%s|%d|%d" % (source, info.st_mtime_ns, info.st_size)
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:32]


def _trim(directory: Path) -> None:
    """Keep the cache bounded; an old preview is only a rebuild away."""
    try:
        entries = sorted(directory.glob("*.pdf"),
                         key=lambda item: item.stat().st_mtime, reverse=True)
    except OSError:
        return
    total = 0
    for item in entries:
        try:
            total += item.stat().st_size
        except OSError:
            continue
        if total > _CACHE_LIMIT:
            try:
                item.unlink()
            except OSError:
                pass


def _word(source: Path, target: Path) -> None:
    import win32com.client  # COM is only available on Windows

    app = win32com.client.DispatchEx("Word.Application")
    try:
        app.Visible = False
        app.DisplayAlerts = 0
        document = app.Documents.Open(str(source), ReadOnly=True,
                                      AddToRecentFiles=False, Visible=False)
        try:
            document.ExportAsFixedFormat(str(target), _WD_EXPORT_PDF)
        finally:
            document.Close(0)
    finally:
        app.Quit()


def _excel(source: Path, target: Path) -> None:
    import win32com.client

    app = win32com.client.DispatchEx("Excel.Application")
    try:
        app.Visible = False
        app.DisplayAlerts = False
        book = app.Workbooks.Open(str(source), ReadOnly=True, UpdateLinks=0)
        try:
            book.ExportAsFixedFormat(_XL_TYPE_PDF, str(target))
        finally:
            book.Close(False)
    finally:
        app.Quit()


def _powerpoint(source: Path, target: Path) -> None:
    import win32com.client

    app = win32com.client.DispatchEx("PowerPoint.Application")
    presentation = None
    try:
        presentation = app.Presentations.Open(str(source), ReadOnly=True,
                                              WithWindow=False)
        presentation.SaveAs(str(target), _PP_SAVE_AS_PDF)
    finally:
        if presentation is not None:
            try:
                presentation.Close()
            except Exception:
                pass
        try:
            app.Quit()
        except Exception:
            pass


def _convert(source: Path, target: Path) -> None:
    """Convert one document, writing the PDF atomically."""
    try:
        import pythoncom
    except ImportError as exc:  # pragma: no cover - Windows only
        raise ConvertError("这台机器上没有装 pywin32，无法生成预览") from exc
    suffix = source.suffix.lower()
    if suffix in WORD:
        worker = _word
    elif suffix in EXCEL:
        worker = _excel
    elif suffix in POWERPOINT:
        worker = _powerpoint
    else:
        raise ConvertError("不支持预览这种文件：%s" % suffix)
    # Excel and PowerPoint refuse to export to a dot-prefixed name, so the
    # in-flight file is ordinary, ends in .pdf, and is renamed when complete.
    holder = target.with_name("tmp-" + target.name)
    pythoncom.CoInitialize()
    try:
        worker(source, holder)
    except ConvertError:
        raise
    except Exception as exc:  # the COM layer raises a zoo of types
        raise ConvertError("转换失败：%s: %s" % (type(exc).__name__, exc)) from exc
    finally:
        pythoncom.CoUninitialize()
    if not holder.exists() or holder.stat().st_size == 0:
        try:
            holder.unlink()
        except OSError:
            pass
        raise ConvertError("转换没有产生结果")
    os.replace(holder, target)


def _convert_with_deadline(source: Path, target: Path) -> None:
    """Run the conversion off-thread so a stuck Office cannot wedge the request."""
    failure: list[BaseException] = []
    finished = threading.Event()

    def run() -> None:
        try:
            _convert(source, target)
        except BaseException as exc:  # reported to the waiting request below
            failure.append(exc)
        finally:
            finished.set()

    threading.Thread(target=run, daemon=True, name="office-preview").start()
    if not finished.wait(_DEADLINE):
        raise ConvertError("转换超时，Office 可能正被占用；稍后再试或直接下载")
    if failure:
        raise failure[0]


def to_pdf(source: Path, home: Path) -> Path:
    """Path to a cached PDF rendering of ``source``, converting it if needed."""
    if not source.is_file():
        raise ConvertError("文件不存在")
    if not supported(source):
        raise ConvertError("不支持预览这种文件：%s" % source.suffix)
    directory = _cache_dir(home)
    target = directory / (_cache_key(source) + ".pdf")
    with _lock:  # Office automation is one-at-a-time in practice
        if target.exists() and target.stat().st_size > 0:
            return target
        _trim(directory)
        _convert_with_deadline(source, target)
    return target
