#!/usr/bin/env python3
"""Write a real project-local Library to develop unit L1 against.

The shelf is disk, not an event store, so this writes actual asset files through
the real `Library` API the daemon serves — nothing is mocked and nothing is
injected past the parser. Every file it writes is a file `$EDITOR` could have
written, which is exactly how v1 authoring works.

    uv run python ui/dev/seed_library.py            # write the fixture
    uv run python ui/dev/seed_library.py --plan      # + an approved plan that froze it
    uv run python ui/dev/seed_library.py --clear    # remove the shelf again

What it stages, on purpose:

* a role with a three-deep reference chain, so the closure sheet has a chain to
  draw rather than a single ring;
* a **broken reference** — a contract naming a skill nothing on the shelf
  answers to, which is the surface's stated hard case;
* an **archived** asset that is still referenced, which is a different finding
  from a missing one and has a different fix;
* a **reference cycle** between two agents, which the walk must break rather
  than follow;
* a **project override** of a bundled skill, so the origin distinction has a
  real pair behind it rather than a badge with nothing under it;
* an asset with **no body at all**, whose frontmatter is the whole document;
* a role whose body is **long, with long fenced code blocks**, and whose whole
  closure crosses the default 2000-token context budget — the unit's stated
  reading priority and its context-size warning in one selection.

`--plan` additionally seeds a small plan whose initiatives *declare* assets and
approves it **through the real `Daemon.approve_plan`**, so the snapshot in the
event log is the one the product writes -- not a hand-built payload. It then
edits one shelf asset and archives another, so the Library's frozen read has all
three drift states on screen at once: a frozen asset the shelf still matches, one
it has edited away from, and one it no longer holds at all.

Never present a seeded asset or plan as authored by a planner or an agent. These
are fixtures with the word "fixture" in their titles.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from herdsman.classes import (  # noqa: E402
    Assignment,
    Event,
    InitiativeSpec,
    PlanCreated,
    PlanProposed,
)
from herdsman.daemon import Daemon  # noqa: E402
from herdsman.classes import AssetKind  # noqa: E402
from herdsman.library import LIBRARY_DIR, Library  # noqa: E402
from herdsman.store import EventStore  # noqa: E402


def long_role_body() -> str:
    """A role document that is genuinely long, with genuinely long code blocks.

    Written out rather than generated from a loop so the reading surface is
    exercised by prose a person could have written: headings at three levels,
    tables, nested lists, a blockquote, inline code inside sentences, and two
    fenced blocks well past a screen.
    """
    checks = "\n".join(
        f"    - name: {name}\n      command: {command}"
        for name, command in [
            ("types", "uv run basedpyright"),
            ("tests", "uv run pytest -q"),
            ("ui-types", "npm --prefix ui run check"),
            ("ui-build", "npm --prefix ui run build"),
            ("field", "node ui/dev/field-check.ts"),
            ("format", "uv run ruff format --check ."),
            ("lint", "uv run ruff check ."),
            ("deadcode", "uv run vulture herdsman"),
        ]
    )
    handler = "\n".join(
        "        case {}:\n            return Outcome(code={}, retry={}, reason={!r})".format(
            code, code, "True" if code in (429, 503) else "False", reason
        )
        for code, reason in [
            (200, "the executor answered"),
            (400, "the packet was malformed before it left"),
            (401, "the harness rejected the credential"),
            (403, "the contract forbade the write"),
            (404, "the worktree was gone"),
            (409, "another attempt held the lease"),
            (429, "the harness asked for a slower rate"),
            (500, "the harness failed inside"),
            (503, "the harness was not accepting work"),
        ]
    )
    return f"""\
The implementer takes one initiative's brief, its contract and its declared
assets, and returns a checkpoint the reviewer can judge without reading the
worktree. It is the only role that writes to a worktree, and the only one whose
output is bytes rather than a judgement.

## What the role receives

The packet an implementer is handed carries exactly the closure its own
initiative declared — never the union across the plan, and never the whole
shelf. A role document that references a skill pulls that skill in behind it,
and the reader of this page is looking at that closure right now.

> A role is not a persona. The persona is the agent; the role is the seat in the
> pipeline that agent is sitting in. Two different harnesses can fill the same
> role on the same plan, and the handoff document has to read the same either
> way.

### The declared set

