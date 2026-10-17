"""Reuse Translate for Zotero's configured OpenAI-compatible service, server-side."""
import json
import os
import re
from collections import OrderedDict
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from workbench.errors import WorkbenchError

PREFIX = 'extensions.zotero.ZoteroPDFTranslate.'


def configuration():
    profiles = Path(os.environ.get('APPDATA', '')) / 'Zotero/Zotero/Profiles'
    prefs = {}
    for path in sorted(profiles.glob('*/prefs.js'), key=lambda p: p.stat().st_mtime, reverse=True):
        for line in path.read_text(encoding='utf-8').splitlines():
            match = re.fullmatch(r'user_pref\("([^"\n]+)", (.*)\);', line)
            if match and match[1].startswith(PREFIX):
                try:
                    prefs[match[1][len(PREFIX):]] = json.loads(match[2])
                except ValueError:
                    pass
        if prefs:
            break
    service = prefs.get('translateSource', '')
    prefix = 'customGPT' + service[-1] if service in ('customgpt1', 'customgpt2', 'customgpt3') else 'chatGPT'
    if service not in ('customgpt1', 'customgpt2', 'customgpt3', 'chatgpt'):
        raise WorkbenchError('网页端当前支持插件的自定义 GPT / ChatGPT 服务，请在 Zotero 翻译设置中配置。', status=503, code='translation_not_configured')
    try:
        secret = json.loads(prefs.get('secretObj', '{}')).get(service, '')
    except ValueError:
        secret = ''
    endpoint = prefs.get(prefix + '.endPoint', '').rstrip('/')
    model = prefs.get(prefix + '.model', '')
    if not secret or not endpoint or not model:
        raise WorkbenchError('Zotero 翻译插件的服务地址、模型或密钥尚未配置完整。', status=503, code='translation_not_configured')
    parts = urlsplit(endpoint)
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        raise WorkbenchError('翻译服务地址无效。', status=503, code='translation_not_configured')
    if not parts.path or parts.path == '/':
        endpoint += '/v1/chat/completions'
    elif not parts.path.endswith('/chat/completions'):
        endpoint += '/chat/completions'
    prompt = prefs.get(prefix + '.prompt') or 'Translate the following text from {langFrom} to {langTo}. Return only the translation.\n{sourceText}'
    return endpoint, model, secret, prompt


def status():
    try:
        _, model, _, _ = configuration()
        return {'configured': True, 'provider': 'Translate for Zotero', 'model': model}
    except WorkbenchError as exc:
        return {'configured': False, 'error': exc.message}


CACHE_LIMIT = 128
TOLERANCE = 2.0
# 缓存按“位置”命中：键是同一份配置 + 目标语言 + PDF 位置（页号 + 选区矩形），
# 与该位置的原文无关，所以重新框选同一处（空白/连字符抖动）依然命中。
# 请求位置被已缓存范围覆盖时也算命中，命中多条时取覆盖范围最小的那条，
# 于是先译大段、再译其中小段会复用到更贴切的那条。强制重译覆盖同位置的旧条目。
_cache = OrderedDict()


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


def _translate(endpoint, model, secret, prompt, text, target, extra=''):
    prompt = prompt.replace('{langFrom}', 'auto-detected language').replace('{langTo}', target)
    if extra:
        # 临时提示词只拼进本次请求，不写回 Zotero 插件的配置。
        prompt = extra + '\n' + prompt
    prompt = prompt.replace('{sourceText}', text) if '{sourceText}' in prompt else prompt + '\n' + text
    body = json.dumps({'model': model, 'messages': [{'role':'user', 'content':prompt}],
                       'stream':False, 'max_tokens':8192}).encode('utf-8')
    request = Request(endpoint, data=body, headers={'Content-Type':'application/json', 'Authorization':'Bearer '+secret})
    try:
        with urlopen(request, timeout=60) as response:
            result = json.load(response)
        translation = result['choices'][0]['message']['content']
        if not isinstance(translation, str) or not translation.strip():
            raise ValueError('empty translation')
        return translation, result.get('usage', {})
    except Exception as exc:
        # Never expose provider bodies, request headers, or credential values.
        raise WorkbenchError('翻译服务暂时不可用，请检查 Zotero 插件的服务配置后重试。', status=502, code='translation_failed') from exc


def translate(body):
    text = str(body.get('text') or '').strip()
    target = {'zh':'Simplified Chinese', 'en':'English'}.get(body.get('target', 'zh'))
    if not text or len(text) > 12000 or not target:
        raise ValueError('请选择或粘贴 1–12000 字符，目标语言支持中文或英文。')
    # 临时提示词只作用于当次请求；它进缓存键，所以带与不带提示词的结果不会互相命中。
    extra = str(body.get('extraPrompt') or '').strip()
    endpoint, model, secret, prompt = configuration()
    location = _location(body)
    key = (endpoint, model, secret, prompt, target, extra, location) if location else None
    hit = None if not key or body.get('force') else _find(location, key[:-1])
    if hit:
        _cache.move_to_end(hit[0])
        translation, usage = hit[1]
        cached = True
    else:
        translation, usage = _translate(endpoint, model, secret, prompt, text, target, extra)
        cached = False
        if key:
            _remember(key, (translation, usage))
    return {'translation': translation, 'model':model, 'cached':cached, 'usage':usage}
