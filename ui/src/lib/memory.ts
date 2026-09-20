/**
 * Unit R11's pure model: what this run carried, what it has drawn since, and
 * whether what it carried is still true.
 *
 * Imports types only, so nothing of `herdsman/classes.py` exists at runtime,
 * and every claim it makes is asserted in `ui/dev/field-check.ts` — the same
 * precedent as `packet.ts` (R7) and `interventions.ts` (R6).
 *
 * What it refuses to do matters as much as what it does: it never re-sorts a
 * receipt, never pads or truncates a carried pairing, never fuses a delivery
 * budget figure with a packet cost figure, and never renders an unread status
 * read as "still current". A newer daemon that interleaves a section into the
 * memory run is reported, never guessed across.
 */
import type {
	Attempt,
	Initiative,
	MemoryLeaf,
	MemoryReceipt,
	MemoryStatus,
	PacketSection,
	PacketSnapshot,
	Plan
} from './daemon';

/**
 * The maximal run of consecutive sections whose name is `memory` or begins
 * with `memory_`, in the served order — verified, never assumed. Contiguous
 * in every packet this daemon writes, and the fence's figure is the sum of
 * its members' `input_tokens`, which is a real number only because the run
 * is contiguous in the packet's marginal-delta prefix chain. A newer daemon
 * that interleaves anything between them gets no fence and no sum, and the
 * rows render exactly as they did before this unit.
 */
export interface MemoryRun {
	sections: PacketSection[];
	/** The sum of the fenced rows' own `input_tokens`. */
	tokens: number;
}

export function memoryRun(snapshot: PacketSnapshot | null): MemoryRun | null {
	if (!snapshot) return null;
	const isMemory = (name: string): boolean => name === 'memory' || name.startsWith('memory_');
	const sections: PacketSection[] = [];
	let gap = false;
	for (const section of snapshot.sections) {
		if (isMemory(section.name)) {
			if (gap) return null;
			sections.push(section);
			continue;
		}
		/* A non-memory section in the middle ends the run; a later memory section
		   proves the family was interleaved, so its cost cannot be stated. */
		if (sections.length > 0) gap = true;
	}
	if (sections.length === 0) return null;
	return {
		sections,
		tokens: sections.reduce((sum, section) => sum + section.input_tokens, 0)
	};
}

/**
 * The leaves this packet carried, `memory_leaf_ids[i]` paired with
 * `memory_leaf_versions[i]` — built in lockstep by `deliver_memory`, so
 * zipping them is reading, not deriving.
 *
 * Unequal lengths return `null`: the daemon served a different number of ids
 * than versions and the pairing cannot be trusted. Never padded, never
 * truncated; the caller prints both lists raw instead.
 */
export function carriedLeaves(attempt: Attempt): { id: string; version: string }[] | null {
	if (attempt.memory_leaf_ids.length !== attempt.memory_leaf_versions.length) return null;
	return attempt.memory_leaf_ids.map((id, index) => ({
		id,
		version: attempt.memory_leaf_versions[index]
	}));
}

/**
 * The receipts this attempt's own history contains, in fold order — never
 * re-sorted. Three rules, and the uneven attribution the daemon writes is the
 * design (`MemoryUseRecorded` names an attempt for pointer/inline/auto-answer,
 * never for `pull`, and never for the plan-level salvage and dreaming):
 *
 * - a receipt naming this attempt is this attempt's, whatever its operation;
 * - a `pull` naming no attempt is included only when it names a leaf this
 *   attempt carried — a join on ids both records carry, and the only join in
 *   the unit, labelled run-scoped;
 * - `salvage` and `dreaming` are plan-level and render in the salvage section,
 *   never here.
 */
export function receiptsFor(
	attempt: Attempt,
	plan: Pick<Plan, 'memory_receipts'>
): { receipt: MemoryReceipt; runScoped: boolean }[] {
	const carried = new Set(carriedLeaves(attempt)?.map((leaf) => leaf.id) ?? []);
	const rows: { receipt: MemoryReceipt; runScoped: boolean }[] = [];
	for (const receipt of plan.memory_receipts) {
		if (receipt.operation === 'salvage' || receipt.operation === 'dreaming') continue;
		if (receipt.attempt_id === attempt.id) {
			rows.push({ receipt, runScoped: false });
			continue;
		}
		if (
			receipt.attempt_id === null &&
			receipt.operation === 'pull' &&
			receipt.leaf_ids.some((id) => carried.has(id))
		) {
			rows.push({ receipt, runScoped: true });
		}
	}
	return rows;
}

