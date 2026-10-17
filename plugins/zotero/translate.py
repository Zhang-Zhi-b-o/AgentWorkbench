"""Reuse Translate for Zotero's configured service, server-side.

网页端（阅读器划词 / 翻译面板）跟 Zotero 桌面插件共用**同一份翻译缓存**，请求也照
插件的 ``gptTranslate`` 复刻，所以同一段原文两边拿到的是同一条译文：

* 缓存文件就是插件的 ``<Zotero 数据目录>/translate-selection-cache-v1.json``，
  键是 ``[规范化原文, 服务, 源语言, 目标语言, 设置指纹]``，设置指纹 = md5(设置数组)，
  设置数组 = 该服务下所有 prefs + attachPaperContext/resultRegex/stripEmptyLines
  + 论文标题/摘要（开了 attachPaperContext 时）+ selectionCleanup + extraPrompt。
  指纹算法必须与插件逐字节一致，否则两边互相看不见。
* 插件占位符是 ``${langFrom}`` / ``${langTo}`` / ``${sourceText}``。``${sourceText}``
  从模板里摘掉，剩下的当 **system 指令**，原文单独当 **user 消息**。
* 温度、思考档位（deepseek 走 ``thinking:{type:"disabled"}``）、`customParams`
  都沿用插件里同一服务的配置；端点按插件 ``normalizeLLMEndpoint`` 补路径。

密钥只在本机进程内使用，绝不回传给前端，也不打印。
"""
import hashlib
import json
import os
import re
import unicodedata
import zipfile
from collections import OrderedDict, namedtuple
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener, url2pathname, urlopen

from workbench.errors import WorkbenchError

PREFIX = 'extensions.zotero.ZoteroPDFTranslate.'
SOURCE_TEXT = '${sourceText}'
# Zotero 桌面端本地 API；只在插件开了 attachPaperContext 时读标题和摘要。
LOCAL_API = 'http://127.0.0.1:23119/api/users/0/items/'
CACHE_FILE = 'translate-selection-cache-v1.json'
CLEANUP_VERSIONS = ('raw-v1', 'body-v1')
THINKING_LEVELS = ('default', 'off', 'low', 'medium', 'high')
SERVICES = {'chatgpt': 'chatGPT',
            'customgpt1': 'customGPT1',
            'customgpt2': 'customGPT2',
            'customgpt3': 'customGPT3'}
# 与插件 src/utils/thinkingParams.ts 的 DIALECT_HOSTS 一致。
DIALECT_HOSTS = (
    ('openai', ('api.openai.com', 'openai.azure.com')),
    ('openrouter', ('openrouter.ai',)),
    ('deepseek', ('deepseek.com',)),
    ('thinkingType', ('bigmodel.cn', 'z.ai', 'moonshot.cn', 'kimi.com', 'kimi.ai',
                      'volces.com')),
    ('dashscope', ('aliyuncs.com',)),
)
DEFAULT_PROMPT = ('Translate the following text from ${langFrom} to ${langTo}. '
                  'Return only the translation.')

Profile = namedtuple('Profile', 'endpoint model secret prompt temperature thinking '
                               'custom target source api_format context service '
                               'strip result_regex')


# ---------------------------------------------------------------- prefs ----

def _profile_dirs():
    profiles = Path(os.environ.get('APPDATA', '')) / 'Zotero/Zotero/Profiles'
    return sorted(profiles.glob('*'), key=lambda p: _stamp(p / 'prefs.js'), reverse=True)


def _stamp(path):
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


_user_cache = {'stamp': None, 'values': {}}


