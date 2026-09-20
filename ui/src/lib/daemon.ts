/**
 * The daemon is the only source of state and the only write path.
 *
 * Every type here mirrors a model the daemon already serialises
 * (`herdsman/graph.py`, `herdsman/classes.py`, and for navigation
 * `herdsman/nav.py` via `NavIndex.to_dict()`); nothing is invented and nothing
 * is widened. `PlanGraph` is documented in `graph.py` as "the stable projection
 * the UI and CLI render a running plan from", and it is what this app renders.
 *
 * The UI and the CLI are peers over this API, not layers: `herdsman run` is
 * itself an HTTP client to the same routes (`herdsman/cli.py`).
 */

import { isHistorical } from './replay';

/** `herdsman/classes.py` — Initiative.state. */
export type InitiativeState =
	| 'pending'
	| 'running'
	| 'settled'
	| 'failed'
	| 'paused'
	| 'cancelled';

/** `herdsman/graph.py` — NodeStatus. */
export interface NodeStatus {
	initiative_id: string;
	name: string;
	digest: string;
	/** Widened to `str` by the daemon; `InitiativeState` is the observed domain. */
	state: string;
	depends_on: string[];
	harness: string;
	model: string;
	attempts: number;
	checkpoint_id: string | null;
	/** Computed, never stored — readiness is derived from the folded plan. */
	ready: boolean;
}

/**
 * `herdsman/graph.py` — Overhead. The ledger-attributed ratio: selected
 * orchestration tokens over actual provider-or-harness productive tokens
 * (`graph.py` `overhead()`), never a packet-count ratio.
 */
/** `herdsman/graph.py` — NodeRevision. Backend-native recalibration classification. */
export type NodeChange = 'unchanged' | 'edited' | 'split' | 'merged' | 'new' | 'removed';
export type EdgeState = 'same' | 'changed' | 'unresolved';

export interface NodeRevision {
	change: NodeChange;
	old_ids: string[];
	new_ids: string[];
	old_digest: string | null;
	new_digest: string | null;
	old_token_caps: (number | null)[];
	new_token_caps: (number | null)[];
	renamed: boolean;
	edge_state: EdgeState;
	old_attempts: number;
	new_attempts: number;
}

export interface PlanRevision {
	plan_id: string;
	from_version: number;
	to_version: number;
	nodes: NodeRevision[];
	counts: Record<NodeChange, number>;
	ambiguous: string[];
	derivation: string;
}

export interface AllowanceReset {
	initiative_id: string;
	source_ids: string[];
	consumed_attempts: number | null;
	source_status: 'proven' | 'candidates' | 'unknown' | 'new';
	candidate_source_ids: string[];
}

export interface RevisionImpact {
	plan_id: string;
	from_version: number;
	to_version: number;
	downstream: NodeImpact[];
	stranded: string[];
	dropped: string[];
	plan_token_cap_from: number | null;
	plan_token_cap_to: number | null;
	allowance_resets: AllowanceReset[];
	derivation: string;
}

export interface RecalibrationReport {
	plan_id: string;
	from_version: number;
	to_version: number;
	approval: string;
	revision: PlanRevision;
	impact: RevisionImpact;
}

/** `herdsman/graph.py` — Overhead. The ledger-attributed ratio. */
export interface Overhead {
	orchestration_tokens: number;
	productive_tokens: number;
	/** `null` when there is no denominator yet. Null is unknown, never zero. */
	ratio: number | null;
	target: number;
	/** `null` when `ratio` is unknown. */
	within_target: boolean | null;
	/** The daemon's own sentence about how the figures were selected. */
	derivation: string;
	provenance: string[];
	/** Re-planning cost, attributed on its own so it never hides in planning. */
	recalibration_tokens: number;
	recalibration_calls: number;
	recalibration_derivation: string;
}

/** `herdsman/graph.py` — PlanGraph. */
export interface PlanGraph {
	plan_id: string;
	version: number;
	approval: string;
	nodes: NodeStatus[];
	edges: [string, string][];
	ready: string[];
	critical_path: string[];
	max_concurrency: number;
	overhead: Overhead;
}

/* --- the folded plan (`GET /plans/{id}`) ------------------------------------
 *
 * `PlanGraph` above is the projection the field is drawn from; it deliberately
 * carries no planner-authored content. The drawer (R2) reads the plan itself,
 * so these mirror `herdsman/classes.py` — every field the drawer reads, and no
 * field it does not. R4 widened `Checkpoint` to the whole model, which is what
 * R2 left for it: `Initiative.checkpoint_versions` is serialised on this same
 * route, so every preserved version's manifest arrives with the fold and only
 * its *decision* needs the checkpoint report below.
 */

/** `herdsman/classes.py` — Assignment. Which harness and model ran this. */
export interface Assignment {
	harness: string;
	model: string;
}

/** `herdsman/classes.py` — Routes. The paths an initiative declared. */
export interface Routes {
	reads: string[];
	writes: string[];
}

/** `herdsman/classes.py` — Contract. What a checkpoint must show to settle. */
export interface Contract {
	id: string;
	role: string;
	required_checks: string[];
	required_paths: string[];
	require_patch: boolean;
	allow_writes: boolean;
	/** `null` means no command policy, which is not the same as an empty list. */
	allowed_commands: string[] | null;
}

/** `herdsman/classes.py` — TokenSource. Where a count came from. */
export type TokenSource = 'harness' | 'provider' | 'gateway' | 'tokenizer' | 'estimate';
/** `herdsman/classes.py` — TokenPhase. The actual/preflight/estimate observation. */
export type TokenPhase = 'actual' | 'preflight' | 'estimate';
/** `herdsman/classes.py` — TokenCategory. The fixed attribution set. */
export type TokenCategory =
	| 'planning'
	| 'execution'
	| 'semantic_integration'
	| 'protocol'
	| 'repeated_context'
	| 'handoff'
	| 'monitoring'
	| 'control_plane'
	| 'retry_replay'
	| 'recalibration_replay'
	| 'memory';

/**
 * `herdsman/classes.py` — Usage. Token facts. `source` is the provenance, never
 * dropped, and counts from different sources are never summed. The surplus
 * fields are optional on the daemon side so old payloads replay unchanged; a
 * legacy `provider` row with no `phase` is coerced to `preflight` deliberately,
 * so it stays out of the productive denominator.
 */
export interface Usage {
	input_tokens: number;
	output_tokens: number;
	source: TokenSource;
	phase: TokenPhase;
	category: TokenCategory;
	provenance: string;
	measurement_id: string | null;
	semantic_work_id: string | null;
	gateway_used: boolean;
}

/**
 * `herdsman/observability.py` — TokenMeasurement. One attributable observation
 * in the ledger, after the fold's own normalization.
 */
export interface TokenMeasurement {
	entry_id: string;
	plan_id: string;
	initiative_id: string | null;
	attempt_id: string | null;
	phase: TokenPhase;
	source: TokenSource;
	category: TokenCategory;
	input_tokens: number;
	output_tokens: number;
	provenance: string;
	/** Checkpoint-usage rows only; packet and planner rows carry `null`. */
	observed_at: string | null;
	semantic_work_id: string | null;
	gateway_used: boolean;
}

/** `herdsman/observability.py` — TokenTotals. */
export interface TokenTotals {
	actual: number;
	preflight: number;
	estimate: number;
	productive: number;
	orchestration: number;
	/** Per figure, the provenance strings behind it. */
	provenance: Record<string, string[]>;
	/** Per figure, the daemon's own derivation sentence. */
	derivation: Record<string, string>;
}

/**
 * `herdsman/observability.py` — TokenLedger, the `GET /plans/{id}/tokens` body.
 *
 * Selection is never summing: one value per `semantic_work_id` at the highest
 * measurement rank, so the same work counted twice is replaced, not added.
 */
export interface TokenLedger {
	plan_id: string;
	entries: TokenMeasurement[];
	totals: TokenTotals;
	by_category: Record<string, number>;
	by_provenance: Record<string, number>;
	derivation: string;
	provenance: string[];
	accounted_tokens: number;
	accounted_derivation: string;
}

/** `herdsman/observability.py` — BurnDown, the single point the fold serves. */
export interface BurnDown {
	accounted_tokens: number;
	productive_tokens: number;
	orchestration_tokens: number;
	/** `null` when no plan cap was declared — not a cap of zero, not unlimited. */
	remaining_plan_cap: number | null;
	/** Per member; `null` means that member declares no cap of its own. */
	remaining_initiative_caps: Record<string, number | null>;
	derivation: string;
	provenance: string[];
}

/** `herdsman/observability.py` — TokenAnomaly. A deterministic burn finding. */
export interface TokenAnomaly {
	code: 'overhead' | 'exhausted-budget' | 'missing-usage' | 'conflicting-usage';
	message: string;
	initiative_id: string | null;
	attempt_id: string | null;
}