/**
 * The verdict on one carried leaf, from the store's recomputed status — the
 * daemon's own word, never derived here.
 *
 * Silence is the common case: carried version, still active, still the same
 * version. Every non-active verdict ends with the unchanged-receipt clause,
 * because an operator reading a verdict beside a packet receipt must not
 * conclude the packet was wrong — it was not; the world moved. A leaf absent
 * from the status read is named the same way. An unread status produces no
 * verdict at all — the caller handles unread before calling.
 */
export type CurrentVerdict =
	| { kind: 'version'; line: string }
	| { kind: 'stale' | 'conflicted' | 'retired'; line: string }
	| { kind: 'gone'; line: string };

const UNCHANGED = 'What this attempt received is unchanged.';

export function currentVerdict(
	status: MemoryStatus | null,
	leafId: string,
	carriedVersion: string
): CurrentVerdict | null {
	if (!status) return null;
	const leaf = status.leaves.find((entry) => entry.id === leafId);
	if (!leaf) return { kind: 'gone', line: `The store no longer lists ${leafId}.` };
	switch (leaf.status) {
		case 'active':
			return String(leaf.version) === carriedVersion
				? null
				: {
						kind: 'version',
						line: `Carried @${carriedVersion}; the store now holds @${leaf.version}.`
					};
		case 'stale':
			return {
				kind: 'stale',
				line: `The daemon now reports ${leafId} stale — its evidence no longer matches — so it is excluded from new packets and from auto-answers. ${UNCHANGED}`
			};
		case 'conflicted':
			return {
				kind: 'conflicted',
				line: `Another active leaf now claims the same subject differently, so the daemon has excluded both. ${UNCHANGED}`
			};
		case 'retired':
			return { kind: 'retired', line: `This leaf has since been retired. ${UNCHANGED}` };
	}
}

/**
 * The leaves an auto-answer's subject select offers: the daemon's own
 * eligibility, mirrored one rule at a time from `eligible_memory` — the
 * client enumerates, the daemon still decides.
 *
 * - only `active`: a stale or conflicted leaf is excluded from distribution by
 *   construction, so offering one would offer a control that cannot work;
 * - `owner_run === null`, or an id this member has ever had — the mirror of the
 *   daemon's `Initiative.known_ids`, which is a `@property` and therefore not
 *   serialized, so the mirror is built from `id_history`;
 * - a project-lifetime leaf with a non-null `owner_run` is excluded, because
 *   `eligible_memory` excludes it.
 */
export function answerableLeaves(
	status: MemoryStatus | null,
	initiative: { id_history: string[]; spec: { id: string } } | null
): MemoryLeaf[] {
	if (!status || !initiative) return [];
	const owned = new Set([...initiative.id_history, initiative.spec.id]);
	return status.leaves.filter(
		(leaf) =>
			leaf.status === 'active' &&
			!(leaf.lifetime === 'project' && leaf.owner_run !== null) &&
			(leaf.owner_run === null || owned.has(leaf.owner_run))
	);
}

/**
 * What salvage would read, as counts of records — never an estimate of tokens,
 * which is unknowable before the write and is not stated.
 *
 * Mirrors `Daemon._salvage_input`'s walk over the live initiatives *and* the
 * retired ones: each recorded failure and its evidence refs, each failed
 * check, each patch reference.
 */
function hasFailedChecks(initiative: Initiative): boolean {
		return initiative.attempts.some((attempt) =>
			attempt.checkpoint?.checks.some((check) => !check.passed) ?? false
		);
}

export interface SalvageEvidence {
	failures: number;
	initiatives: number;
	retired: number;
	evidenceRefs: number;
	failedChecks: number;
	patchRefs: number;
}