def _user_prefs():
    """配置文件里的 user_pref 全名 -> 值（最近改动的那个配置目录）。"""
    for folder in _profile_dirs():
        path = folder / 'prefs.js'
        try:
            stat = path.stat()
        except OSError:
            continue
        stamp = (str(path), stat.st_mtime_ns, stat.st_size)
        if _user_cache['stamp'] == stamp:
            return _user_cache['values']
        try:
            text = path.read_text(encoding='utf-8')
        except OSError:
            continue
        values = {}
        for line in text.splitlines():
            match = re.fullmatch(r'user_pref\("([^"\n]+)", (.*)\);', line)
            if not match:
                continue
            try:
                values[match[1]] = json.loads(match[2])
            except ValueError:
                continue
        if values:
            _user_cache.update(stamp=stamp, values=values)
            return values
    return {}


def _prefs():
    """插件配置：只保留 ZoteroPDFTranslate 那一支，键是去掉前缀的名字。"""
    prefix = PREFIX
    return {name[len(prefix):]: value for name, value in _user_prefs().items()
            if name.startswith(prefix)}


_defaults_cache = {'stamp': None, 'values': {}}


def _pref_defaults():
    """插件自带的默认值。缓存键会把这些算进去，所以必须一起读。"""
    for folder in _profile_dirs():
        xpi = folder / 'extensions' / 'zoteropdftranslate@euclpts.com.xpi'
        try:
            stat = xpi.stat()
        except OSError:
            continue
        stamp = (str(xpi), stat.st_mtime_ns, stat.st_size)
        if _defaults_cache['stamp'] == stamp:
            return _defaults_cache['values']
        values = {}
        try:
            with zipfile.ZipFile(xpi) as archive:
                text = archive.read('prefs.js').decode('utf-8')
        except (KeyError, OSError, zipfile.BadZipFile):
            continue
        for line in text.splitlines():
            match = re.match(r'pref\("([^"]+)",\s*(.*)\);\s*$', line.strip())
            if not match:
                continue
            try:
                values[match[1]] = json.loads(match[2].strip())
            except ValueError:
                continue
        _defaults_cache.update(stamp=stamp, values=values)
        return values
    return _defaults_cache['values']


def _effective_prefs():
    """Zotero.Prefs.get 的等价物：默认值打底，用户设置覆盖。"""
    values = dict(_pref_defaults())
    values.update(_user_prefs())
    return values


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None      # 丢弃 NaN


def _object(value):
    try:
        parsed = json.loads(value or '{}')
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


# ------------------------------------------------------------- provider ----

def normalize_endpoint(end_point, api_format='openai'):
    """照插件的 normalizeLLMEndpoint：只补路径，不猜别的。"""
    raw = (end_point or '').strip()
    if not raw or '?' in raw:
        return raw
    base = raw.rstrip('/')
    path = re.sub(r'^[a-z][a-z0-9+.-]*://[^/]*', '', base, flags=re.I)
    resource = '/messages' if api_format == 'anthropic' else '/chat/completions'
    if not path:
        return base + '/v1' + resource
    last = path.split('/')[-1]
    if last == 'openai' and api_format != 'anthropic':
        return base + resource
    if last == 'anthropic' and api_format == 'anthropic':
        return base + '/v1' + resource
    if re.fullmatch(r'v\d+[a-z0-9]*', last, flags=re.I):
        return base + resource
    return raw


def dialect(url):
    try:
        hostname = (urlsplit(url).hostname or '').lower()
    except ValueError:
        return None
    for name, hosts in DIALECT_HOSTS:
        if any(hostname == host or hostname.endswith('.' + host) for host in hosts):
            return name
    return None


def _openai_effort(model, level):
    if level != 'off':
        return level
    match = re.match(r'^gpt-5(?:\.(\d+))?', model or '', flags=re.I)
    if match and not (match.group(1) and int(match.group(1)) >= 1):
        return 'minimal'
    return 'none'


