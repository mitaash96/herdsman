import adapter from '@sveltejs/adapter-static';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

// The daemon (`herdsman serve`) owns every route the UI reads or writes. In dev
// the app is served by Vite and the daemon by uvicorn, so these are proxied to
// keep the browser on one origin -- in production the daemon serves the built
// asset folder itself and the proxy has no counterpart.
const DAEMON = process.env.HERDSMAN_DAEMON ?? 'http://127.0.0.1:8000';

export default defineConfig({
	plugins: [
		sveltekit({
			compilerOptions: {
				// Force runes mode for the project, except for libraries. Can be removed in svelte 6.
				runes: ({ filename }) =>
					filename.split(/[/\\]/).includes('node_modules') ? undefined : true
			},
			// SPA: one fallback document, no prerendered routes. Deep links resolve
			// on the client, which is what F2 will need and what lets the daemon
			// serve the whole app as a static folder.
			adapter: adapter({ fallback: 'index.html' })
		})
	],
	server: {
		proxy: {
			'/plans': { target: DAEMON, changeOrigin: false }
		}
	}
});