export function salvageEvidence(plan: Pick<Plan, 'initiatives' | 'retired'>): SalvageEvidence {
	const all = [...Object.values(plan.initiatives), ...plan.retired];
	let failures = 0;
	let evidenceRefs = 0;
	let failedChecks = 0;
	let patchRefs = 0;
	let initiatives = 0;
	for (const initiative of all) {
		if (initiative.failures.length === 0 && !hasFailedChecks(initiative)) continue;
		initiatives += 1;
		for (const failure of initiative.failures) {
			failures += 1;
			evidenceRefs += failure.evidence.length;
		}
		for (const attempt of initiative.attempts) {
			const checkpoint = attempt.checkpoint;
			if (!checkpoint) continue;
			failedChecks += checkpoint.checks.filter((check) => !check.passed).length;
			if (checkpoint.patch_path) patchRefs += 1;
		}
	}
	return {
		failures,
		initiatives,
		retired: plan.retired.length,
		evidenceRefs,
		failedChecks,
		patchRefs
	};
}

/**
 * Why salvage can or cannot run, as sentences naming the rules — never a
 * greyed control.
 *
 * Two independent rules hold it and both print when both hold, because they
 * have different fixes: one needs a configured author, the other needs
 * preserved evidence. R6's shared-cause collapsing does not apply.
 *
 * The author rule comes from the capabilities probe: a 400 is the daemon's own
 * message (the unconfigured state, not a failed read) and is quoted, not
 * paraphrased. A 200 with a null author carries the daemon's refusal string.
 * Under the OWNER fallback the daemon projects no author name, so the armed
 * plate cannot say which model — the plate says so before the press (R6's
 * `_attempt_commands` precedent: warn before the press rather than discover
 * from a 409).
 */
export type CapabilitiesPhase =
	| { phase: 'unread' }
	| { phase: 'reading' }
	| { phase: 'read'; authorName: string | null }
	| { phase: 'missing'; detail: string }
	| { phase: 'failed'; detail: string };

export function salvageAvailability(
	plan: Pick<Plan, 'initiatives' | 'retired'>,
	capabilities: CapabilitiesPhase
): { available: boolean; rules: string[] } {
	const rules: string[] = [];
	if (capabilities.phase === 'missing') {
		rules.push(
			`Salvage needs a model declared to author leaves, and the daemon reports the declaration missing: ${capabilities.detail}. Without one, nothing here can run — and nothing else in this build is affected: packets, pointers and auto-answers do not need an author.`
		);
	} else if (capabilities.phase === 'failed') {
		rules.push(`The memory capability read failed: ${capabilities.detail} Salvage is unavailable until the daemon answers.`);
	} else if (capabilities.phase === 'read' && capabilities.authorName === null) {
		rules.push(
			'Salvage needs a model declared to author leaves. The daemon reports no configured memory author model, so nothing here can run — and nothing else in this build is affected: packets, pointers and auto-answers do not need an author.'
		);
	}
	const evidence = salvageEvidence(plan);
	if (evidence.failures === 0) {
		rules.push(
			'Salvage reads this run’s preserved failure evidence — failure reasons, failed checks and patch references. This run has recorded none, so there is nothing to author from.'
		);
	}
	return { available: rules.length === 0, rules };
}

/**
 * The armed consequence, part (2): what is sent, as counts of records — never
 * an estimate of tokens, which is unknowable before the write and is not
 * stated. Mirrors `Daemon._salvage_input`'s own bounds and its deliberate
 * inclusion of retired work.
 */
export function salvageWhatIsSent(evidence: SalvageEvidence): string {
	return (
		`${evidence.failures} recorded ${evidence.failures === 1 ? 'failure' : 'failures'} across ` +
		`${evidence.initiatives} ${evidence.initiatives === 1 ? 'initiative' : 'initiatives'} ` +
		`(retired among them: ${evidence.retired}), their ${evidence.evidenceRefs} evidence refs, ` +
		`${evidence.failedChecks} failed ${evidence.failedChecks === 1 ? 'check' : 'checks'}, and ` +
		`${evidence.patchRefs} patch ${evidence.patchRefs === 1 ? 'reference' : 'references'}. ` +
		'Each failure reason and check summary is cut at 400 characters; the whole report is cut at 10 000, of which at most 5 000 is file content. Every value is passed through the daemon’s credential redaction first. No transcript is sent, and no file the report does not cite.'
	);
}