def thinking_params(url, model, level):
    """思考档位 -> 请求体参数；与插件 buildThinkingParams 同规则。"""
    if level not in THINKING_LEVELS or level == 'default':
        return {}
    mode = dialect(url)
    if mode == 'openai':
        return {'reasoning_effort': _openai_effort(model, level)}
    if mode == 'openrouter':
        return {'reasoning': {'enabled': False} if level == 'off'
                else {'effort': level}}
    if mode == 'deepseek':
        if level == 'off':
            return {'thinking': {'type': 'disabled'}}
        return {'thinking': {'type': 'enabled'},
                'reasoning_effort': {'low': 'low', 'medium': 'high',
                                     'high': 'max'}[level]}
    if mode == 'dashscope':
        if level == 'off':
            return {'enable_thinking': False}
        return {'enable_thinking': True,
                'thinking_budget': {'low': 2048, 'medium': 8192,
                                    'high': 16384}[level]}
    if mode == 'thinkingType':
        return {'thinking': {'type': 'disabled' if level == 'off' else 'enabled'}}
    return {}


def custom_params(prefs, prefix, extra_keys=()):
    """插件里手填的额外请求参数（例如 max_tokens），保留但不能覆盖关键字段。"""
    standard = ('model', 'messages', 'input', 'temperature', 'stream') + tuple(extra_keys)
    return {key: value for key, value in _object(prefs.get(prefix + '.customParams')).items()
            if key not in standard}


def render_instruction(prompt, lang_from, lang_to):
    """把 ${langFrom}/${langTo} 换成语言代码，并摘掉 ${sourceText}（原文单独发）。"""
    marker = next((token for token in (SOURCE_TEXT, '{sourceText}') if token in prompt), None)
    if marker:
        before, _, after = prompt.partition(marker)
        instruction = (before.rstrip() + ' ' + after.lstrip()).strip()
    else:
        instruction = prompt.strip()
    for token, value in (('${langFrom}', lang_from), ('${langTo}', lang_to),
                         ('{langFrom}', lang_from), ('{langTo}', lang_to)):
        instruction = instruction.replace(token, value)
    return instruction


def _languages(prefs, target):
    """目标语言与原文语言都用插件里的语言代码，跟桌面对齐。"""
    wanted = {'zh': 'zh-CN', 'en': 'en-US'}.get(target)
    if not wanted:
        return None, None
    major = wanted.split('-')[0]
    configured = str(prefs.get('targetLanguage') or '')
    lang_to = configured if configured.split('-')[0] == major else wanted
    source = str(prefs.get('sourceLanguage') or 'en-US')
    if source.split('-')[0] == lang_to.split('-')[0]:
        source = 'en-US' if major == 'zh' else 'zh-CN'
    return source, lang_to


def configuration(target='zh'):
    prefs = _prefs()
    service = str(prefs.get('translateSource') or '')
    prefix = SERVICES.get(service)
    if not prefix:
        raise WorkbenchError('网页端当前支持插件的自定义 GPT / ChatGPT 服务，请在 Zotero 翻译设置中配置。',
                             status=503, code='translation_not_configured')
    try:
        secret = json.loads(prefs.get('secretObj') or '{}').get(service, '')
    except ValueError:
        secret = ''
    api_format = str(prefs.get(prefix + '.apiFormat') or 'openai')
    endpoint = normalize_endpoint(prefs.get(prefix + '.endPoint', ''), api_format)
    model = str(prefs.get(prefix + '.model') or '')
    if not secret or not endpoint or not model:
        raise WorkbenchError('Zotero 翻译插件的服务地址、模型或密钥尚未配置完整。',
                             status=503, code='translation_not_configured')
    parts = urlsplit(endpoint)
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        raise WorkbenchError('翻译服务地址无效。', status=503, code='translation_not_configured')
    source, lang_to = _languages(prefs, target)
    if not lang_to:
        raise WorkbenchError('目标语言只支持中文或英文。', status=400, code='invalid_input')
    return Profile(endpoint=endpoint,
                   model=model,
                   secret=secret,
                   prompt=str(prefs.get(prefix + '.prompt') or DEFAULT_PROMPT),
                   temperature=_number(prefs.get(prefix + '.temperature')),
                   thinking=str(prefs.get(prefix + '.thinkingLevel') or 'default'),
                   custom=custom_params(prefs, prefix),
                   target=lang_to,
                   source=source,
                   api_format=api_format,
                   context=bool(prefs.get('attachPaperContext')),
                   service=service,
                   strip=bool(prefs.get('stripEmptyLines')),
                   result_regex=str(prefs.get('resultRegex') or ''))


