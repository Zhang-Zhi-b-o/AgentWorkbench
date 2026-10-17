/*!
 * Agent Hub — touch interaction layer for the embedded Zotero reader.
 *
 * 为什么在阅读器里做而不是改 App：官方 reader 的 chrome 是桌面假设（见
 * mobile.css 顶部说明），而 App 只在 iframe onLoad 时注入一次 ≤700px 的补丁。
 *
 * 结构上有个必须注意的事实：正文 PDF 跑在 reader 内部的**嵌套 iframe** 里
 * （`_pdfView._iframeWindow.PDFViewerApplication`），所以顶层 reader 文档的
 * `document.getSelection()` 看不到 PDF 选区，`window.PDFViewerApplication` 也
 * 不存在。这一层因此按两条路取选区：
 *
 *   1. 主路径——reader 的插件钩子 `customEvent` / `renderTextSelectionPopup`：
 *      它把文本、页面位置和一个同步 `append()` 交给我们（App 用的同一个钩子）。
 *   2. 兜底——自己找到嵌套 viewer 帧，监听它文档里的选区变化，手动把矩形换算
 *      到顶层坐标后浮一个气泡。
 *
 * 结果就地出卡片，不再跳 App 的 textarea 面板。
 */
(function () {
	'use strict';

	var TRANSLATE_URL = '/api/zotero/translate';
	var MAX_TEXT = 12000;
	// 与 agenthub/config.py 的 SHARED_HEADER/SHARED_HEADER_VALUE 一致。
	var MUTATION_HEADERS = { 'Content-Type': 'application/json', 'X-Agent-Hub': '1' };
	var isTouch = window.matchMedia('(pointer: coarse)').matches || 'ontouchstart' in window;
	/** 选区弹层出现后，在这段时间内不再触发自己的兜底气泡。 */
	var POPUP_GRACE = 900;

	/* ── 父页面（App 外壳）补丁 ─────────────────────────────────────── */

	var PARENT_CSS = [
		'@media (max-width:1024px),(pointer:coarse){',
		'.pdf-reader.official-reader{display:flex!important;flex-direction:column;height:100dvh!important;min-height:0!important}',
		'.pdf-heading{min-height:48px;padding:0 6px;gap:6px}',
		'.pdf-heading .reader-icon{display:inline-flex;align-items:center;justify-content:center;min-width:44px;min-height:44px;touch-action:manipulation}',
		'.official-reader-frame{flex:1 1 auto!important;height:auto!important;min-height:0!important;border:0}',
		'.official-reader-actions{display:flex;flex-wrap:wrap;gap:6px;padding:6px 8px calc(6px + env(safe-area-inset-bottom,0px))}',
		'.official-reader-actions button{min-height:44px;min-width:44px;font-size:15px;touch-action:manipulation}',
		'.translation-panel{position:fixed!important;inset:auto 0 0 0!important;max-height:70dvh!important;overflow:auto;z-index:80;border-radius:14px 14px 0 0;padding-bottom:calc(8px + env(safe-area-inset-bottom,0px))}',
		'.translation-panel textarea,.translation-panel select,.translation-panel input{font-size:16px!important}',
		'.translation-panel button{min-height:44px}',
		'}',
	].join('');

	/**
	 * 把触控排版注入承载 App 的父文档。同源时才可行；跨源嵌入时静默跳过，
	 * 阅读器本身不受影响（iframe 内由 mobile.css 兜住）。
	 */
	function patchParentShell() {
		try {
			var host = window.parent;
			if (!host || host === window) return;
			var doc = host.document;
			if (!doc || doc.getElementById('ah-touch-parent')) return;
			var style = doc.createElement('style');
			style.id = 'ah-touch-parent';
			style.textContent = PARENT_CSS;
			(doc.head || doc.documentElement).append(style);
		} catch (error) {
			/* 跨源或已卸载：忽略 */
		}
	}

	/* ── DOM 组件 ──────────────────────────────────────────────────── */

	var bubble = null;
	var card = null;
	var cardBody = null;
	var cardFoot = null;
	var cardTitle = null;
	var state = { open: false, text: '', position: null, lastPopupAt: 0 };

	function element(tag, attributes, children) {
		var node = document.createElement(tag);
		Object.keys(attributes || {}).forEach(function (name) {
			if (name === 'text') node.textContent = attributes[name];
			else node.setAttribute(name, attributes[name]);
		});
		(children || []).forEach(function (child) {
			node.append(child);
		});
		return node;
	}

	function buildBubble() {
		if (bubble) return bubble;
		bubble = element('div', { id: 'ah-bubble', role: 'toolbar', 'aria-label': '划词翻译' }, [
			element('button', { type: 'button', id: 'ah-bubble-translate', text: '译' }),
			element('button', { type: 'button', id: 'ah-bubble-page', text: '本页' }),
		]);
		// pointerdown 必须 preventDefault：否则 iOS 会先收起选区，click 时文本已丢。
		bubble.addEventListener('pointerdown', function (event) {
			event.preventDefault();
		});
		bubble.querySelector('#ah-bubble-translate').addEventListener('click', function (event) {
			event.preventDefault();
			translateSelection(false);
		});
		bubble.querySelector('#ah-bubble-page').addEventListener('click', function (event) {
			event.preventDefault();
			translateCurrentPage();
		});
		document.body.append(bubble);
		return bubble;
	}

	function buildCard() {
		if (card) return card;
		cardTitle = element('strong', { text: '翻译' });
		cardBody = element('div', { class: 'ah-card-body' });
		cardFoot = element('div', { class: 'ah-card-foot' });

		var copy = element('button', { type: 'button', text: '复制' });
		copy.addEventListener('click', function () {
			var text = cardBody.textContent || '';
			if (!text) return;
			if (navigator.clipboard && navigator.clipboard.writeText) {
				navigator.clipboard.writeText(text).catch(function () {});
				return;
			}
			var helper = document.createElement('textarea');
			helper.value = text;
			helper.setAttribute('readonly', 'readonly');
			helper.style.position = 'fixed';
			helper.style.opacity = '0';
			document.body.append(helper);
			helper.select();
			try {
				document.execCommand('copy');
			} catch (error) {
				/* 复制失败不影响阅读 */
			}
			helper.remove();
		});

		var again = element('button', { type: 'button', text: '重译' });
		again.addEventListener('click', function () {
			var selection = currentSelection();
			if (selection) run(selection.text, positionOf(selection.range), selection.rect, true);
			else if (state.text) run(state.text, state.position, selectionAnchor(), true);
		});

		var close = element('button', { type: 'button', 'aria-label': '关闭翻译', text: '关闭' });
		close.addEventListener('click', hideAll);

		card = element('div', { id: 'ah-card', role: 'dialog', 'aria-label': '翻译结果' }, [
			element('div', { class: 'ah-card-head' }, [
				cardTitle,
				element('div', { class: 'ah-card-actions' }, [copy, again, close]),
			]),
			cardBody,
			cardFoot,
		]);
		card.addEventListener('pointerdown', function (event) {
			event.stopPropagation();
		});
		document.body.append(card);
		return card;
	}

	/* ── 视口与定位 ────────────────────────────────────────────────── */

	/** 可视视口；iOS 上要用 visualViewport 才不会被地址栏/键盘算错。 */
	function viewport() {
		var view = window.visualViewport;
		if (view) return { top: view.offsetTop, left: view.offsetLeft, width: view.width, height: view.height };
		return { top: 0, left: 0, width: window.innerWidth, height: window.innerHeight };
	}

	/** 把 fixed 元素放在 anchor 上方；上方放不下就翻到下方，并夹在可视区内。 */
	function place(node, anchor) {
		var view = viewport();
		var box = node.getBoundingClientRect();
		var gap = 8;
		var left = anchor.left + (anchor.width || 0) / 2 - box.width / 2;
		left = Math.max(view.left + 8, Math.min(left, view.left + view.width - box.width - 8));
		var top = anchor.top - box.height - gap;
		if (top < view.top + 8) {
			top = Math.min(anchor.top + (anchor.height || 0) + gap, view.top + view.height - box.height - 8);
		}
		node.style.left = Math.round(left) + 'px';
		node.style.top = Math.round(Math.max(view.top + 8, top)) + 'px';
	}

	/* ── 嵌套 viewer 帧（PDF 正文所在文档）───────────────────────────── */

	var frame = { node: null, doc: null };

	/** 找出当前可用的 PDF viewer 帧；reader 是异步创建它的。 */
	function locateFrame() {
		var candidates = document.querySelectorAll(
			'#primary-view iframe, #secondary-view iframe, .primary-view iframe, .secondary-view iframe',
		);
		var visible = null;
		for (var index = 0; index < candidates.length; index += 1) {
			var node = candidates[index];
			if (!node) continue;
			if (visible === null) visible = node;
			try {
				if (node.contentDocument && node.contentDocument.body) visible = node;
			} catch (error) {
				/* 跨源帧：跳过 */
			}
		}
		return visible;
	}

	/** 帧内矩形换算到顶层视口坐标（帧自身的 border/padding 视作 0）。 */
	function toViewport(rect, node) {
		var box = node.getBoundingClientRect();
		return {
			left: box.left + rect.left,
			top: box.top + rect.top,
			width: rect.width,
			height: rect.height,
		};
	}

	/** 在一个文档里取当前选区（折叠/空/无几何时返回 null）。 */
	function selectionIn(doc) {
		if (!doc || !doc.getSelection) return null;
		var selection = doc.getSelection();
		if (!selection || selection.rangeCount === 0 || selection.isCollapsed) return null;
		var text = String(selection.toString() || '').trim();
		if (!text) return null;
		var range = selection.getRangeAt(0);
		var rect = range.getBoundingClientRect();
		if (!rect || (rect.width === 0 && rect.height === 0)) return null;
		return { text: text, rect: rect, range: range, doc: doc };
	}

	/** 当前选区：先看嵌套 PDF 帧，再看 reader 自身文档。 */
	function currentSelection() {
		var nested = selectionIn(frame.doc);
		if (nested) {
			nested.rect = toViewport(nested.rect, frame.node);
			return nested;
		}
		return selectionIn(document);
	}

	/**
	 * 把选区换算成服务端的缓存位置键：页号 + 页内归一化矩形。
	 * 只用于缓存命中判断（同一处重新框选仍然命中），坐标空间自洽即可。
	 */
	function positionOf(range) {
		if (!range) return null;
		try {
			var node = range.startContainer;
			var element = node && node.nodeType === 1 ? node : node && node.parentElement;
			var page = element && element.closest && element.closest('[data-page-number]');
			if (!page) return null;
			var pageNumber = Number(page.getAttribute('data-page-number'));
			if (!isFinite(pageNumber) || pageNumber < 1) return null;
			var pageRect = page.getBoundingClientRect();
			if (!pageRect.width || !pageRect.height) return null;
			var rects = Array.prototype.slice.call(range.getClientRects()).map(function (rect) {
				return [
					(rect.left - pageRect.left) / pageRect.width,
					(rect.top - pageRect.top) / pageRect.height,
					(rect.right - pageRect.left) / pageRect.width,
					(rect.bottom - pageRect.top) / pageRect.height,
				].map(function (value) {
					return Math.round(value * 10000) / 10000;
				});
			});
			if (!rects.length) return null;
			return { pageIndex: pageNumber - 1, rects: rects };
		} catch (error) {
			return null;
		}
	}

	/** viewer 的 PDFViewerApplication；正文在嵌套帧里时从帧上取。 */
	function viewerApplication() {
		try {
			if (frame.node && frame.node.contentWindow && frame.node.contentWindow.PDFViewerApplication) {
				return frame.node.contentWindow.PDFViewerApplication;
			}
		} catch (error) {
			/* 跨源 */
		}
		return window.PDFViewerApplication || null;
	}

	/* ── 翻译 ──────────────────────────────────────────────────────── */

	/** 当前 PDF 的 Zotero 附件 key：服务端靠它算跟桌面端一致的缓存指纹。 */
	function itemKeyOf() {
		try {
			var app = viewerApplication();
			var match = /\/api\/zotero\/files\/([A-Z0-9]{8})/.exec(String((app && app.url) || ''));
			return match ? match[1] : '';
		} catch (error) {
			return '';
		}
	}

	/** 调后端翻译：服务端复用 Translate for Zotero 的配置，密钥不出本机。 */
	function requestTranslation(text, position, force) {
		var payload = { text: text, target: 'zh' };
		var itemKey = itemKeyOf();
		if (itemKey) payload.itemKey = itemKey;
		if (position) payload.position = position;
		if (force) payload.force = true;
		return fetch(TRANSLATE_URL, {
			method: 'POST',
			credentials: 'same-origin',
			headers: MUTATION_HEADERS,
			body: JSON.stringify(payload),
		}).then(function (response) {
			return response.json().then(function (body) {
				if (!response.ok) {
					var error = new Error((body && body.error) || '翻译失败');
					error.code = body && body.code;
					throw error;
				}
				return body;
			});
		});
	}

	/** 打开卡片并跑一次翻译。 */
	function run(text, position, anchor, force) {
		var trimmed = String(text || '').trim();
		if (!trimmed) return;
		if (trimmed.length > MAX_TEXT) trimmed = trimmed.slice(0, MAX_TEXT);
		buildCard();
		hideBubble();
		state.open = true;
		state.text = trimmed;
		state.position = position || null;
		cardTitle.textContent = '翻译';
		cardBody.dataset.state = 'loading';
		cardBody.textContent = trimmed.slice(0, 180) + (trimmed.length > 180 ? '…' : '');
		cardFoot.textContent = '正在翻译…';
		card.dataset.open = 'true';
		place(card, anchor || selectionAnchor());
		requestTranslation(trimmed, state.position, force).then(
			function (result) {
				cardBody.dataset.state = 'done';
				cardBody.textContent = result.translation || '';
				var parts = [];
				if (result.model) parts.push('模型 ' + result.model);
				if (result.cached) parts.push('已复用之前的结果');
				cardFoot.textContent = parts.join(' · ');
			},
			function (error) {
				cardBody.dataset.state = 'error';
				cardBody.textContent = (error && error.message) || '翻译失败';
				cardFoot.textContent = '服务来自 Zotero 翻译插件配置';
			},
		);
	}

	function translateSelection(force) {
		var selection = currentSelection();
		if (!selection) {
			hideAll();
			return;
		}
		run(selection.text, positionOf(selection.range), selection.rect, force);
	}

	/**
	 * 整页翻译：问嵌套 viewer 要当前页的文字层。
	 * 扫描版 PDF 没有文字层，这里明确报出来而不是给空结果。
	 */
	function translateCurrentPage() {
		hideBubble();
		buildCard();
		var application = viewerApplication();
		if (!application || !application.pdfDocument) {
			card.dataset.open = 'true';
			cardBody.dataset.state = 'error';
			cardBody.textContent = 'PDF 还没有加载完成，请稍后再试。';
			cardFoot.textContent = '';
			place(card, selectionAnchor());
			return;
		}
		var pageNumber = application.pdfViewer.currentPageNumber;
		application.pdfDocument
			.getPage(pageNumber)
			.then(function (page) {
				return page.getTextContent();
			})
			.then(function (content) {
				var text = content.items
					.map(function (item) {
						return item.str || '';
					})
					.join(' ')
					.replace(/\s+/g, ' ')
					.trim();
				if (!text) throw new Error('本页没有文字层，可能是扫描版 PDF。');
				run(text, null, selectionAnchor(), true);
			})
			.catch(function (error) {
				card.dataset.open = 'true';
				cardBody.dataset.state = 'error';
				cardBody.textContent = (error && error.message) || '无法读取本页文字';
				cardFoot.textContent = '';
				place(card, selectionAnchor());
			});
	}

	/* ── 显隐 ──────────────────────────────────────────────────────── */

	/** 没有现成矩形时的兜底锚点：视口中间偏上。 */
	function selectionAnchor() {
		var selection = currentSelection();
		if (selection) return selection.rect;
		var view = viewport();
		return { left: view.left + view.width / 2, top: view.top + view.height / 3, width: 0, height: 0 };
	}

	function showBubble(anchor) {
		buildBubble();
		bubble.dataset.open = 'true';
		place(bubble, anchor);
	}

	function hideBubble() {
		if (bubble) bubble.dataset.open = 'false';
	}

	function hideAll() {
		hideBubble();
		if (card) card.dataset.open = 'false';
		state.open = false;
	}

	/* ── 事件接线 ──────────────────────────────────────────────────── */

	/**
	 * 主路径：reader 渲染选区弹层时派发 customEvent，`detail.append()` 必须
	 * 同步调用。往里塞一个 44px 的「译」。
	 */
	window.addEventListener('customEvent', function (event) {
		if (!isTouch) return; // 桌面继续用 App 的「翻译」按钮 + 面板，不重复
		var detail = event && event.detail;
		if (!detail || detail.type !== 'renderTextSelectionPopup') return;
		state.lastPopupAt = Date.now();
		var params = detail.params || {};
		var annotation = params.annotation || {};
		var text = String(annotation.text || '').trim();
		if (!text || typeof detail.append !== 'function') return;
		var position = annotation.position || null;
		var button = element('button', {
			type: 'button',
			class: 'toolbar-button ah-translate-trigger',
			'aria-label': '翻译选中文字',
			text: '译',
		});
		button.addEventListener('pointerdown', function (pointerEvent) {
			pointerEvent.preventDefault();
			pointerEvent.stopPropagation();
		});
		button.addEventListener('click', function (clickEvent) {
			clickEvent.preventDefault();
			clickEvent.stopPropagation();
			run(text, position, button.getBoundingClientRect());
		});
		try {
			detail.append(button);
		} catch (error) {
			/* 钩子实现不一致：退回自绘气泡，不抛给阅读器 */
		}
	});

	/** reader 自己的选区弹层是否已经在屏幕上。 */
	function popupPresent() {
		var nodes = document.querySelectorAll('.selection-popup, .annotation-popup');
		for (var index = 0; index < nodes.length; index += 1) {
			var node = nodes[index];
			if (node && node.getBoundingClientRect && node.getBoundingClientRect().height > 3) return true;
		}
		return false;
	}

	/* ── 兜底：自己盯嵌套帧的选区 ──────────────────────────────────── */

	var fallbackTimer = null;

	function scheduleFallback() {
		if (!isTouch) return;
		if (fallbackTimer) clearTimeout(fallbackTimer);
		fallbackTimer = setTimeout(function () {
			if (state.open) return;
			if (Date.now() - state.lastPopupAt < POPUP_GRACE) return;
			// reader 自己的弹层已经出现：交给它（里面有我们注入的「译」）。
			if (popupPresent()) return;
			var selection = currentSelection();
			if (!selection) return;
			showBubble(selection.rect);
		}, 420);
	}

	var bound = { doc: null, handlers: [] };

	/** 把兜底监听挂到嵌套帧的文档上；帧重建时会重新挂。 */
	function attachFrame() {
		var node = locateFrame();
		if (!node) return;
		var doc = null;
		try {
			doc = node.contentDocument;
		} catch (error) {
			return; // 跨源：只能靠 customEvent 主路径
		}
		if (!doc) return;
		if (frame.node === node && bound.doc === doc) return;
		detachFrame();
		frame.node = node;
		frame.doc = doc;
		bound.doc = doc;
		['pointerup', 'touchend', 'selectionchange', 'keyup'].forEach(function (name) {
			var handler = function () {
				scheduleFallback();
			};
			doc.addEventListener(name, handler, true);
			bound.handlers.push([doc, name, handler]);
		});
	}

	function detachFrame() {
		bound.handlers.forEach(function (entry) {
			try {
				entry[0].removeEventListener(entry[1], entry[2], true);
			} catch (error) {
				/* 文档已销毁 */
			}
		});
		bound.handlers = [];
		bound.doc = null;
	}

	/** reader 异步创建/重建 viewer 帧，低频巡检即可。 */
	function watchFrames() {
		attachFrame();
		var ticks = 0;
		var timer = setInterval(function () {
			ticks += 1;
			attachFrame();
			if (frame.doc && ticks > 4) clearInterval(timer);
			if (ticks > 120) clearInterval(timer); // 约一分钟仍没有帧就停手
		}, 500);
	}

	// reader 自身文档上的选区（批注输入框等）也接一份，保证行为一致。
	['pointerup', 'touchend', 'selectionchange', 'keyup'].forEach(function (name) {
		document.addEventListener(name, scheduleFallback, true);
	});

	/* 点击组件之外：关掉气泡与卡片（组件内部已 stopPropagation）。 */
	document.addEventListener(
		'pointerdown',
		function (event) {
			var target = event.target;
			if (!target || (target.closest && target.closest('#ah-bubble,#ah-card'))) return;
			hideAll();
		},
		true,
	);

	/* 滚动/缩放/键盘会让锚点失效：收起，避免气泡挂在错位置。
	 * 卡片内部滚动不算（那是用户在读译文）。 */
	function onViewportChange(event) {
		var target = event && event.target;
		if (target && target.closest && target.closest('#ah-card')) return;
		hideAll();
	}

	document.addEventListener('scroll', onViewportChange, true);
	window.addEventListener('resize', onViewportChange);
	if (window.visualViewport) {
		window.visualViewport.addEventListener('resize', onViewportChange);
		window.visualViewport.addEventListener('scroll', onViewportChange);
	}
	document.addEventListener('keydown', function (event) {
		if (event.key === 'Escape') hideAll();
	});

	/* ── 启动 ──────────────────────────────────────────────────────── */

	function start() {
		// 诊断标记：方便在 Safari/Edge 里一眼确认触控层是否生效（也用于自动化冒烟）。
		document.documentElement.dataset.ahTouch = isTouch ? '1' : '0';
		patchParentShell();
		watchFrames();
	}

	if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, { once: true });
	else start();
})();
