import { redirect } from '@sveltejs/kit';

/* Run is the only view whose substrate has landed, so it is where the shell opens. */
export const load = () => {
	redirect(307, '/run');
};
