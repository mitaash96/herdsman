import { redirect } from '@sveltejs/kit';

/* Home is where the shell opens: understand the fleet first, then drill into a run. */
export const load = () => {
	redirect(307, '/home');
};