/**
 * `herdsman/observability.py` — MakespanETA. The daemon's own figure over the
 * longest remaining path of explicit `duration_estimate_seconds`; `eta` is
 * `null` with a `reason` when an estimate is missing, never a guess.
 */
export interface MakespanETA {
	eta: string | null;
	remaining_seconds: number | null;
	reason: string;
	derivation: string;
	provenance: string[];
}

/** `herdsman/classes.py` — CheckResult. One executed check and its verdict. */
export interface CheckResult {
	name: string;
	passed: boolean;
	summary: string;
}

/**
 * `herdsman/classes.py` — Checkpoint, whole. R2 read five fields and left the
 * rest to this unit to declare; R4 is that unit, so the surplus is here.
 *
 * It is an *evidence manifest*, mechanically populated — never model-authored
 * prose. `caveats` is the one field an executor writes, and it is restricted
 * to non-recoverable decisions and blockers, not a summary of the work.
 */
export interface Checkpoint {
	id: string;
	attempt_id: string;
	exit_code: number | null;
	usage: Usage | null;
	changed_paths: string[];
	/** The commit this attempt started from, and the one it left. */
	base_sha: string | null;
	head_sha: string | null;
	/** Every check that ran, passed and failed alike. */
	checks: CheckResult[];
	/**
	 * Project-relative path to this attempt's diff — the physical handoff
	 * artifact, always under `.herdsman/artifacts`. It is a *reference*: the
	 * daemon serves no route that returns the bytes, so this build names the
	 * patch and cannot show it.
	 */
	patch_path: string | null;
	/** Non-recoverable decisions, caveats or blockers written by the executor. */
	caveats: string[];
}

/**
 * `herdsman/classes.py` — PacketSection. One deterministic section of a
 * compiled packet, measured at preflight. `output_tokens` is 0 on every
 * section this build has seen: nothing had been generated when it was
 * measured, which is not an output of zero. `category`, `semantic_work_id`
 * and `gateway_used` are the token ledger's attribution vocabulary, declared
 * here so a later unit need not redeclare this type; the inspector does not
 * render them.
 */
export interface PacketSection {
	name: string;
	value: unknown;
	input_tokens: number;
	output_tokens: number;
	/** `herdsman/classes.py` — TokenSource, whole. The vocabulary is the
	    daemon's; the sentences for the two it measures with today live in
	    `packet.ts`, and anything else prints raw rather than guessed. */
	source: 'estimate' | 'harness' | 'provider' | 'gateway' | 'tokenizer';
	/** `herdsman/classes.py` — TokenPhase, whole. */
	phase: 'actual' | 'preflight' | 'estimate';
	provenance: string;
	/** The token ledger's attribution vocabulary. Not rendered by the inspector. */
	category: string;
	semantic_work_id: string | null;
	gateway_used: boolean;
}

/**
 * `herdsman/classes.py` — PacketSnapshot. The immutable packet receipt
 * persisted with an attempt reservation. `total_tokens` is the sum of the
 * section totals, validated by the daemon and never padded.
 */
export interface PacketSnapshot {
	sections: PacketSection[];
	total_tokens: number;
	provenance: string;
}

/** `herdsman/classes.py` — MemoryLeaf. Canonical project leaves and folded
 * run-scoped leaves share this projection. Versions are printed as served;
 * carried packet versions may also be content digests for run leaves. */
export type LeafOrigin =
	| 'redirect'
	| 'nudge'
	| 'operator-answer'
	| 'failure'
	| 'salvage'
	| 'operator'
	| 'promotion'
	| 'executor-proposal';

export interface MemoryLeaf {
	id: string;
	subject: string;
	claim: string;
	origin: LeafOrigin;
	by: string;
	at: string;
	evidence: string[];
	scope: string[];
	lifetime: 'run' | 'project';
	status: 'active' | 'stale' | 'conflicted' | 'retired';
	ttl: number | string | null;
	ttl_days: number | null;
	ttl_runs: number | null;
	body: string;
	owner_run: string | null;
	version: number;
	content_hash: string | null;
}

export interface MemoryReceipt {
	type: 'memory_use_recorded';
	at: string;
	operation: 'pointer' | 'inline' | 'pull' | 'auto-answer' | 'salvage' | 'dreaming';
	tokens: number;
	provenance: 'estimate';
	attempt_id: string | null;
	run_id: string | null;
	leaf_ids: string[];
	leaf_versions: string[];
	source_run: string | null;
}

export interface MemoryAttentionBatch {
	type: 'memory_attention_recorded';
	at: string;
	batch_id: string;
	initiative_id: string;
	leaf_ids: string[];
	statuses: Record<string, 'stale' | 'conflicted'>;
	summary: string;
}

export interface MemoryDigest {
	type: 'memory_digest_recorded';
	at: string;
	source_run: string;
	leaf_ids: string[];
	summary: string;
}

export interface MemoryStatus {
	plan_id: string;
	leaves: MemoryLeaf[];
	attention: MemoryAttentionBatch[];
}

export interface MemoryCapabilityReport {
	harnesses: Record<string, 'A' | 'B' | 'C'>;
	author: { binary: string; model: string; timeout: number } | null;
}

/**
 * `herdsman/observability.py` — PacketDiff. Whole-section granularity:
 * sections are compared by name and by value, and nothing inside a section is
 * compared. `derivation` is the daemon's own description of what the number
 * means; it is printed verbatim and never paraphrased.
 */
export interface PacketDiff {
	changed_sections: string[];
	added_sections: string[];
	removed_sections: string[];
	token_delta: number;
	before_tokens: number;
	after_tokens: number;
	provenance: string[];
	derivation: string;
}

/** `herdsman/classes.py` — Subtask. Ids are `{initiative}.{n}`, n from 1. */
export interface Subtask {
	id: string;
	brief: string;
	state: 'todo' | 'doing' | 'done' | 'skipped';
}

/** `herdsman/classes.py` — Attempt. One run; a retry appends another. */
export interface Attempt {
	id: string;
	initiative_id: string;
	/** Recorded per attempt, so a reassignment does not rewrite history. */
	assignment: Assignment;
	/**
	 * Which brief version this attempt actually ran on — snapshotted per
	 * attempt, so a redirect never rewrites what an earlier run was told. 1 is
	 * the planner's brief. R6 reads it; R2 could not, because nothing moved it.
	 */
	brief_version: number;
	/** Who reserved the attempt. `daemon` for an ordinary run. */
	by: string;
	/** An ordinary run, or an operator retry of failed work. */
	origin: 'run' | 'retry';
	worktree_ref: string | null;
	pane_ref: string | null;
	started_at: string;
	/** Only a recorded checkpoint closes an attempt; a failure leaves it null. */
	ended_at: string | null;
	checkpoint: Checkpoint | null;
	packet_tokens: number;
	/**
	 * The exact sections this attempt received, when one was persisted. Null is
	 * a recorded absence — a present fact from the fold, not a failed read.
	 */
	packet_snapshot: PacketSnapshot | null;
	/** Leaves and delivery mode recorded when this attempt's packet was compiled. */
	memory_leaf_ids: string[];
	memory_leaf_versions: string[];
	memory_mode: 'legacy' | 'pointer' | 'inline';
}

/** `herdsman/classes.py` — InitiativeSpec. Planner-authored, immutable. */
export interface InitiativeSpec {
	id: string;
	name: string;
	brief: string;
	assignment: Assignment;
	routes: Routes;
	depends_on: string[];
	approval: 'automatic' | 'required';
	contract: Contract | null;
	/** Retry ceiling and escalation rules. The fold enforces `max_attempts`. */
	policy: InitiativePolicy;
	/** Declared admission ceiling for this member alone; `null` is undeclared. */
	token_cap: number | null;
	/** Explicit duration estimate the makespan ETA is computed from. */
	duration_estimate_seconds: number | null;
}

/**
 * `herdsman/classes.py` — InitiativePolicy, narrowed to what R6 reads.
 *
 * `max_attempts` is a *fold* invariant, not daemon memory: `Plan._apply`
 * refuses an `AttemptStarted` past the ceiling, so neither a direct append nor
 * a replay can exceed it. That is why the retry control can state how many
 * attempts are left and be right.
 */
export interface InitiativePolicy {
	max_attempts: number;
}

/**
 * `herdsman/classes.py` — TaskBriefVersion. One operator redirect.
 *
 * Version 1 is the planner-authored `InitiativeSpec.brief` and is *never*
 * stored here; versions 2+ are the appended redirects. So the brief an attempt
 * runs on is the last entry of this list, or the spec's brief when it is empty.
 */
export interface TaskBriefVersion {
	version: number;
	brief: string;
	by: string;
	at: string;
	reason: string;
}

/**
 * `herdsman/classes.py` — InitiativeFailure. One recorded failure and the
 * evidence preserved with it.
 *
 * R2 had to say that `InitiativeFailed.reason` was dropped by the fold and
 * survived only on the live stream. It is projected here now, so a reloaded
 * page reads why a member failed instead of naming the gap.
 */
export interface InitiativeFailure {
	reason: string;
	evidence: string[];
}

