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
const overlays = sources.filter((path) => /drawer|palette/i.test(path));
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
	['light', tokens(':root {')],
	['dark', tokens(":root[data-theme='dark'] {")]
] as const;
const foregrounds = [
	['ink', 4.5],
	['ink-2', 4.5],
	['red', 4.5],
	['seat', 4.5],
	['member-line', 3],
	['ash', 3]
] as const;

for (const [theme, palette] of themes) {
	check(`${theme} colour tokens are readable`, palette.size > 0);
	for (const background of ['ground', 'plate'] as const) {
		for (const [foreground, minimum] of foregrounds) {
			const left = palette.get(foreground);
			const right = palette.get(background);
			const ratio = left && right ? contrast(left, right) : 0;
			check(
				`${theme} ${foreground} on ${background} is at least ${minimum}:1`,
				ratio >= minimum,
				`${ratio.toFixed(2)}:1`
			);
		}
	}
}

console.log(
	failures === 0
		? '\na11y focus and colour checks pass'
		: `\na11y focus and colour checks: ${failures} FAILED`
);
process.exit(failures === 0 ? 0 : 1);
