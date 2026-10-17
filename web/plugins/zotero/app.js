/* Zotero 插件逻辑：资料库分页、条目详情、翻译与 PDF 阅读器。 */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const PAGE = 25, LIMIT = 6000, READER = '/zotero-reader/reader.html';
  const L = $('libraryView'), DV = $('detailView'), RV = $('readerView'), BB = $('backBtn');
  const CL = $('collectionList'), CE = $('collectionError'), CET = $('collectionErrorText'), RC = $('retryCollections');
  const RAIL = $('rail'), RAIL_HEAD = $('railHead'), RAIL_CURRENT = $('collectionCurrent');
  const SI = $('searchInput');
  const LE = $('listError'), LET = $('listErrorText'), RL = $('retryList'), LK = $('listSkeleton');
  const IL = $('itemList'), ES = $('emptyState'), ET = $('emptyTitle'), EX = $('emptyText');
  const LW = $('loadMoreWrap'), LM = $('loadMore');
  const DS = $('detailSkeleton'), DE = $('detailError'), DET = $('detailErrorText'), RD = $('retryDetail');
  const DC = $('detailContent'), DB = $('detailBack'), DT = $('detailTitle'), DA = $('detailAuthors');
  const DP = $('detailPublication'), DD = $('detailDate'), DOI = $('detailDoi'), DTags = $('detailTags');
  const DAb = $('detailAbstract'), PL = $('pdfList');
  const CARD = $('selCard'), CARD_META = $('selMeta'), CARD_TEXT = $('selText');
  const CARD_SRC = $('selSource'), CARD_CLOSE = $('selClose'), CARD_MORE = $('selToggle');
  const RH = $('readerHost');

  let view = 'library', colls = [], coll = '', q = '', items = [], total = 0, next = 0;
  let seq = 0, labort = null, loading = false, scroll = 0, searchTimer = 0;
  let key = null, pdfs = [], dseq = 0, dabort = null, cabort = null;
  let frame = null;

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

  /* 目录与条目列表
     Zotero 的分类本来就是多级目录（这台机器上近 60 个、最深 5 级），铺成一排
     按钮一次全摊开，找起来比翻目录还累。这里按目录来：根是「全部条目」，点
     三角展开下一级，点名字选中这一级；默认只画顶层，所以一屏就那么几行。 */
  const NARROW = () => matchMedia('(max-width:720px)').matches;
  let expanded = new Set(['']), railOpen = false, railTouched = false;

  // 树本身不缓存：59 个目录重建一次比维护一份增量结构省心，也省得父节点变了
  // 还要修补。
  function buildTree() {
    const root = { key: '', name: '全部条目', parent: '', children: [] };
    const nodes = new Map();
    colls.forEach((item) => nodes.set(item.key, {
      key: item.key, name: str(item.name) || item.key,
      parent: str(item.parent) || '', children: []
    }));
    colls.forEach((item) => {
      const node = nodes.get(item.key);
      const parent = node.parent ? nodes.get(node.parent) : null;
      if (parent) parent.children.push(node);
      else root.children.push(node);
    });
    return root;
  }
  function ancestorsOf(target) {
    const out = [];
    let node = colls.find((item) => item.key === target);
    while (node && str(node.parent)) {
      out.push(node.parent);
      node = colls.find((item) => item.key === node.parent);
    }
    return out;
  }
  function pathOf(target) {
    const names = [];
    let node = colls.find((item) => item.key === target);
    while (node) {
      names.unshift(str(node.name) || node.key);
      const parent = str(node.parent);
      node = parent ? colls.find((item) => item.key === parent) : null;
    }
    return names.length ? names.join(' / ') : '全部条目';
  }
  function railRow(node, depth) {
    const row = mk('div', 'rail-row');
    row.style.setProperty('--depth', String(depth));
    const open = expanded.has(node.key);
    if (node.children.length) {
      const caret = mk('button', 'rail-caret', open ? '▾' : '▸');
      caret.type = 'button';
      caret.dataset.act = 'toggle';
      caret.dataset.k = node.key;
      caret.setAttribute('aria-expanded', String(open));
      caret.setAttribute('aria-label', (open ? '收起 ' : '展开 ') + node.name);
      row.append(caret);
    } else {
      const spacer = mk('span', 'rail-caret rail-caret-empty');
      spacer.setAttribute('aria-hidden', 'true');
      row.append(spacer);
    }
    const name = mk('button', 'rail-name', node.name);
    name.type = 'button';
    name.dataset.act = 'pick';
    name.dataset.k = node.key;
    name.title = node.name;
    if (node.key === coll) name.setAttribute('aria-current', 'true');
    row.append(name);
    return row;
  }
  function railBranch(node, depth, frag) {
    frag.append(railRow(node, depth));
    if (!expanded.has(node.key)) return;
    node.children.forEach((child) => railBranch(child, depth + 1, frag));
  }
  function drawCollections() {
    const frag = document.createDocumentFragment();
    railBranch(buildTree(), 0, frag);
    CL.replaceChildren(frag);
    const path = pathOf(coll);
    RAIL_CURRENT.textContent = path;
    RAIL_HEAD.title = '当前目录：' + path;
  }
  // 收起时整块目录只剩一行「当前目录」，条目列表紧跟在下面。
  function setRailOpen(open) {
    railOpen = open;
    RAIL.classList.toggle('is-closed', !open);
    RAIL_HEAD.setAttribute('aria-expanded', String(open));
  }
  // 默认开合看宽度：宽屏常驻，窄屏先收起。插件常常是在隐藏的 iframe 里开始
  // 载入的（宽度 0），那一刻量出来的结论不可信，所以等它有宽度、以及视口跨过
  // 断点时再定；用户自己点过之后就听用户的。
  function syncRailDefault() {
    if (railTouched || !RAIL.clientWidth) return;
    setRailOpen(!NARROW());
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
      LK.hidden = true;
      IL.setAttribute('aria-busy', 'false');
      LW.hidden = !(page.length > 0 && next < total);
      if (!items.length) showEmpty();
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
        ES.hidden = true;
      }
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
    const value = raw.trim().replace(/^https?:\/\/(dx\.)?doi\.org\//i, '');
    return value ? 'https://doi.org/' + encodeURI(value) : null;
  }
  function drawDetail(data) {
    DT.textContent = str(data.title) || '无标题';
    DA.textContent = str(data.authors) || '未知作者';
    DP.textContent = str(data.publication) || '—';
    DD.textContent = str(data.date) || '—';
    DOI.replaceChildren();
    const doi = str(data.doi).trim();
    const href = doiUrl(doi);
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
    } catch (error) {
      if (error.name === 'AbortError' || current !== dseq) return;
      DS.hidden = true;
      DC.hidden = true;
      DE.hidden = false;
      DET.textContent = friendly(error);
    } finally {
      if (dabort === controller) dabort = null;
    }
  }
  function openItem(itemKey) {
    if (!itemKey) return;
    scroll = L.scrollTop;
    key = itemKey;
    selClose();
    setView('detail');
    focus(BB);
    loadDetail();
  }
  function backToList() {
    if (dabort) dabort.abort();
    dabort = null;
    dseq += 1;
    selClose();
    setView('library');
    const active = key;
    requestAnimationFrame(() => {
      L.scrollTop = scroll;
      const row = Array.from(IL.querySelectorAll('.item-row')).find((x) => x.dataset.key === active);
      if (row) focus(row);
    });
  }

  /* 划词翻译：在阅读器里选中文字 → 弹层多出一个「翻译」→ 译文落在底部卡片。
     不再有手动输入框；唯一入口就是选区。 */
  let selText = '', selExpanded = false, selAbort = null, popupWin = null;

  function selDraw(isError) {
    const chars = Array.from(selText);
    CARD_TEXT.textContent = selExpanded ? selText : chars.slice(0, LIMIT).join('');
    CARD_TEXT.classList.toggle('is-error', !!isError);
    CARD_MORE.hidden = !!isError || chars.length <= LIMIT;
    CARD_MORE.textContent = selExpanded ? '收起' : '展开全部';
  }
  function selClose() {
    if (selAbort) selAbort.abort();
    selAbort = null;
    CARD.hidden = true;
  }
  async function translateSelection(text, source) {
    const value = str(text).trim();
    if (!value) return;
    if (selAbort) selAbort.abort();
    const controller = new AbortController();
    selAbort = controller;
    CARD.hidden = false;
    CARD_SRC.textContent = source || '';
    CARD_META.textContent = '翻译中…';
    selText = '正在翻译…';
    selExpanded = false;
    selDraw(false);
    try {
      const data = await read(await request('/api/zotero/translate', {
        method: 'POST',
        headers: { Accept: 'application/json', 'Content-Type': 'application/json', 'X-Workbench': '1' },
        // 中文选区译成英文，其余译成中文。itemKey 让服务端跟 Zotero 桌面插件
        // 用同一条缓存记录（缓存指纹里有论文标题/摘要）。
        body: JSON.stringify({ text: value.slice(0, 12000), itemKey: key,
          target: /[\u4e00-\u9fff]/.test(value) ? 'en' : 'zh', extraPrompt: '' }),
        signal: controller.signal
      }, 90000));
      if (selAbort !== controller) return;
      if (typeof data.translation !== 'string') throw new APIError('翻译接口没有返回译文，请稍后重试。', 0, 'bad');
      selText = data.translation;
      selExpanded = false;
      CARD_META.textContent = [str(data.model), data.cached === true ? '缓存命中' : ''].filter(Boolean).join(' · ');
      selDraw(false);
    } catch (error) {
      if (error.name === 'AbortError' || selAbort !== controller) return;
      selText = friendly(error);
      CARD_META.textContent = '';
      selDraw(true);
    } finally {
      if (selAbort === controller) selAbort = null;
    }
  }
  // 阅读器是官方构建，只暴露一个 customEvent 钩子：勾住渲染选区弹层的时机，
  // 把自己造的按钮塞进去。文字来自 params.annotation.text。
  function bindSelectionPopup(win, doc) {
    if (!win || popupWin === win) return;
    popupWin = win;
    win.addEventListener('customEvent', (event) => {
      const detail = event && event.detail;
      if (!detail || detail.type !== 'renderTextSelectionPopup') return;
      const annotation = (detail.params && detail.params.annotation) || {};
      const selected = str(annotation.text);
      if (!selected || typeof detail.append !== 'function') return;
      const button = doc.createElement('button');
      button.type = 'button';
      button.textContent = '翻译';
      button.setAttribute('data-workbench', 'translate');
      button.style.cssText = 'margin:4px 6px;padding:4px 10px;border-radius:6px;'
        + 'border:1px solid rgba(127,127,127,.45);background:transparent;color:inherit;'
        + 'font:inherit;cursor:pointer';
      button.addEventListener('click', () => translateSelection(selected, '选中文字'));
      detail.append(button);
    });
  }

  /* PDF 阅读器
     官方 reader.html 只是一个空壳：必须由父页面拿到同源的 contentWindow，
     调 createReader() 把 pdf 地址和中文语言包传进去，否则整页空白。 */
  let readerIndex = 0;
  let readerNote = null;

  function readerStatus(text) {
    if (!readerNote) {
      readerNote = mk('p', 'reader-notice');
      readerNote.setAttribute('role', 'status');
      RH.append(readerNote);
    }
    readerNote.textContent = text || '';
    readerNote.hidden = !text;
  }

  async function injectReader(file) {
    const win = frame && frame.contentWindow;
    const doc = frame && frame.contentDocument;
    if (!win || !doc) return;
    try {
      readerStatus('正在加载阅读器…');
      if (typeof win.createReader !== 'function') throw new Error('官方阅读器未就绪，请重试。');
      doc.documentElement.lang = 'zh-CN';
      bindSelectionPopup(win, doc);
      const ftl = await Promise.all(['zotero', 'reader', 'brand'].map(async (name) => {
        const response = await fetch('/zotero-reader/locales/zh-CN/' + name + '.ftl');
        if (!response.ok) throw new Error('中文界面资源加载失败。');
        return response.text();
      }));
      if (!frame || !frame.isConnected || !frame.dataset.key || frame.dataset.key !== file.key) return;
      const native = win.createReader({
        type: 'pdf',
        data: { url: new URL(file.url, location.origin).href },
        ftl: ftl,
        title: str(file.title),
        annotations: [],
        readOnly: false,
        loggedIn: false,
        authorName: '网页阅读',
        sidebarOpen: frame.clientWidth >= 900,
        sidebarView: 'outline',
        sidebarWidth: 240,
        colorScheme: matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light',
        onChangeViewState: () => {},
        onSaveAnnotations: () => {},
        onDeleteAnnotations: () => {},
        onOpenLink: (url) => {
          if (/^https?:/i.test(str(url))) window.open(url, '_blank', 'noopener,noreferrer');
        },
        onConfirm: (_title, text) => window.confirm(text),
        onSetDataTransferAnnotations: () => {},
        onToggleSidebar: () => {},
        onChangeSidebarWidth: () => {},
        onChangeSidebarView: () => {},
        onTextSelectionAnnotationModeChange: () => {},
        onSaveCustomThemes: () => {},
        onSetLightTheme: () => {},
        onSetDarkTheme: () => {}
      });
      const initialized = await native._primaryView.initializedPromise;
      if (initialized === false) throw new Error('PDF 打开失败，请确认附件仍可访问。');
      readerStatus('');
    } catch (error) {
      readerStatus((error && error.message) || '阅读器加载失败');
    }
  }

  function mountReader(file) {
    if (!frame) {
      frame = document.createElement('iframe');
      frame.className = 'reader-frame';
      frame.title = 'PDF 阅读器';
      RH.append(frame);
    }
    frame.dataset.key = file.key;
    readerStatus('');
    frame.onload = () => { void injectReader(file); };
    frame.src = READER;
  }

  function openReader(index) {
    const file = pdfs[Number.isInteger(index) ? index : readerIndex];
    if (!file) return;
    readerIndex = pdfs.indexOf(file);
    setView('reader');
    mountReader(file);
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
    const button = event.target.closest('button[data-act]');
    if (!button) return;
    const value = button.dataset.k || '';
    if (button.dataset.act === 'toggle') {
      if (expanded.has(value)) expanded.delete(value); else expanded.add(value);
      drawCollections();
      return;
    }
    if (value === coll) {
      if (NARROW()) setRailOpen(false);
      return;
    }
    coll = value;
    q = SI.value.trim();
    expanded.add('');
    ancestorsOf(coll).forEach((k) => expanded.add(k));
    drawCollections();
    // 手机上选完目录就把目录收起来，条目列表立刻占满屏幕。
    if (NARROW()) {
      railTouched = true;
      setRailOpen(false);
    }
    clearTimeout(searchTimer);
    loadItems(true);
  });
  RAIL_HEAD.addEventListener('click', () => {
    railTouched = true;
    setRailOpen(!railOpen);
  });
  window.addEventListener('resize', syncRailDefault);
  // iframe 从 display:none 变可见时不一定有 resize 事件，直接盯着根元素尺寸。
  if (typeof ResizeObserver === 'function') {
    new ResizeObserver(() => syncRailDefault()).observe(document.documentElement);
  }
  SI.addEventListener('input', () => {
    q = SI.value.trim();
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
  RL.addEventListener('click', () => loadItems(items.length === 0));
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
  PL.addEventListener('click', (event) => {
    const button = event.target.closest('.pdf-button');
    if (button && pdfs[Number(button.dataset.pdf)]) openReader(Number(button.dataset.pdf));
  });
  CARD_CLOSE.addEventListener('click', selClose);
  CARD_MORE.addEventListener('click', () => {
    selExpanded = !selExpanded;
    selDraw(false);
  });

  DT.setAttribute('tabindex', '-1');
  setView('library');
  // 宽屏目录常驻在左边，窄屏先收起成一行「当前目录」（见 syncRailDefault）。
  syncRailDefault();
  loadCollections();
  loadItems(true);
})();
