/**
 * dsh-workbench-open — browser half.
 *
 * Two compact pills in the session header: “文件” and “VS Code”.  Each opens the
 * AgentWorkbench in a NEW browser tab, rooted at the session's workspace
 * directory, so a remote browser gets the same file browser and editor the
 * desktop “Open In…” menu used to hand to local applications.
 *
 * Where the workbench lives is a host fact (`GET /workbench-open/config`): the
 * loopback origin for a local browser, the private reverse-proxy origin for a
 * browser that reached DSH through it.  An explicit `localStorage` override
 * (`dsh.workbench.origin`) wins over both.
 *
 * @module dsh-workbench-open/client
 */
window.__ModuleLoader__.load({
	id: 'dsh-workbench-open',
	factory: function (require) {
		var module = { exports: {} };
		var exports = module.exports;
		Object.defineProperty(exports, Symbol.toStringTag, { value: 'Module' });
		var React = require('react');

		var CSS = [
			'#dsh-workbench-open{display:inline-flex;align-items:center;gap:4px}',
			'#dsh-workbench-open .wbo-btn{display:inline-flex;align-items:center;gap:5px;height:28px;padding:0 9px;',
			'border:.5px solid var(--dsw-alias-border-l4,rgba(0,0,0,.14));border-radius:14px;background:transparent;',
			'color:var(--dsw-alias-label-primary,#1b1b1c);font-family:var(--dsw-font-family,system-ui,sans-serif);',
			'font-size:11px;line-height:16px;white-space:nowrap;cursor:pointer;box-sizing:border-box}',
			'#dsh-workbench-open .wbo-btn:hover{background:var(--dsw-alias-interactive-bg-hover,rgba(0,0,0,.06))}',
			'#dsh-workbench-open .wbo-btn:focus-visible{outline:2px solid var(--dsw-alias-state-accent-primary,#3b82f6);outline-offset:1px}',
			'#dsh-workbench-open svg{width:14px;height:14px;flex:none;fill:none;stroke:currentColor;stroke-width:1.7;',
			'stroke-linecap:round;stroke-linejoin:round}',
		].join('');

		var CONFIG_URL = '/workbench-open/config';
		var STORE_KEY = 'dsh.workbench.origin';
		var DEFAULT_ORIGIN = 'http://127.0.0.1:8768';
		var EXPLORER_PATH = '/plugins/explorer/';
		var VSCODE_PATH = '/vscode/';

		function injectCss() {
			if (typeof document === 'undefined') return;
			if (document.querySelector('style[data-plugin="dsh-workbench-open"]') !== null) return;
			var tag = document.createElement('style');
			tag.dataset.plugin = 'dsh-workbench-open';
			tag.textContent = CSS;
			document.head.appendChild(tag);
		}

		var configPromise = null;
		function loadConfig() {
			if (configPromise === null) {
				configPromise = fetch(CONFIG_URL, {
					headers: { accept: 'application/json' },
					cache: 'no-store',
				})
					.then(function (response) {
						return response.ok ? response.json() : null;
					})
					.catch(function () {
						return null;
					});
			}
			return configPromise;
		}

		function storedOrigin() {
			try {
				return localStorage.getItem(STORE_KEY) || '';
			} catch (_) {
				return '';
			}
		}

		/** Loopback for a local browser, the private origin for a remote one. */
		function pickOrigin(config) {
			var override = storedOrigin().trim();
			if (override) return override.replace(/\/+$/, '');
			if (!config) return DEFAULT_ORIGIN;
			var remote = String(config.remote || '').replace(/\/+$/, '');
			if (remote) {
				var remotePort = String(config.remoteDshPort || '');
				if (location.protocol === 'https:' || (remotePort && String(location.port) === remotePort)) {
					return remote;
				}
			}
			return String(config.loopback || DEFAULT_ORIGIN).replace(/\/+$/, '');
		}

		/**
		 * The web workbench resolves `?folder=` against its remote authority: only
		 * a value that starts with "/" becomes `vscode-remote://<authority><path>`.
		 * A bare `D:\dir` keeps scheme `d` (dropping the drive) and `file:///D:/dir`
		 * names a scheme the remote server does not serve; either way the editor
		 * opens with an empty explorer and "Workspace does not exist".  A Windows
		 * drive is spelled `/d:/dir` remotely -- leading slash, forward slashes,
		 * lower-case letter -- which is what the workbench itself emits.
		 */
		function folderParam(path) {
			var text = String(path || '').replace(/\\/g, '/');
			if (/^[A-Za-z]:/.test(text)) return '/' + text.charAt(0).toLowerCase() + text.slice(1);
			if (text.slice(0, 2) === '//') return text;
			return '/' + text.replace(/^\/+/, '');
		}

		function targetUrl(origin, kind, cwd) {
			if (kind === 'vscode') {
				return origin + VSCODE_PATH + '?folder=' + encodeURIComponent(folderParam(cwd));
			}
			return origin + EXPLORER_PATH + '?path=' + encodeURIComponent(cwd);
		}

		function FolderIcon() {
			return React.createElement(
				'svg',
				{ viewBox: '0 0 24 24', 'aria-hidden': true },
				React.createElement('path', {
					d: 'M3 7.2A1.6 1.6 0 0 1 4.6 5.6h3.8l1.5 2.1H19a1.4 1.4 0 0 1 1.4 1.4v8.4A1.4 1.4 0 0 1 19 19H4.6A1.6 1.6 0 0 1 3 17.4V7.2Z',
				}),
			);
		}

		function CodeIcon() {
			return React.createElement(
				'svg',
				{ viewBox: '0 0 24 24', 'aria-hidden': true },
				React.createElement('path', { d: 'm8 8-4 4 4 4m8-8 4 4-4 4M13.5 5l-3 14' }),
			);
		}

		/**
		 * Session-header entry point: two buttons that need only the session cwd.
		 * @param props - session runtime plus the injected session hook.
		 */
		function WorkbenchOpenAction(props) {
			var sessionId = props.sessionId;
			var useSessions = props.useSessions;
			var cwd = useSessions
				? useSessions(function (state) {
						var session = state && state.byId ? state.byId[sessionId] : undefined;
						return session ? session.cwd : undefined;
					})
				: undefined;
			var state = React.useState(null);
			var config = state[0];
			var setConfig = state[1];
			React.useEffect(function () {
				var alive = true;
				loadConfig().then(function (value) {
					if (alive && value) setConfig(value);
				});
				return function () {
					alive = false;
				};
			}, []);
			if (!cwd) return null;
			var origin = pickOrigin(config);
			function open(kind) {
				window.open(targetUrl(origin, kind, cwd), '_blank', 'noopener,noreferrer');
			}
			function button(kind, label, title, icon) {
				return React.createElement(
					'button',
					{
						type: 'button',
						className: 'wbo-btn',
						title: title,
						'aria-label': title,
						onClick: function () {
							open(kind);
						},
					},
					icon,
					React.createElement('span', null, label),
				);
			}
			return React.createElement(
				'div',
				{ id: 'dsh-workbench-open' },
				button('explorer', '文件', '在新标签页用工作台打开这个工作区', React.createElement(FolderIcon)),
				button('vscode', 'VS Code', '在新标签页用工作台的 VS Code 打开这个工作区', React.createElement(CodeIcon)),
			);
		}

		/** Services required by the header-slot contribution. */
		var inject = ['sessions', 'slots'];

		/**
		 * Client plugin body: register the header pills.
		 * @param ctx - client root context.
		 */
		function apply(ctx) {
			injectCss();
			ctx.effect(
				function () {
					return ctx.slots.inject('conversation.session.header.utilities', function () {
						return ctx.slots.register(
							{
								name: 'conversation.session.header.utilities',
								id: 'workbench-open',
								order: -9,
							},
							WorkbenchOpenAction,
						);
					});
				},
				'workbench-open: session header entry',
			);
		}

		exports.apply = apply;
		exports.inject = inject;
		return module.exports;
	},
});