| Field | Required | What it does |
| --- | --- | --- |
| `role` | yes | The seat this contract governs |
| `required_checks` | no | Exact `CheckResult.name` values that must have passed |
| `required_paths` | no | Exact artifact paths that must exist |
| `require_patch` | no | The handoff bytes must be present |
| `allow_writes` | no | `false` refuses any changed path at all |
| `allowed_commands` | no | When set, every executed check must be listed |

### The checks a gated contract asks for

```yaml
contract:
  role: implementer
  require_patch: true
  allow_writes: true
  required_checks:
{checks}
  required_paths:
    - notes/handoff.md
  allowed_commands:
    - uv run pytest -q
    - uv run basedpyright
    - npm --prefix ui run check
```

## What the role returns

A checkpoint, and nothing else. The checkpoint carries the changed paths, the
check results, the patch, and the handoff document this role's checkpoint
template names. It does not carry an opinion about whether the work is good:
that is the reviewer's seat, and a role that grades itself is a role with no
gate.

### Failure is typed, not narrated

Every way an attempt can end is a code the fold already understands, so an
implementer never invents a status word:

```python
def outcome_for(code: int) -> Outcome:
    \"\"\"Map one harness response to the outcome the fold records.

    Retryable and terminal are different facts and they are decided here,
    once, rather than at each of the four call sites that used to guess.
    \"\"\"
    match code:
{handler}
        case _:
            return Outcome(
                code=code,
                retry=False,
                reason="the harness answered with a code this build does not know",
            )
```

## Working rules

1. Read the brief, the contract and the declared assets before touching a file.
2. Run the contract's required checks yourself; a checkpoint whose checks were
   never executed is not a smaller checkpoint, it is a false one.
   - A check that fails is reported failed.
   - A check that never ran is reported as never having run, which is a third
     thing and not a failure.
3. Write the handoff document the checkpoint template names.
4. Stop at the contract's edge. A write outside `allow_writes` is refused by the
   settlement gate anyway, and discovering that at settlement wastes the whole
   attempt.

## What this role never does

- It never approves its own checkpoint.
- It never edits another initiative's worktree, even to fix an obvious break:
  that is contention, and the plan drew the lanes precisely to avoid it.
