"""Magazine tool: the librarian's write surface for the morning brief.

Registered only inside the morning compose pass's own narrow registry
(hermes/magazine.py) — the same split almanac_note uses. A write that a future
package hands straight to the agent is the librarian's alone; the doer mid-turn
never holds it.
"""

from __future__ import annotations

from hermes.tools.base import obj_schema, tool


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


TOOLS = [write_magazine]
