"""Subagent delegation: the existing turn loop, invoked recursively with a
minimal context.

A child gets the brief, a subset of the parent's tools, and a stripped header —
no persona, no mission, no history, no summaries. It runs its own bounded loop
and returns a single conclusion. Its intermediate tool spam lives in the child's
local message list and dies when the call returns, so the parent's context grows
by only the brief plus the returned summary. That's what turns the context
ceiling from a hard limit into a soft one.

Permissions: the child dispatches through the SAME tool functions and the SAME
`confirm`, so gated tools still stop for the owner. Its tool set is a subset of
the parent's already-built registry, so a child can never hold broader
permissions than its parent. Recursion depth is capped (default 1).
"""

from __future__ import annotations

import json
import time

from hermes import package
from hermes.llm import LLMTransportError
from hermes.tools import ToolRegistry
from hermes.tools.base import ToolContext
from hermes.ui import cyan, dim, magenta, red


def _child_registry(parent_ctx, allowed_tools, depth, max_depth, cfg):
    """A registry that is a strict subset of the parent's. Unknown or
    unpermitted names are silently dropped — a child cannot widen its reach."""
    parent = parent_ctx.registry
    child = ToolRegistry()
    for name in dict.fromkeys(allowed_tools or []):  # dedupe, keep order
        t = parent._tools.get(name)
        if t is not None and name not in ("finish_run", "delegate"):
            child.register(t)
    # Always able to finish; delegate only if a grandchild is still within depth.
    if "finish_run" in parent._tools:
        child.register(parent._tools["finish_run"])
    if (cfg.get("delegate_enabled", False) and depth < max_depth
            and "delegate" in parent._tools):
        child.register(parent._tools["delegate"])
    return child


def _cap_out(last_text, tool_names, max_turns, reason) -> str:
    used = ", ".join(dict.fromkeys(tool_names)) or "none"
    tail = (last_text or "").strip()
    return (
        f"[sub-agent stopped: {reason}]\n"
        f"Turn cap: {max_turns}. Tools used: {used}.\n"
        f"How far it got: {tail[:800] if tail else '(no output produced)'}\n"
        "Treat this as partial — the parent should decide the next step."
    )


def _lineage_block(body: str, parent: str, generation: int, siblings) -> str:
    """The embodiment note injected into a citizen's prompt — its place in the
    world. Empty for a bodiless (in-process) child, so the placeholder vanishes."""
    if not body:
        return ""
    addressable = [s for s in (siblings or []) if s != body]
    who = ", ".join(addressable) if addressable else "none yet"
    return (
        "\nYou are an EMBODIED CITIZEN of the village. Your body is the container "
        f"`{body}` on the dome network.\n"
        f"- parent: {parent}\n"
        f"- generation: {generation}\n"
        f"- siblings you can reach by name on the network: {who}\n"
        "Your DNA is mounted read-only at /dna (lineage.json, relations.json, "
        "brief.md) — read it from a shell in your body if you need it. Your "
        "`sandbox_shell` runs inside this body; it can reach siblings by their "
        "container name but has no route to the internet.\n"
    )