/** `herdsman/daemon.py` — RecoveryAttempt. */
export interface RecoveryAttempt {
	initiative_id: string;
	attempt_id: string;
	pane_ref: string | null;
	worktree_ref: string | null;
}

/** `herdsman/daemon.py` — RecoveryReport. */
export interface RecoveryReport {
	plan_id: string;
	stale: RecoveryAttempt[];
	outcomes: Record<string, ResumeOutcome>;
	orphaned_worktrees: string[];
	orphaned_panes: string[];
}

export type ResumeOutcome =
	| 'reattached'
	| 'settled'
	| 'review-pending'
	| 'failed'
	| 'skipped'
	| (string & {});

/** `herdsman/classes.py` — the fold's recorded outcome of a member write. */
export interface InterventionResult {
	id?: string;
	[key: string]: unknown;
}

/** `herdsman/classes.py` — Initiative. */
export interface CheckpointDecision {
	state: Decision;
	decided_at: string | null;
	decided_by: string;
	reason: string;
	approved_at: string | null;
}

export interface PolicyDecisionRecorded {
	initiative_id: string;
	attempt_id: string | null;
	checkpoint_id: string | null;
	outcome: 'approved' | 'stopped' | 'escalated';
	rule_ids: string[];
	reason: string;
	at: string;
	seq: number;
}

export interface Initiative {
	spec: InitiativeSpec;
	subtasks: Subtask[];
	attempts: Attempt[];
	state: InitiativeState;
	/**
	 * Every recorded checkpoint version, in record order — the immutable
	 * history R4 reads manifests from. Nothing is ever removed: a revision
	 * after a rejection appends, so refused evidence stays readable. The last
	 * entry is the current version. `checkpoint_decisions` is folded here so
	 * historical replay can read each version's recorded decision.
	 */
	checkpoint_versions: Checkpoint[];
	/** Review decisions folded with the historical plan prefix. */
	checkpoint_decisions?: Record<string, CheckpointDecision>;
	/**
	 * One entry per recorded failure, in event order — the reason as recorded
	 * and the diagnostic paths preserved with it. Bounded by the attempt
	 * ceiling, so at most one per attempt.
	 */
	failures: InitiativeFailure[];
	/** Operator redirects, in order. Empty means the brief is still the planner's. */
	brief_versions: TaskBriefVersion[];
	/**
	 * The operator's harness/model override, or `null` for the planner's choice.
	 * It applies to the *next* attempt only: a running attempt finishes on its
	 * own snapshot and past attempts keep theirs.
	 */
	assignment_override: Assignment | null;
	/** Historical ids this member answered to after recalibration renames. */
	id_history: string[];
}

/**
 * `herdsman/classes.py` — Plan, the fold of one plan's whole event stream.
 *
 * `RuntimeObserved` is streamed and audited without projected state, so activity
 * exists only on the live stream. Automatic decisions are folded in
 * `policy_decisions` and remain available to historical replay.
 */
export interface Plan {
	id: string;
	version: number;
	brief: string;
	approval: 'pending' | 'approved';
	initiatives: Record<string, Initiative>;
	created_at: string;
	/**
	 * The run's declared token ceiling, recorded at proposal. `null` is no
	 * declared cap — not unlimited — and a declared one is enforced when an
	 * attempt starts: the fold refuses a start that would carry the run past it.
	 */
	token_cap: number | null;
	/** Which harness and model planned it. R3 names who proposed what it approves. */
	planner: Assignment | null;
	/**
	 * What the planning call cost — the only token figure a *proposed* plan has,
	 * because nothing has run. R3 reads it, so R3 declares it. `null` is a plan
	 * whose planner reported nothing (every locally seeded fixture), which is
	 * unknown and never zero.
	 */
	planner_usage: Usage | null;
	/**
	 * Every asset each approved version froze, keyed by plan version.
	 *
	 * Immutable by construction: the bytes travelled inside `PlanApproved`,
	 * so a later shelf edit cannot reach backwards into an approval. A
	 * version approved before Sprint 9, or one that declared no assets, has
	 * no entry — which is unknown, not an empty set.
	 */
	asset_snapshots: Record<string, LibrarySnapshot>;
	/** Run-scoped intervention leaves, projected from events. */
	memory_leaves: MemoryLeaf[];
	/** Daemon-written project leaves, retained as an audit projection. */
	project_memory_leaves: MemoryLeaf[];
	memory_receipts: MemoryReceipt[];
	memory_digests: MemoryDigest[];
	memory_attention: MemoryAttentionBatch[];
	/** Initiatives moved out of the live revision; salvage still reads their evidence. */
	retired: Initiative[];
	/** Automatic decisions folded with the historical plan prefix. */
	policy_decisions?: PolicyDecisionRecorded[];
	/** When each attempt stopped being the live attempt. */
	live_until?: Record<string, string>;
}

/**
 * `herdsman/classes.py` — RuntimeObserved, as it arrives on the event stream.
 *
 * The fold matches this event and passes: it is streamed and audited and
 * carries no projected state. So a consumer that wants activity has to keep
 * what it sees, and what it did not see is unread rather than absent.
 */
export interface RuntimeObservedFrame {
	attempt_id: string;
	kind: string;
	at: string;
}

/**
 * `herdsman/classes.py` — InitiativeFailed. The fold stores `state = "failed"`
 * and drops `reason`, so this frame is the only place the sentence exists.
 */
export interface InitiativeFailedFrame {
	initiative_id: string;
	reason: string;
	at: string;
}

/* --- the checkpoint report (`GET /plans/{id}/checkpoints`) -------------------
 *
 * The fourth read, and R4's own. It is a *lifecycle* projection: who decided
 * what about which version, and when. The evidence those decisions are about
 * — checks, artifacts, shas, caveats, the patch reference — is on the
 * `Checkpoint` objects in the folded plan above. Neither read is sufficient on
 * its own, and R4's model joins them by checkpoint id rather than widening
 * either one.
 */

/** `herdsman/classes.py` — CheckpointDecision.state. */
export type Decision = 'pending' | 'approved' | 'rejected' | 'changes_requested';

/** `herdsman/daemon.py` — CheckpointVersionView. One preserved version. */
export interface CheckpointVersionView {
	/** 1-based, in record order. This is the immutable version identity. */
	version: number;
	checkpoint_id: string;
	attempt_id: string;
	decision: Decision;
	decided_at: string | null;
	decided_by: string;
	reason: string;
	/**
	 * When this version was approved, if it ever was — kept through a later
	 * rejection, because consumers released by that approval built on it.
	 */
	approved_at: string | null;
	/** A newer version exists. Nothing is ever removed. */
	superseded: boolean;
	exit_code: number | null;
	failed_checks: string[];
	failed_check_summaries: Record<string, string>;
	changed_paths: string[];
	patch_path: string | null;
	/** Changed paths grouped into logical cohorts. Absent from a daemon older
	 *  than the projection, which the model handles as ungrouped. */
	walkthrough?: Walkthrough;
}

/** `herdsman/walkthrough.py` — Cohort. Name, paths and summary are the
 *  daemon's; the UI classifies nothing and rewrites neither string. */
export interface Cohort {
	/** Tabled name, the top-level directory, or `(root)`. Print verbatim. */
	name: string;
	/** Sorted, deduplicated. */
	paths: string[];
	/** Deterministic count-and-scope line. Never model-authored. */
	summary: string;
}

/** `herdsman/walkthrough.py` — Walkthrough, per preserved version. */
export interface Walkthrough {
	/** Sorted by name. Render in this order; do not re-sort. */
	cohorts: Cohort[];
	/** The daemon's deduplicated total. Print it; do not recompute it. */
	total_files: number;
}

/** `herdsman/daemon.py` — InitiativeReviewView. */
export interface InitiativeReviewView {
	initiative_id: string;
	name: string;
	policy: string;
	state: string;
	awaiting_review: boolean;
	/** The latest version ever approved: the base a change list is read against. */
	approved_version: number | null;
	approved_checkpoint_id: string | null;
	/**
	 * Paths in the latest version that were not in the approved one — a set
	 * subtraction over path names, and only the added half of it. It is not a
	 * content diff and the daemon has no route that serves one.
	 */
	changes_since_approved: string[];
	/** Typed contract failures of the *latest* version, as `code: message`. */
	violations: string[];
	versions: CheckpointVersionView[];
}

/** `herdsman/classes.py` — Taint. Work resting on invalidated evidence. */
export interface Taint {
	initiative_id: string;
	producer_id: string;
	checkpoint_id: string;
	reason: string;
}

/** `herdsman/daemon.py` — CheckpointReport, the whole plan's review surface. */
export interface CheckpointReport {
	plan_id: string;
	initiatives: InitiativeReviewView[];
	attention: Taint[];
}

/** The three review writes. Each is a different downstream consequence. */
export type Verdict = 'approve' | 'reject' | 'changes';

