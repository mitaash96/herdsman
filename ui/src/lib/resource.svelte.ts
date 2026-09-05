import { DaemonError } from './daemon';

export type Phase = 'idle' | 'loading' | 'ready' | 'error';

/**
 * One async read from the daemon, with the states this product actually has.
 *
 * The rule that shapes it: a refresh that fails never erases what we last knew
 * to be true. The value stays on screen and is marked stale, because a
 * supervision tool that blanks its own readout the moment the daemon hiccups is
 * lying by omission. Only a first load with nothing to preserve reaches `error`.
 *
 * Unknown is never zero and never empty: `data` stays `null` until a real
 * response arrives.
 */
export class Resource<T> {
	data = $state<T | null>(null);
	phase = $state<Phase>('idle');
	error = $state<DaemonError | null>(null);
	/** Data is present but no longer known to be current. */
	stale = $state(false);
	loadedAt = $state<Date | null>(null);

	#fetch: (signal: AbortSignal) => Promise<T>;
	#controller: AbortController | null = null;

	constructor(fetcher: (signal: AbortSignal) => Promise<T>) {
		this.#fetch = fetcher;
	}

	/** True once a real response has been seen, whatever happened since. */
	get hasData(): boolean {
		return this.data !== null;
	}

	async load(): Promise<void> {
		this.#controller?.abort();
		const controller = new AbortController();
		this.#controller = controller;

		// A reload with data in hand is a refresh: keep showing it.
		if (!this.hasData) this.phase = 'loading';

		try {
			const value = await this.#fetch(controller.signal);
			if (controller.signal.aborted) return;
			this.data = value;
			this.phase = 'ready';
			this.error = null;
			this.stale = false;
			this.loadedAt = new Date();
		} catch (cause) {
			if (controller.signal.aborted) return;
			const failure =
				cause instanceof DaemonError
					? cause
					: new DaemonError('bad_response', 'Something in this build failed while reading.');
			this.error = failure;
			if (this.hasData) {
				// Preserve it, and say plainly that it is no longer current.
				this.stale = true;
				this.phase = 'ready';
			} else {
				this.phase = 'error';
			}
		}
	}

	/** Mark what is on screen as no longer known-current, without clearing it. */
	markStale(): void {
		if (this.hasData) this.stale = true;
	}

	dispose(): void {
		this.#controller?.abort();
		this.#controller = null;
	}
}