def status():
    try:
        configured = configuration()
        return {'configured': True, 'provider': 'Translate for Zotero',
                'model': configured.model}
    except WorkbenchError as exc:
        return {'configured': False, 'error': exc.message}


# --------------------------------------------------------- shared cache ----
# 缓存按“位置”命中：键是同一份配置 + 目标语言 + PDF 位置（页号 + 选区矩形），
# 与该位置的原文无关，所以重新框选同一处（空白/连字符抖动）依然命中。
# 请求位置被已缓存范围覆盖时也算命中，命中多条时取覆盖范围最小的那条，
# 于是先译大段、再译其中小段会复用到更贴切的那条。强制重译覆盖同位置的旧条目。
_cache = OrderedDict()
_shared = {'stamp': None, 'entries': {}}
CACHE_LIMIT = 128
TOLERANCE = 2.0
_catalog = OrderedDict()


def _remember(key, value):
    _cache[key] = value
    _cache.move_to_end(key)
    while len(_cache) > CACHE_LIMIT:
        _cache.popitem(last=False)


def _rectangles(value):
    rects = []
    for rect in value or []:
        try:
            x1, y1, x2, y2 = (round(float(number), 1) for number in rect)
        except (TypeError, ValueError):
            continue
        rects.append((min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)))
    return tuple(rects)


def _location(body):
    """请求在 PDF 里的位置：((页号, rects), ...)；手输文本或整页翻译没有位置时为空。"""
    position = body.get('position') or {}
    page = position.get('pageIndex')
    rects = _rectangles(position.get('rects'))
    if not rects or not isinstance(page, int) or isinstance(page, bool):
        return ()
    pages = [(page, rects)]
    next_rects = _rectangles(position.get('nextPageRects'))
    if next_rects:
        pages.append((page + 1, next_rects))
    return tuple(pages)


def _covers(outer, inner):
    for page, rects in inner:
        covering = [rect for index, page_rects in outer if index == page for rect in page_rects]
        for x1, y1, x2, y2 in rects:
            if not any(ox1 - TOLERANCE <= x1 and oy1 - TOLERANCE <= y1
                       and x2 <= ox2 + TOLERANCE and y2 <= oy2 + TOLERANCE
                       for ox1, oy1, ox2, oy2 in covering):
                return False
    return True


def _area(location):
    return sum((x2 - x1) * (y2 - y1) for _, rects in location for x1, y1, x2, y2 in rects)


def _find(location, prefix):
    best = None
    for stored, value in _cache.items():
        if stored[:-1] != prefix or not _covers(stored[-1], location):
            continue
        if best is None or _area(stored[-1]) < _area(best[0][-1]):
            best = (stored, value)
    return best


def _data_dir():
    """Zotero 数据目录：优先 prefs 里的 dataDir，否则用户目录下的 Zotero。"""
    values = _user_prefs()
    configured = str(values.get('extensions.zotero.dataDir') or '')
    if values.get('extensions.zotero.useDataDir') and configured:
        return Path(configured)
    for folder in _profile_dirs():
        return folder
    return Path(os.environ.get('USERPROFILE') or Path.home()) / 'Zotero'


def cache_file():
    return _data_dir() / CACHE_FILE


def _normalize(text):
    """与插件的 normalize 一致：NFKC + 空白折叠 + 去首尾。"""
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', str(text or ''))).strip()


def _load_shared():
    """读插件那份缓存；文件没变就复用上次解析结果。"""
    path = cache_file()
    try:
        stat = path.stat()
    except OSError:
        return {}
    stamp = (str(path), stat.st_mtime_ns, stat.st_size)
    if _shared['stamp'] == stamp:
        return _shared['entries']
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return _shared['entries']
    entries = {}
    for pair in (data.get('entries') if isinstance(data, dict) else None) or []:
        if (isinstance(pair, list) and len(pair) == 2 and isinstance(pair[0], str)
                and isinstance(pair[1], dict)):
            entries[pair[0]] = pair[1]
    _shared.update(stamp=stamp, entries=entries)
    return entries


