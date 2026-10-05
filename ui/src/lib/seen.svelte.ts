/**
 * Unread needs-you state, per browser (DS §9.19). The daemon has no read model
 * and the product is single-operator, so a seen item is a `key@since` string in
 * localStorage; `since` changes when the daemon re-raises an item, which makes
 * it unread again. Shared by the sidebar badge and Home's needs-you panel.
 */
const STORE = 'herdsman.seen.attention';
/* ponytail: unbounded set; prune to the visible keys if it ever grows past a few thousand. */

const stamp = (item: { key: string; since: string }) => `${item.key}@${item.since}`;

function load(): Set<string> {
	try {
		const raw = localStorage.getItem(STORE);
		return new Set(raw ? (JSON.parse(raw) as string[]) : []);
	} catch {
		return new Set();
	}
}

class Seen {
	/* Loaded at construction: the SPA has no SSR, and writing state lazily from a
	   reader (a $derived) is an unsafe mutation. */
	#set = $state<Set<string>>(typeof localStorage === 'undefined' ? new Set() : load());

	constructor() {
		if (typeof window !== 'undefined')
			window.addEventListener('storage', (e) => {
				if (e.key === STORE) this.#set = load();
			});
	}
	#save(next: Set<string>) {
		this.#set = next;
		try {
			localStorage.setItem(STORE, JSON.stringify([...next]));
		} catch {
			/* the choice holds for this session */
		}
	}
	isUnread(item: { key: string; since: string }): boolean {
		return !this.#set.has(stamp(item));
	}
	markRead(items: readonly { key: string; since: string }[]) {
		const next = new Set(this.#set);
		for (const item of items) next.add(stamp(item));
		this.#save(next);
	}
	markUnread(item: { key: string; since: string }) {
		const next = new Set(this.#set);
		next.delete(stamp(item));
		this.#save(next);
	}
}

export const seen = new Seen();
