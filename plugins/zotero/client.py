"""Read-only adapter to Zotero's official local API; no library credentials needed.

The local API lives inside the desktop app, so a closed Zotero looks like a
dead socket rather than an HTTP error.  ``request`` treats that one failure as
"the app may simply not be open": it asks :mod:`app` to start Zotero and
then retries once.  Every other failure keeps its own message, because the fix
differs (enable the local API, download the attachment, pick another item).
"""
import json
import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import ProxyHandler, build_opener, url2pathname

from workbench import config
from workbench.errors import WorkbenchError
from . import app

BASE = 'http://127.0.0.1:23119/api/users/0/'
REQUEST_TIMEOUT = 8
# The liveness probe must be quick: it runs while a reader request waits.
PROBE_TIMEOUT = 2

CONNECT_MESSAGE = '无法连接 Zotero，请保持电脑上的 Zotero 运行并开启本地 API。'
HTTP_MESSAGE = 'Zotero 未开启本地 API 或条目不存在，请检查桌面端。'
# 自动启动没成功时按原因给出下一步，而不是把四种问题说成同一句话。
START_MESSAGES = {
    'disabled': 'Zotero 没有运行，自动启动已在设置里关闭（zotero_autostart），请手动打开 Zotero。',
    'not-found': '没有找到 Zotero 程序，请在 plugins.json 里填写 zotero.path（例如 C:\\Program Files\\Zotero\\zotero.exe）。',
    'launch-failed': '启动 Zotero 失败，请手动打开 Zotero 后重试。',
    'start-timeout': '已尝试启动 Zotero，但本地 API 还没有响应；请确认 Zotero 设置里已开启“允许其他应用与本机 Zotero 通信”。',
    'api-disabled': 'Zotero 已在运行，但本地 API 没有响应；请在 Zotero 设置中开启“允许其他应用与本机 Zotero 通信”。',
}


def key(value):
    if not re.fullmatch(r'[A-Z0-9]{8}', value or ''):
        raise ValueError('无效的 Zotero 条目编号')
    return value


def _fetch(url, text):
    """One HTTP exchange with Zotero's local API; transport errors propagate."""
    with build_opener(ProxyHandler({})).open(url, timeout=REQUEST_TIMEOUT) as response:
        raw = response.read().decode('utf-8')
        return (raw if text else json.loads(raw)), response.headers


def _http_error(exc):
    """Map an HTTP status from Zotero onto the reader's error contract."""
    return WorkbenchError(HTTP_MESSAGE, status=404 if exc.code == 404 else 503,
                    code='zotero_unavailable')


def probe():
    """True when Zotero's local HTTP server answers at all."""
    try:
        with build_opener(ProxyHandler({})).open(BASE + 'collections?limit=1',
                                                 timeout=PROBE_TIMEOUT) as response:
            response.read(64)
        return True
    except HTTPError:
        return True  # an HTTP answer means the socket is live
    except (URLError, OSError, ValueError):
        return False


def settings() -> dict:
    """Per-plugin settings from ``$WORKBENCH_HOME/plugins.json``; both optional."""
    return {"autostart": bool(config.plugin_setting("zotero", "autostart", True)),
            "path": str(config.plugin_setting("zotero", "path", "") or "")}


def _auto_start(hub=None):
    """Start Zotero per the plugin settings; returns ``(ok, detail)``."""
    values = settings()
    return app.ensure(probe, enabled=values["autostart"],
                      explicit=values["path"])


def request(path, params=None, text=False, hub=None):
    url = BASE + path + ('?' + urlencode(params) if params else '')
    try:
        return _fetch(url, text)
    except HTTPError as exc:
        raise _http_error(exc) from exc
    except (URLError, OSError, ValueError) as exc:
        started, detail = _auto_start(hub)
        if started:
            try:
                return _fetch(url, text)
            except HTTPError as inner:
                raise _http_error(inner) from inner
            except (URLError, OSError, ValueError) as inner:
                raise WorkbenchError(CONNECT_MESSAGE, status=503,
                               code='zotero_unavailable') from inner
        raise WorkbenchError(START_MESSAGES.get(detail, CONNECT_MESSAGE),
                       status=503, code='zotero_unavailable') from exc


def item_summary(item):
    data = item.get('data', {})
    creators = data.get('creators', [])
    authors = ', '.join(c.get('name') or ' '.join(filter(None, [c.get('firstName'), c.get('lastName')])) for c in creators)
    return {'key': item['key'], 'title': data.get('title') or '未命名文献',
            'authors': authors, 'date': data.get('date', ''),
            'publication': data.get('publicationTitle') or data.get('conferenceName', ''),
            'abstract': data.get('abstractNote', ''), 'doi': data.get('DOI', ''),
            'type': data.get('itemType', ''), 'tags': [t['tag'] for t in data.get('tags', [])],
            'has_pdf': data.get('contentType') == 'application/pdf' or item.get('links', {}).get('attachment', {}).get('attachmentType') == 'application/pdf'}


def collections(hub=None):
    rows, _ = request('collections', {'limit': 1000}, hub=hub)
    return {'collections': [{'key': r['key'], 'name': r['data']['name'],
                             'parent': r['data'].get('parentCollection') or None} for r in rows]}


def items(query, hub=None):
    collection = (query.get('collection') or [''])[0]
    start = max(0, int((query.get('start') or ['0'])[0]))
    params = {'limit': 25, 'start': start, 'sort': 'dateModified', 'direction': 'desc'}
    search = (query.get('q') or [''])[0].strip()[:200]
    if search:
        params['q'] = search
    prefix = 'collections/%s/' % key(collection) if collection else ''
    rows, headers = request(prefix + 'items/top', params, hub=hub)
    return {'items': [item_summary(r) for r in rows],
            'total': int(headers.get('Total-Results', len(rows))), 'start': start}


def detail(item_key, hub=None):
    item, _ = request('items/' + key(item_key), hub=hub)
    result = item_summary(item)
    if item['data'].get('itemType') == 'attachment':
        children = [item]
    else:
        children, _ = request('items/%s/children' % item_key, {'limit': 100}, hub=hub)
    result['pdfs'] = [{'key': c['key'], 'title': c['data'].get('title') or 'PDF',
                       'url': '/api/zotero/files/' + c['key']} for c in children
                      if c.get('data', {}).get('contentType') == 'application/pdf']
    return result


def pdf_path(item_key, hub=None):
    item_key = key(item_key)
    item, _ = request('items/' + item_key, hub=hub)
    if item.get('data', {}).get('contentType') != 'application/pdf':
        raise WorkbenchError('此附件不是 PDF。', status=404, code='not_pdf')
    url, _ = request('items/%s/file/view/url' % item_key, text=True, hub=hub)
    parts = urlsplit(url.strip())
    if parts.scheme != 'file' or parts.netloc not in ('', 'localhost'):
        raise WorkbenchError('附件尚未下载到本机。', status=404, code='pdf_missing')
    path = Path(url2pathname(parts.path))
    try:
        with path.open('rb') as file:
            if file.read(5) != b'%PDF-':
                raise OSError('not PDF')
    except OSError as exc:
        raise WorkbenchError('PDF 文件不存在或尚未下载，请在 Zotero 中打开附件后重试。',
                       status=404, code='pdf_missing') from exc
    return path
