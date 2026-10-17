/* 工作台外壳逻辑：读取插件清单、哈希路由，以及按需挂载 iframe。 */
(() => {
  'use strict';
  const KEY = 'workbench.nav-collapsed';
  const $ = (id) => document.getElementById(id);
  const nav = $('navTabs'), grid = $('pluginGrid'), views = $('pluginViews'), home = $('homeView');
  const count = $('pluginCount'), errorBox = $('loadError'), errorText = $('loadErrorText');
  const toggle = $('navToggle'), toggleLabel = $('navToggleLabel'), navBox = $('pluginNav');
  const S = { plugins: [], byId: new Map(), views: new Map(), loaded: false };
  let collapsed = readCollapsed(), homeScroll = 0;

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
  function entryOf(entry) {
    if (typeof entry !== 'string' || !entry.trim()) return null;
    try {
      const url = new URL(entry, location.href);
      return url.origin === location.origin ? url.pathname + url.search + url.hash : null;
    } catch (_) { return null; }
  }
  function supported(plugin) { return plugin.kind === 'iframe' || plugin.kind === 'link'; }
  function ready(plugin) { return plugin.ready === true && supported(plugin) && !!entryOf(plugin.entry); }
  function nameOf(plugin) { return typeof plugin.name === 'string' && plugin.name.trim() ? plugin.name : plugin.id; }
  function hashOf(id) { return '#' + encodeURIComponent(id); }

  function applyCollapsed() {
    document.documentElement.classList.toggle('nav-collapsed', collapsed);
    toggle.setAttribute('aria-expanded', String(!collapsed));
    toggleLabel.textContent = collapsed ? '展开导航' : '收起导航';
    toggle.title = collapsed ? '展开插件导航' : '收起插件导航';
  }
  function loadingView() {
    errorBox.hidden = true;
    count.textContent = '正在读取插件';
    grid.hidden = false;
    grid.setAttribute('aria-busy', 'true');
    navBox.setAttribute('aria-busy', 'true');
    nav.replaceChildren();
    for (let i = 0; i < 3; i += 1) {
      const stub = el('span', i === 2 ? 'nav-skeleton short' : 'nav-skeleton');
      stub.setAttribute('aria-hidden', 'true');
      nav.append(stub);
    }
    grid.replaceChildren();
    for (let i = 0; i < 3; i += 1) {
      const card = el('div', 'skeleton-card');
      card.setAttribute('aria-hidden', 'true');
      grid.append(card);
    }
    home.hidden = false;
    views.hidden = true;
    document.title = '工作台';
  }
  async function loadManifest() {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 12000);
    try {
      const response = await fetch('/api/plugins', {
        headers: { Accept: 'application/json' }, cache: 'no-store', signal: controller.signal
      });
      let data = null;
      try { data = await response.json(); } catch (_) { /* 交由状态码处理 */ }
      if (!response.ok) {
        const failure = new Error('HTTP ' + response.status);
        failure.status = response.status;
        failure.detail = data && typeof data.error === 'string' ? data.error : '';
        throw failure;
      }
      if (!data || !Array.isArray(data.plugins)) throw new Error('bad manifest');
      const seen = new Set();
      return data.plugins.filter((plugin) => {
        if (!plugin || typeof plugin.id !== 'string' || !plugin.id || seen.has(plugin.id)) return false;
        seen.add(plugin.id);
        return true;
      });
    } catch (error) {
      if (error.name === 'AbortError') {
        const timeout = new Error('请求超时');
        timeout.timeout = true;
        throw timeout;
      }
      throw error;
    } finally { clearTimeout(timer); }
  }
  function manifestError(error) {
    S.loaded = false;
    grid.setAttribute('aria-busy', 'false');
    navBox.setAttribute('aria-busy', 'false');
    nav.replaceChildren(el('span', 'nav-error', '插件清单不可用'));
    grid.replaceChildren();
    grid.hidden = true;
    errorBox.hidden = false;
    let message;
    if (error.status) message = '服务器返回 HTTP ' + error.status + (error.detail ? '：' + error.detail : '。');
    else if (error.timeout) message = '请求超时，请稍后重试。';
    else message = '网络请求失败，请检查连接后重试。';
    errorText.textContent = message;
    count.textContent = '插件清单未载入';
    home.hidden = false;
    views.hidden = true;
    document.title = '工作台';
  }
  function renderNav() {
    nav.replaceChildren();
    S.plugins.forEach((plugin) => {
      const name = nameOf(plugin), usable = ready(plugin);
      const tab = document.createElement(usable ? 'a' : 'button');
      tab.className = 'nav-tab';
      tab.dataset.pluginId = plugin.id;
      tab.append(icon(plugin.icon), el('span', 'nav-label', name));
      if (!usable) {
        tab.type = 'button';
        tab.disabled = true;
        tab.title = name + '：' + (supported(plugin) ? '未就绪' : '类型不支持');
        tab.setAttribute('aria-label', tab.title);
      } else if (plugin.kind === 'link') {
        tab.href = entryOf(plugin.entry);
        tab.target = '_blank';
        tab.rel = 'noopener noreferrer';
        tab.title = '在新标签打开 ' + name;
      } else {
        tab.href = hashOf(plugin.id);
      }
      nav.append(tab);
    });
    navBox.setAttribute('aria-busy', 'false');
  }
  function renderCards() {
    grid.replaceChildren();
    const usable = S.plugins.filter(ready).length;
    count.textContent = S.plugins.length + ' 个插件 · ' + usable + ' 个可用';
    grid.hidden = S.plugins.length === 0;
    $('homeEmpty').hidden = S.plugins.length > 0;
    grid.setAttribute('aria-busy', 'false');
    S.plugins.forEach((plugin) => {
      const canUse = ready(plugin), name = nameOf(plugin);
      const card = document.createElement(canUse ? 'a' : 'div');
      card.className = 'plugin-card' + (canUse ? '' : ' is-disabled');
      if (!canUse) card.setAttribute('aria-disabled', 'true');
      else if (plugin.kind === 'link') {
        card.href = entryOf(plugin.entry);
        card.target = '_blank';
        card.rel = 'noopener noreferrer';
      } else card.href = hashOf(plugin.id);
      const top = el('div', 'plugin-card-top');
      const badge = el('span', 'plugin-icon');
      badge.append(icon(plugin.icon));
      top.append(badge);
      card.append(top, el('h3', '', name));
      const description = typeof plugin.description === 'string' && plugin.description.trim()
        ? plugin.description : '这个插件还没有填写描述。';
      card.append(el('p', 'plugin-desc', description));
      if (!canUse) card.append(el('div', 'plugin-state', supported(plugin) ? '未就绪' : '暂不支持此类型'));
      else {
        const action = el('div', 'plugin-action');
        action.append(
          el('span', '', (plugin.kind === 'link' ? '在新标签打开' : '进入插件') + ' →'),
          el('span', 'plugin-kind', plugin.kind === 'link' ? '外部工具' : '内嵌视图')
        );
        card.append(action);
      }
      grid.append(card);
    });
  }
  function markActive(id) {
    nav.querySelectorAll('.nav-tab').forEach((tab) => {
      if (tab.dataset.pluginId === id) tab.setAttribute('aria-current', 'page');
      else tab.removeAttribute('aria-current');
    });
  }
  function hideViews() {
    S.views.forEach((view) => { view.section.hidden = true; view.frame.hidden = true; });
  }
  function showHome() {
    home.hidden = false;
    views.hidden = true;
    hideViews();
    markActive(null);
    document.title = '工作台';
    home.scrollTop = homeScroll;
  }
  function ensureView(plugin) {
    if (S.views.has(plugin.id)) return S.views.get(plugin.id);
    const section = el('section', 'plugin-view');
    section.setAttribute('aria-label', nameOf(plugin) + ' 插件');
    section.hidden = true;
    const frame = document.createElement('iframe');
    frame.className = 'plugin-frame';
    frame.title = nameOf(plugin);
    frame.src = entryOf(plugin.entry);
    frame.referrerPolicy = 'same-origin';
    frame.hidden = true;
    const status = el('div', 'frame-status');
    const spinner = el('span', 'spinner');
    spinner.setAttribute('aria-hidden', 'true');
    const label = el('span', '', '正在载入 ' + nameOf(plugin) + '…');
    status.append(spinner, label);
    frame.addEventListener('load', () => { status.hidden = true; });
    frame.addEventListener('error', () => {
      spinner.hidden = true;
      label.textContent = nameOf(plugin) + ' 页面载入失败';
    });
    section.append(frame, status);
    views.append(section);
    const view = { section, frame, status };
    S.views.set(plugin.id, view);
    return view;
  }
  function showPlugin(plugin) {
    homeScroll = home.scrollTop;
    ensureView(plugin);
    home.hidden = true;
    views.hidden = false;
    S.views.forEach((view, id) => {
      const visible = id === plugin.id;
      view.section.hidden = !visible;
      view.frame.hidden = !visible;
    });
    markActive(plugin.id);
    document.title = nameOf(plugin) + ' · 工作台';
  }
  function route() {
    if (!S.loaded) return;
    let raw = location.hash.slice(1);
    try { raw = decodeURIComponent(raw); } catch (_) { raw = ''; }
    if (!raw || raw === 'home') {
      if (location.hash !== '#home') history.replaceState(null, '', '#home');
      showHome();
      return;
    }
    const plugin = S.byId.get(raw);
    if (!plugin || !ready(plugin) || plugin.kind !== 'iframe') {
      history.replaceState(null, '', '#home');
      showHome();
      return;
    }
    showPlugin(plugin);
  }
  function loadPlugins() {
    S.loaded = false;
    loadingView();
    loadManifest().then((plugins) => {
      S.plugins = plugins;
      S.byId = new Map(plugins.map((plugin) => [plugin.id, plugin]));
      S.loaded = true;
      renderNav();
      renderCards();
      route();
    }).catch(manifestError);
  }

  toggle.addEventListener('click', () => {
    collapsed = !collapsed;
    saveCollapsed(collapsed);
    applyCollapsed();
  });
  $('retryPlugins').addEventListener('click', loadPlugins);
  window.addEventListener('hashchange', route);
  applyCollapsed();
  loadPlugins();
})();
