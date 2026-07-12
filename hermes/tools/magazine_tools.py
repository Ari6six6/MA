"""The librarian's morning write surfaces: the strategy and the magazine.

Registered only inside the morning compose pass's own narrow registry
(hermes/magazine.py) — the same split almanac_note uses. Both the campaign line
and the brief a future package hands straight to the agent are the librarian's
alone; the doer mid-turn never holds either. The operator owns mission.md; the
strategy is the librarian's.
"""

from __future__ import annotations

from hermes.tools.base import obj_schema, tool


@tool(
    "write_strategy",
    "Set or refine the campaign STRATEGY — the durable line this project is "
    "pursuing, which the agent reads as authoritative and you check its moves "
    "against. This is yours to keep, not the operator's (they own the mission). "
    "Full replace, so write the whole strategy in one call. Set it when it's "
    "absent; refine it when the almanac or the agent's runs show the line has "
    "genuinely drifted — don't churn it every morning.",
    obj_schema(
        {"text": {"type": "string", "description": "the full strategy, markdown"}},
        ["text"],
    ),
)
def write_strategy(args, ctx):
    if ctx.project is None:
        return "ERROR: no project in context."
    text = str(args.get("text", "")).strip()
    if not text:
        return "ERROR: text is required — the strategy can't be empty."
    ctx.project.write_strategy(text)
    return "strategy set — the agent reads it as the line to serve."


@tool(
    "write_magazine",
    "Write the morning magazine — the one short brief handed to the agent "
    "before its turn. Markdown. Lead with what matters most; keep it to a page "
    "the agent will actually read. Calling this again overwrites the draft, so "
    "write the whole brief in a single call.",
    obj_schema(
        {"text": {"type": "string", "description": "the full magazine, markdown"}},
        ["text"],
    ),
)
def write_magazine(args, ctx):
    if ctx.project is None:
        return "ERROR: no project in context."
    text = str(args.get("text", "")).strip()
    if not text:
        return "ERROR: text is required — the magazine can't be empty."
    from hermes import magazine as magazine_mod

    magazine_mod.write_magazine(ctx.project, text)
    return "magazine written — it will ride ahead of the agent's request."


TOOLS = [write_strategy, write_magazine]
