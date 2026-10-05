/** Static accessibility checks that Svelte's compiler cannot make. */

import { globSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const ui = fileURLToPath(new URL('..', import.meta.url));
let failures = 0;

function check(claim: string, held: boolean, detail = '') {
	if (held) console.log(`ok    ${claim}`);
	else {
		failures++;
		console.error(`FAIL  ${claim}${detail ? ` — ${detail}` : ''}`);
	}
}

const sources = globSync('src/**/*.svelte', { cwd: ui });
const overlays = sources.filter((path) => /drawer|palette|dock/i.test(path));
const trapPatterns: [RegExp, string][] = [
	[/<dialog\b/i, 'modal dialog'],
	[/aria-modal\s*=\s*(?:["'{]\s*)?true/i, 'aria-modal'],
	[/\binert(?:\s|=)/i, 'inert subtree'],
	[/\b(?:createFocusTrap|focusTrap|trapFocus)\b/i, 'focus-trap helper'],
	[/\.key\s*(?:===|==)\s*["']Tab["']/i, 'manual Tab capture']
];

check('a drawer or palette source exists to inspect', overlays.length > 0);
for (const path of overlays) {
	const source = readFileSync(`${ui}/${path}`, 'utf8')
		.replace(/<!--[\s\S]*?-->/g, '')
		.replace(/\/\*[\s\S]*?\*\//g, '');
	const found = trapPatterns
		.filter(([pattern]) => pattern.test(source))
		.map(([, name]) => name);
	check(`${path} does not trap focus`, found.length === 0, found.join(', '));
}

const css = readFileSync(`${ui}/src/app.css`, 'utf8');
function tokens(selector: string): Map<string, string> {
	const start = css.indexOf(selector);
	if (start < 0) return new Map();
	const open = css.indexOf('{', start);
	const close = css.indexOf('}', open);
	return new Map(
		[...css.slice(open + 1, close).matchAll(/--([\w-]+):\s*(#[\da-f]{6})\s*;/gi)].map(
			(match) => [match[1], match[2]]
		)
	);
}

function luminance(hex: string): number {
	const channels = [1, 3, 5].map((offset) => Number.parseInt(hex.slice(offset, offset + 2), 16) / 255);
	const linear = channels.map((value) =>
		value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4
	);
	return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
}

function contrast(left: string, right: string): number {
	const values = [luminance(left), luminance(right)].sort((a, b) => b - a);
	return (values[0] + 0.05) / (values[1] + 0.05);
}

const themes = [
	['dark', tokens(':root {')],
	['light', tokens(":root[data-theme='light'] {")]
] as const;
/* Every text token clears 4.5:1 on the ground and both panel tones (DS §10);
   --fnt is a ghost and never sets readable text, so it is not asserted. */
const foregrounds = ['tx', 'tx2', 'dim', 'l405', 'l436', 'l486', 'l546', 'l589', 'l615', 'l656'] as const;

for (const [theme, palette] of themes) {
	check(`${theme} colour tokens are readable`, palette.size > 0);
	for (const background of ['bg', 'p1', 'p2'] as const) {
		for (const foreground of foregrounds) {
			const left = palette.get(foreground);
			const right = palette.get(background);
			const ratio = left && right ? contrast(left, right) : 0;
			check(`${theme} ${foreground} on ${background} is at least 4.5:1`, ratio >= 4.5, `${ratio.toFixed(2)}:1`);
		}
	}
	const pri = contrast(palette.get('on-pri') ?? '#000000', palette.get('tx') ?? '#000000');
	check(`${theme} primary button text on its bone fill is at least 4.5:1`, pri >= 4.5, `${pri.toFixed(2)}:1`);
}

// Every icon-only action routes through IconButton, which tips and describes it.
{
	const source = readFileSync(`${ui}/src/lib/IconButton.svelte`, 'utf8');
	check('IconButton associates its tooltip with the button', source.includes('<Tooltip ') && source.includes('aria-describedby='));
}

// Optional real-browser regression, using the existing dev dependencies and screenshot browser.
// Run: node dev/a11y-check.ts --browser
if (process.argv.includes('--browser')) {
	const { createServer } = await import('vite');
	const { compile } = await import('svelte/compiler');
	const { svelte } = await import('@sveltejs/vite-plugin-svelte');
	const { spawn } = await import('node:child_process');
	const { mkdtempSync, rmSync, writeFileSync } = await import('node:fs');
	const { tmpdir } = await import('node:os');
	const fixture = `
		<script>
			import Tooltip from '/src/lib/Tooltip.svelte';
			import { ACTION_GLOSS, ACTION_WORD } from '/src/lib/interventions.ts';
			const descriptions = [
				...Object.entries(ACTION_GLOSS).map(([action, description]) => [ACTION_WORD[action], description]),
				['Salvage', 'model-consuming authoring from this run’s preserved evidence'],
				['Measure', 'Measuring resolves each added executable and runs one bounded --version; it writes nothing.'],
				['Copy', 'Save in your editor — this page follows the file.']
			];
		</script>
		<div style="position:fixed;right:0;bottom:0;width:min(290px,100vw);height:260px;overflow:auto;clip-path:inset(0);padding:8px">
			{#each descriptions as [label, description]}
				<div style="margin-bottom:8px"><Tooltip {description}>{#snippet children(id)}<button class="act plate" aria-describedby={id}>{label}</button>{/snippet}</Tooltip></div>
			{/each}
			<Tooltip description="Disabled measurement help" disabled label="Measure">
				{#snippet children(id)}<button class="act plate" aria-describedby={id} disabled>Measure</button>{/snippet}
			</Tooltip>
		</div>`;
	const entry = '/__tooltip-check.js';
	const server = await createServer({
		configFile: false,
		root: ui,
		resolve: { alias: { '$lib': `${ui}/src/lib` } },
		plugins: [svelte(), {
			name: 'tooltip-regression-fixture',
			resolveId(id) { if (id === entry) return '\0tooltip-check.js'; },
			load(id) {
				if (id !== '\0tooltip-check.js') return;
				return compile(fixture, { filename: 'TooltipFixture.svelte', generate: 'client' }).js.code
					+ `\nimport { mount } from 'svelte'; mount(TooltipFixture, { target: document.body });`;
			},
			configureServer(vite) {
				vite.middlewares.use((req, res, next) => {
					if (req.url !== '/__tooltip-check') return next();
					res.setHeader('Content-Type', 'text/html');
					res.end(`<html><head><link rel="stylesheet" href="/src/app.css"></head><body><script type="module" src="${entry}"></script></body></html>`);
				});
			}
		}],
		server: { host: '127.0.0.1', port: 0, open: false }
	});
	const profile = mkdtempSync(`${tmpdir()}/herdsman-tooltip-`);
	let browser: ReturnType<typeof spawn> | undefined;
	let socket: WebSocket | undefined;
	try {
		await server.listen();
		browser = spawn(process.env.BROWSER ?? 'brave', ['--headless', '--disable-gpu', '--no-first-run', `--user-data-dir=${profile}`, '--remote-debugging-port=0', 'about:blank']);
		const endpoint = await new Promise<string>((resolve, reject) => {
			let stderr = '';
			const timer = setTimeout(() => reject(new Error('browser debugging endpoint timed out')), 15000);
			browser!.on('error', reject);
			browser!.stderr!.on('data', (chunk) => {
				stderr += String(chunk);
				const match = stderr.match(/DevTools listening on (ws:\/\/\S+)/);
				if (match) { clearTimeout(timer); resolve(match[1]); }
			});
		});
		socket = new WebSocket(endpoint);
		await new Promise<void>((resolve, reject) => { socket!.onopen = () => resolve(); socket!.onerror = () => reject(new Error('CDP connection failed')); });
		let next = 0;
		const pending = new Map<number, (result: any) => void>();
		socket.onmessage = (event) => { const message = JSON.parse(String(event.data)); if (message.method === 'Runtime.exceptionThrown') console.error(JSON.stringify(message.params)); pending.get(message.id)?.(message); pending.delete(message.id); };
		const send = (method: string, params: object = {}, sessionId?: string): Promise<any> => new Promise((resolve, reject) => {
			const id = ++next;
			pending.set(id, (message) => message.error ? reject(new Error(message.error.message)) : resolve(message.result));
			socket!.send(JSON.stringify({ id, method, params, sessionId }));
		});
		const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
		const { sessionId } = await send('Target.attachToTarget', { targetId, flatten: true });
		const evaluate = async (expression: string) => {
			const result = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true }, sessionId);
			if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
			return result.result.value;
		};
		await send('Runtime.enable', {}, sessionId);
		await send('Page.navigate', { url: `${server.resolvedUrls!.local[0]}__tooltip-check` }, sessionId);
		await evaluate(`new Promise((resolve, reject) => { let tries = 0; const timer = setInterval(() => { if (document.querySelectorAll('.tooltip-trigger').length >= 20) { clearInterval(timer); resolve(true); } else if (++tries > 100) { clearInterval(timer); reject(new Error('fixture did not mount')); } }, 100); })`);
		for (const width of [1280, 360]) for (const theme of ['light', 'dark']) {
			await send('Emulation.setDeviceMetricsOverride', { width, height: 720, deviceScaleFactor: 1, mobile: false }, sessionId);
			await evaluate(`document.documentElement.dataset.theme = '${theme}'`);
			const count = await evaluate(`document.querySelectorAll('.tooltip-trigger').length`);
			for (let index = 0; index < count; index++) {
				const point = await evaluate(`(() => { const t = document.querySelectorAll('.tooltip-trigger')[${index}]; t.scrollIntoView({block:'center'}); const r = t.getBoundingClientRect(); return {x:r.left + r.width/2, y:r.top + r.height/2}; })()`);
				await send('Input.dispatchMouseEvent', { type: 'mouseMoved', ...point }, sessionId);
				const visible = `(() => { const t = document.querySelectorAll('.tooltip-trigger')[${index}]; const n = t.querySelector('[role="tooltip"]'); const r = n.getBoundingClientRect(); return n.matches(':popover-open') && r.left >= 0 && r.right <= innerWidth && r.top >= 0 && r.bottom <= innerHeight && document.getElementById(t.querySelector('button').getAttribute('aria-describedby')) === n; })()`;
				check(`${theme}/${width} tooltip ${index} hover, association and clipping`, await evaluate(visible));
				const notePoint = await evaluate(`(() => { const r = document.querySelectorAll('[role="tooltip"]')[${index}].getBoundingClientRect(); return {x:r.left + r.width/2, y:r.top + r.height/2}; })()`);
				await send('Input.dispatchMouseEvent', { type: 'mouseMoved', ...notePoint }, sessionId);
				check(`${theme}/${width} tooltip ${index} remains hoverable`, await evaluate(visible));
				await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: 0, y: 0 }, sessionId);
				await evaluate(`(() => { const previous = document.querySelectorAll('.tooltip-trigger')[${index - 1}]; if (previous) (previous.hasAttribute('tabindex') ? previous : previous.querySelector('button')).focus(); else { document.body.tabIndex = -1; document.body.focus(); } })()`);
				await send('Input.dispatchKeyEvent', { type: 'rawKeyDown', key: 'Tab', windowsVirtualKeyCode: 9 }, sessionId);
				await send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Tab', windowsVirtualKeyCode: 9 }, sessionId);
				check(`${theme}/${width} tooltip ${index} keyboard focus`, await evaluate(visible));
				if (index === 0 || index === 16) {
					const { data } = await send('Page.captureScreenshot', { format: 'png' }, sessionId);
					writeFileSync(`${tmpdir()}/herdsman-tooltip-${theme}-${width}-${index}.png`, Buffer.from(data, 'base64'));
				}
				await send('Input.dispatchKeyEvent', { type: 'rawKeyDown', key: 'Escape', windowsVirtualKeyCode: 27 }, sessionId);
				check(`${theme}/${width} tooltip ${index} Escape dismissal`, await evaluate(`!document.querySelector('[role="tooltip"]:popover-open')`));
				await evaluate(`document.activeElement.blur()`);
			}
			check(`${theme}/${width} disabled control stays disabled and its help is focusable`, await evaluate(`(() => { const t = document.querySelector('.tooltip-trigger[aria-disabled="true"]'); return t.tabIndex === 0 && t.querySelector('button').disabled && !!t.getAttribute('aria-describedby'); })()`));
		}
	} finally {
		socket?.close();
		browser?.kill();
		await server.close();
		rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
	}
}

console.log(
	failures === 0
		? '\na11y focus and colour checks pass'
		: `\na11y focus and colour checks: ${failures} FAILED`
);
process.exit(failures === 0 ? 0 : 1);
