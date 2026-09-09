// The daemon is the only source of state and it is reachable only from the
// user's own machine, so nothing here is prerenderable or server-rendered.
export const ssr = false;
export const prerender = false;
export const trailingSlash = 'never';
