"""App configuration: ~/.hermes/config.json with sane defaults.

HERMES_HOME env var overrides the home dir (used by tests).
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

from hermes.ui import yellow

DEFAULTS: dict = {
    "backend": "openai",  # "openai" (vLLM endpoint) or "mock"
    "base_url": "http://127.0.0.1:8000/v1",
    "api_key": "hermes",  # vLLM doesn't check it, but the client wants one
    "model_id": "hermes",  # which row of hermes.models.CATALOG to serve
    "model": "NousResearch/Hermes-4.3-36B",  # served model name the client sends
    "quantization": "fp8",  # on-the-fly FP8; weight-only fallback on Ampere
    "vast_api_key": "",
    "projects_dir": str(Path.home() / "hermes-projects"),
    "current_project": "",
    "sampling": {"temperature": 0.6, "top_p": 0.95, "top_k": 20},
    "max_turns": 40,
    "max_run_seconds": 0,  # 0 = no wall-clock cap. A hard stop this many seconds
                           # into a run, independent of max_turns — the safety net
                           # that still bounds a run if max_turns is raised or the
                           # model is slow, so "turn cap" and "time cap" are two
                           # separate, composable limits rather than one.
    "delegate_max_seconds": 0,  # same idea, scoped to one delegated child call —
                                # a stuck or slow-going child gets reaped and
                                # returns a partial result instead of hanging.
    "ask_operator_timeout": 900,  # how long the ask_operator tool blocks waiting
                                  # for a live reply before telling the agent to
                                  # decide for itself. Always also capped by the
                                  # run's hard time budget, so a question can never
                                  # push a run past its wall-clock cap.
    "stall_nudges": 2,  # bounce prose-only turns back N times before accepting them as final
    "phantom_nudges": 1,  # bounce a finish that pasted code but wrote/ran nothing
    # Reflection nudge (feature 13): the stop-and-think gate. Small models will
    # chain tool call after tool call with no stated expectation or assessment
    # in between — "acting" without ever checking the result against what they
    # thought would happen. When a run strings together `reflect_nudge_every`
    # tool-call turns with no real prose, one turn is spent forcing a pause:
    # state what you expected, what actually happened, and whether the plan
    # still holds. On by default at the operator's explicit request — see
    # DECISIONS.md ("the second exception to the house rule").
    "reflect_nudge_enabled": True,
    "reflect_nudge_every": 4,  # consecutive silent tool-call turns before pausing
    "reflect_nudges": 3,  # max forced pauses per run — a backstop, not a cage
    "verify_code_runs": True,  # after a code task, an independent pass re-runs it in the sandbox
    "verify_rounds": 2,  # how many times that pass may bounce a failed run back
    "verify_max_turns": 6,  # tool-call budget inside one verification pass
    "max_tool_result_chars": 8000,
    "package_budget_tokens": 10000,  # scaled down automatically on small contexts
    "history_max_prompts": 30,
    "summaries_max": 8,
    # Directive reconciliation (feature 1). The machinery is off by default; the
    # header recency-rule line is on by default (it's true and cheap regardless).
    "directives_enabled": False,  # distil history into directives.md; send it + last K prompts
    "directive_header_rule": True,  # add the "recent instruction wins" line to the header
    "reconcile_every_runs": 10,  # auto-reconcile every N runs (plus migration on first run)
    "directives_recent_k": 5,  # raw prompts still sent when directives are on
    # Lazy compaction of the live within-prompt conversation (feature 2). Off by
    # default; needs a known served context window to compute the thresholds.
    "compaction_enabled": False,
    "compaction_trigger_frac": 0.5,  # compact when the live context passes this fraction of the window
    "compaction_keep_last_turns": 6,  # always keep this many most-recent turns verbatim
    "compaction_floor_frac": 0.25,  # target size after a compaction (documented target, see USAGE)
    # Skills (feature 3): reusable how-to notes. Index of one-liners in the
    # package; load_skill pulls a full body on demand.
    "skills_enabled": True,  # inject the skills index + register load_skill/write_skill
    "skills_nudge": True,  # after a run that took figuring-out, invite writing/updating a skill
    "skills_nudge_max_turns": 3,  # tool-call budget for that post-task skill-writing pass
    # Subagent delegation (feature 4): a delegate tool that runs a clean child
    # loop with a subset of tools and returns one conclusion.
    "delegate_enabled": True,
    "delegate_max_turns": 20,  # child turn cap (lower than the parent's by default)
    "delegate_max_depth": 1,  # 1 = children don't spawn grandchildren
    # The Village (embodied delegation): a delegated child optionally runs with its
    # own Docker "body" — a named citizen container on a shared, air-gapped internal
    # network — carrying DNA (lineage/relations) and harvested to the file system
    # when its life ends. All off/inert by default: with village_enabled False the
    # exec sandbox stays exactly `--network none` and delegation runs in-process.
    "village_enabled": False,   # master switch: give delegated children container bodies
    "village_network": "hermes-net",  # the dome — a user-defined Docker network name
    "village_internal": True,   # create it with `--internal` (no route out; air-gap kept)
    "village_gateway": False,   # single egress gateway for the dome (future; off)
    "village_max_citizens": 8,  # concurrency cap on live citizen bodies per run
    "village_image": "",        # base image for citizen bodies; "" falls back to sandbox_image
    # Inner voice: file the model's excised <think> reasoning to runs/NNNN/
    # thinking.jsonl (and per-citizen when embodied). Write-only — captured, never
    # re-injected into context, so it cannot steer a run. On: nothing is lost.
    "inner_voice": True,
    # Prefix-cache-friendly package ordering (feature 5): move volatile runtime
    # status (date, GPU, hosts) out of the stable header so the header + persona
    # + tools + skills index stay a byte-identical prefix for vLLM prefix caching.
    "prefix_cache_order": False,
    # Checkpointing (feature 6): snapshot the project before a turn mutates files
    # so a run gone sideways is one revert. On by default — pure safety.
    "checkpointing": True,
    "checkpoint_max": 20,  # keep the most recent N snapshots per project
    # Verification enforcement (feature 7): require an executed verification step
    # before a task is reported done. Adds a header rule + a one-shot harness
    # nudge when a file-mutating run finishes without running anything.
    "verify_before_done": False,
    # Stuck-loop guard (feature 12): a model that commits to a failing approach
    # will happily agree in prose to try something else and then retry the same
    # thing anyway — a promise with no enforcement behind it. This makes the
    # correction mechanical instead: once an exact execution attempt has failed
    # `stuck_repeat_threshold` time(s) this run (or the operator vetoes it live),
    # repeating it is hard-DENIED before it runs at all, and enough blocked
    # repeats force a one-shot nudge to actually name a different approach. Off
    # by default.
    "stuck_guard_enabled": False,
    "stuck_repeat_threshold": 1,  # failures of the SAME attempt allowed before repeats are denied
    "stuck_escalate_blocks": 2,  # blocked repeats in one run before the forced-pivot nudge fires
    # Self-build (feature 9): the agent's own source, gated far tighter than
    # project files — off by default, and even when on, a fixed denylist of
    # safety-critical files (the gates themselves) refuses edits regardless.
    "self_build_enabled": False,
    # The scoreboard: when the agent edits Hermes' own source, run the test
    # suite against the proposed change and show the operator the pass/fail
    # result as part of the approval — so a self-edit is judged on evidence, not
    # a diff read in the moment. The change is applied to disk, tested, and
    # reverted if you decline. On by default whenever self-build is on.
    "self_build_run_tests": True,
    "self_build_test_cmd": "python -m pytest -q",  # how the scoreboard runs the suite
    "self_build_test_timeout": 600,  # seconds before the test run is abandoned
    "auto_confirm": False,  # True: unattended mode — approve every y/n gate (local_shell, state-changing web, host writes, forged-tool loads) so a run never stalls waiting for an operator who's away
    "gpu_shell": False,  # False: GPU box is the model's host only; code runs in the air-gapped sandbox. True: also expose remote_shell/read/write for on-card compute
    "allow_gpu_network": False,  # only relevant when gpu_shell is on. False: box may install/build (net), but raw egress + target traffic go via the VPS; True: unrestricted box net
    "sandbox_image": "python:3.12-slim",  # base image for the air-gapped exec container (sandbox_shell)
    # Operator-configured HTTP auto-approval (see hermes/http_policy.py): a list
    # of {"domain": "api.example.com", "methods": ["GET", "POST"]} objects that
    # skip confirmation entirely, in any turn (tainted or not). Manage with the
    # `allow` REPL command. Empty by default: nothing is auto-approved until you
    # add it.
    "http_allow": [],
    # Retrospection (feature 11): every N runs, a bounded fresh-context pass
    # reviews the harness-recorded per-run metrics + its own summaries and banks
    # recurring lessons as notes (and skills, when skills are on). Its only
    # write surface is the agent's own assets — never mission/persona/directives.
    "retrospect_enabled": True,
    "retrospect_every_runs": 5,
    "retrospect_window": 10,  # how many recent runs one pass reviews
    "retrospect_max_turns": 4,  # tool-call budget for one pass
    # The librarian (hermes/catalog.py): a side-pass that keeps one self-
    # describing card per workspace artifact — kind, one-line purpose, tags,
    # provenance, and inferred supersedes/duplicate links. The cards ride in the
    # package in place of the bare workspace listing, so a later run sees WHAT
    # its files are for, not just that they exist. On by default: the
    # deterministic core (hashing, kind, supersession) has no model cost and no
    # risk; only the optional purpose/tags enrichment calls the model.
    "catalog_enabled": True,
    "catalog_enrich": True,  # let the model fill in purpose/tags (deterministic core runs regardless)
    "catalog_every_runs": 1,  # cadence; 1 = keep cards fresh every run
    "catalog_max_file_bytes": 200_000,  # files bigger than this are identity-hashed, not content-hashed
    "catalog_sample_bytes": 1500,  # bytes of each file shown to the model during enrichment
    "catalog_scope": "workspace",  # every card's scope; a future shared lexicon flips this
    "catalog_digest_chars": 2000,  # budget for the card view injected into the package
    # The almanac (feature 14): the librarian's second job. At the end of
    # every run, when this run's code-write/execution attempts show a real
    # mismatch between what was expected and what happened, a bounded pass
    # forms a hypothesis for WHY — researching it with web_search/http_request
    # (GET only) when useful — and banks it to a GLOBAL, cross-project store
    # (unlike the workspace-scoped catalog above). On by default at the
    # operator's explicit request — see DECISIONS.md.
    "almanac_enabled": True,
    "almanac_max_turns": 6,  # tool-call budget for one pass (research + the write)
    "almanac_index_chars": 1200,  # budget for the almanac index in the system prompt
    "almanac_memo_chars": 1500,  # budget for the librarian memo (new-since-last-run) in the package
    # The librarian's magazine (the forward brief): the almanac above is the
    # librarian working BEHIND the agent (a post-mortem on failed outcomes).
    # This is the other half — the librarian working AHEAD of it. In debate mode
    # a synchronous morning pass reads the strategy, the agent's own recent
    # runs, and the almanac (researching when a fact would change the move) and
    # writes magazine.md, handed to the agent ahead of the request so it doesn't
    # re-argue a settled line. At end of turn a night pass logs the line the
    # agent actually argued to the almanac — debate turns have no exit code, so
    # this is the only record a strategically dead line was tried, which is what
    # lets the next morning's brief catch a repeat. Off by default; debate-scoped.
    "magazine_enabled": False,
    "magazine_max_turns": 8,  # tool-call budget for the morning compose (research + the write)
    "magazine_register_max_turns": 4,  # tool-call budget for the end-of-turn attempt log
    "magazine_chars": 2500,  # budget for the magazine injected ahead of the request
    # The narrator voice (feature 15): the outer voice, the opposite number of
    # inner_voice. The model may, at its own discretion via <narrate>...</narrate>,
    # describe the scene in story prose for the operator watching — instead of
    # only the dense technical reply — and the harness itself narrates village
    # lifecycle events (a citizen's birth, its harvest) it already knows happened
    # regardless of whether the model chooses to. Pure display + a write-only
    # narration.jsonl page; never re-injected into context, so it cannot steer a
    # run. On by default: it costs nothing when the model doesn't use it.
    "narrator_enabled": True,
    # How long ONE HTTP call to the model may take before OpenAIBackend gives up
    # on it (httpx read/connect/write/pool timeout, all four). Distinct from
    # max_run_seconds/delegate_max_seconds (Feature 10), which bound a whole RUN
    # across many calls — this bounds a SINGLE call. A slow box or a big prompt
    # (retrospection, catalog enrichment, the almanac pass — anything that isn't
    # the main loop's own turns) can legitimately need more than the old fixed
    # 300s; raise this instead of watching a real-but-slow completion get cut off
    # and redone from scratch every retry.
    "llm_timeout": 300,
    # The tighter cousin of llm_timeout, for the librarian's side-passes only
    # (catalog enrichment, the almanac, retrospection, directive reconciliation,
    # the skills nudge). Those run in the operator's foreground between one run
    # and the next and are pure conveniences — so they get a short, SINGLE-shot
    # call (no retry ladder). On a slow/overloaded box the pass is skipped and
    # the finished run's result stands, instead of a housekeeping call inheriting
    # the 900s-and-retry budget and blocking the prompt for the better part of an
    # hour. Keep this well below llm_timeout; raise only if a genuinely useful
    # pass is being cut off on a healthy-but-slow box.
    "housekeeping_timeout": 120,
    "max_model_len": 0,  # 0 = pick automatically from detected VRAM
    "gpu_port": 8000,
    "local_port": 8000,
    "max_completion_tokens": 8192,
    "extra_vllm_args": [],
    "extra_llama_args": [],  # appended to llama-server for GGUF models
}


def hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))


def config_path() -> Path:
    return hermes_home() / "config.json"


def persona_path() -> Path:
    return hermes_home() / "persona.md"


DEFAULT_PERSONA = """\
You are Hermes: sharp, direct, loyal. You think hard before you act, you keep
your operator informed in plain language, and you finish what you start.
"""


class Config:
    def __init__(self, data: dict):
        self.data = data

    @classmethod
    def load(cls) -> "Config":
        data = copy.deepcopy(DEFAULTS)
        path = config_path()
        if path.exists():
            try:
                stored = json.loads(path.read_text())
                _deep_update(data, stored)
            except (json.JSONDecodeError, OSError) as e:
                print(yellow(f"warning: could not read {path}: {e} — using defaults"))
        return cls(data)

    def save(self) -> None:
        home = hermes_home()
        home.mkdir(parents=True, exist_ok=True)
        config_path().write_text(json.dumps(self.data, indent=2) + "\n")
        os.chmod(config_path(), 0o600)  # holds vast_api_key
        if not persona_path().exists():
            persona_path().write_text(DEFAULT_PERSONA)

    def get(self, key: str, default=None):
        """Dotted-key get: cfg.get("sampling.temperature")."""
        node = self.data
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, key: str, value, coerce: bool = True) -> None:
        """Dotted-key set. Strings are type-coerced (bools/ints/floats) so
        `config set max_turns 40` stores an int — but pass coerce=False for values
        that must stay strings even when they look numeric, like a project named
        "2" (otherwise it becomes int 2 and later `projects_dir / 2` blows up)."""
        parts = key.split(".")
        node = self.data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = _coerce(value) if coerce else value

    def __getitem__(self, key: str):
        return self.data[key]


def _deep_update(base: dict, extra: dict) -> None:
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_update(base[k], v)
        else:
            base[k] = v


def _coerce(value):
    if not isinstance(value, str):
        return value
    low = value.lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def read_persona(max_chars: int = 2000) -> str:
    path = persona_path()
    if not path.exists():
        return DEFAULT_PERSONA
    text = path.read_text()
    if len(text) > max_chars:
        text = text[:max_chars] + "\n[persona truncated]"
    return text
