"""Package assembly: turn project state + a new prompt into [system, user].

This is a pure function of inputs, with a hard per-section budget so the
prompt can never creep — the failure mode of the previous app. Budgets scale
down automatically when the served context window is small.
"""

from __future__ import annotations

import re
import time
from functools import lru_cache
from pathlib import Path

from hermes.config import Config, read_persona
from hermes.project import Project

APPROX_CHARS_PER_TOKEN = 4
PROMPTS_DIR = Path(__file__).parent / "prompts"


@lru_cache(maxsize=None)
def _template(name: str) -> str:
    """Read a prompt template once and cache it. These files are static for the
    life of the process, yet several are read on every agent turn — caching keeps
    that off the hot path without changing the text."""
    return (PROMPTS_DIR / name).read_text()


# Fraction of the total package budget given to each section.
SECTION_SHARES = {
    "mission": 0.20,
    "history": 0.15,
    "summaries": 0.30,
    "last_reply": 0.10,
    "notes": 0.15,
    "workspace": 0.10,
}


_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")


def render(template: str, variables: dict) -> str:
    """Substitute {{key}} placeholders in a single pass. Substituted values are
    NOT rescanned, so a value that itself contains a {{...}} sequence (e.g. a
    project named "{{date}}") can't bleed into a later placeholder. Unknown
    placeholders are left untouched."""
    return _PLACEHOLDER_RE.sub(
        lambda m: str(variables[m.group(1)]) if m.group(1) in variables else m.group(0),
        template,
    )


