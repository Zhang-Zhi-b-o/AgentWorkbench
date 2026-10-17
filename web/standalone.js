/* 插件独立页的外壳：工作台顶栏（插件标签 + 收起键）+ 占满的 iframe。
   宿主只在浏览器标签页导航到插件自己的地址时发这一页，所以 /vscode/、
   /plugins/zotero/ 这类地址单独打开，顶上也有跟工作台一样的那条栏，收起来
   同样只留 10px 手柄；插件的页面本身不动，只是被放进 iframe 里铺满剩下的高度。

   标签点击是整页导航：每个插件的地址都值得能直接收藏、右键新标签页打开，
   所以这里不做壳内换 iframe 那一套。 */
(() => {
  'use strict';
  const KEY = 'workbench.nav-collapsed';
  const $ = (id) => document.getElementById(id);
  const nav = $('navTabs'), navBox = $('pluginNav'), toggle = $('navToggle');
  const frame = $('pluginFrame'), views = $('pluginViews');
  const errorBox = $('viewError'), errorTitle = $('viewErrorTitle'), errorText = $('viewErrorText');
  let collapsed = readCollapsed();

  function readCollapsed() {
    try { return localStorage.getItem(KEY) === '1'; } catch (_) { return false; }
  }
  function saveCollapsed(value) {
    try { localStorage.setItem(KEY, value ? '1' : '0'); } catch (_) { /* 仅当前会话生效 */ }
  }
  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }
  function icon(name) {
    const paths = {
      bot: 'M12 8V5m-6 5h12a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-6a2 2 0 0 1 2-2Zm3 5h.01M15 13h.01',
      book: 'M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5V5.5Zm0 0V20a1 1 0 0 0 1 1h15M8 7h8M8 11h6',
      code: 'm8 8-4 4 4 4m8-8 4 4-4 4M13.5 5l-3 14',
      folder: 'M3 7.2A1.6 1.6 0 0 1 4.6 5.6h3.8l1.5 2.1H19a1.4 1.4 0 0 1 1.4 1.4v8.4A1.4 1.4 0 0 1 19 19H4.6A1.6 1.6 0 0 1 3 17.4V7.2Z',
      doc: 'M6 3h8l4 4v14H6V3Zm8 0v4h4M9 12h6M9 16h5',
      grid: 'M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z'
    };
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('aria-hidden', 'true');
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', paths[name] || paths.grid);
    svg.append(path);
    return svg;
  }
  // 地址里的尾部斜杠两种写法都算同一个入口（/vscode 与 /vscode/）。
  function bare(path) { return String(path || '').replace(/\/+$/, '') || '/'; }
  function usable(plugin) {
    return plugin.ready === true && (plugin.kind === 'iframe' || plugin.kind === 'link')
      && typeof plugin.entry === 'string' && plugin.entry.startsWith('/');
  }
  function nameOf(plugin) { return plugin.name && plugin.name.trim() ? plugin.name : plugin.id; }

  // 收起后整条顶栏收成手柄（见 app.css 的 .nav-collapsed），文案说清点它是展开。
  function applyCollapsed() {
    document.documentElement.classList.toggle('nav-collapsed', collapsed);
    toggle.setAttribute('aria-expanded', String(!collapsed));
    const label = collapsed ? '展开插件导航' : '收起插件导航';
    toggle.setAttribute('aria-label', label);
    toggle.title = label;
  }

  function showError(title, text) {
    views.hidden = true;
    frame.src = 'about:blank';
    errorTitle.textContent = title;
    errorText.textContent = text;
    errorBox.hidden = false;
    nav.replaceChildren(el('span', 'nav-error', '插件不可用'));
    navBox.setAttribute('aria-busy', 'false');
  }

  // 先按地址本身把插件挂上去：地址就是它的入口，不必等清单回来才开始加载。
  function mount(plugin) {
    const src = plugin.entry + location.search;
    if (frame.getAttribute('src') !== src) frame.src = src;
    frame.title = nameOf(plugin);
    document.title = nameOf(plugin) + ' · 工作台';
    views.hidden = false;
    errorBox.hidden = true;
  }

  function renderNav(plugins, current) {
    nav.replaceChildren();
    plugins.forEach((plugin) => {
      const tab = document.createElement('a');
      tab.className = 'nav-tab';
      tab.href = plugin.entry;
      tab.append(icon(plugin.icon), el('span', 'nav-label', nameOf(plugin)));
      tab.title = nameOf(plugin);
      if (plugin.id === current.id) tab.setAttribute('aria-current', 'page');
      nav.append(tab);
    });
    navBox.setAttribute('aria-busy', 'false');
  }

  toggle.addEventListener('click', () => {
    collapsed = !collapsed;
    saveCollapsed(collapsed);
    applyCollapsed();
  });
  applyCollapsed();

  // 立刻开工：当前地址就是插件的入口。内容区要先露出来再设 src —— 插件可能
  // 在载入时就量自己的视口（比如 Zotero 按宽度决定目录开合），藏在一个
  // display:none 的框里会量到 0 宽度，于是一上来就用错布局。
  views.hidden = false;
  errorBox.hidden = true;
  frame.src = location.pathname + location.search;
  document.title = '工作台';

  fetch('/api/plugins', { headers: { Accept: 'application/json' }, cache: 'no-store' })
    .then((response) => {
      if (!response.ok) throw new Error('HTTP ' + response.status);
      return response.json();
    })
    .then((data) => {
      const plugins = (data && Array.isArray(data.plugins) ? data.plugins : []).filter(usable);
      if (!plugins.length) return showError('没有可用插件', '服务返回的插件列表为空，回到工作台添加插件后刷新。');
      const here = bare(location.pathname);
      const current = plugins.find((plugin) => bare(plugin.entry) === here) || plugins[0];
      renderNav(plugins, current);
      mount(current);
    })
    .catch((error) => {
      showError('无法读取插件清单', '网络请求失败：' + (error && error.message ? error.message : error));
    });
})();
