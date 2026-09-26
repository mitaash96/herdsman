import { LIBRARY_EVENTS, type LibraryRevisionEvent } from './daemon';

/** One stream per mounted Library; EventSource owns reconnection. */
export class LibraryWatch {
	revision = $state('');
	changed = $state<string[]>([]);
	connected = $state(false);
	private source: EventSource | null = null;

	start(onchange: (changed: string[]) => void): void {
		if (this.source) return;
		const source = new EventSource(LIBRARY_EVENTS);
		this.source = source;
		source.onopen = () => { this.connected = true; };
		source.onerror = () => { this.connected = false; };
		source.addEventListener('library.revision', (message) => {
			const event = JSON.parse((message as MessageEvent<string>).data) as LibraryRevisionEvent;
			const reconnect = this.revision !== '' && this.revision !== event.revision && event.changed.length === 0;
			this.revision = event.revision;
			this.changed = event.changed;
			// A new connection has no previous snapshot: reconcile the gap on focus's
			// same path rather than silently missing saves made while disconnected.
			if (event.changed.length || reconnect) onchange(event.changed);
		});
	}

	dispose(): void {
		this.source?.close();
		this.source = null;
		this.connected = false;
	}
}
