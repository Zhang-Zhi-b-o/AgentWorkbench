/* Zotero 插件逻辑：资料库分页、条目详情、翻译与 PDF 阅读器。 */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const PAGE = 25, LIMIT = 6000, READER = '/zotero-reader/reader.html';
  const L = $('libraryView'), DV = $('detailView'), RV = $('readerView'), BB = $('backBtn');
  const SP = $('servicePill'), ST = $('serviceText');
  const CL = $('collectionList'), CE = $('collectionError'), CET = $('collectionErrorText'), RC = $('retryCollections');
  const SI = $('searchInput'), CS = $('clearSearch'), LS = $('listSummary'), AC = $('activeCollection');
  const LE = $('listError'), LET = $('listErrorText'), RL = $('retryList'), LK = $('listSkeleton');
  const IL = $('itemList'), ES = $('emptyState'), ET = $('emptyTitle'), EX = $('emptyText');
  const LW = $('loadMoreWrap'), LM = $('loadMore');
  const DS = $('detailSkeleton'), DE = $('detailError'), DET = $('detailErrorText'), RD = $('retryDetail');
  const DC = $('detailContent'), DB = $('detailBack'), DT = $('detailTitle'), DA = $('detailAuthors');
  const DP = $('detailPublication'), DD = $('detailDate'), DOI = $('detailDoi'), DTags = $('detailTags');
  const DAb = $('detailAbstract'), PL = $('pdfList');
  const TC = $('translationConfig'), RTC = $('retryTranslationConfig'), TH = $('translationHint');
  const TA = $('translateText'), CC = $('charCount'), TL = $('targetLang'), TB = $('translateBtn');
  const TE = $('translationError'), TET = $('translationErrorText'), RT = $('retryTranslation');
  const TO = $('translationOutput'), TM = $('translationModel'), CB = $('cacheBadge');
  const TX = $('translationText'), TT = $('toggleTranslation');
  const RH = $('readerHost'), RS = $('readerStatus'), RST = $('readerStatusText');

  let view = 'library', colls = [], coll = '', q = '', items = [], total = 0, next = 0;
  let seq = 0, labort = null, loading = false, scroll = 0, retryMode = 'reset', searchTimer = 0;
  let key = null, pdfs = [], dseq = 0, dabort = null, cabort = null;
  let configured = false, translating = false, tabort = null, tval = '', tmodel = '', tcached = false, texp = false;
  let cabort2 = null, frame = null, readerReady = false;

  const mk = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  };
  const str = (v) => typeof v === 'string' ? v : typeof v === 'number' ? String(v) : '';
  const focus = (n) => { if (n) { try { n.focus({ preventScroll: true }); } catch (_) { n.focus(); } } };

  class APIError extends Error {
    constructor(message, status, code) {
      super(message);
      this.name = 'APIError';
      this.status = status || 0;
      this.code = code || '';
    }
  }
  function friendly(error) {
    if (error instanceof APIError) return error.message;
    if (error && error.name === 'TypeError') return '网络连接失败，请检查网络后重试。';
    return error && error.message || '请求失败，请稍后重试。';
  }
  function setService(state, label) {
    SP.dataset.state = state;
    ST.textContent = label;
  }
  function setView(name) {
    view = name;
    L.hidden = name !== 'library';
    DV.hidden = name !== 'detail';
    RV.hidden = name !== 'reader';
    BB.hidden = name === 'library';
    BB.textContent = name === 'reader' ? '关闭阅读器' : '返回列表';
  }
  async function request(url, options, timeout) {
    const opts = options || {}, controller = new AbortController(), parent = opts.signal;
    let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; controller.abort(); }, timeout || 18000);
    const relay = () => controller.abort();
    if (parent) {
      if (parent.aborted) controller.abort();
      else parent.addEventListener('abort', relay, { once: true });
    }
    try {
      return await fetch(url, Object.assign({}, opts, { signal: controller.signal, cache: 'no-store' }));
    } catch (error) {
      if (timedOut) throw new APIError('请求超时，请稍后重试。', 0, 'timeout');
      throw error;
    } finally {
      clearTimeout(timer);
      if (parent) parent.removeEventListener('abort', relay);
    }
  }
  async function read(response) {
    let data = null;
    try { data = await response.json(); } catch (_) { /* 空响应 */ }
    if (!response.ok) {
      throw new APIError(
        data && typeof data.error === 'string' && data.error.trim() ? data.error : '请求失败（HTTP ' + response.status + '）。',
        response.status, data && data.code
      );
    }
    if (!data || typeof data !== 'object') throw new APIError('服务返回了无法识别的数据。', 0, 'bad_json');
    return data;
  }

  /* 分类与条目列表 */
  function updateActive() {
    const item = colls.find((entry) => entry.key === coll);
    AC.textContent = item ? str(item.name) || item.key : '全部条目';
  }
  function drawCollections() {
    const frag = document.createDocumentFragment();
    const all = mk('button', 'button', '全部条目');
    all.type = 'button';
    all.dataset.k = '';
    all.setAttribute('aria-pressed', String(coll === ''));
    frag.append(all);
    colls.forEach((item) => {
      const button = mk('button', 'button', str(item.name) || item.key);
      button.type = 'button';
      button.dataset.k = item.key;
      button.title = str(item.name) || item.key;
      button.setAttribute('aria-pressed', String(coll === item.key));
      frag.append(button);
    });
    CL.replaceChildren(frag);
    updateActive();
  }
  async function loadCollections() {
    if (cabort) cabort.abort();
    const controller = new AbortController();
    cabort = controller;
    CE.hidden = true;
    CL.setAttribute('aria-busy', 'true');
    try {
      const data = await read(await request('/api/zotero/collections', {
        headers: { Accept: 'application/json' }, signal: controller.signal
      }));
      if (cabort !== controller) return;
      colls = Array.isArray(data.collections) ? data.collections.filter((x) => x && typeof x.key === 'string') : [];
      if (coll && !colls.some((x) => x.key === coll)) coll = '';
      drawCollections();
    } catch (error) {
      if (error.name === 'AbortError' || cabort !== controller) return;
      colls = [];
      drawCollections();
      CE.hidden = false;
      CET.textContent = '分类服务不可用：' + friendly(error);
    } finally {
      if (cabort === controller) {
        CL.setAttribute('aria-busy', 'false');
        cabort = null;
      }
    }
  }
  function updateSummary() {
    LS.textContent = items.length ? '共 ' + total + ' 条 · 已显示 ' + items.length : '共 ' + total + ' 条';
  }
  function showEmpty() {
    ES.hidden = false;
    ET.textContent = q ? '没有匹配的条目' : '此分类还没有条目';
    EX.textContent = q ? '换一个关键词，或切换到全部条目。' : '选择其他分类，或稍后再试。';
  }
  function drawItems() {
    const frag = document.createDocumentFragment();
    items.forEach((item) => {
      const wrap = mk('div', 'item-entry');
      wrap.setAttribute('role', 'listitem');
      const row = mk('button', 'item-row');
      row.type = 'button';
      row.dataset.key = item.key;
      row.append(mk('span', 'item-title', str(item.title) || '无标题'));
      row.append(mk('span', 'item-authors', str(item.authors) || '未知作者'));
      const foot = mk('span', 'item-foot');
      if (str(item.date)) foot.append(mk('span', '', str(item.date)));
      if (str(item.publication)) foot.append(mk('span', 'item-publication', str(item.publication)));
      if (item.has_pdf === true) foot.append(mk('span', 'pdf-badge', 'PDF'));
      row.append(foot);
      wrap.append(row);
      frag.append(wrap);
    });
    IL.replaceChildren(frag);
  }
  async function loadItems(reset) {
    if (!reset && loading) return;
    clearTimeout(searchTimer);
    const current = ++seq;
    if (labort) labort.abort();
    const controller = new AbortController();
    labort = controller;
    loading = true;
    if (reset && SP.dataset.state !== 'online') setService('loading', '连接中');
    LE.hidden = true;
    ES.hidden = true;
    LW.hidden = true;
    IL.setAttribute('aria-busy', 'true');
    if (reset) {
      items = [];
      total = 0;
      next = 0;
      IL.replaceChildren();
      LK.hidden = false;
      LS.textContent = '正在读取条目…';
    } else {
      LM.disabled = true;
      LM.textContent = '加载中…';
    }
    const start = reset ? 0 : next;
    const params = new URLSearchParams();
    params.set('collection', coll);
    params.set('start', String(start));
    if (q) params.set('q', q);
    try {
      const data = await read(await request('/api/zotero/items?' + params.toString(), {
        headers: { Accept: 'application/json' }, signal: controller.signal
      }));
      if (current !== seq) return;
      const page = Array.isArray(data.items) ? data.items.filter((x) => x && typeof x.key === 'string') : [];
      const count = Number(data.total);
      total = Number.isFinite(count) ? Math.max(0, count) : items.length + page.length;
      items = reset ? page : items.concat(page);
      const returned = Number(data.start);
      next = (Number.isFinite(returned) ? returned : start) + PAGE;
      drawItems();
      updateSummary();
      LK.hidden = true;
      IL.setAttribute('aria-busy', 'false');
      LW.hidden = !(page.length > 0 && next < total);
      if (!items.length) showEmpty();
      setService('online', '已连接');
      retryMode = reset ? 'reset' : 'append';
    } catch (error) {
      if (error.name === 'AbortError' || current !== seq) return;
      LK.hidden = true;
      IL.setAttribute('aria-busy', 'false');
      LE.hidden = false;
      LET.textContent = friendly(error);
      LW.hidden = true;
      if (reset) {
        items = [];
        total = 0;
        IL.replaceChildren();
        LS.textContent = '读取失败';
        ES.hidden = true;
      }
      retryMode = reset ? 'reset' : 'append';
      setService('error', '服务不可用');
    } finally {
      if (current === seq) {
        loading = false;
        LM.disabled = false;
        LM.textContent = '加载更多';
      }
    }
  }

  /* 条目详情 */
  function doiUrl(raw) {
    let value = raw.trim();
    if (/^https?:\/\//i.test(value)) {
      try {
        const url = new URL(value);
        if (!/(^|\.)doi\.org$/i.test(url.hostname)) return null;
        value = decodeURIComponent(url.pathname.replace(/^\/+/, ''));
      } catch (_) { return null; }
    }
    if (!/^10\.\d{4,9}\/.+/i.test(value)) return null;
    return 'https://doi.org/' + value.split('/').map(encodeURIComponent).join('/');
  }
  function drawDetail(data) {
    DT.textContent = str(data.title) || '无标题';
    DA.textContent = str(data.authors) || '未知作者';
    DP.textContent = str(data.publication) || '—';
    DD.textContent = str(data.date) || '—';
    DOI.replaceChildren();
    const doi = str(data.doi).trim();
    const href = doi ? doiUrl(doi) : null;
    if (!doi) DOI.textContent = '—';
    else if (!href) DOI.textContent = doi;
    else {
      const link = mk('a', '', doi);
      link.href = href;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.referrerPolicy = 'no-referrer';
      DOI.append(link);
    }
    DTags.replaceChildren();
    (Array.isArray(data.tags) ? data.tags : []).forEach((tag) => {
      if (str(tag).trim()) DTags.append(mk('span', 'tag', str(tag)));
    });
    DAb.textContent = str(data.abstract) || '暂无摘要。';
    DAb.classList.toggle('muted', !str(data.abstract));
    PL.replaceChildren();
    pdfs = (Array.isArray(data.pdfs) ? data.pdfs : []).filter((pdf) => pdf && str(pdf.title));
    if (!pdfs.length) PL.append(mk('p', 'pdf-empty', '此条目暂无 PDF 附件。'));
    pdfs.forEach((pdf, index) => {
      const button = mk('button', 'pdf-button');
      button.type = 'button';
      button.dataset.pdf = String(index);
      button.append(mk('span', 'pdf-tag', 'PDF'), mk('span', 'pdf-title', str(pdf.title)));
      PL.append(button);
    });
    DC.hidden = false;
    DS.hidden = true;
    DE.hidden = true;
    focus(DT);
  }
  async function loadDetail() {
    if (!key) return;
    if (dabort) dabort.abort();
    const controller = new AbortController();
    dabort = controller;
    const current = ++dseq;
    DS.hidden = false;
    DC.hidden = true;
    DE.hidden = true;
    DV.scrollTop = 0;
    try {
      const data = await read(await request('/api/zotero/items/' + encodeURIComponent(key), {
        headers: { Accept: 'application/json' }, signal: controller.signal
      }));
      if (current !== dseq) return;
      drawDetail(data);
      setService('online', '已连接');
    } catch (error) {
      if (error.name === 'AbortError' || current !== dseq) return;
      DS.hidden = true;
      DC.hidden = true;
      DE.hidden = false;
      DET.textContent = friendly(error);
      setService('error', '服务不可用');
    } finally {
      if (dabort === controller) dabort = null;
    }
  }
  function openItem(itemKey) {
    if (!itemKey) return;
    scroll = L.scrollTop;
    key = itemKey;
    resetTranslation();
    setView('detail');
    focus(BB);
    loadDetail();
  }
  function backToList() {
    if (dabort) dabort.abort();
    dabort = null;
    dseq += 1;
    resetTranslation();
    setView('library');
    const active = key;
    requestAnimationFrame(() => {
      L.scrollTop = scroll;
      const row = Array.from(IL.querySelectorAll('.item-row')).find((x) => x.dataset.key === active);
      if (row) focus(row);
    });
  }

  /* 翻译 */
  function updateTranslateButton() {
    const value = TA.value;
    TB.disabled = !configured || translating || !value.trim() || value.length > 12000;
    TB.textContent = translating ? '翻译中…' : '翻译';
  }
  function updateCount() {
    CC.textContent = TA.value.length + ' / 12000';
    updateTranslateButton();
  }
  async function loadConfig() {
    if (cabort2) cabort2.abort();
    const controller = new AbortController();
    cabort2 = controller;
    configured = false;
    TC.textContent = '读取中';
    TC.dataset.state = '';
    TH.textContent = '正在检查翻译服务配置。';
    RTC.hidden = true;
    updateTranslateButton();
    try {
      const data = await read(await request('/api/zotero/translation', {
        headers: { Accept: 'application/json' }, signal: controller.signal
      }));
      if (cabort2 !== controller) return;
      if (data.configured === true) {
        configured = true;
        TC.textContent = '翻译就绪';
        TC.dataset.state = 'ready';
        const bits = [str(data.provider), str(data.model)].filter(Boolean);
        TH.textContent = bits.length ? '服务：' + bits.join(' · ') : '翻译服务已就绪。';
      } else {
        TC.textContent = '未配置';
        TC.dataset.state = 'error';
        TH.textContent = '服务端尚未配置翻译模型，暂时无法使用翻译。';
      }
    } catch (error) {
      if (error.name === 'AbortError' || cabort2 !== controller) return;
      TC.textContent = '不可用';
      TC.dataset.state = 'error';
      TH.textContent = '翻译服务不可用：' + friendly(error);
      RTC.hidden = false;
    } finally {
      if (cabort2 === controller) cabort2 = null;
      updateTranslateButton();
    }
  }
  function resetTranslation() {
    if (tabort) tabort.abort();
    tabort = null;
    translating = false;
    tval = '';
    tmodel = '';
    tcached = false;
    texp = false;
    TA.value = '';
    TE.hidden = true;
    TET.textContent = '';
    TO.hidden = true;
    TX.textContent = '';
    TM.textContent = '';
    CB.hidden = true;
    TT.hidden = true;
    TT.textContent = '展开全部';
    updateCount();
  }
  function renderTranslation() {
    const chars = Array.from(tval);
    TX.textContent = texp ? tval : chars.slice(0, LIMIT).join('');
    TM.textContent = tmodel ? '模型：' + tmodel : '模型：未提供';
    CB.hidden = !tcached;
    TT.hidden = chars.length <= LIMIT;
    TT.textContent = texp ? '收起译文' : '展开全部';
    TO.hidden = false;
  }
  function showTranslationError(message) {
    TE.hidden = false;
    TET.textContent = message;
  }
  async function translate() {
    if (translating) return;
    const value = TA.value;
    if (!configured) return showTranslationError('翻译服务尚未配置，暂时无法翻译。');
    if (!value.trim()) return showTranslationError('请先输入需要翻译的文本。');
    if (value.length > 12000) return showTranslationError('文本超过 12000 个字符，请删减后再试。');
    if (tabort) tabort.abort();
    const controller = new AbortController();
    tabort = controller;
    translating = true;
    TE.hidden = true;
    updateTranslateButton();
    try {
      const data = await read(await request('/api/zotero/translate', {
        method: 'POST',
        headers: { Accept: 'application/json', 'Content-Type': 'application/json', 'X-Workbench': '1' },
        body: JSON.stringify({ text: value, target: TL.value, extraPrompt: '' }),
        signal: controller.signal
      }, 90000));
      if (typeof data.translation !== 'string') throw new APIError('翻译接口没有返回译文，请稍后重试。', 0, 'bad');
      tval = data.translation;
      tmodel = str(data.model);
      tcached = data.cached === true;
      texp = false;
      renderTranslation();
    } catch (error) {
      if (error.name !== 'AbortError') showTranslationError(friendly(error));
    } finally {
      if (tabort === controller) {
        tabort = null;
        translating = false;
        updateTranslateButton();
      }
    }
  }

  /* PDF 阅读器 */
  function mountReader() {
    frame = document.createElement('iframe');
    frame.className = 'reader-frame';
    frame.title = 'PDF 阅读器';
    frame.src = READER;
    frame.hidden = true;
    frame.addEventListener('load', () => {
      readerReady = true;
      frame.hidden = false;
      RS.hidden = true;
    });
    frame.addEventListener('error', () => {
      const spinner = RS.querySelector('.spinner');
      if (spinner) spinner.hidden = true;
      RST.textContent = '阅读器载入失败，请返回详情重试。';
    });
    RH.append(frame);
  }
  function openReader() {
    setView('reader');
    if (!frame) mountReader();
    if (readerReady) {
      RS.hidden = true;
      frame.hidden = false;
    } else {
      RS.hidden = false;
      frame.hidden = true;
    }
    focus(BB);
  }
  function closeReader() {
    if (view !== 'reader') return;
    setView('detail');
    focus(DT);
  }
  function goBack() {
    if (view === 'reader') closeReader();
    else if (view === 'detail') backToList();
  }

  /* 事件 */
  CL.addEventListener('click', (event) => {
    const button = event.target.closest('button[data-k]');
    if (!button) return;
    const value = button.dataset.k || '';
    if (value === coll) return;
    coll = value;
    q = SI.value.trim();
    CL.querySelectorAll('button[data-k]').forEach((item) => {
      item.setAttribute('aria-pressed', String((item.dataset.k || '') === coll));
    });
    updateActive();
    clearTimeout(searchTimer);
    loadItems(true);
  });
  SI.addEventListener('input', () => {
    q = SI.value.trim();
    CS.hidden = !SI.value;
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => loadItems(true), 320);
  });
  SI.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      clearTimeout(searchTimer);
      q = SI.value.trim();
      loadItems(true);
    }
  });
  CS.addEventListener('click', () => {
    SI.value = '';
    q = '';
    CS.hidden = true;
    clearTimeout(searchTimer);
    loadItems(true);
    focus(SI);
  });
  RL.addEventListener('click', () => loadItems(retryMode !== 'append'));
  LM.addEventListener('click', () => loadItems(false));
  IL.setAttribute('role', 'list');
  IL.addEventListener('click', (event) => {
    const row = event.target.closest('.item-row');
    if (row) openItem(row.dataset.key);
  });
  IL.addEventListener('keydown', (event) => {
    const rows = Array.from(IL.querySelectorAll('.item-row'));
    const index = rows.indexOf(document.activeElement);
    if (index < 0) return;
    let target = -1;
    if (event.key === 'ArrowDown') target = Math.min(rows.length - 1, index + 1);
    else if (event.key === 'ArrowUp') target = Math.max(0, index - 1);
    else if (event.key === 'Home') target = 0;
    else if (event.key === 'End') target = rows.length - 1;
    if (target >= 0) {
      event.preventDefault();
      rows[target].focus();
    }
  });
  BB.addEventListener('click', goBack);
  DB.addEventListener('click', backToList);
  RD.addEventListener('click', loadDetail);
  RC.addEventListener('click', loadCollections);
  RTC.addEventListener('click', loadConfig);
  PL.addEventListener('click', (event) => {
    const button = event.target.closest('.pdf-button');
    if (button && pdfs[Number(button.dataset.pdf)]) openReader();
  });
  TA.addEventListener('input', updateCount);
  TB.addEventListener('click', translate);
  RT.addEventListener('click', translate);
  TT.addEventListener('click', () => {
    texp = !texp;
    renderTranslation();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && view === 'reader') {
      event.preventDefault();
      closeReader();
    }
  });

  DT.setAttribute('tabindex', '-1');
  setView('library');
  setService('loading', '连接中');
  loadCollections();
  loadConfig();
  loadItems(true);
})();