def _save_shared(entries):
    path = cache_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps({'version': 1, 'entries': [[key, value] for key, value in entries.items()]},
                      ensure_ascii=False)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(body, encoding='utf-8')
    os.replace(temporary, path)
    stat = path.stat()
    _shared.update(stamp=(str(path), stat.st_mtime_ns, stat.st_size), entries=entries)


def _settings_pairs(service):
    """插件缓存指纹的输入：该服务下的全部 prefs + 三个固定项。"""
    fixed = ('attachPaperContext', 'resultRegex', 'stripEmptyLines')
    values = _effective_prefs()
    pairs = []
    for name in sorted(values):
        if not name.startswith(PREFIX):
            continue
        rest = name[len(PREFIX):]
        if rest.split('.')[0].lower() == service.lower() or rest in fixed:
            pairs.append([name, values[name]])
    return pairs


def settings_digest(service, paper=None, cleanup='raw-v1', extra=''):
    """与插件 localTranslationCache.key 同算法的设置指纹。"""
    pairs = _settings_pairs(service)
    if paper:
        pairs = pairs + [['paper', paper[0], paper[1]]]
    pairs = pairs + [['selectionCleanup', cleanup], ['extraPrompt', extra]]
    blob = json.dumps(pairs, ensure_ascii=False, separators=(',', ':'))
    return hashlib.md5(blob.encode('utf-8')).hexdigest()


def cache_key(text, service, lang_from, lang_to, digest):
    return json.dumps([_normalize(text), service, lang_from, lang_to, digest],
                      ensure_ascii=False, separators=(',', ':'))


def cache_lookup(text, service, lang_from, lang_to, paper, extra):
    """先查指纹命中，再按原文相等兜底，最后按“缓存原文包含所选片段”兜底。

    指纹里含论文标题/摘要，网页端拿不到条目时也算不出桌面那份指纹，
    所以多两级退路；退路只比服务、语言对和规范化原文，不会串语言。
    """
    entries = _load_shared()
    if not entries:
        return None
    normalized = _normalize(text)
    if not normalized:
        return None
    for cleanup in CLEANUP_VERSIONS:
        key = cache_key(normalized, service, lang_from, lang_to,
                        settings_digest(service, paper, cleanup, extra))
        entry = entries.get(key)
        result = entry.get('result') if isinstance(entry, dict) else None
        if isinstance(result, str) and result.strip():
            return result
    same_text = None
    contained = None
    for key, entry in entries.items():
        try:
            parts = json.loads(key)
        except ValueError:
            continue
        if not isinstance(parts, list) or len(parts) != 5:
            continue
        if parts[1:4] != [service, lang_from, lang_to]:
            continue
        result = entry.get('result')
        if not isinstance(result, str) or not result.strip():
            continue
        raw = _normalize(entry.get('raw') or parts[0])
        if not raw or normalized not in raw:
            continue
        if raw == normalized:
            saved = str(entry.get('savedAt') or '')
            if same_text is None or saved > same_text[0]:
                same_text = (saved, result)
        elif contained is None or len(raw) < len(contained[0]):
            contained = (raw, result)
    if same_text:
        return same_text[1]
    if contained:
        return '【已读取包含所选片段的缓存译文】\n\n原文：%s\n译文：%s' % contained
    return None


def cache_store(text, service, lang_from, lang_to, paper, extra, result, document=None):
    """写回插件那份缓存，桌面端重启后（或插件重载缓存后）能直接命中。"""
    entries = dict(_load_shared())
    entries[cache_key(text, service, lang_from, lang_to,
                      settings_digest(service, paper, 'raw-v1', extra))] = {
        'result': result, 'audio': [], 'raw': text, 'document': document,
        'savedAt': datetime.now(timezone.utc).isoformat(timespec='milliseconds')
                           .replace('+00:00', 'Z'),
    }
    try:
        _save_shared(entries)
    except OSError:
        pass                                  # 缓存写不进去不该让翻译失败