/** Armed part (3): the caps, verbatim from the route's own behaviour. */
export const SALVAGE_CAPS =
	'At most 10 leaves. Each leaf must carry evidence the daemon can resolve inside this project, and each body is capped at 300 tokens. Origin, lifetime, status, author and version are stamped by the daemon, never by the model. A leaf whose evidence does not resolve, or whose id already exists, refuses the whole write — nothing is half-written. An on-demand salvage carries no token budget: the leash is the bounded report above and the leaf cap.';

/** Armed part (1), with the model named by the daemon capability report. */
export function salvageAuthor(model: string, binary: string, timeout: number): string {
	return `${model} on ${binary} authors the leaves. The daemon waits up to ${timeout}s for it.`;
}

/** Armed part (4): the cost, honestly unknown until it runs. */
export const SALVAGE_COST =
	'What it costs is not known before it runs. The cost is recorded on the receipt when it lands, as an estimate.';

/** The sending line: the retry precedent, which already says a write can take minutes. */
export const SALVAGE_SENDING =
	'Authoring. The daemon holds this open until the model answers or the timeout configured for it elapses, which can take minutes.';

/**
 * The landed plate's attribution line — not optional. It is what makes the
 * spend auditable.
 */
export function salvageAttribution(tokens: number): string {
	return `Recorded: salvage · ${tokens.toLocaleString()} tokens · estimate · attributed to this run.`;
}

/**
 * The settled provenance sentence: the literal text, `id@version`, matching the
 * working note's "answered from memory: <leaf>" with the version made explicit.
 * A claim without its version is not a receipt.
 */
export function answeredFromMemory(id: string, version: string): string {
	return `Answered from memory: ${id}@${version}.`;
}
export const ANSWERED_FROM_MEMORY = answeredFromMemory;

/**
 * The one sentence per delivery mode, printed under the fence and only one.
 * `legacy` is a project that has not declared memory capabilities, not a
 * failure.
 */
export const MODE_SENTENCES: Record<string, string> = {
	legacy:
		'This packet was compiled before the project declared memory capabilities, so it carried run-scoped intervention lines only: no pointer block, no pull path, no inlined leaf.',
	pointer:
		'Class A or B: the packet named these leaves and the agent could pull any of them in full. The budget for that block is 40 tokens.',
	inline:
		'Class C: this harness is not trusted to pull, so whole leaves were written into the packet within a 200-token budget.'
};

/**
 * The delivery-versus-packet line, said once when both figures are on screen.
 */
export const DELIVERY_VS_PACKET =
	'the budget figure below counts the delivered lines; the figure above counts what those sections added to the packet.';

/**
 * The current-versus-carried class line, printed only when the Kitchen's
 * *current* declaration disagrees with what the attempt received. A project
 * that declares nothing prints nothing here — undeclared is never guessed.
 */
export function classDisagreement(currentClass: string | null, mode: string): string | null {
	if (currentClass === null) return null;
	const declared = currentClass === 'C' ? 'inline' : 'pointer';
	if (declared === mode) return null;
	return `The project now declares this harness class ${currentClass}; this attempt was compiled under ${mode} delivery. What it received is unchanged.`;
}

/**
 * The band's own line, said once so silence in stratum C means agreement
 * rather than unread — the R7 band idiom, one level down.
 */
export const CARRIED_HEADER =
	'every carried leaf is still active at its carried version unless a line below says otherwise';

/** The block's own line under the receipt list, said once. */
export const RECEIPT_ESTIMATES =
	'These are estimates. Memory tokens are orchestration overhead and no provider measured them.';

/** The fence figure's gloss — R7's marginal-delta vocabulary, verbatim. */
export const FENCE_GLOSS = 'what the memory sections added to this packet, in this order';

/** The two lists raw, printed under a line when the pairing is refused. */
export const PAIRING_REFUSED =
	'The daemon served a different number of leaf ids than versions, so the pairing cannot be trusted and nothing is paired here.';

/** The stratum's unread line, plus the section's Read again control beside it. */
export const STATUS_UNREAD = 'Whether these leaves are still current is unread.';

/** The no-leaves state: slack, never red, no empty table. */
export const NO_LEAVES = 'This packet carried no memory.';
