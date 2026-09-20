/**
 * Screenshot the driver UI over the DevTools protocol.
 *
 *   node ui/dev/shot.mjs <url> <out.png> [--width 1440] [--height 900]
 *                        [--scheme dark|light] [--wait 3000] [--click <selector>]
 *                        [--fill <selector>=<value>] [--scroll <selector>]
 *                        [--full]
 *
 * `--click` may be repeated, in order, so a state that takes more than one
 * click to reach is still capturable. A selector of the form `text=Retry`
 * clicks the first button whose label is exactly that, which is how the
 * interventions are reached: they are a row of labelled controls whose order
 * depends on which of them the fold currently allows, so a positional selector
 * would capture a different control on a different member.
 *
 * `--fill` sets an input or textarea and dispatches the events Svelte binds
 * on, so a control gated on its own field being filled can be driven to the
 * state where it is actually pressable. The argument splits at the LAST `=`,
 * so a selector may itself contain `=` (e.g. `input[role="combobox"]`) and
 * the value may not.
 *
 * `--scroll` brings one element into view, which is how anything below the
 * fold of the detail drawer is reached: the drawer is a fixed sheet with its
 * own scrolling body, so a page-level full-height capture never reaches it.
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
const steps = rest.flatMap((token, at) =>
	token === '--click' || token === '--fill' || token === '--scroll' || token === '--key' || token === '--eval'
		? [[token.slice(2), rest[at + 1]]]
		: []
);
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

/** Click the first button whose trimmed label is exactly `label`. */
const byText = (label) => `
	[...document.querySelectorAll('button')]
		.find((button) => button.textContent.trim() === ${JSON.stringify(label)})
		?.click()`;

/**
 * Set a field and tell the framework, which a bare value assignment does not:
 * Svelte binds on `input` and `change`, so a value written straight to the
 * property leaves the component holding the old one and the control disabled.
 */
const fill = (argument) => {
	/* The LAST `=` splits: a selector may contain `=` (an attribute selector
	   like `input[role="combobox"]`), the value may not. Splitting at the first
	   cut such a selector in half and filled the wrong thing, silently. */
	const at = argument.lastIndexOf('=');
	const [selector, value] = [argument.slice(0, at), argument.slice(at + 1)];
	return `
		(() => {
			const field = document.querySelector(${JSON.stringify(selector)});
			if (!field) return;
			const proto = field instanceof HTMLTextAreaElement
				? HTMLTextAreaElement.prototype
				: field instanceof HTMLSelectElement
					? HTMLSelectElement.prototype
					: HTMLInputElement.prototype;
			Object.getOwnPropertyDescriptor(proto, 'value').set.call(field, ${JSON.stringify(value)});
			field.dispatchEvent(new Event('input', { bubbles: true }));
			field.dispatchEvent(new Event('change', { bubbles: true }));
		})()`;
};

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

	for (const [kind, argument] of steps) {
		// Selection state is half of what this view does; capturing it needs a
		// real click, not a URL the product does not have.
		if (kind === 'key') {
			/* A real Input-domain key press, not a scripted focus: the
			   :focus-visible ring only follows keyboard input, and a capture of
			   the ring must be a capture of the browser's own behaviour. */
			await send('Input.dispatchKeyEvent', {
				type: 'rawKeyDown', key: argument, windowsVirtualKeyCode: argument === 'Tab' ? 9 : 0,
				text: '', unmodifiedText: '',
			}, sessionId);
			await send('Input.dispatchKeyEvent', { type: 'keyUp', key: argument }, sessionId);
			await sleep(600);
			continue;
		}
		if (kind === 'eval') {
			/* Trusted repository tooling, not user input: an expression the
			   captures need that neither a click nor a key can reach (framing a
			   label by its text, dispatching a window event). */
			await send('Runtime.evaluate', { expression: argument }, sessionId);
			await sleep(600);
			continue;
		}
		const expression =
			kind === 'scroll'
				? `document.querySelector(${JSON.stringify(argument)})
				     ?.scrollIntoView({ block: 'center' })`
				: kind === 'fill'
					? fill(argument)
					: argument.startsWith('text=')
							? byText(argument.slice(5))
						: `document.querySelector(${JSON.stringify(argument)})?.click()`;
		await send('Runtime.evaluate', { expression }, sessionId);
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
	console.log(
		`${out} ${width}x${clip ? clip.height : height} ${scheme}` +
			steps.map(([kind, argument]) => ` ${kind}=${argument}`).join('')
	);
} finally {
	socket.close();
	browser.kill();
}