# ------------------------------------------------------------- 论文上下文 ----

def _item_json(item_key):
    if not re.fullmatch(r'[A-Z0-9]{8}', item_key or ''):
        raise ValueError('无效的 Zotero 条目编号')
    if item_key in _catalog:
        _catalog.move_to_end(item_key)
        return _catalog[item_key]
    opener = build_opener(ProxyHandler({}))
    with opener.open(LOCAL_API + item_key, timeout=5) as response:
        item = json.loads(response.read().decode('utf-8'))
    if not isinstance(item, dict):
        raise ValueError('无效的 Zotero 条目')
    _catalog[item_key] = item
    while len(_catalog) > CACHE_LIMIT:
        _catalog.popitem(last=False)
    return item


def _item_data(item_key):
    return _item_json(item_key).get('data', {}) or {}


def _paper_fields(item_key):
    """顶层论文的标题 + 摘要（插件 attachPaperContext 用的那一对）。"""
    data = _item_data(item_key)
    parent = data.get('parentItem')
    if parent:
        data = _item_data(parent)
    return str(data.get('title') or ''), str(data.get('abstractNote') or '')


def paper_context(item_key):
    """插件 attachPaperContext 的同一段上下文：顶层论文的标题 + 摘要。"""
    if not item_key:
        return ''
    title, abstract = _paper_fields(item_key)
    context = ''
    if title:
        context = 'Paper Title: ' + title
        if abstract:
            context += '\n\nPaper Abstract: ' + abstract
    elif abstract:
        context = 'Paper Abstract: ' + abstract
    if context:
        context = '\n\nContext from the academic paper:\n' + context
    return context


def _document(item_key):
    """缓存里的 document：[库 id, 附件 key, 文件大小, 修改时间]，与插件 capture 同形。

    插件用的是 Zotero 内部 libraryID（“我的文库”=1，群组库另算）。本地 API 给的是
    API 用户 id（如 22100132），不能直接用；群组库映射不到内部 id 时宁可不写。
    """
    try:
        item = _item_json(item_key)
        data = item.get('data', {}) or {}
        if data.get('itemType') != 'attachment':
            return None
        library = item.get('library') or {}
        if library.get('type') != 'user':
            return None
        key = str(item.get('key') or item_key)
        opener = build_opener(ProxyHandler({}))
        with opener.open(LOCAL_API + key + '/file/view/url', timeout=5) as response:
            url = response.read().decode('utf-8').strip()
        parts = urlsplit(url)
        if parts.scheme != 'file':
            return None
        path = Path(url2pathname(parts.path))
        stat = path.stat()
        return json.dumps([1, key, stat.st_size, int(stat.st_mtime * 1000)],
                          separators=(',', ':'))
    except Exception:
        return None


# -------------------------------------------------------------- 请求/响应 ----

def request_body(profile, instruction, text):
    """与插件 gptTranslate 的请求体同形（system 指令 + user 原文）。"""
    if profile.api_format == 'anthropic':
        body = {'model': profile.model, 'max_tokens': 4000, 'system': instruction,
                'messages': [{'role': 'user', 'content': text}], 'stream': False}
        if profile.temperature is not None:
            body['temperature'] = profile.temperature
        body.update(profile.custom)
        return body
    body = {'model': profile.model,
            'messages': [{'role': 'system', 'content': instruction},
                         {'role': 'user', 'content': text}],
            'stream': False}
    if profile.temperature is not None:
        body['temperature'] = profile.temperature
    thinking = thinking_params(profile.endpoint, profile.model, profile.thinking)
    body.update(thinking)
    body.update({key: value for key, value in profile.custom.items()
                 if key not in thinking})
    return body