/* --- downstream impact (`GET /plans/{id}/initiatives/{iid}/impact`) ---------
 *
 * R6's own read, and the only honest answer to "what does this disturb". It is
 * the daemon's own projection over the folded plan, not a second opinion
 * computed in the browser: the same figure the write routes return under
 * `preview`, so the sentence shown before confirming and the rule applied on
 * confirming come from one place.
 */

/** `herdsman/graph.py` — NodeImpact. One member downstream of the target. */
export interface NodeImpact {
	initiative_id: string;
	state: string;
	attempts: number;
}

/** `herdsman/graph.py` — DownstreamImpact. */
export interface DownstreamImpact {
	/** The action's target. Documented as *not* itself part of the impact. */
	initiative_id: string;
	/** Everything downstream, in build order. */
	descendants: NodeImpact[];
	/** Descendants that already ran — work the action would strand or redo. */
	started: string[];
}

/** `herdsman/graph.py` — ContentionKind. */
export type ContentionKind = 'write_write' | 'write_read';

/** `herdsman/graph.py` — Contention. */
export interface Contention {
	/** Sorted, so the pair has one stable identity regardless of direction. */
	initiatives: [string, string];
	paths: string[];
	kind: ContentionKind;
	writer: string | null;
	reader: string | null;
}

/** `herdsman/graph.py` — NodeRisk. */
export interface NodeRisk {
	initiative_id: string;
	digest: string;
	blast_radius: number;
	articulation: boolean;
	on_critical_path: boolean;
}

/** `herdsman/graph.py` — RiskReport. */
export interface RiskReport {
	plan_id: string;
	version: number;
	critical_path: string[];
	max_concurrency: number;
	nodes: NodeRisk[];
	conflicts: Contention[];
	suggested_edges: Contention[];
	warnings: string[];
}

/** `herdsman/nav.py` — one indexed file (`NavIndex.files`). */
export interface NavFile {
	path: string;
	loc: number;
}

/** `herdsman/nav.py` — one indexed symbol (`NavIndex.symbols`). */
export interface NavSymbol {
	name: string;
	kind: 'class' | 'function' | 'method';
	module: string;
	file: string;
	line: number;
	end_line: number;
	signature: string;
	bases: string[];
	returns: string;
	exported: boolean;
	doc: string;
}

/** `herdsman/nav.py` — one resolved edge (`NavIndex.edges`). */
export interface NavEdge {
	kind: 'contains' | 'imports' | 'calls' | 'instantiates' | 'references';
	src: string;
	dst: string;
	file: string;
	line: number;
	resolution: 'static' | 'dynamic' | 'external';
}

/** `herdsman/nav.py` — one unresolvable call (`NavIndex.unresolved`). */
export interface NavUnresolved {
	kind: string;
	src: string;
	name: string;
	file: string;
	line: number;
}

/** `herdsman/nav.py` — PEP 621 `[project.scripts]` entry. */
export interface NavConsoleScript {
	name: string;
	target: string;
	file: string;
	line: number | null;
}

/** `herdsman/nav.py` — one Typer command (`entry_points.cli`). */
export interface NavCliCommand {
	command: string;
	file: string;
	line: number;
}

/** `herdsman/nav.py` — one `add_api_route` call (`entry_points.routes`). */
export interface NavRoute {
	method: string;
	path: string;
	handler: string;
	file: string;
	line: number;
}

/** `herdsman/nav.py` — one `test*` function in a pytest test file (`entry_points.tests`). */
export interface NavTestEntry {
	node: string;
	file: string;
	line: number;
}

/** `herdsman/nav.py` — `_EntryPoints.to_dict()`. */
export interface NavEntryPoints {
	console_script: NavConsoleScript | null;
	cli: NavCliCommand[];
	routes: NavRoute[];
	tests: NavTestEntry[];
}

/** `herdsman/nav.py` — one codegraph-only cross-check row (`coverage.deep_conflicts`). */
export interface NavDeepConflict {
	symbol: string;
	direction: string;
	edge: string;
	note: string;
}

/** `herdsman/nav.py` — `NavIndex.coverage`. */
export interface NavCoverage {
	languages: string[];
	excluded: string[];
	deep: boolean;
	deep_note?: string;
	deep_conflicts?: NavDeepConflict[];
}

/** `herdsman/nav.py` — `NavIndex.to_dict()`, the `GET /nav/codemap` body. */
export interface NavIndex {
	repo_ref: string | null;
	fingerprint: string;
	coverage: NavCoverage;
	files: NavFile[];
	symbols: NavSymbol[];
	edges: NavEdge[];
	entry_points: NavEntryPoints;
	unresolved: NavUnresolved[];
}

/** The text envelope for `GET /nav/tour`, `/nav/flow/{name}`, and `/nav/symbol/{name}`. */
export interface NavText {
	text: string;
}

/**
 * Why a request did not produce data. The distinction matters on screen: a
 * daemon that is not running is a different sentence from a plan that does not
 * exist, and both are different from a plan the daemon refused to act on.
 */
export type FailureKind = 'unreachable' | 'not_found' | 'conflict' | 'bad_response' | 'aborted';

export class DaemonError extends Error {
	readonly kind: FailureKind;
	readonly status: number | null;

	constructor(kind: FailureKind, message: string, status: number | null = null) {
		super(message);
		this.name = 'DaemonError';
		this.kind = kind;
		this.status = status;
	}
}

/** Same origin in production (the daemon serves the built folder); proxied in dev. */
const BASE = '';

async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
	let response: Response;
	try {
		response = await fetch(`${BASE}${path}`, { signal, headers: { accept: 'application/json' } });
	} catch (cause) {
		if (signal?.aborted) throw new DaemonError('aborted', 'request cancelled');
		throw new DaemonError('unreachable', 'The Herdsman daemon is not answering.', null);
	}
	if (response.status === 404) {
		throw new DaemonError('not_found', await detail(response, 'Not found.'), 404);
	}
	// A dead daemon behind the dev proxy surfaces as a gateway error, not a
	// network failure, so the operator must still be told the daemon is down and
	// how to start it -- not handed the proxy's status code.
	if (response.status === 502 || response.status === 503 || response.status === 504) {
		throw new DaemonError('unreachable', 'The Herdsman daemon is not answering.', response.status);
	}
	if (!response.ok) {
		const kind: FailureKind = response.status === 409 ? 'conflict' : 'bad_response';
		throw new DaemonError(kind, await detail(response, `Daemon returned ${response.status}.`), response.status);
	}
	try {
		return (await response.json()) as T;
	} catch {
		throw new DaemonError('bad_response', 'The daemon returned a body this build cannot read.', response.status);
	}
}

async function post<T>(path: string, signal?: AbortSignal, body?: unknown): Promise<T> {
	if (isHistorical()) throw new DaemonError('conflict', 'historical replay is read-only');
	let response: Response;
	try {
		response = await fetch(`${BASE}${path}`, {
			method: 'POST',
			signal,
			headers:
				body === undefined
					? { accept: 'application/json' }
					: { accept: 'application/json', 'content-type': 'application/json' },
			body: body === undefined ? undefined : JSON.stringify(body)
		});
	} catch {
		if (signal?.aborted) throw new DaemonError('aborted', 'request cancelled');
		throw new DaemonError('unreachable', 'The Herdsman daemon is not answering.', null);
	}
	if (response.status === 404) {
		throw new DaemonError('not_found', await detail(response, 'Not found.'), 404);
	}
	if (response.status === 502 || response.status === 503 || response.status === 504) {
		throw new DaemonError('unreachable', 'The Herdsman daemon is not answering.', response.status);
	}
	if (!response.ok) {
		const kind: FailureKind = response.status === 409 ? 'conflict' : 'bad_response';
		throw new DaemonError(kind, await detail(response, `Daemon returned ${response.status}.`), response.status);
	}
	try {
		return (await response.json()) as T;
	} catch {
		throw new DaemonError('bad_response', 'The daemon returned a body this build cannot read.', response.status);
	}
}

/** FastAPI puts its message in `detail`; fall back rather than showing `[object Object]`. */
async function detail(response: Response, fallback: string): Promise<string> {
	try {
		const body: unknown = await response.json();
		if (body && typeof body === 'object' && 'detail' in body) {
			const value = (body as { detail: unknown }).detail;
			if (typeof value === 'string' && value.length > 0) return value;
		}
	} catch {
		/* fall through */
	}
	return fallback;
}

/** `herdsman/fleet.py` — DeepLink. The one spelling of a Run location. */
export interface DeepLink {
	path: string;
}

/**
 * `herdsman/fleet.py` — AttentionKind. Closed set, matched by name; the daemon
 * widens it to `str`, so an unknown kind from a newer daemon must still read.
 */
export type AttentionKind =
	| 'plan_gate'
	| 'checkpoint_review'
	| 'blocked_on_user'
	| 'failed'
	| 'stalled';

/** `herdsman/fleet.py` — ActionTarget. The one call that resolves an item. */
export interface ActionTarget {
	method: string;
	/** Already substituted by the daemon — never a template. */
	path: string;
	label: string;
}