def run_child(parent_ctx: ToolContext, brief: str, allowed_tools, cfg,
              log=None, role: str = "") -> str:
    """Run one delegated child loop and return its single conclusion string.

    When the village is enabled and the daemon supports it, the child is given a
    body — its own citizen container on the dome — and is harvested to the file
    system when it returns (in a `finally`, so even an errored body leaves a
    record). Otherwise it runs in-process exactly as before."""
    from hermes.agent import (
        _assistant_msg, extract_narrate, extract_think, strip_narrate, strip_think,
    )

    backend = parent_ctx.backend
    if backend is None:
        return "ERROR: no backend available for delegation."
    think_re = parent_ctx.think_re
    depth = (parent_ctx.depth or 0) + 1
    max_depth = int(cfg.get("delegate_max_depth", 1))
    max_turns = max(1, int(cfg.get("delegate_max_turns", 20)))
    # Wall-clock reaper: a child that's stuck or just going slowly (rather than
    # burning turns) still gets cut off and returns a partial result instead of
    # running unsupervised for however long the backend takes. 0 = no cap.
    max_seconds = int(cfg.get("delegate_max_seconds", 0))
    started = time.monotonic()

    child_reg = _child_registry(parent_ctx, allowed_tools, depth, max_depth, cfg)
    child_ctx = ToolContext(
        project=parent_ctx.project, cfg=cfg, gpu=parent_ctx.gpu,
        sandbox=parent_ctx.sandbox, hosts=parent_ctx.hosts,
        confirm=parent_ctx.confirm, served_ctx=parent_ctx.served_ctx,
        backend=backend, think_re=think_re, depth=depth,
    )
    child_ctx.registry = child_reg
    child_ctx._delegate_log = log  # nested delegation logs into the same transcript

    # Village: give this child a body if enabled and the daemon can do it.
    body, runtime, siblings = _maybe_embody(parent_ctx, child_ctx, cfg, brief,
                                             role, depth, log)

    tool_list = ", ".join(child_reg.names())
    parent_name = parent_ctx.body or "main"
    system = package.render(package.subagent_prompt(), {
        "tools": tool_list,
        "lineage": _lineage_block(body, parent_name, depth, siblings),
    })
    msgs = [{"role": "system", "content": system},
            {"role": "user", "content": brief.strip()}]
    if log:
        log({"role": "delegate",
             "content": f"depth={depth} tools=[{tool_list}]"
                        f"{' body=' + body if body else ''}\n{brief[:500]}"})

    tool_names: list[str] = []
    think_lines: list[str] = []
    last_text = ""
    conclusion = None

    def _loop() -> str:
        nonlocal last_text
        for _ in range(max_turns):
            if max_seconds and (time.monotonic() - started) >= max_seconds:
                return _cap_out(last_text, tool_names, max_turns,
                                f"wall-clock budget {max_seconds}s reached")
            try:
                result = backend.chat(msgs, tools=child_reg.schemas())
            except LLMTransportError:
                return _cap_out(last_text, tool_names, max_turns, "backend unreachable")
            shown = strip_think(result.content, think_re) if think_re else strip_think(
                result.content
            )
            if cfg.get("inner_voice", True):
                for seg in extract_think(result.content, think_re or None):
                    think_lines.append(json.dumps(
                        {"role": "child", "content": seg}, ensure_ascii=False))
            if cfg.get("narrator_enabled", True):
                for seg in extract_narrate(shown):
                    if log:
                        log({"role": "narrate", "content": seg})
                    print(red("  ✦ ") + red(seg))
            shown = strip_narrate(shown)
            if shown:
                last_text = shown
                print(magenta("  [child] ") + dim(shown.splitlines()[0][:120]))
            if not result.tool_calls:
                # A child that stops without finishing still owes a conclusion.
                if last_text:
                    return last_text
                continue
            msgs.append(_assistant_msg(result))
            for tc in result.tool_calls:
                tool_names.append(tc.name)
                out = child_reg.dispatch(tc.name, tc.arguments, child_ctx)
                if tc.name != "finish_run":
                    print(dim("    [child] → ") + cyan(tc.name))
                if log:
                    log({"role": "delegate-tool", "name": tc.name, "content": out})
                msgs.append({"role": "tool", "tool_call_id": tc.id, "content": out})
            if child_ctx.finish_summary is not None:
                return child_ctx.finish_summary or _cap_out(
                    last_text, tool_names, max_turns, "finished with an empty summary"
                )
        return _cap_out(last_text, tool_names, max_turns, "turn cap reached")

    try:
        conclusion = _loop()
        return conclusion
    finally:
        if body:
            # Carry the body up the mountain before it is removed — always, even
            # if the loop raised or the child was reaped.
            from hermes.sandbox import village
            try:
                village.harvest(parent_ctx.sandbox, parent_ctx.project, body,
                                runtime, report=conclusion or last_text,
                                thinking="\n".join(think_lines))
                if cfg.get("narrator_enabled", True):
                    print(red(f"    ✦ {body}'s watch ends — its body is carried up "
                               "the mountain, logs and voice kept whole, before the "
                               "dome reclaims the container."))
            except Exception as e:  # harvest must never mask the real result
                print(dim(f"    [village] harvest of {body} failed: {e}"))


def _maybe_embody(parent_ctx, child_ctx, cfg, brief, role, depth, log):
    """Create a citizen body for this child when the village is on and usable.
    Returns (body_name_or_None, runtime, siblings). Falls back silently to
    in-process delegation on any problem — a village that can't run must never
    break a delegation."""
    if not cfg.get("village_enabled", False) or parent_ctx.sandbox is None:
        return None, "", []
    from hermes.sandbox import village
    ep = parent_ctx.sandbox
    ok, detail = village.village_usable(ep)
    if not ok:
        print(dim(f"    [village] disabled (daemon can't host it: {detail[:80]}) "
                  "— running in-process"))
        return None, "", []
    # Cap concurrent bodies so a fan-out can't exhaust the box.
    live = getattr(parent_ctx, "_citizens", None)
    if live is None:
        live = parent_ctx._citizens = []
    if len(live) >= int(cfg.get("village_max_citizens", 8)):
        print(dim("    [village] citizen cap reached — running this child in-process"))
        return None, "", []
    seq = getattr(parent_ctx, "_citizen_seq", 0) + 1
    parent_ctx._citizen_seq = seq
    project = parent_ctx.project
    parent_name = parent_ctx.body or "main"
    run_id = getattr(parent_ctx, "_run_id", "")
    name = village.citizen_name(project, role=role or "worker", gen=depth, n=seq)
    siblings = list(live)  # who already exists, before adding self
    try:
        dna = village.write_dna(project, name, brief, parent=parent_name,
                                generation=depth, run_id=run_id,
                                siblings=siblings + [name], role=role)
        runtime, _ = village.ensure_citizen(
            ep, project, name, cfg, parent=parent_name, generation=depth,
            role=role, run_id=run_id, dna_dir=dna,
        )
    except Exception as e:
        print(dim(f"    [village] could not embody child ({e}) — running in-process"))
        return None, "", []
    live.append(name)
    child_ctx.body = name
    if log:
        log({"role": "village", "content": f"born {name} (parent={parent_name}, "
             f"gen={depth})"})
    print(magenta(f"    [village] ") + dim(f"citizen {name} born"))
    if cfg.get("narrator_enabled", True):
        kin = (f"{len(siblings)} sibling(s) already pace the dome" if siblings
               else "first of its generation, alone on the dome")
        print(red(f"    ✦ {name} draws its first breath, sent by {parent_name} "
                   f"to {role or 'work'} — {kin}."))
    return name, runtime, siblings