def _headers(profile):
    if profile.api_format == 'anthropic':
        return {'Content-Type': 'application/json',
                'anthropic-version': '2023-06-01',
                'x-api-key': profile.secret}
    return {'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + profile.secret,
            'api-key': profile.secret}


def _parse(profile, result):
    if profile.api_format == 'anthropic':
        blocks = result.get('content') or []
        return ''.join(block.get('text') or '' for block in blocks
                       if isinstance(block, dict))
    return result['choices'][0]['message']['content']


def _strip_thinking(text):
    return re.sub(r'<think>[\s\S]*?(?:</think>|$)', '', text, flags=re.I)


def _postprocess(profile, result):
    """插件在写缓存前做的收尾：<think> 标签、空行压缩、结果正则。"""
    result = _strip_thinking(result)
    if profile.strip:
        text = result.replace('\r\n', '\n').replace('\r', '\n')
        result = re.sub(r'\n+', ' ', re.sub(r'^\n+', '', text))
    if profile.result_regex:
        try:
            result = re.sub(profile.result_regex, '', result)
        except re.error:
            pass
    return result


def _translate(profile, instruction, text):
    payload = json.dumps(request_body(profile, instruction, text)).encode('utf-8')
    request = Request(profile.endpoint, data=payload, headers=_headers(profile))
    try:
        with urlopen(request, timeout=120) as response:
            result = json.load(response)
        translation = _parse(profile, result)
        if not isinstance(translation, str) or not translation.strip():
            raise ValueError('empty translation')
        return translation, result.get('usage', {})
    except Exception as exc:
        # Never expose provider bodies, request headers, or credential values.
        raise WorkbenchError('翻译服务暂时不可用，请检查 Zotero 插件的服务配置后重试。',
                             status=502, code='translation_failed') from exc


def translate(body):
    text = str(body.get('text') or '').strip()
    target = body.get('target', 'zh')
    if not text or len(text) > 12000 or target not in ('zh', 'en'):
        raise ValueError('请选择或粘贴 1–12000 字符，目标语言支持中文或英文。')
    profile = configuration(target)
    extra = str(body.get('extraPrompt') or '').strip()
    item_key = str(body.get('itemKey') or '').strip()
    paper = None
    document = None
    if item_key:
        try:
            paper = _paper_fields(item_key)
            document = _document(item_key)
        except Exception:
            paper, document = None, None
    location = _location(body)
    prefix = (profile.endpoint, profile.model, profile.secret, profile.prompt,
              profile.target, profile.source, profile.temperature, profile.thinking,
              json.dumps(profile.custom, sort_keys=True), extra, item_key)

    shared = None
    if not body.get('force'):
        shared = cache_lookup(text, profile.service, profile.source, profile.target,
                              paper if profile.context else None, extra)
        if shared:
            if location:
                _remember(prefix + (location,), (shared, {}))
            return {'translation': shared, 'model': profile.model, 'cached': True,
                    'usage': {}, 'source': 'zotero-cache'}

    hit = None if body.get('force') or not location else _find(location, prefix)
    if hit:
        _cache.move_to_end(hit[0])
        translation, usage = hit[1]
        return {'translation': translation, 'model': profile.model, 'cached': True,
                'usage': usage, 'source': 'session-cache'}

    instruction = render_instruction(profile.prompt, profile.source, profile.target)
    if profile.context and item_key:
        try:
            instruction += paper_context(item_key)
        except Exception:
            pass
    if extra:
        instruction += '\n\nAdditional instruction for this translation only: ' + extra

    translation, usage = _translate(profile, instruction, text)
    translation = _postprocess(profile, translation)
    if location:
        _remember(prefix + (location,), (translation, usage))
    cache_store(text, profile.service, profile.source, profile.target,
                paper if profile.context else None, extra, translation, document)
    return {'translation': translation, 'model': profile.model, 'cached': False,
            'usage': usage, 'source': 'provider'}