/**
 * `herdsman/fleet.py` — AttentionItem. One thing that needs the user.
 *
 * Home reads these to *count* what is waiting and to address the single
 * oldest one precisely. The attention feed itself is H2's; this build does
 * not list them, and `action` is deliberately unused here — pressing it is
 * the feed's job, not the overview's.
 */
export interface AttentionItem {
	key: string;
	kind: string;
	plan_id: string;
	initiative_id: string | null;
	attempt_id: string | null;
	checkpoint_id: string | null;
	summary: string;
	since: string;
	/** Nothing proceeds until the user acts. The notification predicate. */
	blocking: boolean;
	action: ActionTarget;
	link: DeepLink;
}

/**
 * `herdsman/fleet.py` — RunSpend. What one run cost, and what it has left.
 *
 * Coarse on purpose: per-role attribution is Sprint 6-A's. `sources` is the
 * provenance the shared contract requires — an empty list means nothing has
 * been measured, which is unknown and must never be drawn as a measured zero.
 * `cap: null` means no budget was ever declared; it is not a cap of zero and
 * not an unlimited one. A declared cap IS enforced when an attempt starts —
 * the fold refuses a start that would carry the run past it — but it does not
 * interrupt an attempt already running.
 */
export interface RunSpend {
	accounted: number;
	/** `actual` | `preflight` | `estimate`, highest precedence first. */
	sources: string[];
	cap: number | null;
	remaining: number | null;
}

/** `herdsman/fleet.py` — FleetSpend. Summed over the listed runs only. */
export interface FleetSpend {
	accounted: number;
	sources: string[];
	/** How many listed runs declare a cap; every figure below covers only those. */
	capped_runs: number;
	cap: number | null;
	remaining: number | null;
}

/** `herdsman/fleet.py` — RunRollup. One run's row in the fleet. */
export interface RunRollup {
	plan_id: string;
	brief: string;
	version: number;
	approval: string;
	/** Widened to `str` by the daemon; `idle | running | awaiting_approval | …`. */
	status: string;
	archived: boolean;
	created_at: string;
	updated_at: string;
	/** Initiatives per state. */
	counts: Record<string, number>;
	total: number;
	/** Settled over total, 0 for an empty or unproposed plan. */
	progress: number;
	/** Oldest first. Absent on a daemon older than this build. */
	attention?: AttentionItem[];
	/** Absent on a daemon older than this build; unknown, never zero. */
	spend?: RunSpend;
	link: DeepLink;
}

/**
 * `herdsman/fleet.py` — Fleet. Sprint 10's collection route, and the plan
 * enumeration this UI chooses a plan from. `unreadable` names plans on disk
 * whose events would not fold: they are in no count, and a picker that hides
 * them would report fewer runs than exist.
 */
export interface Fleet {
	runs: RunRollup[];
	/** Archived runs among those given, listed or not — the toggle's count. */
	archived: number;
	counts: Record<string, number>;
	running_runs: number;
	total_runs: number;
	/** Merged across the listed runs, oldest first. Absent on an older daemon. */
	attention?: AttentionItem[];
	/** The blocking subset of `attention`, same order. */
	notifications?: AttentionItem[];
	/** Absent on an older daemon. */
	spend?: FleetSpend;
	unreadable: string[];
}

/* --- the observability projections (`GET /plans/{id}/status|tokens`) --------
 *
 * R8's instruments. `Daemon.status` bundles the graph, the overhead ratio,
 * the burn-down point, the makespan ETA, the anomalies and the activity
 * projections into one read; `Daemon.tokens` is the separate ledger read the
 * category attribution comes from, because the bundle carries no
 * `by_category`. Both are deterministic daemon reducers — no model call.
 */

/**
 * `herdsman/daemon.py` — the `GET /plans/{id}/status` bundle.
 *
 * R8 reads `overhead`, `burn_down`, `eta` and `anomalies` and ignores the rest
 * of the bundle rather than adding reads for it; the shapes it ignores are
 * held as `unknown` until a consumer unit declares them.
 */
export interface StatusBundle {
	plan_id: string;
	graph: PlanGraph;
	overhead: Overhead;
	burn_down: BurnDown;
	eta: MakespanETA;
	anomalies: TokenAnomaly[];
	/** Not read by this build's consumer; the daemon sends them regardless. */
	vitals: unknown;
	activity: unknown;
	attention: unknown;
	events: unknown;
}


export type CapabilityState = 'supported' | 'unsupported' | 'unknown';

/** `herdsman/kitchen.py` — HealthState, observed by the version probe. */
export type HealthState = 'healthy' | 'unhealthy' | 'unknown';

/** `herdsman/kitchen.py` — ReadinessState, the daemon's own verdict per harness. */
export type ReadinessState = 'ready' | 'degraded' | 'unavailable' | 'unknown' | 'unconfigured';

/** `herdsman/kitchen.py` — MemoryClass. A pointer+pull, B A+project sugar, C budgeted inline. */
export type MemoryClass = 'A' | 'B' | 'C';

/**
 * `herdsman/kitchen.py` — Capabilities. Every field is a *declaration*: the
 * project wrote it into `.herdsman/kitchen.json`, and nothing in the daemon
 * observes any of them. `unknown` is undeclared and is never read as a no;
 * `memory: null` is undeclared and is never guessed at a class.
 */
export interface KitchenCapabilities {
	structured_output: CapabilityState;
	resume: CapabilityState;
	usage: CapabilityState;
	/** `supported` means this harness *needs* a PTY; herdr owns supplying one. */
	pty: CapabilityState;
	memory: MemoryClass | null;
}

/**
 * `herdsman/kitchen.py` — Adapter. One configured harness.
 *
 * `argv` and `model_argv` are deliberately absent from this type. They are the
 * launch template, and a launch template can carry a credential in a flag; the
 * resolved executable on `HarnessFacts` is the identity an operator needs, so
 * this app never has the rest of the command line in hand to render by mistake.
 */
/**
 * `herdsman/classes.py` — AssetKind. Closed set, matched by name.
 *
 * `memory-leaf` is a Library kind but not a Library *shelf* kind: those leaves
 * live in the memory store and the memory shelf is L3's. It is typed here
 * because `GET /library` can return one and a client that cannot name it would
 * have to drop it silently.
 */
export type AssetKind =
	| 'role'
	| 'contract'
	| 'skill'
	| 'agent'
	| 'checkpoint-template'
	| 'memory-leaf';

/** `herdsman/classes.py` — AssetOrigin. Bundled ships read-only; project shadows it. */
export type AssetOrigin = 'bundled' | 'project';

/** `herdsman/classes.py` — AssetStatus. One vocabulary for every kind. */
export type AssetStatus = 'active' | 'stale' | 'conflicted' | 'retired';

/** `herdsman/classes.py` — LibraryIssueCode. Closed set, matched by code. */
export type LibraryIssueCode =
	| 'reference-missing'
	| 'reference-retired'
	| 'reference-cycle'
	| 'context-size'
	| 'memory-stale'
	| 'memory-conflicted'
	| 'contract-ambiguous'
	| 'contract-conflict';

/** `herdsman/classes.py` — LibraryIssue. One finding against an asset or a set. */
export interface LibraryIssue {
	code: LibraryIssueCode;
	severity: 'error' | 'warning';
	/** The asset it is about, or the owner name for a set-wide finding. */
	ref: string;
	message: string;
	detail: string;
}

/** `herdsman/library.py` — AssetSummary. One browse row, without the body. */
export interface AssetSummary {
	ref: string;
	kind: AssetKind;
	name: string;
	title: string;
	origin: AssetOrigin;
	status: AssetStatus;
	/** Content-addressed revision; derived, and it excludes origin. */
	digest: string;
	/** Effective context cost, counted the way every memory budget is. */
	tokens: number;
	references: string[];
	/** A project copy is standing in front of a bundled asset of the same ref. */
	shadows_bundled: boolean;
}

/**
 * `herdsman/library.py` — Asset, plus the derived fields the route adds.
 *
 * `fields` is the parsed frontmatter beyond the structured ones: semantic on a
 * contract, where the gates live, and identity noise everywhere else.
 */
export interface Asset {
	ref: string;
	kind: AssetKind;
	name: string;
	title: string;
	references: string[];
	fields: Record<string, unknown>;
	body: string;
	origin: AssetOrigin;
	status: AssetStatus;
	digest: string;
	tokens: number;
}

/** `herdsman/classes.py` — AssetSnapshot. One asset frozen byte-for-byte at approval. */
export interface AssetSnapshot {
	ref: string;
	kind: AssetKind;
	name: string;
	origin: AssetOrigin;
	title: string;
	references: string[];
	body: string;
	digest: string;
	tokens: number;
	contract: Contract | null;
}

/**
 * `herdsman/classes.py` — LibrarySnapshot. What one approved plan version froze.
 *
 * `by_initiative` is the narrow-injection rule as data: an initiative carries
 * only the closure it declared, never the union and never the shelf. An
 * initiative that declared nothing has no entry at all.
 */
