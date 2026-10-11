import type { AssetSummary, Branches, CreatePlanRequest, Kitchen, KitchenAssignment } from './daemon';

export const ready = (kitchen: Kitchen, assignment: KitchenAssignment): boolean =>
	kitchen.readiness.find((item) => item.harness === assignment.harness)?.state === 'ready';

export const assignments = (kitchen: Kitchen): KitchenAssignment[] =>
	kitchen.models.filter((item) => ready(kitchen, item)).map(({ harness, model }) => ({ harness, model }));

export const activeAssets = (assets: AssetSummary[], kind: 'role' | 'contract'): AssetSummary[] =>
	assets.filter((asset) => asset.kind === kind && asset.status === 'active');

export const assignmentKey = (assignment: KitchenAssignment): string =>
	`${assignment.harness}/${assignment.model}`;

/** An old draft may name a deleted branch; only default to a listed current branch. */
export function startBranch(branches: Branches, selected: string): string | null {
	if (!branches.git) return null;
	const name = selected || branches.current;
	if (branches.branches.some((branch) => branch.name === name)) return name;
	return branches.branches.some((branch) => branch.name === branches.current) ? branches.current : null;
}

export function branchFields(branches: Branches, start: string, target: string): Pick<CreatePlanRequest, 'start_branch' | 'target_branch'> {
	return { start_branch: startBranch(branches, start), target_branch: branches.git ? target || null : null };
}

/** Format feedback only. Protection, existence and landing safety remain server-owned. */
export function targetBranchError(name: string): string | null {
	if (!name) return null;
	if (name === '@' || name.startsWith('-') || name.endsWith('.') || name.includes('..') || name.includes('@{') ||
		/[\x00-\x20\x7f~^:?*\[\\]/.test(name) || name.split('/').some((part) => !part || part.startsWith('.') || part.endsWith('.lock'))) {
		return 'Use a Git branch name without spaces, .., or special ref characters.';
	}
	return null;
}

export function landingHint(branches: Branches, start: string | null, target: string): string {
	if (!target) return `Planner names it: ${branches.default_target_prefix}<title>. Leave empty to use the proposal title.`;
	if (target === start) return `Lands as a commit on ${target}; your checkout must be clean.`;
	if (!branches.branches.some((branch) => branch.name === target)) return `Creates ${target} at landing.`;
	return `Lands as a commit on ${target}; a checked-out target must be clean and in the project root.`;
}

/** A pair already written its own way, with the one effort level it runs under
 * appended when one is set: `claude/opus · high`. An unspecified level adds
 * nothing — a harness default is not a level this build can name. */
export const withEffort = (label: string, effort: string | null | undefined): string =>
	effort ? `${label} · ${effort}` : label;