def truncate_keep_tail(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return "[...truncated...]\n" + text[-max_chars:]


def truncate_keep_head(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n[...truncated...]"


def package_budget_chars(cfg: Config, context_window: int) -> int:
    budget_tokens = cfg.get("package_budget_tokens", 10000)
    if context_window:
        # Leave room for tool schemas, the system prompt, the in-run tool
        # loop, and the model's output.
        budget_tokens = min(budget_tokens, int(context_window * 0.30))
    return max(budget_tokens, 1500) * APPROX_CHARS_PER_TOKEN


def runtime_status_text(env: dict) -> str:
    """The volatile runtime status: date, GPU, managed hosts, context window.
    These bytes change between calls, so under prefix-cache ordering they move
    out of the early header and ride late in the user message instead."""
    ctx = env.get("context_window") or 0
    window = f"~{ctx} tokens" if ctx else "unknown (assume modest)"
    return (
        f"Date: {time.strftime('%Y-%m-%d')} · GPU: {env.get('gpu_status', 'not attached')}\n"
        f"Managed hosts: {env.get('managed_hosts', 'none')}\n"
        f"Context window: {window} — plan your reading and output accordingly."
    )


def build_system_prompt(project: Project, env: dict, cfg: Config | None = None) -> str:
    from hermes.tools import toolbox_catalog

    template = _template("system.md")
    # Prefix-cache ordering (feature 5): keep the volatile status out of the
    # stable header so the header + persona + tools + skills index are a
    # byte-identical prefix across calls. Off by default (status stays inline).
    prefix_order = cfg is not None and cfg.get("prefix_cache_order", False)
    runtime_status = "" if prefix_order else "\n" + runtime_status_text(env)
    variables = {
        "model_identity": env.get("model_identity", "Hermes (NousResearch Hermes-4.3-36B)"),
        "project_name": project.name,
        "project_dir": str(project.root),
        "remote_workspace": env.get("remote_workspace", "~/hermes-workspace"),
        "runtime_status": runtime_status,
        "toolbox_catalog": toolbox_catalog(),
    }
    system = render(template, variables)
    if cfg is None or cfg.get("directive_header_rule", True):
        from hermes.directives import RECENCY_HEADER_LINE
        system += "\n\n" + RECENCY_HEADER_LINE
    if cfg is not None and cfg.get("verify_before_done", False):
        system += (
            "\n\nVerification rule: never report a task done until you have "
            "EXECUTED a verification step this run — run the script, run the "
            "tests, or hit the endpoint — and seen the real output. Written but "
            "not run is not done; say \"written, not yet run\" and then run it."
        )
    if cfg is not None and cfg.get("stuck_guard_enabled", False):
        system += (
            "\n\nStuck-loop rule: if an execution attempt fails, repeating that "
            "exact approach — even reworded — is mechanically DENIED, not just "
            "discouraged. The same is true the instant your operator vetoes an "
            "approach live. A DENIED (stuck guard) or an operator VETO means "
            "that path is closed for the rest of this run: name a genuinely "
            "different approach instead of retrying it with small variations."
        )
    if cfg is not None and cfg.get("skills_enabled", False):
        from hermes import skills as skills_mod
        idx = skills_mod.index(project)
        if idx:
            system += (
                "\n\n## Skills — load the full procedure before you need it\n\n"
                "Each line is a skill you can pull in full with `load_skill(name)`. "
                "When one covers the task at hand, load it first — it holds the "
                "gotchas. After a task that took real figuring-out, capture what you "
                "learned with `write_skill` (or update an existing skill).\n\n" + idx
            )
    if cfg is not None and cfg.get("almanac_enabled", False):
        from hermes import almanac as almanac_mod
        idx = almanac_mod.index(int(cfg.get("almanac_index_chars", 1200)))
        if idx:
            system += (
                "\n\n## Almanac — hypotheses from every project, not just this one\n\n"
                "Each line is a theory of WHY something failed or succeeded, banked "
                "by the librarian after a run where expected and actual didn't match. "
                "`load_almanac(topic)` for the full writeup and evidence. Check it "
                "before an approach that smells like one that's already gone wrong "
                "somewhere else.\n\n" + idx
            )
    guidance = (env.get("model_tool_guidance") or "").strip()
    if guidance:
        # Model-specific tool-calling discipline. Empty for the baseline model,
        # so its system prompt is byte-for-byte what it always was.
        system += "\n\n## Operating notes for this model\n\n" + guidance
    persona = read_persona().strip()
    if persona:
        system += "\n\n## Persona\n\n" + persona
    return system


def assemble(project: Project, prompt: str, env: dict, cfg: Config,
             magazine_text: str | None = None) -> list[dict]:
    """Build the two-message package. `env` carries gpu_status,
    remote_workspace and context_window (0 if unknown). `magazine_text`, when
    given, is the librarian's morning brief (composed by the caller in debate
    mode) — it rides in the pre-request slot in place of the new-since memo."""
    total_chars = package_budget_chars(cfg, env.get("context_window") or 0)
    budget = {k: int(total_chars * share) for k, share in SECTION_SHARES.items()}

    mission = truncate_keep_head(project.read_mission().strip(), budget["mission"])
    strategy = truncate_keep_head(project.read_strategy().strip(), budget["mission"])

    # Directive reconciliation (feature 1): when on, the distilled directives.md
    # is the authoritative standing-instruction channel and only the last K raw
    # prompts ride along. When off, the full recent prompt log is sent as before.
    directives_on = cfg.get("directives_enabled", False)
    if directives_on:
        directives = truncate_keep_head(
            project.read_directives().strip(), budget["history"]
        )
        history_entries = project.recent_prompts(cfg.get("directives_recent_k", 5))
    else:
        directives = ""
        history_entries = project.recent_prompts(cfg.get("history_max_prompts", 30))
    history_lines = [
        f"[{e.get('run', '?'):>4}] {e.get('text', '')}" for e in history_entries
    ]
    history = truncate_keep_tail("\n".join(history_lines), budget["history"])

    summary_entries = project.recent_summaries(cfg.get("summaries_max", 8))
    summary_blocks = [
        f"## Run {run_id:04d}\n{text}" for run_id, text in summary_entries
    ]
    summaries = truncate_keep_tail("\n\n".join(summary_blocks), budget["summaries"])

    last = project.last_final_reply()
    if last:
        last_run, last_text = last
        last_reply_block = (
            f"# YOUR LAST REPLY (run {last_run:04d}, verbatim — the operator may "
            "refer to it)\n" + truncate_keep_tail(last_text, budget["last_reply"])
        )
    else:
        last_reply_block = "# YOUR LAST REPLY\n(none yet)"

    notes = truncate_keep_tail(project.read_notes().strip(), budget["notes"])
    # The librarian's cards (kind + purpose + supersedes/duplicate links) replace
    # the bare name+size listing when a catalog exists, so the model sees what its
    # files are FOR. Falls back to the plain listing before the first catalog pass.
    catalog_view = ""
    if cfg.get("catalog_enabled", True):
        from hermes import catalog as catalog_mod
        catalog_view = catalog_mod.digest(
            project, min(cfg.get("catalog_digest_chars", 2000), budget["workspace"])
        )
    workspace = catalog_view or truncate_keep_head(
        project.workspace_listing(), budget["workspace"]
    )
    workspace_header = "# WORKSPACE (your files — what each is for)" if catalog_view \
        else "# WORKSPACE"

    if directives_on:
        history_header = (
            "# RECENT PROMPTS (operator, last few verbatim — the full history is "
            "distilled into DIRECTIVES above; recency wins there)"
        )
    else:
        history_header = "# PROMPT HISTORY (operator, oldest first)"

    sections = ["# MISSION\n" + (mission or "(empty)")]
    # The campaign plan, when the operator has set one. Placed right after the
    # mission (the standing purpose) and before directives: the strategy is the
    # current line the day-to-day moves should serve. Absent by default, so a
    # project with no strategy.md adds no section at all.
    if strategy:
        sections.append(
            "# STRATEGY (the general line this project is pursuing — your "
            "day-to-day moves should serve this)\n" + strategy
        )
    if directives_on:
        sections.append(
            "# DIRECTIVES (authoritative standing instructions — obey these; "
            "when they conflict with an old prompt, these win)\n"
            + (directives or "(none yet — none distilled)")
        )
    # Under prefix-cache ordering the volatile runtime status was pulled out of
    # the stable header; re-add it here, after the slow-changing mission and
    # directives and before the already-volatile history/summaries tail.
    if cfg.get("prefix_cache_order", False):
        sections.append("# RUNTIME STATUS\n" + runtime_status_text(env))
    sections += [
        history_header + "\n" + (history or "(none yet)"),
        "# RUN SUMMARIES (your own past runs)\n" + (summaries or "(none yet)"),
        last_reply_block,
        "# NOTES (your own)\n" + (notes or "(none)"),
        workspace_header + "\n" + workspace,
    ]
    # The librarian's memo (feature 14 follow-up): cards banked or refined
    # since this project's own last run, placed last — right next to the
    # request itself — on purpose. RUN SUMMARIES/NOTES/LAST REPLY above are
    # the agent's own past output and self-reinforce; a passive index buried
    # in the system prompt is easy to never check. This is neither: it's new,
    # it's unmissable, and it's gone once read (the cursor advances the
    # moment this package is built for a real run — see agent.run).
    if magazine_text:
        # The librarian's morning brief (debate): it already reasoned over the
        # almanac, the strategy, and the agent's own recent runs, so it stands
        # in place of the raw new-since memo below — one considered page, not
        # two. Same slot, same "colleague not commander" posture.
        brief = truncate_keep_head(magazine_text.strip(),
                                   int(cfg.get("magazine_chars", 2500)))
        sections.append(
            "# LIBRARIAN'S MAGAZINE (your morning brief — read this first)\n"
            "Your librarian worked ahead of you this morning: read the "
            "strategy, your own recent runs, and the almanac, and left you "
            "this. A colleague's brief, not an order — but if it says you "
            "already tried something and why it failed, don't spend the turn "
            "re-trying it.\n\n" + brief
        )
    elif cfg.get("almanac_enabled", False):
        from hermes import almanac as almanac_mod
        memo = almanac_mod.new_since(
            project.almanac_cursor(), int(cfg.get("almanac_memo_chars", 1500))
        )
        if memo:
            sections.append(
                "# LIBRARIAN MEMO (since your last run here)\n"
                "A colleague's notes, not an order — the librarian reviewed "
                "outcomes across projects and flagged what's below as "
                "possibly relevant to what you're about to do. Worth a look "
                "if any of it overlaps with your plan; use your own judgment "
                "on whether it applies.\n\n" + memo
            )
    sections.append("# CURRENT REQUEST\n" + prompt.strip())
    user = "\n\n".join(sections)

    return [
        {"role": "system", "content": build_system_prompt(project, env, cfg)},
        {"role": "user", "content": user},
    ]


def reconcile_prompt() -> str:
    return _template("reconcile.md")


def compact_prompt() -> str:
    return _template("compact.md")


def retrospect_prompt() -> str:
    return _template("retrospect.md")


def almanac_prompt() -> str:
    return _template("almanac.md")


def magazine_prompt() -> str:
    return _template("magazine.md")


def attempt_prompt() -> str:
    return _template("attempt.md")


def skills_nudge() -> str:
    return _template("skills_nudge.md").strip()


def subagent_prompt() -> str:
    return _template("subagent.md")


def verify_before_done_nudge() -> str:
    return _template("verify_before_done.md").strip()


def summary_nudge() -> str:
    return _template("summary.md").strip()


def wrapup_warning() -> str:
    return _template("wrapup.md").strip()


def time_wrapup_warning() -> str:
    return _template("time_wrapup.md").strip()


def phantom_nudge() -> str:
    return _template("phantom.md").strip()


def verifier_prompt() -> str:
    return _template("verifier.md").strip()


def verifier_request(request: str, files: list[str]) -> str:
    file_list = "\n".join(f"- {f}" for f in files) if files else "(none reported)"
    return (
        "The agent was asked:\n\n"
        f"{request.strip()}\n\n"
        "It claims to have finished, writing/changing these files:\n"
        f"{file_list}\n\n"
        "Verify it independently in the sandbox now, then give your VERDICT."
    )


def verify_failed(report: str) -> str:
    return (
        "An INDEPENDENT verification pass ran your code in the real sandbox and "
        "it did NOT pass:\n\n"
        f"{report.strip()}\n\n"
        "That is ground truth from the sandbox, not an opinion, and it overrides "
        "your own conclusion. Do not finish again until you have fixed the real "
        "problem: read the failure, change the code (`edit_file` / `write_file`), "
        "run the actual program yourself and read its real output, and only then "
        "`finish_run`."
    )


def stuck_blocked(name: str, brief: str, vetoed: bool, fails: int) -> str:
    if vetoed:
        reason = "your operator explicitly vetoed this exact approach, live"
    else:
        reason = f"this exact approach already failed {fails} time(s) this run"
    return (
        f"DENIED (stuck guard): {reason} — {name}({brief}) will not run again "
        "this run, not even reworded. Stop and reconsider: name the OTHER "
        "approaches you had in mind for this task and pick a genuinely "
        "different one, or use ask_operator if you're out of ideas."
    )


def veto_ack(brief: str) -> str:
    return (
        f"[operator VETO — sent live: they are explicitly telling you to stop "
        f"the approach you just tried ({brief}) and not repeat it. This is now "
        "hard-blocked for the rest of this run — do not retry it, even "
        "reworded. Acknowledge briefly, then pick a genuinely different "
        "approach.]"
    )


def stuck_escalation_nudge() -> str:
    return _template("stuck_guard.md").strip()


def stall_nudge(repeated: bool = False) -> str:
    text = _template("stall.md").strip()
    if repeated:
        text += (
            "\n\nYou have now sent essentially the same message twice without "
            "acting. Stop announcing and make the tool call NOW."
        )
    return text


def reflect_nudge() -> str:
    return _template("reflect.md").strip()


def operator_message(text: str) -> str:
    """Wraps a message the operator sent live, mid-run (`go`/`go say`), so the
    model reads it as new direction rather than part of the original prompt —
    and treats it as a real exchange, not silent redirection. The operator is
    watching this land live and wants to see a reply, not just a changed plan."""
    return (
        "[operator message — sent live while you were mid-run, read this now:]\n\n"
        f"{text.strip()}\n\n"
        "[Reply to your operator directly first — a short, plain-text "
        "acknowledgment, no tool call yet — then carry on with the task, "
        "folding this in. They're here with you, not just issuing orders.]"
    )
