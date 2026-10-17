/* Office 插件：先确认文档服务在，再拿会话配置，最后把 ONLYOFFICE 编辑器挂上。
   新建文档复用资源管理器的接口（/api/explorer/new），所以两处生成的是同一种文件。 */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const fileName = $('fileName'), fileMeta = $('fileMeta'), note = $('note');
  const placeholder = $('placeholder'), editorBox = $('editor');
  const banner = $('banner'), bannerText = $('bannerText');
  const newActions = $('newActions'), reloadBtn = $('reloadBtn');
  const explorerLink = $('explorerLink'), downloadLink = $('downloadLink');
  const modeBadge = $('modeBadge'), modeBtn = $('modeBtn');
  const params = new URLSearchParams(location.search);
  const KIND_NAMES = { word: 'Word 文档', excel: 'Excel 表格', ppt: 'PPT 演示文稿' };
  let mounted = false;
  // "view" is an explicit read-only open; the editor is editable by default.
  let mode = params.get('mode') === 'view' ? 'view' : 'edit';
  let currentPath = '';

  function setNote(text, busy) {
    note.replaceChildren();
    note.append(document.createTextNode(text));
    placeholder.hidden = false;
    editorBox.replaceChildren();
    if (busy) note.insertAdjacentHTML('afterbegin', '<b>' + busy + '</b><br>');
  }
  function showBanner(text) {
    bannerText.textContent = text;
    banner.hidden = false;
  }
  function hideBanner() { banner.hidden = true; }
  function showNew() {
    newActions.hidden = false;
    placeholder.hidden = false;
  }
  async function api(path, options) {
    const opts = options || {};
    const response = await fetch(path, {
      method: opts.method || 'GET',
      headers: Object.assign(
        { Accept: 'application/json' },
        opts.method === 'POST' ? { 'X-Workbench': '1' } : {},
        opts.body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      cache: 'no-store'
    });
    let data = null;
    try { data = await response.json(); } catch (_) { data = null; }
    if (!response.ok) {
      const error = new Error(data && data.error ? data.error : 'HTTP ' + response.status);
      error.status = response.status;
      throw error;
    }
    return data;
  }
  function loadScript(src) {
    return new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = src;
      script.async = false;
      script.onload = () => resolve();
      script.onerror = () => reject(new Error('无法加载编辑器脚本：' + src));
      document.head.append(script);
    });
  }

  async function createNew(kind, dir) {
    const name = '新建 ' + (KIND_NAMES[kind] || '文档');
    const data = await api('/api/explorer/new', { method: 'POST', body: { path: dir, name, kind } });
    return data.entry.path;
  }

  async function open(path) {
    hideBanner();
    currentPath = path || '';
    fileName.textContent = path ? path.split(/[\\/]/).pop() : 'Office 编辑器';
    fileMeta.textContent = '';
    mount(path);
  }

  /** Show the current mode and the way to change it, independently of the editor. */
  function showMode() {
    const editable = mode !== 'view';
    modeBadge.hidden = false;
    modeBadge.textContent = editable ? '可编辑' : '只读';
    modeBadge.classList.toggle('is-readonly', !editable);
    modeBtn.hidden = false;
    modeBtn.textContent = editable ? '切换为查看' : '切换为编辑';
  }

  function switchMode() {
    mode = mode === 'view' ? 'edit' : 'view';
    showMode();
    const query = new URLSearchParams(location.search);
    if (mode === 'view') query.set('mode', 'view');
    else query.delete('mode');
    history.replaceState(null, '', '?' + query.toString());
    if (currentPath) mount(currentPath);
  }

  async function mount(path) {
    mounted = false;
    setNote('正在打开文档…', '');
    let state;
    try {
      state = await api('/api/office/status');
    } catch (error) {
      setNote('无法读取 Office 服务状态：' + error.message);
      showBanner(error.message);
      return;
    }
    if (!state.available) {
      setNote('这台电脑上还没有 ONLYOFFICE 文档服务。');
      showBanner(state.hint || state.error || 'ONLYOFFICE 文档服务不可用');
      return;
    }
    if (!path) {
      setNote('从「文件」里选一个 Word、Excel 或 PPT，或者直接新建一个。');
      showNew();
      return;
    }
    let session;
    try {
      session = await api('/api/office/session?path=' + encodeURIComponent(path)
        + '&mode=' + encodeURIComponent(mode));
    } catch (error) {
      setNote('无法打开这个文档：' + error.message);
      showBanner(error.message);
      return;
    }
    mode = session.mode === 'view' ? 'view' : 'edit';
    showMode();
    fileName.textContent = session.title || path;
    fileMeta.textContent = (session.docs || '') + (session.jwt ? ' · JWT' : '');
    const parent = path.replace(/[\\/][^\\/]*$/, '') || path;
    explorerLink.href = '/plugins/explorer/?path=' + encodeURIComponent(parent);
    explorerLink.hidden = false;
    downloadLink.href = '/api/explorer/raw?path=' + encodeURIComponent(path) + '&download=1';
    downloadLink.hidden = false;
    reloadBtn.hidden = false;
    setNote('正在加载编辑器…');
    try {
      await loadScript(session.script);
    } catch (error) {
      setNote(error.message + '\n如果文档服务刚启动，请稍等几秒再试。');
      showBanner(error.message);
      return;
    }
    if (typeof window.DocsAPI === 'undefined') {
      setNote('编辑器脚本已加载，但没有找到 DocsAPI。请确认文档服务地址正确。');
      showBanner('没有找到 DocsAPI');
      return;
    }
    try {
      // eslint-disable-next-line no-undef
      new DocsAPI.DocEditor('editor', session.config);
      placeholder.hidden = true;
      mounted = true;
    } catch (error) {
      setNote('编辑器初始化失败：' + error.message);
      showBanner('编辑器初始化失败：' + error.message);
    }
  }

  async function boot() {
    $('retryBtn').addEventListener('click', () => {
      hideBanner();
      if (params.get('path') || params.get('new')) location.reload();
      else mount('');
    });
    reloadBtn.addEventListener('click', () => location.reload());
    modeBtn.addEventListener('click', switchMode);
    newActions.addEventListener('click', async (event) => {
      const button = event.target.closest('button[data-kind]');
      if (!button) return;
      const kind = button.dataset.kind;
      let dir = params.get('dir') || '';
      if (!dir) {
        try { dir = (await api('/api/explorer/info')).start || ''; } catch (_) { dir = ''; }
      }
      if (!dir) {
        setNote('不知道该把新文件放在哪里。请先在「文件」里进入一个文件夹再新建。');
        return;
      }
      button.disabled = true;
      try {
        const path = await createNew(kind, dir);
        location.replace('/plugins/office/?path=' + encodeURIComponent(path));
      } catch (error) {
        setNote('新建失败：' + error.message);
        showBanner('新建失败：' + error.message);
      } finally {
        button.disabled = false;
      }
    });
    let path = params.get('path') || '';
    const kind = (params.get('new') || '').toLowerCase();
    if (!path && kind) {
      try {
        const dir = params.get('dir') || '';
        if (!dir) throw new Error('缺少目标文件夹');
        path = await createNew(kind, dir);
        history.replaceState(null, '', '?path=' + encodeURIComponent(path));
      } catch (error) {
        setNote('新建失败：' + error.message);
        showBanner('新建失败：' + error.message);
        return;
      }
    }
    open(path);
  }

  window.addEventListener('beforeunload', (event) => {
    if (!mounted) return;
    // ONLYOFFICE autosaves; this is only a courtesy prompt for an in-flight edit.
    event.preventDefault();
    event.returnValue = '';
  });

  boot();
})();
