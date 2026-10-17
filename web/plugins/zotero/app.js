/* Zotero 插件逻辑：资料库分页、条目详情、翻译与 PDF 阅读器。 */
(() => {
  'use strict';

  const PAGE_SIZE = 25;
  const TRANSLATION_RENDER_LIMIT = 6000;
  const READER_SRC = '/zotero-reader/reader.html';
  const TYPE_NAMES = {
    journalArticle: '期刊论文', book: '图书', bookSection: '图书章节',
    conferencePaper: '会议论文', thesis: '学位论文', preprint: '预印本',
    report: '报告', webpage: '网页', manuscript: '手稿'
  };

  const dom = {};
  [
    'libraryView', 'detailView', 'readerView', 'backBtn', 'servicePill', 'serviceText',
    'collectionList', 'collectionError', 'collectionErrorText', 'retryCollections',
    'searchInput', 'clearSearch', 'listSummary', 'activeCollection', 'listError',
    'listErrorText', 'retryList', 'listSkeleton', 'itemList', 'emptyState', 'emptyTitle',
    'emptyText', 'loadMoreWrap', 'loadMore', 'detailSkeleton', 'detailError',
    'detailErrorText', 'retryDetail', 'detailContent', 'detailBack', 'detailType',
    'detailTitle', 'detailAuthors', 'detailPublication', 'detailDate', 'detailDoi',
    'detailTags', 'detailAbstract', 'pdfList', 'translationConfig',
    'retryTranslationConfig', 'translationHint', 'translateText', 'charCount',
    'targetLang', 'translateBtn', 'translationError', 'translationErrorText',
    'retryTranslation', 'translationOutput', 'translationModel', 'cacheBadge',
    'translationText', 'toggleTranslation', 'readerHost', 'readerStatus', 'readerStatusText'
  ].forEach((id) => { dom[id] = document.getElementById(id); });

  const state = {
    view: 'library',
    service: 'loading',
    collections: [],
    selectedCollection: '',
    query: '',
    items: [],
    total: 0,
    nextStart: 0,
    listSeq: 0,
    listAbort: null,
    listLoading: false,
    libraryScroll: 0,
    activeKey: null,
    activePdfs: [],
    detailSeq: 0,
    detailAbort: null,
    collectionsAbort: null,
    configAbort: null,
    translationConfigured: false,
    translating: false,
    translateAbort: null,
    translationValue: '',
    translationModelName: '',
    translationCached: false,
    translationExpanded: false,
    readerFrame: null,
    readerLoaded: false
  };
  let searchTimer = 0;
  let listRetryMode = 'reset';

  class ApiError extends Error {
    constructor(message, status, code) {
      super(message);
      this.name = 'ApiError';
      this.status = status || 0;
      this.code = code || '';
    }
  }

  function make(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function asText(value) {
    if (typeof value === 'string') return value;
    if (typeof value === 'number') return String(value);
    return '';
  }

  function friendlyError(error) {
    if (error instanceof ApiError) return error.message;
    if (error && error.name === 'TypeError') return '网络连接失败，请检查网络后重试。';
    if (error && error.message) return error.message;
    return '请求失败，请稍后重试。';
  }

  function setService(mode, label) {
    state.service = mode;
    dom.servicePill.dataset.state = mode;
    dom.serviceText.textContent = label;
  }

  function setView(view) {
    state.view = view;
    dom.libraryView.hidden = view !== 'library';
    dom.detailView.hidden = view !== 'detail';
    dom.readerView.hidden = view !== 'reader';
    dom.backBtn.hidden = view === 'library';
    dom.backBtn.textContent = view === 'reader' ? '关闭阅读器' : '返回列表';
  }

  function focusNode(node) {
    if (!node) return;
    try { node.focus({ preventScroll: true }); } catch (_) { node.focus(); }
  }

  function apiFetch(url, options, timeoutMs) {
    const opts = options || {};
    const timeout = timeoutMs || 18000;
    const controller = new AbortController();
    const parent = opts.signal;
    let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; controller.abort(); }, timeout);
    const relay = () => controller.abort();
    if (parent) {
      if (parent.aborted) controller.abort();
      else parent.addEventListener('abort', relay, { once: true });
    }
    return fetch(url, Object.assign({}, opts, { signal: controller.signal, cache: 'no-store' }))
      .catch((error) => {
        if (timedOut) throw new ApiError('请求超时，请稍后重试。', 0, 'timeout');
        throw error;
      })
      .finally(() => {
        clearTimeout(timer);
        if (parent) parent.removeEventListener('abort', relay);
      });
  }

  async function readPayload(response) {
    let data = null;
    try { data = await response.json(); } catch (_) { /* 空响应也按失败处理 */ }
    if (!response.ok) {
      const message = data && typeof data.error === 'string' && data.error.trim()
        ? data.error
        : '请求失败（HTTP ' + response.status + '）。';
      throw new ApiError(message, response.status, data && data.code ? String(data.code) : '');
    }
    if (!data || typeof data !== 'object') throw new ApiError('服务返回了无法识别的数据。', 0, 'bad_json');
    return data;
  }

  /* ---------- 分类 ---------- */
  function updateActiveCollection() {
    const found = state.collections.find((item) => item.key === state.selectedCollection);
    dom.activeCollection.textContent = found ? asText(found.name) || found.key : '全部条目';
  }

  function renderCollections() {
    const frag = document.createDocumentFragment();
    const all = make('button', 'collection-button', '全部条目');
    all.type = 'button';
    all.dataset.collection = '';
    all.setAttribute('aria-pressed', String(state.selectedCollection === ''));
    frag.appendChild(all);
    state.collections.forEach((collection) => {
      const button = make('button', 'collection-button', asText(collection.name) || collection.key);
      button.type = 'button';
      button.dataset.collection = collection.key;
      button.title = asText(collection.name) || collection.key;
      button.setAttribute('aria-pressed', String(state.selectedCollection === collection.key));
      frag.appendChild(button);
    });
    dom.collectionList.replaceChildren(frag);
    updateActiveCollection();
  }

  async function loadCollections() {
    if (state.collectionsAbort) state.collectionsAbort.abort();
    const controller = new AbortController();
    state.collectionsAbort = controller;
    dom.collectionError.hidden = true;
    dom.collectionList.setAttribute('aria-busy', 'true');
    try {
      const response = await apiFetch('/api/zotero/collections', {
        headers: { Accept: 'application/json' }, signal: controller.signal
      });
      const data = await readPayload(response);
      if (state.collectionsAbort !== controller) return;
      state.collections = Array.isArray(data.collections)
        ? data.collections.filter((item) => item && typeof item.key === 'string')
        : [];
      if (state.selectedCollection && !state.collections.some((item) => item.key === state.selectedCollection)) {
        state.selectedCollection = '';
      }
      renderCollections();
    } catch (error) {
      if (error.name === 'AbortError' || state.collectionsAbort !== controller) return;
      state.collections = [];
      renderCollections();
      dom.collectionError.hidden = false;
      dom.collectionErrorText.textContent = '分类服务不可用：' + friendlyError(error);
    } finally {
      if (state.collectionsAbort === controller) {
        dom.collectionList.setAttribute('aria-busy', 'false');
        state.collectionsAbort = null;
      }
    }
  }

  /* ---------- 条目列表 ---------- */
  function updateListSummary() {
    dom.listSummary.textContent = state.items.length
      ? '共 ' + state.total + ' 条 · 已显示 ' + state.items.length
      : '共 ' + state.total + ' 条';
  }

  function showEmptyState() {
    dom.emptyState.hidden = false;
    if (state.query) {
      dom.emptyTitle.textContent = '没有匹配的条目';
      dom.emptyText.textContent = '换一个关键词，或切换到全部条目。';
    } else {
      dom.emptyTitle.textContent = '此分类还没有条目';
      dom.emptyText.textContent = '选择其他分类，或稍后再试。';
    }
  }

  function renderItems() {
    const frag = document.createDocumentFragment();
    state.items.forEach((item) => {
      const wrap = make('div', 'item-entry');
      wrap.setAttribute('role', 'listitem');
      const button = make('button', 'item-row');
      button.type = 'button';
      button.dataset.key = item.key;
      button.appendChild(make('span', 'item-title', asText(item.title) || '无标题'));
      button.appendChild(make('span', 'item-authors', asText(item.authors) || '未知作者'));
      const foot = make('span', 'item-foot');
      const date = asText(item.date);
      const publication = asText(item.publication);
      if (date) foot.appendChild(make('span', '', date));
      if (publication) foot.appendChild(make('span', 'item-publication', publication));
      if (item.has_pdf === true) foot.appendChild(make('span', 'pdf-badge', 'PDF'));
      button.appendChild(foot);
      wrap.appendChild(button);
      frag.appendChild(wrap);
    });
    dom.itemList.replaceChildren(frag);
  }

  async function loadItems(reset) {
    if (!reset && state.listLoading) return;
    clearTimeout(searchTimer);
    const seq = ++state.listSeq;
    if (state.listAbort) state.listAbort.abort();
    const controller = new AbortController();
    state.listAbort = controller;
    state.listLoading = true;

    if (reset && state.service !== 'online') setService('loading', '连接中');
    dom.listError.hidden = true;
    dom.emptyState.hidden = true;
    dom.loadMoreWrap.hidden = true;
    dom.itemList.setAttribute('aria-busy', 'true');
    if (reset) {
      state.items = [];
      state.total = 0;
      state.nextStart = 0;
      dom.itemList.replaceChildren();
      dom.listSkeleton.hidden = false;
      dom.listSummary.textContent = '正在读取条目…';
    } else {
      dom.loadMore.disabled = true;
      dom.loadMore.textContent = '加载中…';
    }

    const requestedStart = reset ? 0 : state.nextStart;
    const params = new URLSearchParams();
    params.set('collection', state.selectedCollection);
    params.set('start', String(requestedStart));
    if (state.query) params.set('q', state.query);

    try {
      const response = await apiFetch('/api/zotero/items?' + params.toString(), {
        headers: { Accept: 'application/json' }, signal: controller.signal
      });
      const data = await readPayload(response);
      if (seq !== state.listSeq) return;
      const pageItems = Array.isArray(data.items)
        ? data.items.filter((item) => item && typeof item.key === 'string')
        : [];
      const total = Number.isFinite(Number(data.total))
        ? Math.max(0, Number(data.total))
        : state.items.length + pageItems.length;
      state.items = reset ? pageItems : state.items.concat(pageItems);
      state.total = total;
      const returnedStart = Number(data.start);
      state.nextStart = (Number.isFinite(returnedStart) ? returnedStart : requestedStart) + PAGE_SIZE;
      renderItems();
      updateListSummary();
      dom.listSkeleton.hidden = true;
      dom.itemList.setAttribute('aria-busy', 'false');
      dom.loadMoreWrap.hidden = !(pageItems.length > 0 && state.nextStart < state.total);
      if (!state.items.length) showEmptyState();
      setService('online', '已连接');
      listRetryMode = reset ? 'reset' : 'append';
    } catch (error) {
      if (error.name === 'AbortError' || seq !== state.listSeq) return;
      dom.listSkeleton.hidden = true;
      dom.itemList.setAttribute('aria-busy', 'false');
      dom.listError.hidden = false;
      dom.listErrorText.textContent = friendlyError(error);
      dom.loadMoreWrap.hidden = true;
      if (reset) {
        state.items = [];
        state.total = 0;
        dom.itemList.replaceChildren();
        dom.listSummary.textContent = '读取失败';
        dom.emptyState.hidden = true;
      }
      listRetryMode = reset ? 'reset' : 'append';
      setService('error', '服务不可用');
    } finally {
      if (seq === state.listSeq) {
        state.listLoading = false;
        dom.loadMore.disabled = false;
        dom.loadMore.textContent = '加载更多';
      }
    }
  }

  /* ---------- 详情 ---------- */
  function doiHref(raw) {
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

  function renderDoi(raw) {
    dom.detailDoi.replaceChildren();
    const text = asText(raw).trim();
    if (!text) { dom.detailDoi.textContent = '—'; return; }
    const href = doiHref(text);
    if (!href) { dom.detailDoi.textContent = text; return; }
    const link = make('a', '', text);
    link.href = href;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.referrerPolicy = 'no-referrer';
    dom.detailDoi.appendChild(link);
  }

  function renderTags(tags) {
    dom.detailTags.replaceChildren();
    (Array.isArray(tags) ? tags : []).forEach((tag) => {
      const value = asText(tag).trim();
      if (value) dom.detailTags.appendChild(make('span', 'tag', value));
    });
  }

  function renderPdfs(pdfs) {
    dom.pdfList.replaceChildren();
    state.activePdfs = (Array.isArray(pdfs) ? pdfs : []).filter((pdf) => pdf && asText(pdf.title));
    if (!state.activePdfs.length) {
      dom.pdfList.appendChild(make('p', 'pdf-empty', '此条目暂无 PDF 附件。'));
      return;
    }
    state.activePdfs.forEach((pdf, index) => {
      const button = make('button', 'pdf-button');
      button.type = 'button';
      button.dataset.pdfIndex = String(index);
      button.appendChild(make('span', 'pdf-button-tag', 'PDF'));
      button.appendChild(make('span', 'pdf-button-title', asText(pdf.title) || 'PDF 附件'));
      dom.pdfList.appendChild(button);
    });
  }

  function renderDetail(item) {
    dom.detailTitle.textContent = asText(item.title) || '无标题';
    dom.detailAuthors.textContent = asText(item.authors) || '未知作者';
    dom.detailPublication.textContent = asText(item.publication) || '—';
    dom.detailDate.textContent = asText(item.date) || '—';
    renderDoi(item.doi);
    renderTags(item.tags);
    dom.detailAbstract.textContent = asText(item.abstract) || '暂无摘要。';
    dom.detailAbstract.classList.toggle('muted', !asText(item.abstract));
    const type = asText(item.type);
    dom.detailType.hidden = !type;
    dom.detailType.textContent = TYPE_NAMES[type] || type;
    renderPdfs(item.pdfs);
    dom.detailContent.hidden = false;
    dom.detailSkeleton.hidden = true;
    dom.detailError.hidden = true;
    focusNode(dom.detailTitle);
  }

  async function loadDetail(key) {
    if (state.detailAbort) state.detailAbort.abort();
    const controller = new AbortController();
    state.detailAbort = controller;
    const seq = ++state.detailSeq;
    dom.detailSkeleton.hidden = false;
    dom.detailContent.hidden = true;
    dom.detailError.hidden = true;
    dom.detailView.scrollTop = 0;
    try {
      const response = await apiFetch('/api/zotero/items/' + encodeURIComponent(key), {
        headers: { Accept: 'application/json' }, signal: controller.signal
      });
      const data = await readPayload(response);
      if (seq !== state.detailSeq) return;
      renderDetail(data);
      setService('online', '已连接');
    } catch (error) {
      if (error.name === 'AbortError' || seq !== state.detailSeq) return;
      dom.detailSkeleton.hidden = true;
      dom.detailContent.hidden = true;
      dom.detailError.hidden = false;
      dom.detailErrorText.textContent = friendlyError(error);
      setService('error', '服务不可用');
    } finally {
      if (state.detailAbort === controller) state.detailAbort = null;
    }
  }

  function openDetail(key) {
    if (!key) return;
    state.libraryScroll = dom.libraryView.scrollTop;
    state.activeKey = key;
    resetTranslation();
    setView('detail');
    focusNode(dom.backBtn);
    loadDetail(key);
  }

  function showLibrary() {
    if (state.detailAbort) state.detailAbort.abort();
    state.detailAbort = null;
    state.detailSeq += 1;
    resetTranslation();
    setView('library');
    const activeKey = state.activeKey;
    requestAnimationFrame(() => {
      dom.libraryView.scrollTop = state.libraryScroll;
      if (activeKey) {
        const row = Array.from(dom.itemList.querySelectorAll('.item-row'))
          .find((button) => button.dataset.key === activeKey);
        if (row) focusNode(row);
      }
    });
  }

  /* ---------- 阅读器 ---------- */
  function ensureReaderFrame() {
    if (state.readerFrame) return;
    const frame = document.createElement('iframe');
    frame.className = 'reader-frame';
    frame.title = 'PDF 阅读器';
    frame.src = READER_SRC;
    frame.hidden = true;
    const spinner = dom.readerStatus.querySelector('.spinner');
    const slowTimer = setTimeout(() => {
      if (!state.readerLoaded) {
        if (spinner) spinner.hidden = true;
        dom.readerStatusText.textContent = '阅读器仍在载入，可稍候或返回详情。';
      }
    }, 20000);
    frame.addEventListener('load', () => {
      clearTimeout(slowTimer);
      state.readerLoaded = true;
      frame.hidden = false;
      if (state.view === 'reader') dom.readerStatus.hidden = true;
    });
    frame.addEventListener('error', () => {
      clearTimeout(slowTimer);
      if (spinner) spinner.hidden = true;
      dom.readerStatus.hidden = false;
      dom.readerStatusText.textContent = '阅读器载入失败，请返回详情重试。';
    });
    dom.readerHost.appendChild(frame);
    state.readerFrame = frame;
  }

  function openReader() {
    setView('reader');
    ensureReaderFrame();
    if (state.readerLoaded) dom.readerStatus.hidden = true;
    else {
      dom.readerStatus.hidden = false;
      dom.readerFrame.hidden = true;
    }
    focusNode(dom.backBtn);
  }

  function closeReader() {
    if (state.view !== 'reader') return;
    setView('detail');
    focusNode(dom.detailTitle);
  }

  function handleBack() {
    if (state.view === 'reader') closeReader();
    else if (state.view === 'detail') showLibrary();
  }

  /* ---------- 翻译 ---------- */
  function setConfigState(text, mode) {
    dom.translationConfig.textContent = text;
    dom.translationConfig.dataset.state = mode || '';
  }

  function updateTranslateButton() {
    const text = dom.translateText.value;
    dom.translateBtn.disabled = !state.translationConfigured || state.translating ||
      text.trim().length === 0 || text.length > 12000;
    dom.translateBtn.textContent = state.translating ? '翻译中…' : '翻译';
  }

  function updateCharCount() {
    dom.charCount.textContent = dom.translateText.value.length + ' / 12000';
    updateTranslateButton();
  }

  async function loadTranslationConfig() {
    if (state.configAbort) state.configAbort.abort();
    const controller = new AbortController();
    state.configAbort = controller;
    state.translationConfigured = false;
    setConfigState('读取中', '');
    dom.translationHint.textContent = '正在检查翻译服务配置。';
    dom.retryTranslationConfig.hidden = true;
    updateTranslateButton();
    try {
      const response = await apiFetch('/api/zotero/translation', {
        headers: { Accept: 'application/json' }, signal: controller.signal
      });
      const data = await readPayload(response);
      if (state.configAbort !== controller) return;
      if (data.configured === true) {
        state.translationConfigured = true;
        setConfigState('翻译就绪', 'ready');
        const provider = asText(data.provider);
        const model = asText(data.model);
        dom.translationHint.textContent = provider || model
          ? '服务：' + [provider, model].filter(Boolean).join(' · ')
          : '翻译服务已就绪。';
      } else {
        state.translationConfigured = false;
        setConfigState('未配置', 'error');
        dom.translationHint.textContent = '服务端尚未配置翻译模型，暂时无法使用翻译。';
      }
    } catch (error) {
      if (error.name === 'AbortError' || state.configAbort !== controller) return;
      state.translationConfigured = false;
      setConfigState('不可用', 'error');
      dom.translationHint.textContent = '翻译服务不可用：' + friendlyError(error);
      dom.retryTranslationConfig.hidden = false;
    } finally {
      if (state.configAbort === controller) state.configAbort = null;
      updateTranslateButton();
    }
  }

  function resetTranslation() {
    if (state.translateAbort) state.translateAbort.abort();
    state.translateAbort = null;
    state.translating = false;
    state.translationValue = '';
    state.translationModelName = '';
    state.translationCached = false;
    state.translationExpanded = false;
    dom.translateText.value = '';
    dom.translationError.hidden = true;
    dom.translationErrorText.textContent = '';
    dom.translationOutput.hidden = true;
    dom.translationText.textContent = '';
    dom.translationModel.textContent = '';
    dom.cacheBadge.hidden = true;
    dom.toggleTranslation.hidden = true;
    dom.toggleTranslation.textContent = '展开全部';
    updateCharCount();
  }

  function renderTranslation() {
    const characters = Array.from(state.translationValue);
    const overLimit = characters.length > TRANSLATION_RENDER_LIMIT;
    dom.translationText.textContent = state.translationExpanded
      ? state.translationValue
      : characters.slice(0, TRANSLATION_RENDER_LIMIT).join('');
    dom.translationModel.textContent = state.translationModelName
      ? '模型：' + state.translationModelName
      : '模型：未提供';
    dom.cacheBadge.hidden = !state.translationCached;
    dom.toggleTranslation.hidden = !overLimit;
    dom.toggleTranslation.textContent = state.translationExpanded ? '收起译文' : '展开全部';
    dom.translationOutput.hidden = false;
  }

  function showTranslationError(message) {
    dom.translationError.hidden = false;
    dom.translationErrorText.textContent = message;
  }

  async function translate() {
    if (state.translating) return;
    const text = dom.translateText.value;
    if (!state.translationConfigured) {
      showTranslationError('翻译服务尚未配置，暂时无法翻译。');
      return;
    }
    if (text.trim().length === 0) {
      showTranslationError('请先输入需要翻译的文本。');
      return;
    }
    if (text.length > 12000) {
      showTranslationError('文本超过 12000 个字符，请删减后再试。');
      return;
    }
    if (state.translateAbort) state.translateAbort.abort();
    const controller = new AbortController();
    state.translateAbort = controller;
    state.translating = true;
    dom.translationError.hidden = true;
    updateTranslateButton();
    try {
      const response = await apiFetch('/api/zotero/translate', {
        method: 'POST',
        headers: {
          Accept: 'application/json',
          'Content-Type': 'application/json',
          'X-Workbench': '1'
        },
        body: JSON.stringify({ text, target: dom.targetLang.value, extraPrompt: '' }),
        signal: controller.signal
      }, 90000);
      const data = await readPayload(response);
      if (typeof data.translation !== 'string') {
        throw new ApiError('翻译接口没有返回译文，请稍后重试。', 0, 'bad_translation');
      }
      state.translationValue = data.translation;
      state.translationModelName = asText(data.model);
      state.translationCached = data.cached === true;
      state.translationExpanded = false;
      renderTranslation();
    } catch (error) {
      if (error.name === 'AbortError') return;
      showTranslationError(friendlyError(error));
    } finally {
      if (state.translateAbort === controller) {
        state.translateAbort = null;
        state.translating = false;
        updateTranslateButton();
      }
    }
  }

  /* ---------- 事件绑定 ---------- */
  function bindEvents() {
    dom.collectionList.addEventListener('click', (event) => {
      const button = event.target.closest('.collection-button');
      if (!button) return;
      const key = button.dataset.collection || '';
      if (key === state.selectedCollection) return;
      state.selectedCollection = key;
      updateActiveCollection();
      dom.collectionList.querySelectorAll('.collection-button').forEach((item) => {
        item.setAttribute('aria-pressed', String((item.dataset.collection || '') === key));
      });
      state.query = dom.searchInput.value.trim();
      clearTimeout(searchTimer);
      loadItems(true);
    });

    dom.searchInput.addEventListener('input', () => {
      state.query = dom.searchInput.value.trim();
      dom.clearSearch.hidden = !dom.searchInput.value;
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => loadItems(true), 320);
    });
    dom.searchInput.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') {
        event.preventDefault();
        clearTimeout(searchTimer);
        state.query = dom.searchInput.value.trim();
        loadItems(true);
      }
    });
    dom.clearSearch.addEventListener('click', () => {
      dom.searchInput.value = '';
      state.query = '';
      dom.clearSearch.hidden = true;
      clearTimeout(searchTimer);
      loadItems(true);
      focusNode(dom.searchInput);
    });

    dom.retryList.addEventListener('click', () => loadItems(listRetryMode !== 'append'));
    dom.loadMore.addEventListener('click', () => loadItems(false));

    dom.itemList.setAttribute('role', 'list');
    dom.itemList.addEventListener('click', (event) => {
      const row = event.target.closest('.item-row');
      if (row) openDetail(row.dataset.key);
    });
    dom.itemList.addEventListener('keydown', (event) => {
      const rows = Array.from(dom.itemList.querySelectorAll('.item-row'));
      const index = rows.indexOf(document.activeElement);
      if (index < 0) return;
      let next = -1;
      if (event.key === 'ArrowDown') next = Math.min(rows.length - 1, index + 1);
      else if (event.key === 'ArrowUp') next = Math.max(0, index - 1);
      else if (event.key === 'Home') next = 0;
      else if (event.key === 'End') next = rows.length - 1;
      if (next >= 0) {
        event.preventDefault();
        rows[next].focus();
      }
    });

    dom.backBtn.addEventListener('click', handleBack);
    dom.detailBack.addEventListener('click', showLibrary);
    dom.retryDetail.addEventListener('click', () => {
      if (state.activeKey) loadDetail(state.activeKey);
    });
    dom.retryCollections.addEventListener('click', loadCollections);
    dom.retryTranslationConfig.addEventListener('click', loadTranslationConfig);

    dom.pdfList.addEventListener('click', (event) => {
      const button = event.target.closest('.pdf-button');
      if (!button) return;
      const pdf = state.activePdfs[Number(button.dataset.pdfIndex)];
      if (pdf) openReader();
    });

    dom.translateText.addEventListener('input', updateCharCount);
    dom.translateBtn.addEventListener('click', translate);
    dom.retryTranslation.addEventListener('click', translate);
    dom.toggleTranslation.addEventListener('click', () => {
      state.translationExpanded = !state.translationExpanded;
      renderTranslation();
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && state.view === 'reader') {
        event.preventDefault();
        closeReader();
      }
    });
  }

  function init() {
    setView('library');
    setService('loading', '连接中');
    dom.detailTitle.setAttribute('tabindex', '-1');
    bindEvents();
    loadCollections();
    loadTranslationConfig();
    loadItems(true);
  }

  init();
})();
