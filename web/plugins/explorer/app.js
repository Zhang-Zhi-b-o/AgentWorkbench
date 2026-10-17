/* 文件插件：浏览整台电脑、预览/编辑文本、上传下载、复制移动删除，
   并把当前目录交给工作台里的 VS Code 或 Office 编辑器。
   所有写请求都带 X-Workbench: 1（宿主的写操作闸门），路径一律绝对路径。 */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const listEl = $('list'), crumbsEl = $('crumbs'), quickEl = $('quickList');
  const emptyEl = $('empty'), statusText = $('statusText'), statusRight = $('statusRight');
  const selInfo = $('selInfo'), searchInput = $('searchInput'), menuEl = $('menu');
  const modal = $('modal'), modalCard = $('modalCard'), modalTitle = $('modalTitle');
  const modalBody = $('modalBody'), modalFoot = $('modalFoot'), toastEl = $('toast');
  const modalActions = $('modalActions');
  const fileInput = $('fileInput'), progress = $('progress'), progressBar = $('progressBar');
  const S = {
    info: null, path: '', parent: '', name: '', isRoot: true, writable: false,
    entries: [], selected: new Set(), anchor: -1, sort: { key: 'name', dir: 1 },
    view: 'list', history: [], index: -1, query: '', searching: false,
    clipboard: { mode: '', paths: [] }, busy: false, dragDepth: 0
  };
  const STORE = {
    view: 'workbench.explorer.view', sort: 'workbench.explorer.sort',
    last: 'workbench.explorer.path'
  };
  const ICONS = {
    dir: 'M3 7.2A1.6 1.6 0 0 1 4.6 5.6h3.8l1.5 2.1H19a1.4 1.4 0 0 1 1.4 1.4v8.4A1.4 1.4 0 0 1 19 19H4.6A1.6 1.6 0 0 1 3 17.4V7.2Z',
    drive: 'M4 6.5h16v6H4zM6.5 16.5h11M7 9.5h.01M10 9.5h.01',
    word: 'M6 3h8l4 4v14H6V3Zm8 0v4h4M9 11.5l1.4 5 1.6-3.4 1.6 3.4 1.4-5',
    excel: 'M6 3h8l4 4v14H6V3Zm8 0v4h4M9 11l4.5 6M13.5 11 9 17',
    ppt: 'M6 3h8l4 4v14H6V3Zm8 0v4h4M10 17v-6h2.2a1.9 1.9 0 0 1 0 3.8H10',
    pdf: 'M6 3h8l4 4v14H6V3Zm8 0v4h4M9 16v-4.5h1.8a1.5 1.5 0 0 1 0 3H9',
    image: 'M4 5h16v14H4zM4 15l4.5-4.5L13 15l2.5-2.5L20 17M15.5 8.5h.01',
    video: 'M4 6h11v12H4zM15 10l5-2.5v9L15 14',
    audio: 'M9 18V6l10-2v12M9 18a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0Zm10-2a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0Z',
    archive: 'M4 5h16v4H4zM5 9v11h14V9M10 13h4',
    text: 'M6 3h8l4 4v14H6V3Zm8 0v4h4M9 12h6M9 16h4',
    other: 'M6 3h8l4 4v14H6V3Zm8 0v4h4',
    home: 'M4 11 12 4l8 7M6.5 9.5V20h11V9.5',
    laptop: 'M4 6h16v9H4zM2 18h20'
  };

  // -- 基础工具 ---------------------------------------------------------
  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }
  function svgIcon(kind) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('aria-hidden', 'true');
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', ICONS[kind] || ICONS.other);
    svg.append(path);
    return svg;
  }
  function fmtSize(size) {
    if (!size) return '—';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let value = size, unit = 0;
    while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1; }
    return (unit === 0 ? String(value) : value.toFixed(value >= 10 ? 0 : 1)) + ' ' + units[unit];
  }
  function pad(n) { return String(n).padStart(2, '0'); }
  function fmtDate(ts) {
    if (!ts) return '—';
    const d = new Date(ts * 1000);
    return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate())
      + ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes());
  }
  function typeLabel(entry) {
    if (entry.dir) return '文件夹';
    const ext = (entry.ext || '').replace('.', '').toUpperCase();
    const names = { docx: 'Word 文档', doc: 'Word 文档', xlsx: 'Excel 工作表', xls: 'Excel 工作表',
      pptx: 'PowerPoint 演示文稿', ppt: 'PowerPoint 演示文稿', pdf: 'PDF 文档', txt: '文本文档',
      md: 'Markdown', json: 'JSON', js: 'JavaScript', ts: 'TypeScript', py: 'Python',
      zip: '压缩文件夹', '7z': '压缩文件', rar: '压缩文件', png: 'PNG 图片', jpg: 'JPEG 图片',
      jpeg: 'JPEG 图片', gif: 'GIF 图片', svg: 'SVG 图片', mp4: 'MP4 视频', mp3: 'MP3 音频' };
    return names[ext.toLowerCase()] || (ext ? ext + ' 文件' : '文件');
  }
  function toast(message, isError) {
    toastEl.textContent = message;
    toastEl.className = 'ex-toast' + (isError ? ' is-error' : '');
    toastEl.hidden = false;
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => { toastEl.hidden = true; }, isError ? 5200 : 2400);
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
      const message = data && typeof data.error === 'string' ? data.error : ('HTTP ' + response.status);
      const error = new Error(message);
      error.status = response.status;
      error.code = data && data.code;
      throw error;
    }
    return data;
  }
  function store(key, value) { try { localStorage.setItem(key, value); } catch (_) { /* 无痕模式 */ } }
  function recall(key, fallback) { try { return localStorage.getItem(key) || fallback; } catch (_) { return fallback; } }
  function joinPath(dir, name) {
    const sep = (S.info && S.info.sep) || '\\';
    return dir.endsWith(sep) ? dir + name : dir + sep + name;
  }
  function parentOf(path) {
    if (!path) return '';
    const sep = (S.info && S.info.sep) || '\\';
    const trimmed = path.replace(/[\\/]+$/, '');
    const cut = Math.max(trimmed.lastIndexOf('\\'), trimmed.lastIndexOf('/'));
    if (cut <= 0) return '';
    const parent = trimmed.slice(0, cut);
    return /^[A-Za-z]:$/.test(parent) ? parent + sep : parent;
  }
  function crumbList(path) {
    if (!path) return [];
    const sep = (S.info && S.info.sep) || '\\';
    const trimmed = path.replace(/[\\/]+$/, '');
    const bits = trimmed.split(/[\\/]+/);
    const out = [];
    let acc = '';
    bits.forEach((bit, index) => {
      if (index === 0) {
        if (/^[A-Za-z]:$/.test(bit)) { acc = bit + sep; out.push({ name: bit + ':', path: acc }); }
        else if (!bit) { acc = sep; out.push({ name: sep, path: sep }); }
        else { acc = sep + bit; out.push({ name: bit, path: acc }); }
      } else {
        acc = acc.endsWith(sep) ? acc + bit : acc + sep + bit;
        out.push({ name: bit, path: acc });
      }
    });
    return out;
  }

  // -- 排序 / 选择 ------------------------------------------------------
  function natural(text) {
    return String(text || '').split(/(\d+)/).map((part) => (/^\d+$/.test(part) ? Number(part) : part.toLowerCase()));
  }
  function sorted(entries) {
    const { key, dir } = S.sort;
    return entries.slice().sort((a, b) => {
      if (a.dir !== b.dir) return a.dir ? -1 : 1;
      let result = 0;
      if (key === 'date') result = a.mtime - b.mtime;
      else if (key === 'size') result = a.size - b.size;
      else if (key === 'type') result = natural(a.ext || (a.dir ? '' : 'zz')) < natural(b.ext || (b.dir ? '' : 'zz')) ? -1 : 1;
      if (!result) result = natural(a.name) < natural(b.name) ? -1 : (natural(a.name) > natural(b.name) ? 1 : 0);
      return result * dir;
    });
  }
  function selectedEntries() {
    return S.entries.filter((entry) => S.selected.has(entry.path));
  }
  function focusEntry() {
    const visible = sorted(S.entries);
    return visible[S.anchor] || null;
  }
  function setSelection(paths, anchorPath) {
    S.selected = new Set(paths);
    if (anchorPath !== undefined) {
      const visible = sorted(S.entries);
      S.anchor = Math.max(0, visible.findIndex((entry) => entry.path === anchorPath));
    }
    paintSelection();
  }
  function paintSelection() {
    listEl.querySelectorAll('.ex-row').forEach((row) => {
      row.setAttribute('aria-selected', String(S.selected.has(row.dataset.path)));
    });
    const count = S.selected.size;
    selInfo.textContent = count ? '已选 ' + count + ' 项' : '';
    $('pasteBtn').disabled = !S.clipboard.paths.length || !S.writable;
  }

  // -- 读取 / 导航 ------------------------------------------------------
  async function loadInfo() {
    S.info = await api('/api/explorer/info');
    renderQuick();
  }
  function renderQuick() {
    quickEl.replaceChildren();
    quickEl.setAttribute('aria-busy', 'false');
    const groups = [];
    if (S.info.quick && S.info.quick.length) groups.push({ title: '常用位置', items: S.info.quick });
    if (S.info.drives && S.info.drives.length) {
      groups.push({
        title: '此电脑', items: S.info.drives.map((drive) => ({
          name: (drive.label || drive.name) + '  (' + drive.path + ')',
          path: drive.path, sub: drive.kind === 'fixed' ? '' : drive.kind
        }))
      });
    }
    groups.forEach((group) => {
      quickEl.append(el('div', 'ex-quick-group', group.title));
      group.items.forEach((item) => {
        const button = el('button', 'ex-quick-item');
        button.type = 'button';
        button.dataset.path = item.path;
        button.title = item.path;
        button.append(svgIcon(item.path && /^[A-Za-z]:\\?$/.test(item.path) ? 'drive'
          : (item.path === S.info.home ? 'home' : 'dir')));
        button.append(el('span', 'ex-q-name', item.name));
        if (item.sub) button.append(el('span', 'ex-q-sub', item.sub));
        button.addEventListener('click', () => { navigate(item.path); closeSide(); });
        quickEl.append(button);
      });
    });
  }
  function highlightQuick() {
    quickEl.querySelectorAll('.ex-quick-item').forEach((item) => {
      const same = (item.dataset.path || '').replace(/[\\/]+$/, '').toLowerCase()
        === (S.path || '').replace(/[\\/]+$/, '').toLowerCase();
      if (same) item.setAttribute('aria-current', 'true');
      else item.removeAttribute('aria-current');
    });
  }
  async function navigate(path, options) {
    const opts = options || {};
    if (S.busy) return;
    S.busy = true;
    S.query = '';
    searchInput.value = '';
    listEl.setAttribute('aria-busy', 'true');
    statusText.textContent = '正在读取 ' + (path || '此电脑') + ' …';
    listEl.replaceChildren(el('div', 'ex-skeleton', '正在读取…'));
    emptyEl.hidden = true;
    try {
      const data = await api('/api/explorer/list?path=' + encodeURIComponent(path || ''));
      S.path = data.path; S.parent = data.parent; S.name = data.name;
      S.isRoot = data.isRoot; S.writable = data.writable;
      S.entries = data.entries || [];
      S.selected = new Set(); S.anchor = S.entries.length ? 0 : -1;
      if (!opts.replace) {
        S.history = S.history.slice(0, S.index + 1);
        S.history.push(S.path);
        S.index = S.history.length - 1;
      }
      store(STORE.last, S.path || '');
      syncUrl();
      render();
    } catch (error) {
      listEl.replaceChildren();
      statusText.textContent = error.message;
      emptyEl.hidden = false;
      emptyEl.textContent = error.message;
      toast('打开失败：' + error.message, true);
    } finally {
      S.busy = false;
      listEl.setAttribute('aria-busy', 'false');
    }
  }
  function syncUrl() {
    try {
      const query = S.path ? '?path=' + encodeURIComponent(S.path) : '';
      history.replaceState(null, '', location.pathname + query);
    } catch (_) { /* 壳内 iframe 也可能拒绝 */ }
  }

  // -- 渲染 -------------------------------------------------------------
  function render() {
    renderCrumbs();
    renderRows();
    highlightQuick();
    renderStatus();
    $('backBtn').disabled = S.index <= 0;
    $('forwardBtn').disabled = S.index >= S.history.length - 1;
    $('upBtn').disabled = !S.path;
    listEl.classList.toggle('is-grid', S.view === 'grid');
    paintSelection();
  }
  function renderCrumbs() {
    crumbsEl.replaceChildren();
    const home = el('button', 'ex-crumb', '此电脑');
    home.type = 'button';
    home.addEventListener('click', () => navigate(''));
    if (!S.path) home.setAttribute('aria-current', 'page');
    crumbsEl.append(home);
    crumbList(S.path).forEach((crumb, index, all) => {
      crumbsEl.append(el('span', 'ex-crumb-sep', '/'));
      const button = el('button', 'ex-crumb', crumb.name);
      button.type = 'button';
      button.title = crumb.path;
      if (index === all.length - 1) button.setAttribute('aria-current', 'page');
      button.addEventListener('click', () => navigate(crumb.path));
      crumbsEl.append(button);
    });
    requestAnimationFrame(() => { crumbsEl.scrollLeft = crumbsEl.scrollWidth; });
  }
  function renderRows() {
    listEl.replaceChildren();
    if (S.view === 'list') {
      const head = el('div', 'ex-head');
      const sortMark = (key) => S.sort.key === key ? (S.sort.dir > 0 ? ' ↑' : ' ↓') : '';
      const nameBtn = el('button', '', '名称' + sortMark('name'));
      const typeBtn = el('button', 'ex-h-type', '类型' + sortMark('type'));
      const sizeBtn = el('button', 'ex-h-size', '大小' + sortMark('size'));
      nameBtn.type = typeBtn.type = sizeBtn.type = 'button';
      nameBtn.addEventListener('click', () => setSort('name'));
      typeBtn.addEventListener('click', () => setSort('type'));
      sizeBtn.addEventListener('click', () => setSort('size'));
      head.append(nameBtn, typeBtn, sizeBtn);
      listEl.append(head);
    }
    const entries = sorted(S.entries);
    if (!entries.length) {
      emptyEl.hidden = false;
      emptyEl.textContent = S.query ? '没有匹配的项目' : '这个文件夹是空的';
    } else {
      emptyEl.hidden = true;
    }
    const fragment = document.createDocumentFragment();
    entries.forEach((entry, index) => {
      const row = el('button', 'ex-row');
      row.type = 'button';
      row.dataset.path = entry.path;
      row.dataset.index = String(index);
      row.setAttribute('role', 'option');
      row.setAttribute('aria-selected', 'false');
      row.title = entry.path;
      if (entry.link) row.classList.add('is-link');
      const nameCell = el('span', 'ex-cell-name');
      const badge = el('span', 'ex-ico');
      badge.dataset.kind = entry.kind || (entry.dir ? 'dir' : 'other');
      badge.append(svgIcon(entry.dir ? (entry.kind === 'drive' ? 'drive' : 'dir') : badge.dataset.kind));
      nameCell.append(badge, el('span', 'ex-name', entry.name));
      row.append(nameCell, el('span', 'ex-cell-meta ex-cell-type', typeLabel(entry)),
        el('span', 'ex-cell-meta', entry.dir ? '—' : fmtSize(entry.size)));
      row.addEventListener('click', (event) => selectRow(entry, index, event));
      row.addEventListener('dblclick', () => activate(entry));
      row.addEventListener('contextmenu', (event) => {
        event.preventDefault();
        if (!S.selected.has(entry.path)) setSelection([entry.path], entry.path);
        openSelectionMenu(event.clientX, event.clientY, entry);
      });
      row.addEventListener('dragenter', (event) => {
        if (!entry.dir || !event.dataTransfer || !event.dataTransfer.types.includes('Files')) return;
        event.preventDefault();
        row.classList.add('is-drop');
      });
      row.addEventListener('dragleave', () => row.classList.remove('is-drop'));
      row.addEventListener('dragover', (event) => {
        if (!entry.dir) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = 'copy';
      });
      row.addEventListener('drop', (event) => {
        row.classList.remove('is-drop');
        if (!entry.dir || !event.dataTransfer) return;
        event.preventDefault();
        event.stopPropagation();
        uploadFiles(event.dataTransfer.files, entry.path);
      });
      fragment.append(row);
    });
    listEl.append(fragment);
  }
  function setSort(key) {
    if (S.sort.key === key) S.sort.dir *= -1;
    else { S.sort.key = key; S.sort.dir = 1; }
    store(STORE.sort, S.sort.key + ':' + S.sort.dir);
    $('sortBtn').textContent = ({ name: '名称', date: '日期', size: '大小', type: '类型' })[S.sort.key]
      + (S.sort.dir > 0 ? ' ↑' : ' ↓');
    renderRows();
    paintSelection();
  }
  function renderStatus() {
    const total = S.entries.length;
    const selected = S.selected.size;
    statusText.textContent = (S.query ? '搜索“' + S.query + '” · ' : '')
      + (S.path || '此电脑') + ' · ' + total + ' 项' + (selected ? ' · 已选 ' + selected + ' 项' : '');
    statusRight.textContent = (S.writable ? '可写' : '只读') + ' · ' + (S.info && S.info.platform === 'windows' ? 'Windows' : (S.info ? S.info.platform : ''));
  }
  function selectRow(entry, index, event) {
    const visible = sorted(S.entries);
    if (event.shiftKey && S.anchor >= 0) {
      const start = Math.min(S.anchor, index), end = Math.max(S.anchor, index);
      const paths = visible.slice(start, end + 1).map((item) => item.path);
      setSelection(event.ctrlKey || event.metaKey ? Array.from(S.selected).concat(paths) : paths);
    } else if (event.ctrlKey || event.metaKey) {
      const next = new Set(S.selected);
      if (next.has(entry.path)) next.delete(entry.path); else next.add(entry.path);
      setSelection(Array.from(next), entry.path);
    } else {
      setSelection([entry.path], entry.path);
    }
    listEl.focus({ preventScroll: true });
  }

  // -- 打开 / 预览 / 编辑 -----------------------------------------------
  // Office 文档不在浏览器里编辑：点开先给一份只读预览（由本机 Office 转成的
  // PDF），右上角「下载并打开」把文件交给设备自带的 Word / Pages / WPS。
  // 浏览器无法代为唤起本机应用，所以"打开"这一步只能由设备完成。
  const OFFICE_KINDS = { word: 'Word', excel: 'Excel', ppt: 'PPT' };
  const OFFICE_EXT = /\.(docx?|docm|dotx?|odt|rtf|xlsx?|xlsm|xltx?|ods|csv|pptx?|pptm|potx?|odp)$/i;

  function isOffice(entry) {
    if (!entry || entry.dir) return false;
    if (OFFICE_KINDS[entry.kind]) return true;
    return OFFICE_EXT.test(entry.name || '');
  }

  function downloadFile(entry) {
    const link = document.createElement('a');
    link.href = rawUrl(entry, true);
    link.download = entry.name || '';
    link.rel = 'noopener';
    document.body.append(link);
    link.click();
    link.remove();
    toast('已开始下载「' + (entry.name || '文件') + '」，用这台设备上的 Word / Pages / WPS 打开即可');
  }

  function previewOffice(entry) {
    // 预览要看到完整画面：本机 Office 把它转成只读 PDF，弹窗里整页显示。
    // 「下载并打开」给的是源文件本体，不是预览用的那份转换结果。
    openModal(entry.name + ' · ' + typeLabel(entry), '', { wide: true, pad: false });
    addHeadButton('下载并打开', () => downloadFile(entry));
    const note = el('div', 'ex-preview-note', '正在生成预览…（第一次打开要等几秒）');
    modalBody.append(note);
    const src = '/api/explorer/preview?path=' + encodeURIComponent(entry.path);
    // Ask for a single byte first: a conversion failure then reads as a message
    // instead of an empty frame, and the whole PDF is fetched only once more.
    fetch(src, { headers: { Range: 'bytes=0-0' }, cache: 'no-store' })
      .then((response) => {
        if (response.ok) {
          const frame = document.createElement('iframe');
          frame.className = 'ex-preview-frame';
          frame.title = entry.name;
          frame.src = src;
          modalBody.replaceChildren(frame);
          return null;
        }
        return response.json().then(
          (data) => { throw new Error((data && data.error) || ('HTTP ' + response.status)); },
          () => { throw new Error('HTTP ' + response.status); });
      })
      .catch((error) => {
        note.replaceChildren(el('b', '', '无法生成预览'),
          el('span', '', error.message + '。可以直接下载后用设备上的编辑器打开。'));
        addFootButton('用 VS Code 打开', () => openIn('vscode', entry), true);
      });
  }

  async function activate(entry) {
    if (entry.dir) return navigate(entry.path);
    const kind = entry.kind || 'other';
    if (isOffice(entry)) return previewOffice(entry);
    if (kind === 'pdf' || kind === 'image' || kind === 'video' || kind === 'audio') return preview(entry);
    if (kind === 'text') return preview(entry);
    return preview(entry);
  }
  function rawUrl(entry, download) {
    return '/api/explorer/raw?path=' + encodeURIComponent(entry.path)
      + (download ? '&download=1' : '');
  }
  function openIn(target, entry) {
    const path = entry ? entry.path : S.path;
    if (!path) return toast('请先进入一个文件夹', true);
    const body = { path, app: target };
    api('/api/explorer/open', { method: 'POST', body })
      .then((data) => {
        if (!data || !data.url) throw new Error('没有返回地址');
        window.open(data.url, '_blank', 'noopener');
      })
      .catch((error) => toast('打开失败：' + error.message, true));
  }
  function preview(entry) {
    const kind = entry.kind || 'other';
    const title = entry.name;
    openModal(title, '', { wide: kind === 'pdf', pad: false });
    const body = el('div', 'ex-preview-media');
    if (kind === 'image') {
      const img = document.createElement('img');
      img.src = rawUrl(entry); img.alt = entry.name;
      body.append(img);
    } else if (kind === 'video') {
      const video = document.createElement('video');
      video.src = rawUrl(entry); video.controls = true;
      body.append(video);
    } else if (kind === 'audio') {
      const audio = document.createElement('audio');
      audio.src = rawUrl(entry); audio.controls = true;
      body.append(audio);
    } else if (kind === 'pdf') {
      body.className = 'ex-preview-media';
      const frame = document.createElement('iframe');
      frame.className = 'ex-preview-frame';
      frame.src = rawUrl(entry);
      frame.title = entry.name;
      modalBody.append(frame);
      addFootButton('下载', () => { location.href = rawUrl(entry, true); });
      addFootButton('用 VS Code 打开', () => openIn('vscode', entry));
      return;
    } else if (kind === 'text') {
      return openEditor(entry);
    } else {
      const note = el('div', 'ex-preview-note');
      note.append(el('b', '', '没有内置预览'), el('span', '', typeLabel(entry) + '（' + fmtSize(entry.size) + '）'));
      body.append(note);
      addFootButton('下载', () => { location.href = rawUrl(entry, true); });
      addFootButton('用 VS Code 打开', () => openIn('vscode', entry));
    }
    modalBody.append(body);
  }
  async function openEditor(entry) {
    openModal(entry.name, '正在读取…');
    let data;
    try {
      data = await api('/api/explorer/read?path=' + encodeURIComponent(entry.path));
    } catch (error) {
      modalBody.replaceChildren(el('div', 'ex-preview-note', error.message));
      return;
    }
    const pane = el('div', 'ex-editor');
    const area = document.createElement('textarea');
    area.value = data.content;
    area.spellcheck = false;
    area.setAttribute('aria-label', entry.name);
    pane.append(area);
    modalBody.replaceChildren(pane);
    const info = el('span', 'ex-foot-info',
      (data.encoding || 'utf-8') + ' · ' + fmtSize(data.size) + ' · Ctrl+S 保存');
    addFootButton('用 VS Code 打开', () => openIn('vscode', entry), true);
    addFootButton('重新载入', () => openEditor(entry), true);
    const saveButton = addFootButton('保存', () => save());
    modalFoot.prepend(info);
    modalBody.dataset.encoding = data.encoding || 'utf-8';
    let dirty = false;
    area.addEventListener('input', () => {
      dirty = true;
      info.textContent = '已修改 · ' + (data.encoding || 'utf-8') + ' · Ctrl+S 保存';
    });
    async function save() {
      saveButton.disabled = true;
      try {
        await api('/api/explorer/write', {
          method: 'POST',
          body: { path: entry.path, content: area.value, encoding: modalBody.dataset.encoding }
        });
        dirty = false;
        info.textContent = '已保存 · ' + (data.encoding || 'utf-8');
        toast('已保存 ' + entry.name);
        refreshEntry(entry);
      } catch (error) {
        toast('保存失败：' + error.message, true);
      } finally {
        saveButton.disabled = false;
      }
    }
    area.addEventListener('keydown', (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
        event.preventDefault();
        save();
      }
      if (event.key === 'Tab' && !event.shiftKey) {
        event.preventDefault();
        const start = area.selectionStart, end = area.selectionEnd;
        area.setRangeText('  ', start, end, 'end');
        dirty = true;
      }
    });
    modal._onClose = () => dirty;
    setTimeout(() => area.focus(), 30);
  }
  function refreshEntry(entry) {
    const index = S.entries.findIndex((item) => item.path === entry.path);
    if (index < 0) return;
    api('/api/explorer/list?path=' + encodeURIComponent(S.path)).then((data) => {
      S.entries = data.entries || [];
      renderRows(); paintSelection(); renderStatus();
    }).catch(() => {});
  }

  // -- 菜单 -------------------------------------------------------------
  function closeMenu() { menuEl.hidden = true; menuEl.replaceChildren(); }
  function openMenu(items, x, y, header) {
    menuEl.replaceChildren();
    if (header) menuEl.append(el('div', 'ex-menu-head', header));
    items.forEach((item) => {
      if (item.sep) return menuEl.append(el('div', 'ex-menu-sep'));
      const button = el('button', 'ex-menu-item' + (item.danger ? ' danger' : ''), item.label);
      button.type = 'button';
      button.disabled = !!item.disabled;
      button.addEventListener('click', () => { closeMenu(); item.run(); });
      menuEl.append(button);
    });
    menuEl.hidden = false;
    const rect = menuEl.getBoundingClientRect();
    menuEl.style.left = Math.min(x, window.innerWidth - rect.width - 8) + 'px';
    menuEl.style.top = Math.min(y, window.innerHeight - rect.height - 8) + 'px';
  }
  function selectionMenu(entry) {
    const multi = S.selected.size > 1;
    const items = [];
    if (!multi && entry) {
      items.push({ label: entry.dir ? '打开' : '打开', run: () => activate(entry) });
      if (!entry.dir && entry.kind === 'text') items.push({ label: '编辑文本', run: () => openEditor(entry) });
      if (isOffice(entry))
        items.push({ label: '下载', run: () => downloadFile(entry) });
      items.push({ label: '用 VS Code 打开', run: () => openIn('vscode', entry) });
      if (!entry.dir && entry.kind === 'pdf') items.push({ label: '预览', run: () => preview(entry) });
      if (entry.kind === 'archive' && entry.ext === '.zip')
        items.push({ label: '解压到此处', run: () => unzip(entry) });
      items.push({ sep: true });
      items.push({ label: '下载' , run: () => { location.href = rawUrl(entry, true); } });
      if (entry.dir) items.push({ label: '打包下载', run: () => { location.href = '/api/explorer/zip?path=' + encodeURIComponent(entry.path); } });
      if (entry.dir) items.push({ label: '在 VS Code 中打开此文件夹', run: () => openIn('vscode', entry) });
      items.push({ sep: true });
    }
    items.push({ label: '复制', run: () => { S.clipboard = { mode: 'copy', paths: selectedPaths() }; paintSelection(); toast('已复制 ' + S.clipboard.paths.length + ' 项'); } });
    items.push({ label: '剪切', run: () => { S.clipboard = { mode: 'cut', paths: selectedPaths() }; paintSelection(); toast('已剪切 ' + S.clipboard.paths.length + ' 项'); } });
    if (S.clipboard.paths.length) items.push({ label: '粘贴到此处', run: () => paste() });
    if (!multi) items.push({ label: '重命名', run: () => renameEntry(entry || focusEntry()) });
    items.push({ label: '复制完整路径', run: () => copyText(entry ? entry.path : S.path) });
    items.push({ sep: true });
    items.push({ label: S.info && S.info.trash ? '移到回收站' : '删除', danger: true, run: () => removeEntries() });
    if (S.info && S.info.trash) items.push({ label: '永久删除', danger: true, run: () => removeEntries(true) });
    return items;
  }
  function openSelectionMenu(x, y, entry) { openMenu(selectionMenu(entry), x, y); }
  function selectedPaths() { return selectedEntries().map((entry) => entry.path); }
  function copyText(text) {
    if (!text) return;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(() => toast('已复制路径')).catch(() => toast(text));
    } else {
      toast(text);
    }
  }

  // -- 写操作 -----------------------------------------------------------
  async function newItem(kind) {
    if (!S.writable) return toast('当前文件夹不可写', true);
    const base = kind === 'folder' ? '新建文件夹' : ({
      text: '新建文本文档', word: '新建 Word 文档', excel: '新建 Excel 表格', ppt: '新建 PPT 演示文稿'
    })[kind] || '新建';
    const name = await askName('新建 ' + base.replace('新建 ', ''), base);
    if (!name) return;
    try {
      const data = kind === 'folder'
        ? await api('/api/explorer/mkdir', { method: 'POST', body: { path: S.path, name } })
        : await api('/api/explorer/new', { method: 'POST', body: { path: S.path, name, kind } });
      await navigate(S.path, { replace: true });
      if (data && data.entry) setSelection([data.entry.path], data.entry.path);
      toast('已创建 ' + (data.entry ? data.entry.name : name));
    } catch (error) {
      toast('新建失败：' + error.message, true);
    }
  }
  async function renameEntry(entry) {
    if (!entry) return;
    const name = await askName('重命名', entry.name);
    if (!name || name === entry.name) return;
    try {
      const data = await api('/api/explorer/rename', { method: 'POST', body: { path: entry.path, name } });
      await navigate(S.path, { replace: true });
      if (data && data.entry) setSelection([data.entry.path], data.entry.path);
      toast('已重命名为 ' + name);
    } catch (error) {
      toast('重命名失败：' + error.message, true);
    }
  }
  async function removeEntries(permanent) {
    const entries = selectedEntries();
    if (!entries.length) return;
    const what = entries.length === 1 ? '“' + entries[0].name + '”' : entries.length + ' 个项目';
    const ok = await confirmBox(permanent ? '永久删除' : '移到回收站',
      (permanent ? '确定永久删除 ' : '确定把 ') + what + (permanent ? ' 吗？此操作不可恢复。' : ' 移到回收站吗？'));
    if (!ok) return;
    try {
      const data = await api('/api/explorer/delete', {
        method: 'POST', body: { paths: entries.map((entry) => entry.path), permanent: !!permanent }
      });
      toast('已删除 ' + data.deleted.length + ' 项');
      await navigate(S.path, { replace: true });
    } catch (error) {
      toast('删除失败：' + error.message, true);
    }
  }
  async function paste() {
    if (!S.clipboard.paths.length || !S.writable) return;
    const mode = S.clipboard.mode;
    try {
      const endpoint = mode === 'cut' ? '/api/explorer/move' : '/api/explorer/copy';
      const data = await api(endpoint, { method: 'POST', body: { paths: S.clipboard.paths, dest: S.path } });
      if (mode === 'cut') S.clipboard = { mode: '', paths: [] };
      toast('已' + (mode === 'cut' ? '移动' : '粘贴') + ' ' + data.entries.length + ' 项');
      await navigate(S.path, { replace: true });
    } catch (error) {
      toast('粘贴失败：' + error.message, true);
    }
  }
  async function unzip(entry) {
    try {
      const data = await api('/api/explorer/unzip', { method: 'POST', body: { path: entry.path } });
      toast('已解压到 ' + data.dest);
      await navigate(S.path, { replace: true });
    } catch (error) {
      toast('解压失败：' + error.message, true);
    }
  }
  function uploadFiles(files, dir) {
    const target = dir || S.path;
    if (!files || !files.length) return;
    if (dir && !S.writable) return toast('目标文件夹不可写', true);
    Array.from(files).reduce((chain, file) => chain.then(() => uploadOne(file, target)), Promise.resolve())
      .then(() => { navigate(S.path, { replace: true }); })
      .catch(() => { /* 单个失败已提示 */ });
  }
  function uploadOne(file, dir) {
    return new Promise((resolve) => {
      const xhr = new XMLHttpRequest();
      const url = '/api/explorer/upload?dir=' + encodeURIComponent(dir) + '&name=' + encodeURIComponent(file.name);
      xhr.open('POST', url);
      xhr.setRequestHeader('X-Workbench', '1');
      xhr.setRequestHeader('Content-Type', 'application/octet-stream');
      progress.hidden = false;
      progressBar.style.setProperty('--p', '0%');
      $('progressText').textContent = '上传 ' + file.name + ' · 0%';
      xhr.upload.onprogress = (event) => {
        if (!event.lengthComputable) return;
        const percent = Math.round(event.loaded / event.total * 100);
        progressBar.style.setProperty('--p', percent + '%');
        $('progressText').textContent = '上传 ' + file.name + ' · ' + percent + '%';
      };
      xhr.onload = () => {
        progress.hidden = true;
        if (xhr.status >= 200 && xhr.status < 300) {
          toast('已上传 ' + file.name);
          resolve();
        } else {
          let message = 'HTTP ' + xhr.status;
          try { message = JSON.parse(xhr.responseText).error || message; } catch (_) { /* 非 JSON */ }
          toast('上传失败：' + message, true);
          resolve();
        }
      };
      xhr.onerror = () => { progress.hidden = true; toast('上传失败：网络错误', true); resolve(); };
      xhr.send(file);
    });
  }

  // -- 弹窗 -------------------------------------------------------------
  function openModal(title, message, options) {
    const opts = options || {};
    modal.hidden = false;
    modalCard.className = 'ex-modal-card' + (opts.wide ? ' is-wide' : '') + (opts.narrow ? ' is-narrow' : '');
    modalTitle.textContent = title;
    modalBody.replaceChildren();
    modalFoot.replaceChildren();
    modalActions.replaceChildren();
    modalBody.classList.toggle('is-pad', opts.pad !== false);
    if (message) modalBody.append(el('p', 'ex-dialog-text', message));
    modal._onClose = null;
    setTimeout(() => { const close = $('modalClose'); if (close) close.focus(); }, 10);
  }
  function closeModal(force) {
    if (modal.hidden) return;
    if (!force && typeof modal._onClose === 'function' && modal._onClose() === true) {
      if (!window.confirm('还有未保存的修改，确定关闭吗？')) return;
    }
    modal.hidden = true;
    modalBody.replaceChildren();
    modalFoot.replaceChildren();
    modalActions.replaceChildren();
  }
  function addHeadButton(label, run) {
    const button = el('button', 'button small primary', label);
    button.type = 'button';
    button.addEventListener('click', run);
    modalActions.append(button);
    return button;
  }
  function addFootButton(label, run, secondary) {
    const button = el('button', 'button small' + (secondary ? '' : ' primary'), label);
    button.type = 'button';
    button.addEventListener('click', run);
    modalFoot.append(button);
    return button;
  }
  function confirmBox(title, message) {
    return new Promise((resolve) => {
      openModal(title, message, { narrow: true });
      const cancel = el('button', 'button small', '取消');
      cancel.type = 'button';
      cancel.addEventListener('click', () => { closeModal(true); resolve(false); });
      const ok = el('button', 'button small primary', '确定');
      ok.type = 'button';
      ok.addEventListener('click', () => { closeModal(true); resolve(true); });
      modal._onClose = () => { resolve(false); return false; };
      setTimeout(() => ok.focus(), 10);
    });
  }
  function askName(title, initial) {
    return new Promise((resolve) => {
      openModal(title, '', { narrow: true });
      const input = document.createElement('input');
      input.className = 'ex-input';
      input.value = initial || '';
      input.spellcheck = false;
      modalBody.append(input);
      const cancel = el('button', 'button small', '取消');
      cancel.type = 'button';
      cancel.addEventListener('click', () => { closeModal(true); resolve(''); });
      const ok = el('button', 'button small primary', '确定');
      ok.type = 'button';
      const finish = () => { closeModal(true); resolve(input.value.trim()); };
      ok.addEventListener('click', finish);
      input.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') { event.preventDefault(); finish(); }
      });
      modalFoot.append(cancel, ok);
      modal._onClose = () => { resolve(''); return false; };
      setTimeout(() => { input.focus(); input.select(); }, 20);
    });
  }

  // -- 事件绑定 ---------------------------------------------------------
  function closeSide() { document.querySelector('.ex-app').classList.remove('side-open'); }
  function bind() {
    $('backBtn').addEventListener('click', () => {
      if (S.index > 0) { S.index -= 1; navigate(S.history[S.index], { replace: true }); }
    });
    $('forwardBtn').addEventListener('click', () => {
      if (S.index < S.history.length - 1) { S.index += 1; navigate(S.history[S.index], { replace: true }); }
    });
    $('upBtn').addEventListener('click', () => { if (S.parent || S.path) navigate(S.parent); });
    $('refreshBtn').addEventListener('click', () => navigate(S.path, { replace: true }));
    $('rootBtn').addEventListener('click', () => { navigate(''); closeSide(); });
    $('sideOpen').addEventListener('click', () => document.querySelector('.ex-app').classList.add('side-open'));
    $('sideClose').addEventListener('click', closeSide);
    $('vscodeBtn').addEventListener('click', () => openIn('vscode', null));
    $('pasteBtn').addEventListener('click', paste);
    $('viewBtn').addEventListener('click', () => {
      S.view = S.view === 'list' ? 'grid' : 'list';
      store(STORE.view, S.view);
      render();
    });
    $('sortBtn').addEventListener('click', () => {
      const order = ['name', 'date', 'size', 'type'];
      setSort(order[(order.indexOf(S.sort.key) + 1) % order.length]);
    });
    $('newBtn').addEventListener('click', (event) => {
      const rect = event.currentTarget.getBoundingClientRect();
      openMenu([
        { label: '文件夹', run: () => newItem('folder') },
        { label: '文本文档', run: () => newItem('text') },
        { label: 'Word 文档', run: () => newItem('word') },
        { label: 'Excel 表格', run: () => newItem('excel') },
        { label: 'PPT 演示文稿', run: () => newItem('ppt') }
      ], rect.left, rect.bottom + 4);
      event.currentTarget.setAttribute('aria-expanded', 'true');
    });
    $('uploadBtn').addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', () => {
      uploadFiles(fileInput.files, S.path);
      fileInput.value = '';
    });
    listEl.addEventListener('mousedown', (event) => { if (event.target === listEl) setSelection([]); });
    listEl.addEventListener('contextmenu', (event) => {
      if (event.target.closest('.ex-row')) return;
      event.preventDefault();
      openMenu(selectionMenu(null), event.clientX, event.clientY);
    });
    listEl.addEventListener('keydown', (event) => {
      const visible = sorted(S.entries);
      const current = focusEntry();
      const currentIndex = current ? visible.findIndex((entry) => entry.path === current.path) : -1;
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        const next = Math.max(0, Math.min(visible.length - 1, currentIndex + (event.key === 'ArrowDown' ? 1 : -1)));
        if (visible[next]) setSelection([visible[next].path], visible[next].path);
        const row = listEl.querySelector('.ex-row[data-index="' + next + '"]');
        if (row) row.scrollIntoView({ block: 'nearest' });
      } else if (event.key === 'Enter') {
        event.preventDefault();
        if (current) activate(current);
      } else if (event.key === 'Backspace') {
        event.preventDefault();
        if (S.parent || S.path) navigate(S.parent);
      } else if (event.key === 'Delete') {
        event.preventDefault();
        removeEntries(event.shiftKey);
      } else if (event.key === 'F2') {
        event.preventDefault();
        if (current) renameEntry(current);
      } else if (event.key === 'a' && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        setSelection(visible.map((entry) => entry.path), S.anchor >= 0 ? undefined : 0);
      } else if (event.key === 'c' && (event.ctrlKey || event.metaKey)) {
        if (S.selected.size) { S.clipboard = { mode: 'copy', paths: selectedPaths() }; paintSelection(); }
      } else if (event.key === 'x' && (event.ctrlKey || event.metaKey)) {
        if (S.selected.size) { S.clipboard = { mode: 'cut', paths: selectedPaths() }; paintSelection(); }
      } else if (event.key === 'v' && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        paste();
      } else if (event.key === 'F5') {
        event.preventDefault();
        navigate(S.path, { replace: true });
      }
    });
    // 内联重命名（双击列表头之外）——保持简单：F2 / 右键菜单。
    let searchTimer = 0;
    searchInput.addEventListener('input', () => {
      clearTimeout(searchTimer);
      const term = searchInput.value.trim();
      if (!term) { if (S.query) { S.query = ''; navigate(S.path, { replace: true }); } return; }
      searchTimer = setTimeout(() => runSearch(term), 280);
    });
    searchInput.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        searchInput.value = '';
        if (S.query) { S.query = ''; navigate(S.path, { replace: true }); }
        searchInput.blur();
      }
    });
    // 拖放到空白处 = 上传到当前文件夹
    const dropZone = document.querySelector('.ex-main');
    dropZone.addEventListener('dragenter', (event) => {
      if (!event.dataTransfer || !event.dataTransfer.types.includes('Files')) return;
      event.preventDefault();
      S.dragDepth += 1;
      listEl.classList.add('is-drop');
    });
    dropZone.addEventListener('dragover', (event) => {
      if (!event.dataTransfer || !event.dataTransfer.types.includes('Files')) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = 'copy';
    });
    dropZone.addEventListener('dragleave', () => {
      S.dragDepth = Math.max(0, S.dragDepth - 1);
      if (!S.dragDepth) listEl.classList.remove('is-drop');
    });
    dropZone.addEventListener('drop', (event) => {
      S.dragDepth = 0;
      listEl.classList.remove('is-drop');
      const target = event.target.closest && event.target.closest('.ex-row');
      if (target) return;  // 行自己的 drop 处理了
      if (!event.dataTransfer || !event.dataTransfer.files.length) return;
      event.preventDefault();
      uploadFiles(event.dataTransfer.files, S.path);
    });
    $('modalClose').addEventListener('click', () => closeModal(false));
    modal.addEventListener('click', (event) => { if (event.target === modal) closeModal(false); });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        if (!menuEl.hidden) { closeMenu(); return; }
        if (!modal.hidden) { closeModal(false); return; }
      }
    });
    document.addEventListener('mousedown', (event) => {
      if (!menuEl.hidden && !menuEl.contains(event.target)) closeMenu();
    });
    window.addEventListener('resize', closeMenu);
    window.addEventListener('scroll', closeMenu, true);
  }
  async function runSearch(term) {
    if (!S.path) return toast('请先进入一个文件夹再搜索', true);
    S.query = term;
    listEl.setAttribute('aria-busy', 'true');
    try {
      const data = await api('/api/explorer/search?path=' + encodeURIComponent(S.path) + '&q=' + encodeURIComponent(term));
      S.entries = data.entries || [];
      S.selected = new Set(); S.anchor = S.entries.length ? 0 : -1;
      renderRows(); paintSelection();
      statusText.textContent = '在 ' + (S.path || '此电脑') + ' 中搜索“' + term + '” · '
        + S.entries.length + ' 个结果' + (data.truncated ? '（已截断）' : '');
    } catch (error) {
      toast('搜索失败：' + error.message, true);
    } finally {
      listEl.setAttribute('aria-busy', 'false');
    }
  }

  // -- 启动 -------------------------------------------------------------
  async function boot() {
    const savedView = recall(STORE.view, 'list');
    S.view = savedView === 'grid' ? 'grid' : 'list';
    const savedSort = recall(STORE.sort, 'name:1').split(':');
    S.sort = { key: ['name', 'date', 'size', 'type'].includes(savedSort[0]) ? savedSort[0] : 'name',
      dir: savedSort[1] === '-1' ? -1 : 1 };
    $('sortBtn').textContent = ({ name: '名称', date: '日期', size: '大小', type: '类型' })[S.sort.key]
      + (S.sort.dir > 0 ? ' ↑' : ' ↓');
    bind();
    try {
      await loadInfo();
    } catch (error) {
      statusText.textContent = '无法读取文件服务：' + error.message;
      return;
    }
    const params = new URLSearchParams(location.search);
    const requested = params.get('path');
    let start = requested || recall(STORE.last, '');
    if (!requested && !start) start = S.info.start || '';
    await navigate(start || '');
  }
  boot();
})();
