"""网页端翻译：请求跟 Zotero 插件同形，缓存跟插件同一份文件。"""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from plugins.zotero import translate
from plugins.zotero.translate import Profile, PREFIX


def profile(**overrides):
    base = dict(endpoint='https://api.deepseek.com/v1/chat/completions',
                model='deepseek-flash', secret='private-key',
                prompt='你是学术论文翻译专家，把 ${langFrom} 的论文内容翻译成 ${langTo}。\n\n'
                       '只输出译文本身。\n\n待翻译内容：\n${sourceText}',
                temperature=0.3, thinking='off', custom={}, target='zh-CN',
                source='en-US', api_format='openai', context=False,
                service='customgpt1', strip=False, result_regex='')
    base.update(overrides)
    return Profile(**base)


def reply(text='译文'):
    payload = {'choices': [{'message': {'content': text}}], 'usage': {'total_tokens': 9}}
    return io.BytesIO(json.dumps(payload).encode('utf-8'))


class Isolated(unittest.TestCase):
    """每个用例一个临时 Zotero 数据目录，别碰真实缓存文件。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.stash = [
            patch.object(translate, '_data_dir', return_value=self.dir),
            patch.object(translate, '_effective_prefs', return_value={
                PREFIX + 'attachPaperContext': True,
                PREFIX + 'resultRegex': '',
                PREFIX + 'stripEmptyLines': False,
                PREFIX + 'customGPT1.prompt': 'p',
                PREFIX + 'customGPT1.temperature': '0.3',
            }),
        ]
        for item in self.stash:
            item.start()
        translate._cache.clear()
        translate._catalog.clear()
        translate._shared.update(stamp=None, entries={})

    def tearDown(self):
        for item in self.stash:
            item.stop()
        translate._cache.clear()
        translate._catalog.clear()
        translate._shared.update(stamp=None, entries={})
        self.tmp.cleanup()

    def seed(self, entries):
        path = self.dir / translate.CACHE_FILE
        path.write_text(json.dumps({'version': 1, 'entries': entries}, ensure_ascii=False),
                        encoding='utf-8')
        translate._shared.update(stamp=None, entries={})
        return path


class PluginParityTests(Isolated):
    def test_prompt_placeholders_are_the_plugin_ones(self):
        # 插件模板用 ${langFrom} / ${langTo}，只认单花括号会把 "$" 漏进提示词。
        instruction = translate.render_instruction(profile().prompt, 'en-US', 'zh-CN')
        self.assertIn('把 en-US 的论文内容翻译成 zh-CN', instruction)
        self.assertNotIn('$', instruction)
        self.assertNotIn('{', instruction)

    def test_request_body_matches_plugin_shape(self):
        instruction = translate.render_instruction(profile().prompt, 'en-US', 'zh-CN')
        body = translate.request_body(profile(), instruction, 'the paper text')
        self.assertEqual(body['messages'][0], {'role': 'system', 'content': instruction})
        self.assertEqual(body['messages'][1], {'role': 'user', 'content': 'the paper text'})
        self.assertEqual(body['temperature'], 0.3)
        self.assertEqual(body['thinking'], {'type': 'disabled'})
        self.assertFalse(body['stream'])
        self.assertNotIn('max_tokens', body)   # 插件不发，这里也不发

    def test_thinking_and_custom_params(self):
        idle = translate.request_body(profile(thinking='default'), 'x', 't')
        self.assertNotIn('thinking', idle)
        enabled = translate.request_body(profile(thinking='medium'), 'x', 't')
        self.assertEqual(enabled['thinking'], {'type': 'enabled'})
        self.assertEqual(enabled['reasoning_effort'], 'high')
        custom = translate.request_body(profile(custom={'max_tokens': 2048}), 'x', 't')
        self.assertEqual(custom['max_tokens'], 2048)

    def test_anthropic_format(self):
        body = translate.request_body(profile(api_format='anthropic'), 'x', 't')
        self.assertEqual(body['system'], 'x')
        self.assertEqual(body['messages'], [{'role': 'user', 'content': 't'}])
        self.assertEqual(body['max_tokens'], 4000)

    def test_endpoint_normalisation_matches_plugin(self):
        cases = {
            'https://api.deepseek.com': 'https://api.deepseek.com/v1/chat/completions',
            'https://api.deepseek.com/': 'https://api.deepseek.com/v1/chat/completions',
            'https://api.deepseek.com/v1': 'https://api.deepseek.com/v1/chat/completions',
            'https://api.openai.com/openai': 'https://api.openai.com/openai/chat/completions',
            'https://x.invalid/v1/chat/completions?x=1':
                'https://x.invalid/v1/chat/completions?x=1',
        }
        for raw, expected in cases.items():
            self.assertEqual(translate.normalize_endpoint(raw), expected, raw)

    def test_languages_follow_plugin_prefs(self):
        prefs = {'targetLanguage': 'zh-CN', 'sourceLanguage': 'en-US'}
        self.assertEqual(translate._languages(prefs, 'zh'), ('en-US', 'zh-CN'))
        # 译成英文时原文不能还是英文，否则等于同语言互译。
        self.assertEqual(translate._languages(prefs, 'en'), ('zh-CN', 'en-US'))
        fallback = {'targetLanguage': 'zh-CN', 'sourceLanguage': 'zh-CN'}
        self.assertEqual(translate._languages(fallback, 'zh'), ('en-US', 'zh-CN'))

    def test_configuration_reads_real_plugin_prefs(self):
        prefs = {'translateSource': 'customgpt1',
                 'secretObj': json.dumps({'customgpt1': 'sk-test'}),
                 'customGPT1.endPoint': 'https://api.deepseek.com',
                 'customGPT1.model': 'deepseek-flash',
                 'customGPT1.temperature': '0.3',
                 'customGPT1.thinkingLevel': 'off',
                 'customGPT1.apiFormat': 'openai',
                 'customGPT1.prompt': '把 ${langFrom} 翻成 ${langTo}：${sourceText}',
                 'targetLanguage': 'zh-CN',
                 'sourceLanguage': 'en-US',
                 'attachPaperContext': True}
        with patch.object(translate, '_prefs', return_value=prefs):
            configured = translate.configuration('zh')
            self.assertEqual(configured.endpoint,
                             'https://api.deepseek.com/v1/chat/completions')
            self.assertEqual(configured.model, 'deepseek-flash')
            self.assertAlmostEqual(configured.temperature, 0.3)
            self.assertEqual(configured.thinking, 'off')
            self.assertEqual(configured.service, 'customgpt1')
            self.assertTrue(configured.context)
            self.assertEqual((configured.source, configured.target), ('en-US', 'zh-CN'))
            self.assertEqual(translate.status()['model'], 'deepseek-flash')
        with patch.object(translate, '_prefs', return_value={}):
            self.assertFalse(translate.status()['configured'])

    def test_translate_sends_plugin_messages_and_keeps_key_secret(self):
        captured = {}
        def fake_urlopen(request, timeout=None):
            captured['body'] = json.loads(request.data.decode())
            return reply()
        with patch.object(translate, 'configuration', return_value=profile()):
            with patch.object(translate, 'urlopen', side_effect=fake_urlopen):
                result = translate.translate({'text': 'the paper text'})
        self.assertEqual(result['translation'], '译文')
        self.assertEqual(result['model'], 'deepseek-flash')
        self.assertEqual(result['source'], 'provider')
        self.assertNotIn('private-key', json.dumps(result, ensure_ascii=False))
        body = captured['body']
        self.assertEqual(body['messages'][1], {'role': 'user', 'content': 'the paper text'})
        self.assertIn('把 en-US 的论文内容翻译成 zh-CN', body['messages'][0]['content'])
        self.assertNotIn('$', body['messages'][0]['content'])
        self.assertEqual(body['temperature'], 0.3)
        self.assertEqual(body['thinking'], {'type': 'disabled'})

    def test_paper_context_matches_plugin_block(self):
        items = {'AAAA1111': {'data': {'parentItem': 'BBBB2222'}},
                 'BBBB2222': {'data': {'title': 'GraphRAG', 'abstractNote': 'A survey.'}}}
        with patch.object(translate, '_item_json',
                          side_effect=lambda key: items[key]):
            context = translate.paper_context('AAAA1111')
        self.assertEqual(context,
                         '\n\nContext from the academic paper:\n'
                         'Paper Title: GraphRAG\n\nPaper Abstract: A survey.')
        with self.assertRaises(ValueError):
            translate.paper_context('bad-key')

    def test_context_and_hash_only_when_enabled(self):
        captured = {}
        def fake_urlopen(request, timeout=None):
            captured['body'] = json.loads(request.data.decode())
            return reply()
        items = {'BBBB2222': {'data': {'title': 'GraphRAG', 'abstractNote': ''}}}
        with patch.object(translate, '_item_json', side_effect=lambda key: items[key]):
            with patch.object(translate, 'urlopen', side_effect=fake_urlopen):
                with patch.object(translate, 'configuration',
                                  return_value=profile(context=True)):
                    translate.translate({'text': 't', 'itemKey': 'BBBB2222'})
                self.assertIn('Context from the academic paper:\nPaper Title: GraphRAG',
                              captured['body']['messages'][0]['content'])
                with patch.object(translate, 'configuration',
                                  return_value=profile(context=False)):
                    translate.translate({'text': 't2', 'itemKey': 'BBBB2222'})
        self.assertNotIn('Paper Title', captured['body']['messages'][0]['content'])

    def test_lookup_failure_does_not_block_translation(self):
        with patch.object(translate, '_item_json', side_effect=OSError('zotero closed')):
            with patch.object(translate, 'urlopen', side_effect=lambda *a, **k: reply('ok')):
                with patch.object(translate, 'configuration',
                                  return_value=profile(context=True)):
                    result = translate.translate({'text': 't', 'itemKey': 'BBBB2222'})
        self.assertEqual(result['translation'], 'ok')


class SharedCacheTests(Isolated):
    def test_store_writes_plugin_shaped_entry_and_lookup_hits(self):
        with patch.object(translate, 'configuration', return_value=profile()):
            with patch.object(translate, 'urlopen', side_effect=lambda *a, **k: reply('译文甲')):
                first = translate.translate({'text': 'Hello  world.', 'target': 'zh'})
        self.assertFalse(first['cached'])
        path = self.dir / translate.CACHE_FILE
        data = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(data['version'], 1)
        self.assertEqual(len(data['entries']), 1)
        key, value = data['entries'][0]
        parts = json.loads(key)
        self.assertEqual(parts[0], 'Hello world.')          # NFKC + 空白折叠
        self.assertEqual(parts[1:4], ['customgpt1', 'en-US', 'zh-CN'])
        self.assertEqual(len(parts[4]), 32)                  # md5 指纹
        self.assertEqual(value['result'], '译文甲')
        self.assertEqual(value['raw'], 'Hello  world.')
        self.assertEqual(value['audio'], [])
        self.assertIn('savedAt', value)

        # 第二次（哪怕换了位置）直接命中同一份缓存，不再请求服务端。
        with patch.object(translate, 'configuration', return_value=profile()):
            with patch.object(translate, 'urlopen') as call:
                second = translate.translate({'text': 'Hello world.',
                                              'position': {'pageIndex': 1,
                                                           'rects': [[1, 2, 3, 4]]}})
        self.assertEqual(call.call_count, 0)
        self.assertEqual(second['translation'], '译文甲')
        self.assertEqual(second['source'], 'zotero-cache')

    def test_exact_and_contained_hits_from_desktop_entries(self):
        service, en, zh = 'customgpt1', 'en-US', 'zh-CN'
        digest = translate.settings_digest(service, None, 'raw-v1', '')
        long_text = 'Retrieval-augmented generation grounds a model. It adds latency.'
        self.seed([[translate.cache_key(long_text, service, en, zh, digest),
                    {'result': '缓存译文', 'raw': long_text, 'audio': []}]])
        self.assertEqual(translate.cache_lookup(long_text, service, en, zh, None, ''),
                         '缓存译文')
        fragment = translate.cache_lookup('It adds latency.', service, en, zh, None, '')
        self.assertTrue(fragment.startswith('【已读取包含所选片段的缓存译文】'))
        self.assertIn('缓存译文', fragment)
        # 语言对不一样就不该命中。
        self.assertIsNone(translate.cache_lookup(long_text, service, zh, en, None, ''))

    def test_same_text_without_item_key_still_hits_plainly(self):
        service, en, zh = 'customgpt1', 'en-US', 'zh-CN'
        # 桌面端指纹含论文标题/摘要；网页端拿不到条目时算不出它，也要能命中。
        digest = translate.settings_digest(service, ('GraphRAG', 'A survey.'), 'raw-v1', '')
        text = 'Dense retrieval encodes queries and documents.'
        self.seed([[translate.cache_key(text, service, en, zh, digest),
                    {'result': '稠密检索译文', 'raw': text, 'audio': []}]])
        self.assertEqual(translate.cache_lookup(text, service, en, zh, None, ''),
                         '稠密检索译文')
        self.assertNotIn('【', translate.cache_lookup(text, service, en, zh, None, ''))

    def test_body_v1_digest_also_hits(self):
        service, en, zh = 'customgpt1', 'en-US', 'zh-CN'
        digest = translate.settings_digest(service, None, 'body-v1', '')
        self.seed([[translate.cache_key('cleaned text', service, en, zh, digest),
                    {'result': '清理后的译文', 'raw': 'cleaned text', 'audio': []}]])
        self.assertEqual(translate.cache_lookup('cleaned text', service, en, zh, None, ''),
                         '清理后的译文')

    def test_force_bypasses_cache_and_rewrites(self):
        service, en, zh = 'customgpt1', 'en-US', 'zh-CN'
        digest = translate.settings_digest(service, None, 'raw-v1', '')
        self.seed([[translate.cache_key('same', service, en, zh, digest),
                    {'result': '旧译文', 'raw': 'same', 'audio': []}]])
        with patch.object(translate, 'configuration', return_value=profile()):
            with patch.object(translate, 'urlopen', side_effect=lambda *a, **k: reply('新译文')):
                result = translate.translate({'text': 'same', 'force': True})
        self.assertEqual(result['translation'], '新译文')
        self.assertFalse(result['cached'])
        self.assertEqual(translate.cache_lookup('same', service, en, zh, None, ''), '新译文')

    def test_session_position_cache_still_short_circuits_repeat_selection(self):
        wide = {'pageIndex': 2, 'rects': [[10, 20, 200, 80]]}
        narrow = {'pageIndex': 2, 'rects': [[12, 22, 90, 38]]}
        answers = iter(['wide', 'other', 'narrow', 'plain'])
        def fake_urlopen(*args, **kwargs):
            return reply(next(answers))
        with patch.object(translate, 'configuration', return_value=profile()):
            with patch.object(translate, 'urlopen', side_effect=fake_urlopen) as call:
                translate.translate({'text': 'a', 'position': wide})
                self.assertEqual(translate.translate({'text': 'b', 'position': wide})['translation'],
                                 'wide')
                self.assertEqual(call.call_count, 1)
                forced = translate.translate({'text': 'c', 'position': narrow, 'force': True})
                self.assertEqual(forced['translation'], 'other')
                self.assertEqual(translate.translate({'text': 'd', 'position': narrow})['translation'],
                                 'other')
                self.assertEqual(call.call_count, 2)
                self.assertEqual(translate.translate({'text': 'e'})['translation'], 'narrow')
                self.assertEqual(call.call_count, 3)

    def test_postprocess_applies_plugin_cleanup(self):
        with patch.object(translate, 'configuration',
                          return_value=profile(strip=True, result_regex=r'（.*?）')):
            with patch.object(translate, 'urlopen',
                              side_effect=lambda *a, **k: reply('开头\n\n<think>x</think>正文（注）\n尾')):
                result = translate.translate({'text': 't'})
        self.assertNotIn('<think>', result['translation'])
        self.assertNotIn('（注）', result['translation'])
        self.assertEqual(result['translation'], '开头 正文 尾')


class RealCacheTests(unittest.TestCase):
    """跟本机真实的桌面缓存对一次键：算法漂移会立刻暴露。"""

    @unittest.skipUnless(translate.cache_file().exists(), '本机没有桌面翻译缓存')
    def test_rebuilds_desktop_keys(self):
        entries = translate._load_shared()
        self.assertGreater(len(entries), 0)
        matched = 0
        for key, value in list(entries.items())[:60]:
            parts = json.loads(key)
            document = value.get('document')
            if len(parts) != 5 or not document:
                continue
            try:
                paper = translate._paper_fields(json.loads(document)[1])
            except Exception:
                continue
            digests = {translate.settings_digest(parts[1], paper, cleanup, '')
                       for cleanup in translate.CLEANUP_VERSIONS}
            if parts[4] in digests:
                matched += 1
        self.assertGreater(matched, 0, '算不出桌面端的设置指纹')


if __name__ == '__main__':
    unittest.main()
