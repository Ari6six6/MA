"""Catalog write tool for the reflection pass.

The librarian (hermes/catalog.py) builds cards mechanically. This tool lets the
cross-run retrospection pass *curate* them: sharpen a purpose, add tags, or flag
a recommendation ("duplicate of scraper.py — consolidate") that then rides in
the workspace section of every future package. It is registered only for the
retrospect pass (and only when the catalog is on), because its output — like a
note or a skill — recirculates into future context; that is the bar for
belonging in that deliberately narrow toolset.

It is a free write to the agent's own asset, exactly like write_note: no shell,
no network, scoped to the catalog file. It appends a superseding card (the prior
one is retained) stamped source="retrospect".
"""

from __future__ import annotations

from hermes import catalog as catalog_mod
from hermes.tools.base import obj_schema, tool


@tool(
    "catalog_note",
    "Curate a workspace artifact's catalog card: set a sharper `purpose`, add "
    "`tags`, and/or raise a `flag` — a short standing recommendation (e.g. "
    "'duplicate of scraper.py; consolidate') that will show beside the file in "
    "every future package. Use it when reviewing your files reveals sprawl or "
    "re-derivation. `path` must already be in the catalog.",
    obj_schema(
        {
            "path": {"type": "string", "description": "exact catalogued path"},
            "purpose": {"type": "string", "description": "one-line what-it's-for (optional)"},
            "tags": {"type": "array", "items": {"type": "string"},
                     "description": "keywords (optional)"},
            "flag": {"type": "string",
                     "description": "short recommendation to surface, or '' to clear (optional)"},
        },
        ["path"],
    ),
)
def catalog_note(args, ctx):
    return catalog_mod.annotate(
        ctx.project,
        str(args["path"]),
        purpose=args.get("purpose"),
        tags=args.get("tags"),
        flag=args.get("flag"),
        source="retrospect",
    )


TOOLS = [catalog_note]
