/**
 * Screenshot the driver UI over the DevTools protocol.
 *
 *   node ui/dev/shot.mjs <url> <out.png> [--width 1440] [--height 900]
 *                        [--scheme dark|light] [--wait 3000] [--click <selector>]
 *                        [--full]
 *
 * Why this exists: the Run view holds an open server-sent-events stream, and
 * `brave --headless --screenshot --virtual-time-budget` never returns while a
 * network task is pending — it hangs until something kills it and writes no
 * file. Driving the browser directly means the capture happens on a wall clock
 * we control, with the live stream still connected, which is the state the
 * view is actually in.
 *
 * No dependency: Node's own WebSocket speaks CDP.
 */

import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { writeFileSync } from 'node:fs';

const [url, out, ...rest] = process.argv.slice(2);
if (!url || !out) {
	console.error('usage: node ui/dev/shot.mjs <url> <out.png> [options]');
	process.exit(2);
}
const flag = (name, fallback) => {
	const at = rest.indexOf(`--${name}`);
	return at === -1 ? fallback : rest[at + 1];
};
const width = Number(flag('width', 1440));
const height = Number(flag('height', 900));
const scheme = flag('scheme', 'dark');
const wait = Number(flag('wait', 3000));
const click = flag('click', null);
const full = rest.includes('--full');

const port = 9200 + Math.floor(Math.random() * 700);
const browser = spawn(
	process.env.BROWSER ?? 'brave',
	[
		'--headless',
		'--disable-gpu',
		'--hide-scrollbars',
		'--no-first-run',
		`--remote-debugging-port=${port}`,
		`--window-size=${width},${height}`,
		'about:blank'
	],
	{ stdio: 'ignore' }
);

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function endpoint() {
	for (let attempt = 0; attempt < 60; attempt++) {
		try {
			const response = await fetch(`http://127.0.0.1:${port}/json/version`);
			return (await response.json()).webSocketDebuggerUrl;
		} catch {
			await sleep(150);
		}
	}
	throw new Error('the browser never opened a debugging port');
}

const socket = new WebSocket(await endpoint());
await once(socket, 'open');

let nextId = 0;
const pending = new Map();
socket.addEventListener('message', (event) => {
	const message = JSON.parse(event.data);
	const settle = pending.get(message.id);
	if (!settle) return;
	pending.delete(message.id);
	if (message.error) settle.reject(new Error(message.error.message));
	else settle.resolve(message.result);
});
const send = (method, params = {}, sessionId) =>
	new Promise((resolve, reject) => {
		const id = ++nextId;
		pending.set(id, { resolve, reject });
		socket.send(JSON.stringify({ id, method, params, sessionId }));
	});

try {
	const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
	const { sessionId } = await send('Target.attachToTarget', { targetId, flatten: true });

	await send('Emulation.setDeviceMetricsOverride',
		{ width, height, deviceScaleFactor: 1, mobile: false }, sessionId);
	await send('Emulation.setEmulatedMedia',
		{ features: [{ name: 'prefers-color-scheme', value: scheme }] }, sessionId);
	await send('Page.enable', {}, sessionId);
	await send('Page.navigate', { url }, sessionId);
	await sleep(wait);

	if (click) {
		// Selection state is half of what this view does; capturing it needs a
		// real click, not a URL the product does not have.
		await send('Runtime.evaluate',
			{ expression: `document.querySelector(${JSON.stringify(click)})?.click()` }, sessionId);
		await sleep(600);
	}

	let clip;
	if (full) {
		const { cssContentSize } = await send('Page.getLayoutMetrics', {}, sessionId);
		clip = { x: 0, y: 0, width, height: Math.ceil(cssContentSize.height), scale: 1 };
	}
	const { data } = await send('Page.captureScreenshot',
		{ format: 'png', captureBeyondViewport: full, ...(clip ? { clip } : {}) }, sessionId);
	writeFileSync(out, Buffer.from(data, 'base64'));
	console.log(`${out} ${width}x${clip ? clip.height : height} ${scheme}${click ? ` click=${click}` : ''}`);
} finally {
	socket.close();
	browser.kill();
}