- It never reports an unknown as a zero. An unmeasured token count is unknown.
"""


ASSETS: list[dict[str, object]] = [
    {
        "kind": "role",
        "name": "implementer",
        "title": "Implementer (fixture)",
        # The bundled skill is in the closure on purpose and it is the reason
        # this set crosses the 2000-token context budget: a role that pulls one
        # large shipped skill in behind it is exactly how the warning happens.
        "references": [
            "contract/gated",
            "checkpoint-template/handoff",
            "skill/herdr-delegate",
        ],
        "body": long_role_body(),
    },
    {
        "kind": "role",
        "name": "reviewer",
        "title": "Reviewer (fixture)",
        "references": ["contract/review-only"],
        "body": (
            "Judges one checkpoint against its contract and returns a verdict.\n\n"
            "The reviewer writes nothing into the worktree — `allow_writes: false`\n"
            "on its contract is what makes that structural rather than a promise.\n\n"
            "## The three verdicts\n\n"
            "- **approve** — the contract is met and the work may settle.\n"
            "- **changes** — the contract is not met and the gap is nameable.\n"
            "- **reject** — the approach is wrong; changes would not fix it.\n"
        ),
    },
    {
        "kind": "role",
        "name": "planner",
        "title": "Planner (fixture)",
        "references": ["checkpoint-template/handoff", "agent/architect"],
        "body": (
            "Turns one brief into a DAG of independent initiatives.\n\n"
            "The planner is the only role that runs before the plan exists, so it\n"
            "is the only one whose cost is `planner_usage` rather than an\n"
            "attempt's. It reads the shelf; it does not write to it.\n"
        ),
    },
    {
        "kind": "contract",
        "name": "gated",
        "title": "Gated settlement (fixture)",
        # `skill/handoff-writing` is deliberately absent from the shelf: the
        # broken-reference case the unit is asked to handle well.
        "references": ["skill/checks-first", "skill/handoff-writing"],
        # Booleans are written as the strings a file round-trip produces:
        # `serialize_asset` emits `true`/`false` and `parse_asset` reads them
        # back as strings, and `compile_contract` accepts only that spelling.
        "fields": {
            "role": "implementer",
            "require_patch": "true",
            "required_checks": ["uv run pytest -q", "uv run basedpyright"],
            "required_paths": ["notes/handoff.md"],
        },
        "body": (
            "Settlement waits on evidence: the patch, the two checks, and the\n"
            "handoff document. A checkpoint missing any of the three is refused by\n"
            "the gate rather than argued about by a reviewer.\n"
        ),
    },
    {
        "kind": "contract",
        "name": "review-only",
        "title": "Review, no writes (fixture)",
        "references": [],
        "fields": {"role": "reviewer", "allow_writes": "false"},
        # No body at all: the frontmatter is the whole document, which for a
        # contract is legitimate — the gates *are* the frontmatter.
        "body": "",
    },
    {
        "kind": "skill",
        "name": "checks-first",
        "title": "Run the checks first (fixture)",
        "references": ["skill/retired-lint"],
        "body": (
            "Run the contract's required checks before writing the handoff, not\n"
            "after.\n\n"
            "```sh\n"
            "uv run basedpyright\n"
            "uv run pytest -q\n"
            "```\n\n"
            "A handoff written against unrun checks describes work that might not\n"
            "exist. The order is not a style preference; it is what makes the\n"
            "document true when it is written rather than true later.\n"
        ),
    },
    {
        "kind": "skill",
        "name": "retired-lint",
        "title": "Old lint pass (fixture, archived)",
        "references": [],
        "body": (
            "Superseded by `ruff`. Kept archived rather than deleted so a plan\n"
            "approved while it was live still resolves its own snapshot.\n"
        ),
        "archive": True,
    },
    {
        "kind": "agent",
        "name": "architect",
        "title": "Architect (fixture)",
        "references": ["agent/escalation"],
        "body": (
            "Decides what to do when a packet exhausts its correction rounds.\n"
            "Writes no code.\n"
        ),
    },
    {
        "kind": "agent",
        "name": "escalation",
        "title": "Escalation (fixture)",
        # Back to architect: the cycle the walk has to break rather than follow.
        "references": ["agent/architect"],
        "body": (
            "The seat an unresolvable conflict is handed to. It references the\n"
            "architect, which references it back — a real cycle, staged on purpose\n"
            "so the closure walk has one to break.\n"
        ),
    },
    {
        "kind": "checkpoint-template",
        "name": "handoff",
        "title": "Handoff document (fixture)",
        "references": [],
        "body": (
            "# Handoff\n\n"
            "## What changed\n\n"
            "One paragraph, in the words the next role needs, not the words the\n"
            "diff uses.\n\n"
            "## What was checked\n\n"
            "| Check | Result |\n"
            "| --- | --- |\n"
            "| `uv run pytest -q` | |\n"
            "| `uv run basedpyright` | |\n\n"
            "## What is still open\n\n"
            "Name it, or write *nothing is open* — never leave the heading empty.\n"
        ),
    },
]

# A project copy of a bundled skill, so the override distinction has a real pair
# behind it. The name matches a skill shipped in `assets/skills/`.
OVERRIDE_NAME = "herdsman-nav"


def seed(root: Path) -> None:
    library = Library(root)
    for spec in ASSETS:
        kind = str(spec["kind"])
        name = str(spec["name"])
        ref = f"{kind}/{name}"
        try:
            _ = library.show(ref)
        except Exception:
            pass
        else:
            print(f"  kept   {ref} (already on the shelf)")
            continue
        references = cast("list[str]", spec.get("references", []))
        fields = cast("dict[str, object] | None", spec.get("fields"))
        asset = library.create(
            cast(AssetKind, kind),
            name,
            title=str(spec.get("title", "")),
            body=str(spec.get("body", "")),
            references=references,
            fields=fields,
        )
        if spec.get("archive"):
            asset = library.archive(ref)
        print(f"  wrote  {asset.ref}  ({asset.tokens} tokens, {asset.status})")

    # The override: copy the bundled skill into the project and change one line,
    # so the digests genuinely differ and the shelf reports `shadows_bundled`.
    override = f"skill/{OVERRIDE_NAME}"
    try:
        bundled = library.show(override)
    except Exception:
        print(f"  skip   {override} (no bundled skill of that name)")
    else:
        if bundled.origin == "project":
            print(f"  kept   {override} (already overridden)")
        else:
            note = (
                "<!-- Project override (fixture). The bundled copy is on disk, "
                + "unchanged and read-only; this is the one every read resolves "
                + "to. -->\n\n"
            )
            edited = library.edit(
                override,
                body=note + bundled.body,
            )
            print(f"  wrote  {edited.ref}  (project override of a bundled skill)")


PLAN_ID = "ui-l1-frozen"


def seed_plan(root: Path) -> None:
    """Approve a plan that declares assets, then move the shelf out from under it.

    Everything here goes through the product's own code: the events are appended
    to the real `EventStore` and the approval is `Daemon.approve_plan`, which is
    what calls `Library.snapshot_for`. Declaring an asset whose closure has a
    missing, archived or cyclic reference *refuses* the approval by design, so
    the declared set is the clean half of the shelf.
    """
    store = EventStore()
    if PLAN_ID in store.plans():
        print(f"  kept   plan {PLAN_ID} (already in the event store)")
    else:
        now = datetime.now(UTC)
        claude = Assignment(harness="claude-code", model="claude-opus-5")
        specs = [
            InitiativeSpec(
                id="review",
                name="Review the handoff (fixture)",
                brief="Judge one checkpoint against its contract.",
                assignment=claude,
                assets=["role/reviewer"],
            ),
            InitiativeSpec(
                id="write",
                name="Write the handoff (fixture)",
                brief="Return the handoff document the template names.",
                assignment=claude,
                depends_on=["review"],
                assets=["checkpoint-template/handoff", "skill/herdsman-nav"],
            ),
        ]
        events: list[Event] = [
            PlanCreated(
                plan_id=PLAN_ID,
                at=now,
                brief="A locally seeded plan (fixture) that declares Library assets.",
                planner=claude,
            ),
            PlanProposed(plan_id=PLAN_ID, at=now, version=1, initiatives=specs),
        ]
        for event in events:
            _ = store.append(event)
        print(f"  wrote  plan {PLAN_ID} (proposed, two initiatives)")

    daemon = Daemon(project_root=root, store=store)
    plan = store.load(PLAN_ID)
    if plan.approval == "approved":
        print(f"  kept   plan {PLAN_ID} (already approved)")
    else:
        try:
            plan = daemon.approve_plan(PLAN_ID)
        except Exception as exc:  # the approval refuses a broken declared set
            print(f"  FAILED to approve {PLAN_ID}: {exc}")
            return
        frozen = plan.asset_snapshots.get(plan.version)
        n = 0 if frozen is None else len(frozen.assets)
        print(f"  froze  {n} assets into {PLAN_ID} v{plan.version}")

    # Now move the shelf, so the frozen read has something to disagree with.
    library = Library(root)
    try:
        addition = (
            "\n## Edited after the approval\n\n"
            "This line is on the shelf and is not in the snapshot.\n"
        )
        edited = library.edit(
            "checkpoint-template/handoff",
            body=library.show("checkpoint-template/handoff").body + addition,
        )
    except Exception as exc:
        print(f"  skip   editing checkpoint-template/handoff: {exc}")
    else:
        print(f"  edited {edited.ref} after the approval (drift: edited)")

    # A rename is the only move that makes a ref genuinely leave the shelf:
    # archiving keeps it readable, which is `edited` at most, not `gone`.
    try:
        renamed = library.rename("role/reviewer", "reviewer-v2")
    except Exception as exc:
        print(f"  skip   renaming role/reviewer: {exc}")
    else:
        print(f"  renamed role/reviewer to {renamed.ref} (drift: gone)")


def clear(root: Path) -> None:
    directory = root / LIBRARY_DIR
    if not directory.exists():
        print(f"nothing to clear at {directory}")
        return
    shutil.rmtree(directory)
    print(f"removed {directory}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="project root holding .herdsman/ (default: the working directory)",
    )
    _ = parser.add_argument(
        "--clear", action="store_true", help="remove the project-local library instead"
    )
    _ = parser.add_argument(
        "--plan",
        action="store_true",
        help="also seed and approve a plan that freezes part of this shelf",
    )
    args = parser.parse_args()
    root = Path(cast(Path, args.root)).resolve()

    if cast(bool, args.clear):
        clear(root)
        return

    print(f"seeding {root / LIBRARY_DIR}")
    seed(root)
    if cast(bool, args.plan):
        print(f"\nseeding the approved plan {PLAN_ID}")
        seed_plan(root)
    print(
        "\nOpen http://localhost:5173/library and pick role/implementer — it is the"
        + "\nlong one, and its closure carries the broken reference and the archived"
        + "\nskill. agent/architect carries the cycle."
        + (
            f"\nThen open Approved plan and pick {PLAN_ID}."
            if cast(bool, args.plan)
            else "\nPass --plan to also seed the approved-plan read."
        )
    )


if __name__ == "__main__":
    main()
