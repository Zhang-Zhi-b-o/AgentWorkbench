/**
 * dsh-workbench-open — host half.
 *
 * One read-only route, `GET /workbench-open/config`, tells the browser where the
 * AgentWorkbench lives.  DSH itself does not know about that deployment, so the
 * origins arrive as environment from the workbench's `agents` plugin:
 *
 *   WORKBENCH_ORIGIN             loopback origin, e.g. http://127.0.0.1:8768
 *   WORKBENCH_REMOTE_ORIGIN      the private reverse-proxy origin, when configured
 *   WORKBENCH_DSH_PORT           loopback port this DSH listens on
 *   WORKBENCH_REMOTE_DSH_PORT    the reverse proxy's port for this DSH
 *
 * The browser half picks loopback or remote by looking at how it was reached.
 * Nothing here launches a process, and no secret is involved.
 *
 * @module dsh-workbench-open
 */
export const name = 'workbench-open'

/** Only the HTTP route carrier is needed. */
export const inject = ['webServer']

/** Single config route consumed by the browser half. */
const CONFIG_ROUTE = '/workbench-open/config'

function env(name, fallback) {
	const value = typeof process.env[name] === 'string' ? process.env[name].trim() : ''
	return value || fallback
}

function payload() {
	return {
		loopback: env('WORKBENCH_ORIGIN', 'http://127.0.0.1:8768'),
		remote: env('WORKBENCH_REMOTE_ORIGIN', ''),
		localDshPort: env('WORKBENCH_DSH_PORT', '3080'),
		remoteDshPort: env('WORKBENCH_REMOTE_DSH_PORT', '8770'),
		explorer: '/plugins/explorer/',
		vscode: '/vscode/',
	}
}

/**
 * Register the config route.
 * @param ctx - plugin context with the webServer service.
 */
export function apply(ctx) {
	ctx.effect(
		() =>
			ctx.webServer.register({
				kind: 'exact',
				path: CONFIG_ROUTE,
				handler: (req, res) => {
					if (req.method !== 'GET') {
						res.statusCode = 405
						res.setHeader('allow', 'GET')
						res.end()
						return
					}
					res.statusCode = 200
					res.setHeader('content-type', 'application/json; charset=utf-8')
					res.setHeader('cache-control', 'no-store')
					res.end(JSON.stringify(payload()))
				},
			}),
		`workbench-open: GET ${CONFIG_ROUTE}`,
	)
}
