import type { AssetSummary, Kitchen, KitchenAssignment } from './daemon';

export const ready = (kitchen: Kitchen, assignment: KitchenAssignment): boolean =>
	kitchen.readiness.find((item) => item.harness === assignment.harness)?.state === 'ready';

export const assignments = (kitchen: Kitchen): KitchenAssignment[] =>
	kitchen.models.filter((item) => ready(kitchen, item)).map(({ harness, model }) => ({ harness, model }));

export const activeAssets = (assets: AssetSummary[], kind: 'role' | 'contract'): AssetSummary[] =>
	assets.filter((asset) => asset.kind === kind && asset.status === 'active');

export const assignmentKey = (assignment: KitchenAssignment): string =>
	`${assignment.harness}/${assignment.model}`;

/** A pair already written its own way, with the one effort level it runs under
 * appended when one is set: `claude/opus · high`. An unspecified level adds
 * nothing — a harness default is not a level this build can name. */
export const withEffort = (label: string, effort: string | null | undefined): string =>
	effort ? `${label} · ${effort}` : label;
