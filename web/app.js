/* 工作台外壳逻辑：读取插件清单、哈希路由，以及按需挂载 iframe。 */
(() => {
  'use strict';

  const NAV_KEY = 'workbench.nav-collapsed';
  const homeView = document.getElementById('homeView');
  const pluginViews = document.getElementById('pluginViews');
  const navTabs = document.getElementById('navTabs');
  const navToggle = document.getElementById('navToggle');
  const navToggleLabel = document.getElementById('navToggleLabel');
  const pluginGrid = document.getElementById('pluginGrid');
  const pluginCount = document.getElementById('pluginCount');
  const homeEmpty = document.getElementById('homeEmpty');
  const loadError = document.getElementById('loadError');
  const loadErrorText = document.getElementById('loadErrorText');
  const retryPlugins = document.getElementById('retryPlugins');

  const state = {
    plugins: [],
    byId: new Map(),
    views: new Map(),
    activeId: null,
    loadState: 'loading'
  };
  let collapsed = readCollapsed();
  let homeScroll = 0;

  function readCollapsed() {
    try { return localStorage.getItem(NAV_KEY) === '1'; } catch (_) { return false; }
  }

  function saveCollapsed(value) {
    try { localStorage.setItem(NAV_KEY, value ? '1' : '0'); } catch (_) { /* 存储不可用时仅保留当前会话状态 */ }
  }

  function make(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function icon(name) {
    const paths = {
      bot: 'M12 8V5m-6 5h12a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-6a2 2 0 0 1 2-2Zm3 5h.01M15 13h.01M9 19v1m6-1v1',
      book: 'M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5V5.5Zm0 0V20a1 1 0 0 0 1 1h15M8 7h8M8 11h6',
      code: 'm8 8-4 4 4 4m8-8 4 4-4 4M13.5 5l-3 14',
      grid: 'M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z'
    };
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('aria-hidden', 'true');
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', paths[name] || paths.grid);
    svg.appendChild(path);
    return svg;
  }

  function isSupported(plugin) {
    return plugin.kind === 'iframe' || plugin.kind === 'link';
  }

  function entryUrl(entry) {
    if (typeof entry !== 'string' || !entry.trim()) return null;
    try {
      const url = new URL(entry, location.href);
      if (url.origin !== location.origin) return null;
      return url.pathname + url.search + url.hash;
    } catch (_) {
      return null;
    }
  }

  function isReady(plugin) {
    return plugin.ready === true && isSupported(plugin) && !!entryUrl(plugin.entry);
  }

  function displayName(plugin) {
    return typeof plugin.name === 'string' && plugin.name.trim() ? plugin.name : plugin.id;
  }

  function hashFor(id) {
    return '#' + encodeURIComponent(id);
  }

  function applyCollapsed() {
    document.documentElement.classList.toggle('nav-collapsed', collapsed);
    navToggle.setAttribute('aria-expanded', String(!collapsed));
    navToggleLabel.textContent = collapsed ? '展开导航' : '收起导航';
    navToggle.title = collapsed ? '展开插件导航' : '收起插件导航';
  }

  function renderNavSkeletons() {
    navTabs.replaceChildren();
    for (let i = 0; i < 3; i += 1) {
      const stub = make('span', i === 2 ? 'nav-skeleton short' : 'nav-skeleton');
      stub.setAttribute('aria-hidden', 'true');
      navTabs.appendChild(stub);
    }
    document.getElementById('pluginNav').setAttribute('aria-busy', 'true');
  }

  function renderCardSkeletons() {
    pluginGrid.replaceChildren();
    for (let i = 0; i < 3; i += 1) {
      const card = make('div', 'skeleton-card');
      card.setAttribute('aria-hidden', 'true');
      card.append(make('span'), make('i'), make('i'), make('i'));
      pluginGrid.appendChild(card);
    }
  }

  function setLoadingView() {
    loadError.hidden = true;
    homeEmpty.hidden = true;
    pluginGrid.hidden = false;
    pluginCount.textContent = '正在读取插件';
    pluginGrid.setAttribute('aria-busy', 'true');
    renderNavSkeletons();
    renderCardSkeletons();
    homeView.hidden = false;
    pluginViews.hidden = true;
    document.title = '工作台';
  }

  async function fetchManifest() {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 12000);
    try {
      const response = await fetch('/api/plugins', {
        headers: { Accept: 'application/json' },
        cache: 'no-store',
        signal: controller.signal
      });
      let data = null;
      try { data = await response.json(); } catch (_) { /* 由下方统一处理空响应 */ }
      if (!response.ok) {
        const detail = data && typeof data.error === 'string' ? data.error : '';
        const error = new Error(detail || ('HTTP ' + response.status));
        error.status = response.status;
        error.detail = detail;
        throw error;
      }
      if (!data || !Array.isArray(data.plugins)) throw new Error('返回数据缺少插件列表');
      return normalizePlugins(data.plugins);
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('请求超时，请稍后重试。');
      throw error;
    } finally {
      clearTimeout(timer);
    }
  }

  function normalizePlugins(raw) {
    const seen = new Set();
    return raw.filter((plugin) => {
      if (!plugin || typeof plugin.id !== 'string' || !plugin.id || seen.has(plugin.id)) return false;
      seen.add(plugin.id);
      return true;
    });
  }

  function showManifestError(error) {
    state.loadState = 'error';
    pluginGrid.setAttribute('aria-busy', 'false');
    document.getElementById('pluginNav').setAttribute('aria-busy', 'false');
    navTabs.replaceChildren(make('span', 'nav-error', '插件清单不可用'));
    pluginGrid.replaceChildren();
    pluginGrid.hidden = true;
    homeEmpty.hidden = true;
    loadError.hidden = false;
    let message;
    if (error.status) {
      message = '服务器返回 HTTP ' + error.status;
      if (error.detail) message += '：' + error.detail;
      else message += '。';
    } else {
      message = error.message || '网络请求失败，无法连接服务。';
    }
    loadErrorText.textContent = message;
    pluginCount.textContent = '插件清单未载入';
    homeView.hidden = false;
    pluginViews.hidden = true;
    document.title = '工作台';
  }

  function renderNav() {
    navTabs.replaceChildren();
    state.plugins.forEach((plugin) => {
      const ready = isReady(plugin);
      const name = displayName(plugin);
      const tab = document.createElement(ready ? 'a' : 'button');
      tab.className = 'nav-tab';
      tab.dataset.pluginId = plugin.id;
      tab.appendChild(icon(plugin.icon));
      tab.appendChild(make('span', 'nav-label', name));
      if (!ready) {
        tab.type = 'button';
        tab.disabled = true;
        tab.title = name + '：' + (isSupported(plugin) ? '未就绪' : '类型不支持');
        tab.setAttribute('aria-label', tab.title);
      } else if (plugin.kind === 'link') {
        tab.href = entryUrl(plugin.entry);
        tab.target = '_blank';
        tab.rel = 'noopener noreferrer';
        tab.title = '在新标签打开 ' + name;
      } else {
        tab.href = hashFor(plugin.id);
      }
      navTabs.appendChild(tab);
    });
    document.getElementById('pluginNav').setAttribute('aria-busy', 'false');
  }

  function renderCards() {
    pluginGrid.replaceChildren();
    const readyCount = state.plugins.filter(isReady).length;
    pluginCount.textContent = state.plugins.length + ' 个插件 · ' + readyCount + ' 个可用';
    homeEmpty.hidden = state.plugins.length > 0;
    pluginGrid.hidden = state.plugins.length === 0;
    pluginGrid.setAttribute('aria-busy', 'false');

    state.plugins.forEach((plugin, index) => {
      const ready = isReady(plugin);
      const name = displayName(plugin);
      const card = document.createElement(ready ? 'a' : 'div');
      card.className = 'plugin-card' + (ready ? '' : ' is-disabled');
      if (!ready) card.setAttribute('aria-disabled', 'true');
      else if (plugin.kind === 'link') {
        card.href = entryUrl(plugin.entry);
        card.target = '_blank';
        card.rel = 'noopener noreferrer';
      } else {
        card.href = hashFor(plugin.id);
      }

      const top = make('div', 'plugin-card-top');
      const badge = make('span', 'plugin-icon');
      badge.appendChild(icon(plugin.icon));
      top.append(badge, make('span', 'plugin-index', String(index + 1).padStart(2, '0')));
      card.append(top, make('h3', '', name));

      const description = typeof plugin.description === 'string' && plugin.description.trim()
        ? plugin.description
        : '这个插件还没有填写描述。';
      card.appendChild(make('p', 'plugin-desc', description));

      if (!ready) {
        card.appendChild(make('div', 'plugin-state', isSupported(plugin) ? '未就绪' : '暂不支持此类型'));
      } else {
        const action = make('div', 'plugin-action');
        const label = document.createElement('span');
        label.textContent = plugin.kind === 'link' ? '在新标签打开' : '进入插件';
        const kind = make('span', 'plugin-kind', plugin.kind === 'link' ? '外部工具' : '内嵌视图');
        action.append(label, kind, make('span', 'plugin-arrow', '→'));
        card.appendChild(action);
      }
      pluginGrid.appendChild(card);
    });
  }

  function markActive(id) {
    navTabs.querySelectorAll('.nav-tab').forEach((tab) => {
      if (tab.dataset.pluginId === id) tab.setAttribute('aria-current', 'page');
      else tab.removeAttribute('aria-current');
    });
  }

  function hideAllViews() {
    state.views.forEach((view) => {
      view.section.hidden = true;
      view.frame.hidden = true;
    });
  }

  function showHome() {
    homeView.hidden = false;
    pluginViews.hidden = true;
    hideAllViews();
    state.activeId = null;
    markActive(null);
    document.title = '工作台';
    homeView.scrollTop = homeScroll;
  }

  function ensurePluginView(plugin) {
    const existing = state.views.get(plugin.id);
    if (existing) return existing;

    const section = make('section', 'plugin-view');
    section.setAttribute('aria-label', displayName(plugin) + ' 插件');
    section.hidden = true;

    const frame = document.createElement('iframe');
    frame.className = 'plugin-frame';
    frame.title = displayName(plugin);
    frame.src = entryUrl(plugin.entry);
    frame.referrerPolicy = 'same-origin';
    frame.hidden = true;

    const status = make('div', 'frame-status');
    const spinner = make('span', 'spinner');
    spinner.setAttribute('aria-hidden', 'true');
    const statusText = make('span', '', '正在载入 ' + displayName(plugin) + '…');
    status.append(spinner, statusText);
    frame.addEventListener('load', () => { status.hidden = true; });
    frame.addEventListener('error', () => {
      spinner.hidden = true;
      statusText.textContent = displayName(plugin) + ' 页面载入失败';
    });

    section.append(frame, status);
    pluginViews.appendChild(section);
    const view = { section, frame, status, spinner, statusText };
    state.views.set(plugin.id, view);
    return view;
  }

  function showPlugin(plugin) {
    homeScroll = homeView.scrollTop;
    const view = ensurePluginView(plugin);
    homeView.hidden = true;
    pluginViews.hidden = false;
    state.views.forEach((item, id) => {
      const visible = id === plugin.id;
      item.section.hidden = !visible;
      item.frame.hidden = !visible;
    });
    state.activeId = plugin.id;
    markActive(plugin.id);
    document.title = displayName(plugin) + ' · 工作台';
    const activeTab = navTabs.querySelector('.nav-tab[data-plugin-id="' + cssEscape(plugin.id) + '"]');
    if (activeTab) activeTab.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    view.frame.focus({ preventScroll: true });
  }

  function cssEscape(value) {
    if (window.CSS && typeof window.CSS.escape === 'function') return window.CSS.escape(value);
    return String(value).replace(/["\\]/g, '\\$&');
  }

  function route() {
    if (state.loadState !== 'ready') return;
    let raw = location.hash.slice(1);
    try { raw = decodeURIComponent(raw); } catch (_) { raw = ''; }
    if (!raw || raw === 'home') {
      if (location.hash !== '#home') history.replaceState(null, '', '#home');
      showHome();
      return;
    }
    const plugin = state.byId.get(raw);
    if (!plugin || !isReady(plugin) || plugin.kind !== 'iframe') {
      history.replaceState(null, '', '#home');
      showHome();
      return;
    }
    showPlugin(plugin);
  }

  function loadPlugins() {
    state.loadState = 'loading';
    setLoadingView();
    fetchManifest().then((plugins) => {
      state.plugins = plugins;
      state.byId = new Map(plugins.map((plugin) => [plugin.id, plugin]));
      state.loadState = 'ready';
      renderNav();
      renderCards();
      route();
    }).catch((error) => {
      showManifestError(error);
    });
  }

  navToggle.addEventListener('click', () => {
    collapsed = !collapsed;
    saveCollapsed(collapsed);
    applyCollapsed();
  });

  retryPlugins.addEventListener('click', loadPlugins);
  window.addEventListener('hashchange', route);

  applyCollapsed();
  loadPlugins();
})();
