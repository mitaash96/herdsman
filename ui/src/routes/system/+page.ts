import { error } from '@sveltejs/kit';

/* Dev-only style guide: production builds 404 it. */
export const load = () => {
	if (!import.meta.env.DEV) error(404, 'Not found');
};
