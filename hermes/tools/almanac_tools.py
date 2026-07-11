"""Almanac tools: read is broadly available, write is the librarian's alone.

`load_almanac` is a normal read tool (like `load_skill`) — any run, in any
project, can pull the full writeup for a topic that looks relevant. `almanac_note`
is registered only inside the end-of-run librarian pass's own narrow registry
(hermes/librarian.py) — the same split `catalog_note` uses for retrospect:
a write that recirculates into every future package, in every future project,
is curated by the one pass built to reason about outcomes, not by the doer
mid-task.
"""

from __future__ import annotations

from hermes import almanac as almanac_mod
from hermes.tools.base import obj_schema, tool


@tool(
    "load_almanac",
    "Load the FULL writeup for one almanac topic — the hypothesis for why "
    "something failed or succeeded, the expected-vs-actual outcome that "
    "prompted it, and any research evidence behind it. This is shared across "
    "every project, not just this one. Use it when a topic in the almanac "
    "index (in your system prompt) looks relevant to what you're about to "
    "try, so you don't repeat a known dead end.",
    obj_schema(
        {"topic": {"type": "string", "description": "exact topic slug from the index"}},
        ["topic"],
    ),
)
def load_almanac(args, ctx):
    entry = almanac_mod.get(str(args["topic"]))
    if entry is None:
        return f"ERROR: no almanac entry '{args['topic']}'."
    lines = [f"# {entry['topic']}", "", entry.get("claim", "")]
    if entry.get("expected") or entry.get("actual"):
        lines += [
            "",
            f"Expected: {entry.get('expected') or '(not recorded)'}",
            f"Actual: {entry.get('actual') or '(not recorded)'}",
        ]
    lines += ["", "## Hypothesis", entry.get("hypothesis", "")]
    if entry.get("evidence"):
        lines += ["", "## Evidence", entry["evidence"]]
    if entry.get("confidence"):
        lines.append(f"\nConfidence: {entry['confidence']}")
    if entry.get("project"):
        lines.append(f"First observed in project: {entry['project']}")
    return "\n".join(lines)


@tool(
    "almanac_note",
    "Bank a hypothesis into the almanac — the cross-project record of WHY "
    "something failed or succeeded, not just that it did. `topic` is a short "
    "lowercase slug (letters/digits/hyphens); `claim` is the one-line lesson; "
    "`hypothesis` is your actual theory of why, including anything you "
    "researched. Include `expected`/`actual` from the outcome that prompted "
    "this. Writing again with the same `topic` refines that entry instead of "
    "duplicating it — check the almanac index first.",
    obj_schema(
        {
            "topic": {"type": "string"},
            "claim": {"type": "string"},
            "hypothesis": {"type": "string"},
            "expected": {"type": "string"},
            "actual": {"type": "string"},
            "evidence": {"type": "string",
                         "description": "research findings, citations, or reasoning (optional)"},
            "confidence": {"type": "string", "description": "low/medium/high (optional)"},
        },
        ["topic", "claim", "hypothesis"],
    ),
)
def almanac_note(args, ctx):
    return almanac_mod.write_entry(
        str(args["topic"]), str(args["claim"]), str(args["hypothesis"]),
        expected=args.get("expected", ""), actual=args.get("actual", ""),
        evidence=args.get("evidence", ""), confidence=args.get("confidence", ""),
        project=ctx.project.name if ctx.project else "",
    )


READ_TOOLS = [load_almanac]
WRITE_TOOLS = [almanac_note]
TOOLS = READ_TOOLS + WRITE_TOOLS