export interface LibrarySnapshot {
	assets: AssetSnapshot[];
	by_initiative: Record<string, string[]>;
	/** Warnings recorded at approval. Errors block approval, so none appear. */
	issues: LibraryIssue[];
}

export interface KitchenAdapter {
	name: string;
	source: string;
	capabilities: KitchenCapabilities;
}

/** `herdsman/kitchen.py` — ModelEntry. Identity is the *pair*, never the label. */
export interface KitchenModel {
	harness: string;
	model: string;
	source: string;
	tier: string | null;
}

/**
 * `herdsman/kitchen.py` — HarnessFacts. Observed, not declared: what one
 * bounded `--version` probe actually saw. `detail` is the probe's own sentence
 * about why it stopped, and is quoted rather than rewritten.
 */
export interface HarnessFacts {
	harness: string;
	executable: string | null;
	version: string | null;
	health: HealthState;
	detail: string;
}

/** `herdsman/kitchen.py` — Readiness. One state plus the one action that clears it. */
export interface KitchenReadiness {
	harness: string;
	state: ReadinessState;
	reason: string;
	action: string;
	version: string | null;
}

/**
 * `herdsman/daemon.py` — the discovery pass behind the projection.
 *
 * `models` is always empty and that is the substrate's own decision, not a gap
 * in this read: a generic adapter has no read-only model-listing seam, so the
 * catalog stays declaration-fed (`herdsman/discovery.py`).
 */
export interface KitchenDiscovery {
	facts: HarnessFacts[];
	models: KitchenModel[];
}

/**
 * `herdsman/kitchen.py` — KitchenProjection, plus the daemon's latest discovery.
 *
 * `configured: false` is the empty-catalog case and is not an error: it means
 * `.herdsman/kitchen.json` declares no adapter yet, and `blockers` says so in
 * the daemon's own words.
 *
 * `discovery.facts` is held in daemon memory, not on disk: a daemon that has
 * not probed since it started answers with an empty list, and every readiness
 * is `unknown` until something asks it to look.
 */
export interface Kitchen {
	version: number;
	configured: boolean;
	ready: boolean;
	revision: string;
	adapters: KitchenAdapter[];
	models: KitchenModel[];
	readiness: KitchenReadiness[];
	discovery: KitchenDiscovery;
	blockers: string[];
	notes: string[];
	/** The effective-context warning threshold the Library validates against. */
	context_warning_tokens: number;
}

