"""The librarian's magazine (the forward brief) and the debate attempt-register.

The almanac (hermes/almanac.py) is the librarian working BEHIND the agent: a
post-mortem that only fires when a run's outcomes ledger shows a real failure.
This module is the other half — the librarian working AHEAD of the agent, and
watching the one place the almanac is blind: debate, where a turn is pure prose
with no exit code, so a strategically dead line never trips the failure gate.

Two passes, same posture as the almanac (own narrow registry, a confirm that
fails closed so only read-only GET research gets through, never raises):

- compose(): the MORNING pass. Before the agent's turn, a bounded pass reads the
  strategy, mission, directives, the agent's own recent summaries, and the
  almanac; researches when a fact would change the move; and writes magazine.md.
  The package hands it to the agent ahead of the request. Its whole point is to
  catch the agent about to repeat a line already found wanting.

- register_attempt(): the NIGHT pass (debate). After the turn, a bounded pass
  reads the line the agent argued, judges it against the strategy and the
  almanac, and banks it — so tomorrow's magazine can catch the repeat.
"""

from __future__ import annotations

from pathlib import Path

from hermes.llm import LLMTransportError


# -- the magazine file -------------------------------------------------------

def magazine_path(project) -> Path:
    return project.root / "magazine.md"


def read_magazine(project) -> str:
    p = magazine_path(project)
    return p.read_text() if p.exists() else ""


def write_magazine(project, text: str) -> None:
    magazine_path(project).write_text(text.rstrip() + "\n")


# -- narrow registries -------------------------------------------------------

def _compose_registry():
    """The morning pass writes the magazine and may research + read almanac
    cards to do it well — but never touches the workspace or the almanac's own
    write surface."""
    from hermes.tools import ToolRegistry, web, almanac_tools, magazine_tools

    registry = ToolRegistry()
    for t in web.TOOLS:
        registry.register(t)
    for t in almanac_tools.READ_TOOLS:  # load_almanac — pull a full card before citing it
        registry.register(t)
    for t in magazine_tools.TOOLS:
        registry.register(t)
    return registry


def _register_registry():
    """The night pass reads/refines the almanac and may research — the same
    write surface the outcomes pass uses, since it banks to the same store."""
    from hermes.tools import ToolRegistry, web, almanac_tools

    registry = ToolRegistry()
    for t in web.TOOLS:
        registry.register(t)
    for t in almanac_tools.TOOLS:  # load_almanac (refine check) + almanac_note (the write)
        registry.register(t)
    return registry


# -- the shared bounded loop -------------------------------------------------

def _bounded_pass(project, backend, cfg, prompt, registry, *, max_turns,
                  success_tool, think_re, log, narrate, role) -> bool:
    """One bounded tool-loop for the librarian's two debate passes. Mirrors
    catalog.reflect_outcomes' shape: its own fail-closed context, a finish_run
    that's bounced back, and it returns True once `success_tool` completed
    without ERROR/DENIED. Never raises past an LLMTransportError."""
    from hermes.agent import _assistant_msg, strip_think
    from hermes.tools.base import ToolContext
    from hermes.ui import dim, magenta

    ctx = ToolContext(project=project, cfg=cfg, confirm=lambda *a, **k: False)
    ctx.registry = registry
    msgs = [{"role": "user", "content": prompt}]
    if log:
        log({"role": role, "content": prompt[:4000]})
    done = False
    for _ in range(max(1, int(max_turns))):
        try:
            result = backend.chat(msgs, tools=registry.schemas())
        except LLMTransportError:
            return done
        shown = strip_think(result.content, think_re) if think_re \
            else strip_think(result.content)
        if log:
            log({"role": role, "content": result.content,
                 "tool_calls": [{"name": tc.name, "arguments": tc.arguments}
                                for tc in result.tool_calls]})
        if shown:
            narrate(magenta(f"  [{role}] ") + dim(shown.splitlines()[0][:120]))
        if not result.tool_calls:
            return done
        msgs.append(_assistant_msg(result))
        for tc in result.tool_calls:
            if tc.name == "finish_run":
                out = ("Not here — this is the librarian's pass, not a run. "
                       f"Use {success_tool}, then stop calling tools.")
            else:
                out = registry.dispatch(tc.name, tc.arguments, ctx)
                if tc.name == success_tool and not out.startswith(("ERROR", "DENIED")):
                    done = True
            if log:
                log({"role": f"{role}-tool", "name": tc.name, "content": out})
            msgs.append({"role": "tool", "tool_call_id": tc.id, "content": out})
    return done


# -- context blocks ----------------------------------------------------------

def _summaries_block(project) -> str:
    entries = project.recent_summaries(6)
    return "\n\n".join(f"## Run {rid:04d}\n{text}" for rid, text in entries)


def _recent_prompts_block(project) -> str:
    entries = project.recent_prompts(6)
    return "\n".join(
        f"[{e.get('run', '?'):>4}] {e.get('text', '')}" for e in entries
    )


# -- the two passes ----------------------------------------------------------

def compose(project, backend, cfg, prompt, think_re=None, log=None,
            narrate=print) -> str | None:
    """The morning pass. Writes magazine.md and returns its text for injection
    ahead of the request, or None if nothing was written. Never raises."""
    from hermes import almanac as almanac_mod, package

    rendered = package.render(package.magazine_prompt(), {
        "strategy": project.read_strategy().strip()
        or "(none yet — this is yours to set with write_strategy)",
        "mission": project.read_mission().strip() or "(empty)",
        "directives": project.read_directives().strip() or "(none)",
        "summaries": _summaries_block(project) or "(no past runs yet)",
        "recent_prompts": _recent_prompts_block(project) or "(none)",
        "almanac_index": almanac_mod.index() or "(empty)",
        "request": prompt.strip() or "(empty)",
    })
    try:
        wrote = _bounded_pass(
            project, backend, cfg, rendered, _compose_registry(),
            max_turns=cfg.get("magazine_max_turns", 8),
            success_tool="write_magazine",
            think_re=think_re, log=log, narrate=narrate, role="librarian",
        )
    except LLMTransportError:
        return None
    return read_magazine(project) if wrote else None


def register_attempt(project, backend, cfg, prompt, reply, think_re=None,
                     log=None, narrate=print) -> bool:
    """The night pass (debate). Banks the line the agent argued this turn to the
    almanac so tomorrow's magazine can catch a repeat. Returns True if it banked
    or refined an entry. Never raises."""
    from hermes import almanac as almanac_mod, package

    reply = (reply or "").strip()
    if not reply:
        return False
    rendered = package.render(package.attempt_prompt(), {
        "strategy": project.read_strategy().strip() or "(no strategy set)",
        "request": prompt.strip() or "(empty)",
        "reply": reply[:6000],
        "almanac_index": almanac_mod.index() or "(empty)",
    })
    try:
        return _bounded_pass(
            project, backend, cfg, rendered, _register_registry(),
            max_turns=cfg.get("magazine_register_max_turns", 4),
            success_tool="almanac_note",
            think_re=think_re, log=log, narrate=narrate, role="librarian",
        )
    except LLMTransportError:
        return False