export const daemon = {
	/**
	 * `GET /fleet` — every run on disk. This is the plan enumeration; there is
	 * no `GET /plans` collection route and none is needed.
	 */
	fleet: (signal?: AbortSignal): Promise<Fleet> => get<Fleet>('/fleet', signal),

	/**
	 * `GET /fleet/archived` — the runs taken out of active navigation.
	 *
	 * A separate read rather than one `?include_archived=true` list, because
	 * every aggregate on a `Fleet` — counts, running runs, attention, spend —
	 * is summed over the runs it lists. Reading both halves at once would give
	 * the active view archived totals. The active read's `archived` field is
	 * the count the toggle shows, so this is only fetched once it is opened.
	 */
	fleetArchived: (signal?: AbortSignal): Promise<Fleet> =>
		get<Fleet>('/fleet/archived', signal),

	/**
	 * `POST /plans/{id}/archive` — move one run out of active fleet navigation.
	 *
	 * Navigation only, by the daemon's own definition: it appends a
	 * `PlanArchived` event and changes no work. A running initiative keeps
	 * running; the run simply stops appearing in the active list.
	 *
	 * `action_id` is the daemon's idempotency key — a repeat of the same
	 * request is answered from the fold's record rather than appending twice.
	 */
	archive: (
		planId: string,
		reason: string,
		actionId: string,
		signal?: AbortSignal
	): Promise<Plan> =>
		post<Plan>(`/plans/${encodeURIComponent(planId)}/archive`, signal, {
			by: 'operator',
			reason,
			action_id: actionId
		}),

	/** `POST /plans/{id}/unarchive` — return one run to active navigation. */
	unarchive: (
		planId: string,
		reason: string,
		actionId: string,
		signal?: AbortSignal
	): Promise<Plan> =>
		post<Plan>(`/plans/${encodeURIComponent(planId)}/unarchive`, signal, {
			by: 'operator',
			reason,
			action_id: actionId
		}),

	/**
	 * `GET /library` — the shelf, read whole.
	 *
	 * `status=all` on purpose, and it is the only sensible read for this surface:
	 * the reference closure has to be able to tell an archived reference from a
	 * missing one, and a shelf index that already dropped retired assets reports
	 * the first as the second. Which rows are *shown* is a client-side filter
	 * over this one read, so changing a filter costs nothing and cannot move the
	 * reading position. Every call reads what is on disk right now — a terminal
	 * edit is visible to the next read with nothing to invalidate.
	 */
	library: (signal?: AbortSignal): Promise<AssetSummary[]> =>
		get<AssetSummary[]>('/library?status=all', signal),

	/** `GET /library/{kind}/{name}` — one asset, project copy winning over bundled. */
	asset: (ref: string, signal?: AbortSignal): Promise<Asset> =>
		get<Asset>(
			`/library/${ref
				.split('/')
				.map((part) => encodeURIComponent(part))
				.join('/')}`,
			signal
		),

	/**
	 * `POST /library/validate` — the daemon's findings for one declared set.
	 *
	 * The authority for every finding this view prints. The sheet walks the
	 * reference graph itself to draw the chain, but what is *wrong* with a
	 * closure — a missing reference, an archived one, a cycle, and whether the
	 * effective context is over the project's budget — is the daemon's answer,
	 * computed against the budget in `.herdsman/kitchen.json` rather than a
	 * number this build carries.
	 */
	validateAssets: (
		refs: string[],
		owner: string,
		signal?: AbortSignal
	): Promise<{ issues: LibraryIssue[] }> =>
		post<{ issues: LibraryIssue[] }>('/library/validate', signal, { refs, owner }),

	/** `GET /kitchen` — the harness and model catalog a choice is made from. */
	kitchen: (signal?: AbortSignal): Promise<Kitchen> => get<Kitchen>('/kitchen', signal),

	/**
	 * `POST /kitchen/discovery` — re-probe every declared harness, read-only.
	 *
	 * Read-only over configuration, not passive: the daemon resolves each declared
	 * executable and runs one bounded `--version` on it (`herdsman/discovery.py`).
	 * Nothing is written anywhere — not the project's Kitchen, and emphatically not
	 * any harness's own global configuration — but real processes are started, so
	 * this is an operator's action and never a poll.
	 *
	 * The response is the whole projection, so a probe and a read are one round
	 * trip. The result lives in daemon memory only: a restarted daemon has no
	 * facts again, and `Kitchen.save` clears them by design.
	 */
	probeKitchen: (signal?: AbortSignal): Promise<Kitchen> =>
		post<Kitchen>('/kitchen/discovery', signal, {}),

	graph: (planId: string, signal?: AbortSignal): Promise<PlanGraph> =>
		get<PlanGraph>(`/plans/${encodeURIComponent(planId)}/graph`, signal),

	/**
	 * `GET /plans/{id}/status` — the whole routine observability bundle in one
	 * read: graph, overhead, burn-down point, makespan ETA and anomalies (plus
	 * activity projections this build's consumer does not read). One read, not
	 * four; the existing `graph` read above stays what the field draws from,
	 * because moving a landed unit's read path buys nothing.
	 */
	status: (planId: string, signal?: AbortSignal): Promise<StatusBundle> =>
		get<StatusBundle>(`/plans/${encodeURIComponent(planId)}/status`, signal),

	/**
	 * `GET /plans/{id}/tokens` — the attributed ledger the category attribution
	 * reads from. Required separately: the status bundle carries no
	 * `by_category`, and a failed read here leaves the category string
	 * explicitly unread rather than absent.
	 */
	tokens: (planId: string, signal?: AbortSignal): Promise<TokenLedger> =>
		get<TokenLedger>(`/plans/${encodeURIComponent(planId)}/tokens`, signal),

	/** `GET /plans/{id}/memory/status` — status is recomputed by the daemon. */
	memoryStatus: (planId: string, signal?: AbortSignal): Promise<MemoryStatus> =>
		get<MemoryStatus>(`/plans/${encodeURIComponent(planId)}/memory/status`, signal),

	/** `GET /memory/capabilities` — 400 means the declaration is unconfigured. */
	memoryCapabilities: (signal?: AbortSignal): Promise<MemoryCapabilityReport> =>
		get<MemoryCapabilityReport>('/memory/capabilities', signal),

	risk: (planId: string, signal?: AbortSignal): Promise<RiskReport> =>
		get<RiskReport>(`/plans/${encodeURIComponent(planId)}/risk`, signal),

	/** `GET /plans/{id}` — the folded plan, with planner-authored content. */
	plan: (planId: string, signal?: AbortSignal): Promise<Plan> =>
		get<Plan>(`/plans/${encodeURIComponent(planId)}`, signal),

	/** `GET /plans/{id}/replay` — the same Plan, folded from an event prefix. */
	replay: (planId: string, throughAt: string, signal?: AbortSignal): Promise<Plan> =>
		get<Plan>(
			`/plans/${encodeURIComponent(planId)}/replay?through_at=${encodeURIComponent(throughAt)}`,
			signal
		),

	/**
	 * `POST /plans/{id}/approve?version=N` — approve one revision of a plan.
	 *
	 * The version is always sent, and it is the version the operator actually
	 * read. That is not ceremony: `Plan._apply` refuses a `PlanApproved` whose
	 * version is not current, and refuses a second one outright, so a revision
	 * that landed between the read and the click comes back 409 instead of
	 * approving something nobody looked at. Duplicate approval is the same 409.
	 *
	 * Recalibration is the counterpart: it returns a later proposal which must
	 * be approved with this same version-pinned write.
	 */
	approve: (planId: string, version: number, signal?: AbortSignal): Promise<Plan> =>
		post<Plan>(
			`/plans/${encodeURIComponent(planId)}/approve?version=${encodeURIComponent(version)}`,
			signal
		),

	/**
	 * `POST /plans/{id}/initiatives/{iid}/focus` — bring this initiative's most
	 * recent recorded pane to the front of the operator's herdr session.
	 *
	 * The one write this build's Run view makes. It appends no event: which
	 * pane the user is looking at is not plan history. A 409 means the plan is
	 * real and the pane is not focusable — no attempt recorded one, or herdr
	 * refused — and the message says which.
	 */
	revision: (planId: string, signal?: AbortSignal): Promise<RecalibrationReport> =>
		get<RecalibrationReport>(`/plans/${encodeURIComponent(planId)}/revision`, signal),

	recalibrate: (
		planId: string,
		body: { reason?: string | null; action_id?: string },
		signal?: AbortSignal
	): Promise<RecalibrationReport> =>
		post<RecalibrationReport>(
			`/plans/${encodeURIComponent(planId)}/recalibrate`,
			signal,
			body
		),

	focus: (planId: string, initiativeId: string, signal?: AbortSignal): Promise<{ pane_ref: string }> =>
		post<{ pane_ref: string }>(
			`/plans/${encodeURIComponent(planId)}/initiatives/${encodeURIComponent(initiativeId)}/focus`,
			signal
		),

	/* --- the interventions (R6) ---------------------------------------------
	 *
	 * Six writes, each a different thing, and the daemon draws the lines this
	 * client does not get to blur: a retry is a fresh attempt on the current
	 * brief, a restart is the *same* attempt's command re-issued in place, a
	 * reassignment and a redirect change what the *next* attempt gets, and a
	 * nudge or an answer reaches the live pane and nothing else.
	 *
	 * Every one of them is refused by the fold rather than by this build, so a
	 * 409's `detail` is the rule in the daemon's own words and is shown as it
	 * arrives. Nothing here invents a reason a write failed.
	 */

	/**
	 * `GET /plans/{id}/initiatives/{iid}/impact` — what a disruptive action on
	 * this member would disturb.
	 *
	 * Read before arming, so the consequence on screen is the daemon's and not
	 * a graph walk this build did twice.
	 */
	impact: (planId: string, initiativeId: string, signal?: AbortSignal): Promise<DownstreamImpact> =>
		get<DownstreamImpact>(
			`/plans/${encodeURIComponent(planId)}/initiatives/${encodeURIComponent(initiativeId)}/impact`,
			signal
		),

	/** `GET /plans/{id}/recovery` — what this daemon no longer tracks. */
	recovery: (planId: string, signal?: AbortSignal): Promise<RecoveryReport> =>
		get<RecoveryReport>(`/plans/${encodeURIComponent(planId)}/recovery`, signal),

	/** `POST /plans/{id}/resume` — reconcile stale attempts without starting work. */
	resume: (
		planId: string,
		options: { assumeMissing?: boolean; timeout?: number } = {},
		signal?: AbortSignal
	): Promise<RecoveryReport> =>
		post<RecoveryReport>(`/plans/${encodeURIComponent(planId)}/resume`, signal, {
			assume_missing: options.assumeMissing ?? false,
			timeout: options.timeout ?? 600
		}),

	/** The per-member hold controls live beside R6's other daemon writes. */
	pause: (
		planId: string,
		initiativeId: string,
		reason: string,
		actionId: string,
		signal?: AbortSignal
	): Promise<Plan> =>
		post<Plan>(
			`/plans/${encodeURIComponent(planId)}/initiatives/${encodeURIComponent(initiativeId)}/pause`,
			signal,
			{ by: 'operator', reason, action_id: actionId }
		),

	unpause: (
		planId: string,
		initiativeId: string,
		reason: string,
		actionId: string,
		signal?: AbortSignal
	): Promise<Plan> =>
		post<Plan>(
			`/plans/${encodeURIComponent(planId)}/initiatives/${encodeURIComponent(initiativeId)}/unpause`,
			signal,
			{ by: 'operator', reason, action_id: actionId }
		),

	cancel: (
		planId: string,
		initiativeId: string,
		reason: string,
		actionId: string,
		signal?: AbortSignal
	): Promise<Plan> =>
		post<Plan>(
			`/plans/${encodeURIComponent(planId)}/initiatives/${encodeURIComponent(initiativeId)}/cancel`,
			signal,
			{ by: 'operator', reason, action_id: actionId }
		),

	/**
	 * `POST /plans/{id}/initiatives/{iid}/retry` — a new attempt on failed work.
	 *
	 * It compiles a fresh packet from the *current* brief version and
	 * assignment and opens a new worktree; the failed attempt and its evidence
	 * stay in the history. The fold refuses it unless the initiative is
	 * `failed` and under its attempt ceiling.
	 *
	 * `action_id` is the daemon's own idempotency key: a repeat of the same
	 * request is answered from the fold's record instead of launching a second
	 * agent. It is sent because this route can be slow enough for an operator
	 * to doubt it landed, and a double-press must not cost a second run.
	 *
	 * Resolves when the attempt has *settled*, not when it starts.
	 */
	retry: (
		planId: string,
		initiativeId: string,
		actionId: string,
		signal?: AbortSignal
	): Promise<{ checkpoint: Checkpoint | null }> =>
		post<{ checkpoint: Checkpoint | null }>(
			`/plans/${encodeURIComponent(planId)}/initiatives/${encodeURIComponent(initiativeId)}/retry`,
			signal,
			{ action_id: actionId }
		),

	/**
	 * `POST /plans/{id}/initiatives/{iid}/restart` — re-issue the live
	 * attempt's own command in its own pane. Not a retry: same packet, same
	 * worktree, same attempt, and the attempt counter does not move.
	 *
	 * The command it re-issues is held in daemon *memory*, not in the fold, so
	 * a daemon that restarted since the attempt launched refuses this with
	 * "has no recorded command to restart". That is a real ceiling and the
	 * surface says so rather than offering a control that cannot work.
	 */
	restart: (planId: string, initiativeId: string, signal?: AbortSignal): Promise<{ pane_ref: string }> =>
		post<{ pane_ref: string }>(
			`/plans/${encodeURIComponent(planId)}/initiatives/${encodeURIComponent(initiativeId)}/restart`,
			signal
		),

	/**
	 * `POST /plans/{id}/initiatives/{iid}/reassign` — a harness/model override
	 * for the next attempt. Running and past attempts keep their own snapshot.
	 *
	 * The daemon validates that both halves are non-empty and that the pair is
	 * not the current assignment; it does *not* validate that the harness can
	 * be launched. An unconfigured harness fails later, at command
	 * compilation, when the next attempt starts.
	 */
	reassign: (
		planId: string,
		initiativeId: string,
		harness: string,
		model: string,
		reason: string,
		signal?: AbortSignal
	): Promise<Plan> =>
		post<Plan>(
			`/plans/${encodeURIComponent(planId)}/initiatives/${encodeURIComponent(initiativeId)}/reassign`,
			signal,
			{ harness, model, reason }
		),

	/**
	 * `POST /plans/{id}/initiatives/{iid}/redirect` — a new brief version, or
	 * an existing checkpoint whose derived brief the next attempt continues
	 * from. Exactly one of the two; the fold refuses both together.
	 *
	 * The running attempt keeps its snapshot, so nothing live is disturbed.
	 * The redirect is ground truth by definition — the brief changed — so the
	 * fold also records it as a run-scoped leaf that later packets carry.
	 */
	redirect: (
		planId: string,
		initiativeId: string,
		target: { brief: string } | { checkpointId: string },
		reason: string,
		signal?: AbortSignal
	): Promise<Plan> =>
		post<Plan>(
			`/plans/${encodeURIComponent(planId)}/initiatives/${encodeURIComponent(initiativeId)}/redirect`,
			signal,
			'brief' in target
				? { brief: target.brief, reason }
				: { checkpoint_id: target.checkpointId, reason }
		),

	/**
	 * `POST /plans/{id}/initiatives/{iid}/nudge` — free text steered at the
	 * live attempt's pane.
	 *
	 * Delivery precedes the record: a refused pane write leaves no event, so a
	 * failure here means the agent did not receive it and the fold does not
	 * claim otherwise. `groundTruth` additionally records the correction as a
	 * run-scoped leaf, so a later packet carries it rather than it living only
	 * in a terminal nobody re-reads.
	 */
	nudge: (
		planId: string,
		initiativeId: string,
		text: string,
		groundTruth: boolean,
		signal?: AbortSignal
	): Promise<Plan> =>
		post<Plan>(
			`/plans/${encodeURIComponent(planId)}/initiatives/${encodeURIComponent(initiativeId)}/nudge`,
			signal,
			{ text, ground_truth: groundTruth }
		),

	/**
	 * `POST /plans/{id}/attempts/{aid}/answer` — one operator answer to an
	 * agent's blocking request. Addressed by *attempt*, not initiative.
	 *
	 * `subject` is what makes the answer durable: the fold records it as a
	 * run-scoped leaf keyed on that subject, which is what a later repeat of
	 * the same question is matched against. There is no route that lists what
	 * an agent asked, so the subject is operator-typed and the surface says so.
	 */
	answer: (
		planId: string,
		attemptId: string,
		subject: string,
		answer: string,
		signal?: AbortSignal
	): Promise<Plan> =>
		post<Plan>(
			`/plans/${encodeURIComponent(planId)}/attempts/${encodeURIComponent(attemptId)}/answer`,
			signal,
			{ subject, answer }
		),

	autoAnswer: (
		planId: string,
		attemptId: string,
		subject: string,
		signal?: AbortSignal
	): Promise<{ leaf: MemoryLeaf | null }> =>
		post<{ leaf: MemoryLeaf | null }>(
			`/plans/${encodeURIComponent(planId)}/attempts/${encodeURIComponent(attemptId)}/auto-answer`,
			signal,
			{ subject }
		),

	salvage: (
		planId: string,
		actionId: string,
		signal?: AbortSignal
	): Promise<{ leaves: MemoryLeaf[] }> =>
		post<{ leaves: MemoryLeaf[] }>(
			`/plans/${encodeURIComponent(planId)}/salvage`,
			signal,
			{ action_id: actionId }
		),

	/**
	 * `GET /plans/{id}/checkpoints` — every initiative's review lifecycle.
	 *
	 * Plan-wide by construction; R4 reads one initiative out of it, and reads
	 * `attention` whole because a taint is always about somebody downstream.
	 */
	checkpoints: (planId: string, signal?: AbortSignal): Promise<CheckpointReport> =>
		get<CheckpointReport>(`/plans/${encodeURIComponent(planId)}/checkpoints`, signal),

	/**
	 * `GET /plans/{id}/packets/{before}/diff/{after}` — the daemon's whole-
	 * section comparison between two of a plan's attempts. Read on demand,
	 * when a comparison is actually asked for: the sentence the operator sees
	 * and the rule the daemon applied come from one place, so the browser never
	 * computes its own "changed" from the two snapshots it already holds.
	 *
	 * `daemon.packet` — the single-snapshot read — is deliberately not added:
	 * `GET /plans/{id}` already serialises every attempt's `packet_snapshot` in
	 * full, and the route's one extra capability (searching `plan.retired`) is
	 * unreachable from any surface this build has. Adding an unused client
	 * method is scaffolding. What flips this: a surface that reads a retired
	 * member's packet, or the daemon trimming `packet_snapshot` out of the plan
	 * projection for payload reasons. Either one adds the method, with a reason.
	 */
	packetDiff: (
		planId: string,
		beforeAttemptId: string,
		afterAttemptId: string,
		signal?: AbortSignal
	): Promise<PacketDiff> =>
		get<PacketDiff>(
			`/plans/${encodeURIComponent(planId)}/packets/` +
				`${encodeURIComponent(beforeAttemptId)}/diff/` +
				`${encodeURIComponent(afterAttemptId)}`,
			signal
		),

	/**
	 * `POST /plans/{id}/checkpoints/{cid}/{approve|reject|changes}` — one
	 * review verdict, recorded permanently. Returns the re-read report.
	 *
	 * `by` has no source in this build: the daemon is local and unauthenticated
	 * and there is no identity anywhere in the UI, so it is left to the route's
	 * own default (`operator`) rather than invented here. `reason` is what makes
	 * the verdict auditable and is the field the next reader actually acts on.
	 *
	 * A 409 is the fold refusing the write, and each refusal is a different
	 * sentence: a second approval, an approval of rejected evidence, changes
	 * requested on a decided version, or — for `approve` alone — the contract
	 * validation that runs *before* anything is appended, so a violating
	 * version leaves the decision pending and no event behind.
	 */
	review: (
		planId: string,
		checkpointId: string,
		verdict: Verdict,
		reason: string,
		signal?: AbortSignal
	): Promise<CheckpointReport> =>
		post<CheckpointReport>(
			`/plans/${encodeURIComponent(planId)}/checkpoints/${encodeURIComponent(checkpointId)}/${verdict}`,
			signal,
			{ reason }
		),

	/** `GET /nav/codemap` — the full `NavIndex.to_dict()` JSON. */
	codemap: (signal?: AbortSignal): Promise<NavIndex> =>
		get<NavIndex>('/nav/codemap', signal),

	/** `GET /nav/tour` — the guided-tour projection as text. */
	tour: (signal?: AbortSignal): Promise<NavText> =>
		get<NavText>('/nav/tour', signal),

	/**
	 * `GET /nav/flow/{name}` — one curated-flow projection as text.
	 * An unknown flow 404s, which surfaces as `not_found`.
	 */
	flow: (name: string, signal?: AbortSignal): Promise<NavText> =>
		get<NavText>(`/nav/flow/${encodeURIComponent(name)}`, signal),

	/**
	 * `GET /nav/symbol/{name}` — one symbol projection as text.
	 * An unknown symbol 404s, which surfaces as `not_found`.
	 */
	symbol: (name: string, signal?: AbortSignal): Promise<NavText> =>
		get<NavText>(`/nav/symbol/${encodeURIComponent(name)}`, signal),

	/**
	 * Live domain events for one plan (`GET /plans/{id}/events`, SSE).
	 *
	 * `onEvent` receives the parsed payload; `onStatus` reports whether the
	 * stream is currently connected, so a view can mark its data stale rather
	 * than silently showing a frozen projection as if it were live.
	 */
	events(
		planId: string,
		onEvent: (type: string, data: unknown) => void,
		onStatus: (connected: boolean) => void
	): () => void {
		const source = new EventSource(`${BASE}/plans/${encodeURIComponent(planId)}/events`);
		// The daemon names every event by its domain type, so there is no
		// default-typed message to listen for -- subscribe to the union.
		const types = [
			'plan_created', 'plan_proposed', 'plan_approved',
			'attempt_started', 'attempt_provisioned', 'subtask_advanced',
			'runtime_observed', 'checkpoint_recorded',
			'initiative_settled', 'initiative_failed'
		];
		const handle = (event: MessageEvent<string>) => {
			try {
				onEvent(event.type, JSON.parse(event.data) as unknown);
			} catch {
				/* A malformed frame is not worth tearing the stream down for. */
			}
		};
		for (const type of types) source.addEventListener(type, handle as EventListener);
		source.addEventListener('open', () => onStatus(true));
		source.addEventListener('error', () => onStatus(false));
		return () => {
			for (const type of types) source.removeEventListener(type, handle as EventListener);
			source.close();
		};
	}
};

/**
 * Enumerations this UI chooses from, and where they come from.
 *
 * - **Plans:** `GET /fleet` (Sprint 10). There is no `GET /plans` collection
 *   route and none is wanted — the fleet row already carries the brief,
 *   revision, approval, status and progress a picker has to show, plus the
 *   deep link to open. An earlier note here claimed plans could not be
 *   enumerated; that was true before Sprint 10 landed and is not true now.
 * - **Harnesses and models:** `GET /kitchen` (Sprint 8). `adapters` are the
 *   harnesses and `models` are harness/model pairs. An unconfigured project
 *   returns both empty with `configured: false` and a blocker saying so, which
 *   is a configuration state, not a missing route.
 * - Navigation (`GET /nav/codemap`, `/nav/tour`, `/nav/flow/{name}`,
 *   `/nav/symbol/{name}`) is served by the daemon from the same
 *   `herdsman/nav.py` evidence the CLI reads offline; the typed client above
 *   is consumed by Map's R13/R14 repository-reading surface.
 *
 * Nothing this build needs is unexposed, so the list is empty. Keep it that
 * way by reading the daemon's routes before declaring a gap.
 */
export const MISSING_ROUTES = [] as const;
