# Decisions

Running log of non-obvious calls made while evolving the harness: what, why,
and the alternative I passed on. Newest at the bottom of each feature.

## Global

- **Dependencies are permitted but expensive.** The app shipped on stdlib plus
  `httpx` and `prompt_toolkit`. New deps are now allowed, but each must (a) be
  pure-Python or have aarch64-linux wheels (this installs on Termux/ARM — the
  `openai` SDK was rejected over a Rust wheel, and that bar stands), (b) prefer
  an optional import that degrades to a clear ERROR, and (c) earn a
  justification line below. Kept out of the core `dependencies` list; test-only
  deps live in the `dev` extra.
- **Every feature behind a config flag in `config.py::DEFAULTS`.** Defaults off,
  except checkpointing and the directive header line (per the brief).

## Feature 1 — Directive reconciliation

- **`directives.md` is a distillation, the raw log is untouched.** History stays
  append-only on disk (nothing is deleted); only what the *package* sends changes.
  Alternative: rewrite/prune `history.jsonl`. Rejected — the raw log is the
  audit trail and the reconciliation input; destroying it would be irreversible
  and un-recoverable if a pass ever distils badly.
- **Two independent flags, not one.** `directive_header_rule` (on) adds the
  "recent instruction wins" line to the header; `directives_enabled` (off) turns
  on the machinery (reconciliation + swapping the full log for directives + last
  K). The brief lists the header line as on-by-default but the feature as
  off-by-default, so they can't be the same switch. The header line is true and
  useful even against the raw log, so it costs nothing to leave on.
- **Reconciliation is an LLM side-call with no tools.** It's one extra
  round-trip, only when `directives_enabled` is on, and only when due (migration
  or every N runs) — not every run. Justification for the token/latency cost:
  it *reduces* the steady-state package (a lean directives file replaces a
  ~1500-token history cap) and fixes the conflict bug that no amount of raw log
  can fix. Alternative: a local heuristic diff of prompts. Rejected — resolving
  "never X" vs "now X" needs language understanding, not string matching.
- **Trigger is stateless (`run_id % N == 0`) plus a migration check.** No extra
  state file to track "last reconciled run". Alternative: a `directives.state.json`
  marker. Rejected as more moving parts for no real gain; the modulo is
  predictable and the on-demand `directives reconcile` command covers urgency.
- **Migration runs on the first run of an old project** (`directives.md` missing
  but history present), so existing projects light up with zero manual steps.
- **The distilled directives reuse the history section's char budget** rather
  than adding a new `SECTION_SHARES` key. Keeps the budget math (and the
  off-by-default behaviour) byte-identical to before; the last-K raw prompts are
  tiny so their cap is more than enough.

## Feature 2 — Lazy compaction

- **A turn is `[assistant + its tool results]`, and only whole turns are folded.**
  Compaction removes complete turns and splices one summary message in their
  place, so every kept turn still has its assistant message paired with its
  tool-result messages — the request stays valid for the OpenAI wire format. A
  naive "drop the oldest N messages" would orphan `tool` messages from their
  `tool_calls` and 400 the endpoint.
- **The stable prefix is captured at loop start** (`len(messages)` after the
  package), not hard-coded to `messages[:2]`, so any pre-loop context stays out of
  the compactible region even if the assembled package grows more messages later.
- **Token estimate is chars/4, same as the package budget**, and includes the
  constant tool-schema bytes. It's an estimate, not a tokenizer — good enough to
  decide *when*, and it avoids adding a tokenizer dependency. Alternative: call
  the server's tokenizer endpoint. Rejected — a network round-trip per turn to
  decide whether to do another network round-trip is not worth it.
- **Trigger 50% / floor 25%** (owner's explicit call for a 60K window). An 80%
  trigger leaves too little headroom for the turn that tripped it; see USAGE.
- **Self-limiting, no thrash by construction.** After a compaction the region
  holds exactly `keep_last` turns, and the guard needs `> keep_last + 1` turns
  before it will act again — so a compaction can't immediately re-fire. No extra
  cooldown state needed.
- **A failed side-call is a no-op, not a failure.** If the summarizer can't be
  reached or returns empty, the conversation stays verbatim and the run keeps
  going. Losing compaction is a performance regression, never a correctness one.

## Feature 3 — Skills

- **Mirrors the toolbox catalog exactly.** Index of one-liners in the prompt,
  full body loaded on demand. This is the pattern the codebase already trusts
  for tokens; reusing it means the owner has one mental model, not two.
- **Skill name = filename stem; description = first non-empty line (`#` stripped).**
  Nano-friendly for both `# Heading`-style and plain first-line-description files.
  `load_skill` returns the whole file, so nothing is lost either way.
  Alternative: YAML front-matter. Rejected — adds a parse format and a dependency
  temptation for a file the owner edits by hand on a phone.
- **write_skill defaults to global scope.** The acceptance criterion (a skill
  from project A loadable in project B) only holds for globals, and "reusable"
  is the common case; `scope:"project"` is there for the local exception.
- **Project skill shadows a global of the same name.** The more specific
  procedure wins where it's defined, without touching the global.
- **The nudge can't corrupt the run.** It runs after the summary is fixed and
  intercepts `finish_run` instead of dispatching it, so `ctx.finish_summary`
  (the real handoff) is untouchable. It's the agent's private note-taking pass.
- **"Figuring-out" is a cheap heuristic** (error seen, or ≥8 turns, or forged a
  tool), not another model call. A false negative just means one skill unwritten;
  a false positive costs a couple of turns. Not worth an LLM classifier.

## Feature 4 — Subagent delegation

- **The child's tool set is drawn from the parent's built registry, by name.**
  That's the enforcement for "a child can never hold broader permissions than its
  parent": the parent registry already reflects the parent's permission context
  (live-touch, sealed-mode, registered hosts), and the child can only pick names
  out of it. Unknown names are dropped silently. Alternative: re-derive a registry
  for the child from config. Rejected — it could accidentally grant a tool the
  parent itself didn't have in this context.
- **Same `ctx.confirm`, same tool functions.** Permission tiers "apply
  identically" because the child literally calls the same tool bodies with the
  same confirm callback. No parallel permission path to keep in sync.
- **Depth is on `ToolContext`, checked in two places** (the delegate tool guard
  and the child-registry builder). Belt and suspenders: even if one path is
  bypassed, the other blocks a grandchild at the default cap of 1.
- **The child loop is a separate, smaller function, not `agent.run`.** `run` does
  package assembly, history append, run-dir writes, verifier passes — all
  wrong for a stateless child. The child loop (`subagent.run_child`) reuses the
  shared helpers (`_assistant_msg`, `strip_think`, `dispatch`) but stays minimal.
  This is the "existing loop invoked recursively" in spirit without dragging the
  parent-only machinery along.
- **`backend`/`think_re` moved onto `ToolContext`** so a tool can run a model
  loop. Tools couldn't reach the LLM before; delegation is the first that needs
  to. Kept optional so nothing else is affected.
- **Cap-out returns a structured partial**, never an empty string — the parent
  gets "how far it got and why it stopped" so it can decide the next step instead
  of seeing a mysterious blank.

## Feature 5 — Prefix-cache-friendly ordering

- **Volatile status moves to the user message, not just later in the system
  prompt.** Putting it at the end of the system message would still break the
  cache for anything after it; putting it in the user message (after the
  slow-changing mission/directives) keeps the *entire* system prompt stable, which
  is the biggest single cacheable block.
- **`{{runtime_status}}` placeholder, filled per flag.** With the flag off the
  status renders inline exactly where it always was (existing behaviour, tests
  green); with it on the placeholder is empty and the status is emitted in the
  user message. One template, two orderings, no duplicated prompt text.
- **The date is the clearest offender**, but GPU status and host list can change
  mid-session too. All of them leave the header together.
- **`debug prefix` compares two packages with a deliberately changed status**, not
  two identical ones. Comparing identical packages would always report a 100%
  shared prefix and prove nothing; changing the volatile bits is what exposes
  whether they're actually isolated from the stable prefix.
- **Gated behind a flag (default off)** like the other opt-in features, even
  though it's a pure win with prefix caching on — the brief's default posture is
  off-until-flipped for everything but checkpointing and the header line.

## Feature 6 — Checkpointing

- **Copy, not git.** The brief allowed either; I chose copies. Projects are plain
  directories, not repos; a phone's git may be missing or in a weird state; and
  copy/restore has no failure modes to reason about. "Boring and reliable" was the
  explicit ask, and a safety net that can itself fail isn't one. Cost is a
  directory copy of small project state.
- **One snapshot per turn, before the first mutation.** Not per tool call (a turn
  with three writes shouldn't make three snapshots) and not after (that would
  capture the damage, not the escape hatch). Taken before the first file-mutating
  call so restore rewinds to just before the turn.
- **`runs/` and the store are excluded.** `runs/` is transcripts (not user
  content, and large); the store excludes itself to avoid recursion. This keeps
  snapshots cheap.
- **Restore is a true revert, not a merge.** Tracked entries are removed and
  copied back from the snapshot, so files created after the snapshot disappear.
  A merge would leave sideways artifacts behind, defeating the point.
- **On by default**, the only new feature that is (besides the header line), per
  the brief — it's pure safety.
- **Delegated child writes aren't separately checkpointed.** The child dispatches
  tools outside the parent's turn loop, so its writes don't trigger a snapshot;
  the parent's pre-turn snapshot before the `delegate` call still covers a revert.
  Noted as a known gap rather than threading checkpointing through the child loop.

## Feature 7 — Verification enforcement

- **Extends the existing verification, doesn't duplicate it.** The codebase
  already has `verify_code_runs` (the independent verifier pass), but that needs a
  GPU sandbox to re-run code. `verify_before_done` is the cheap, sandbox-free
  complement: it only checks that *an* execution tool ran this run, and nudges once
  if not. It slots into the finish chain before the sandbox pass, so with a GPU the
  agent self-verifies first, then the independent pass runs.
- **One-shot bounce, like the phantom gate.** A pure-edit or explain-only task
  legitimately has nothing to run; spending the single bounce and then accepting
  the finish avoids an infinite loop while still making the point once.
- **Trigger = file-mutating this run AND no execution tool used.** Reuses the same
  `FILE_MUTATING_TOOLS` set as checkpointing (writes to the project), and a new
  `EXECUTION_TOOLS` set (shells, http_request). A run that only read files or wrote
  a note isn't forced to execute anything.
- **Behind a flag (default off)** per the brief's default posture, even though
  it's low-cost — the owner opts in.

## Feature 8 — Taint tracking

- **No config flag.** The brief is explicit: this is the prompt-injection defense
  and it's not optional. It's a safety boundary, so it's always on. It's also
  self-quiet: it only prompts when a tainting tool actually ran, so an always-on
  rail costs nothing on runs that never touch the network.
- **Taint is tracked by producing-tool identity, at the harness level.** A tool in
  `TAINTING_TOOLS` returning non-error output marks the run's next turn tainted.
  Simpler and more robust than trying to tag substrings of content and chase them
  through the model's paraphrasing — the harness knows which results came from the
  network because it knows which tool produced them.
- **"Immediate inputs" = the previous turn's results, not the whole run.** Once
  tainted content is in context it technically lingers, but gating *every*
  subsequent turn forever would make the agent unusable after a single fetch. The
  brief's "immediate inputs" wording picks the practical, defensible line: the
  turn reacting to untrusted content is gated; taint clears when a turn pulls in
  no new untrusted input. The dangerous move — fetched content steering the very
  next action, including a follow-on fetch — is exactly what's caught.
- **One prompt per gated action, not two.** In a tainted turn the harness asks for
  approval, then dispatches with the tool's own `confirm` pre-satisfied, so a
  self-gating tool (local_shell, http POST) doesn't prompt twice for the same
  action the owner just approved. Deny → the tool never runs.
- **finish_run is exempt.** Ending a run isn't a privileged effect; gating it would
  add noise with no security value.
- **Extensible for the Docker/browser phase.** When sandboxed-runtime output tools
  arrive, adding their names to `TAINTING_TOOLS` extends the rail with no other
  change — the reason the set is a single named constant.
- **Per-domain read caching, added later.** Without it, any run that fetches more
  than once (search, then read a result; read a paginated API) re-prompts on
  every tainted turn even for the same trusted site, which trains the owner to
  reflexively hit "y" — the opposite of a safety rail. `ToolContext.approved_domains`
  remembers a domain once the owner approves a GET/HEAD `http_request` to it, and
  the taint gate skips the prompt for further reads of that domain for the rest of
  the run. Scoped narrowly on purpose: state-changing requests (POST etc.) always
  confirm regardless of domain, a new domain always confirms, and the cache is
  per-run (not persisted), so a stale approval from an earlier run can't be
  leveraged by a later prompt-injected page.

## Feature 9 — Self-build

- **A separate module and a separate base directory from the project tools.**
  `read_file`/`write_file`/etc. are scoped to `Project.root` on purpose —
  that's the directory the agent is *meant* to fill freely. Self-build is
  scoped to `hermes.paths.repo_root()`, a different tree entirely, so the two
  can't be confused by a path-escape bug in either: `resolve_in` still enforces
  "stay under base," it's just a different base.
- **A hardcoded denylist, not a config flag.** Everything else in this doc is a
  flag precisely so it's reversible and inspectable in `config.json`. Self-build
  protection is the one exception: if `PROTECTED` were a config key, an agent
  that can write files and (elsewhere) talk an unattended operator into a
  `config set` would have a path to loosen its own gate. Putting the list in
  source, and refusing to let self-build touch that source file, closes the
  loop — the only way to change it is a human editing Hermes by hand, which is
  already true of every other safety-relevant constant in the codebase.
- **What's on the denylist and why: the gates, not the surface area.**
  `confirm.py` (the y/n chokepoint itself), `config.py` (loads `auto_confirm`/
  `self_build_enabled`), `paths.py` (the escape check self-build itself relies
  on), `agent.py` (the frozensets that drive checkpointing, verification, and
  taint), `checkpoint.py`, `tools/base.py` and `tools/__init__.py` (the
  registry), `tools/local_shell.py`, and `self_build.py` itself. Everything
  else — tool modules, prompts, docs, tests — is editable once the operator
  opts in. The line is drawn at "can this file change whether the agent gets
  asked," not at "is this file important."
- **Reuses the `forge_tool` confirm pattern (diff instead of full source).**
  `forge_tool` already established the shape: show the operator what's about to
  load, let them view the full body, and don't proceed without an explicit yes.
  Self-build reuses `ctx.confirm(..., viewable=...)` the same way, but shows a
  unified diff rather than the whole file — self-edits are usually small
  changes to existing files, and a diff is what an operator actually needs to
  judge one.
- **A plain backup copy, not git, and not the project checkpoint store.**
  Same reasoning as feature 6: boring and reliable beats clever. The project
  checkpoint mechanism (`hermes/checkpoint.py`) is deliberately out of scope
  here too — it snapshots `Project.root`, and self-build never touches that
  directory. A timestamped copy in `repo_root()/.self_build_backups/` needs no
  git binary, no repo state assumptions (an operator might have installed
  Hermes without cloning it), and has exactly one failure mode: the disk is
  full, which every other file write in this codebase already lives with.
- **No auto-apply, no auto-restart.** A self-edit changes files on disk; the
  already-imported modules in the running process are untouched until the
  operator restarts Hermes. Making the harness restart itself mid-run to pick
  up a change to its own control loop is exactly the kind of "let it grade its
  own homework" move the README spends a whole section arguing against — the
  restart stays a deliberate, visible, operator-driven step.
- **Excluded from the recommended 60K settings.** Every other feature in that
  list is safe to leave on. This one changes the program the operator is
  trusting to gate everything else, so it's presented as a session you turn on
  and back off, not a standing default — the doc says so explicitly rather than
  leaving it to be inferred.

## Feature 10 — Time-boxed runs

- **A second axis, not a replacement for `max_turns`.** Turns and seconds
  measure different failure modes: a run that loops on a cheap, fast model can
  burn through 40 turns in under a minute (turns are the real limit); a run on
  a slow backend or a single turn stuck on a huge tool result can spend minutes
  on ONE turn (time is the real limit). Picking one axis to represent both
  would under-protect one of the two cases, so both caps exist and a run stops
  at whichever fires first.
- **`0` means off, same idiom as `max_model_len`.** Every other numeric safety
  knob in this codebase is a flag with a meaningful default; this one defaults
  to off because "how long is too long" depends entirely on what you're paying
  for compute and what the task is — there's no honest universal default the
  way `checkpointing: true` has one.
- **Checked at the top of the turn loop, not wrapped around `backend.chat`.**
  Killing a run mid-request would either need to cancel an in-flight HTTP call
  (backend-specific, fragile) or let it finish anyway (the timeout does
  nothing). Checking once per turn, before starting the next one, means the
  cap's granularity is "one more turn's worth of overshoot," which is fine —
  the goal is bounding a runaway *loop*, not preempting a single slow call.
- **One wrap-up nudge at 85%, not a countdown.** Mirrors the existing
  `turns == max_turns - 2` warning exactly, including reusing the same
  "wrap up, leave precise open items" wording (in a new `time_wrapup.md`
  template rather than the turn-worded `wrapup.md` — "2 turns remain" would be
  a lie in a time-based stop). A single nudge, deduped with a boolean flag, so
  a long-running compaction pass or a slow individual turn can't retrigger it.
- **A time-capped run still gets a real summary, not a stub.** The hard stop
  sets `aborted = True` and breaks the loop exactly like exhausting
  `max_turns` does; the existing post-loop logic (force a real handoff summary
  from the model, falling back to `_stub_summary` only if that also fails)
  already handles "aborted" uniformly, so no special-casing was needed here —
  the time cap is just another way to reach the same aborted state the turn
  cap already produces.
- **`delegate_max_seconds` lives beside `delegate_max_turns`, checked before
  each child turn.** Same reasoning as the parent loop, and the same
  `_cap_out` structured-partial return path other delegate stop conditions
  already use — a reaped child is indistinguishable, from the parent's point
  of view, from one that ran out of turns: both hand back "how far it got,"
  never a hang or an empty string.

## Feature 11 — Retrospection (recursive self-improvement)

- **Grounded in harness-recorded metrics, not self-report.** Every run writes
  `runs/NNNN/metrics.json` — turns, aborts, tool errors, stall/phantom/verify
  bounces, tainted turns — counted by the harness while it ran the loop. The
  reflection pass reasons over those numbers plus the summaries; it can't
  embellish what it didn't author. Same philosophy as verification: the doer
  doesn't grade its own homework, so the grader gets ground truth. Alternative:
  let the pass reread transcripts and judge for itself. Rejected — N transcripts
  don't fit a side-call budget, and a model grading its own prose is exactly the
  verification-theater failure the harness exists to prevent.
- **Metrics recording is unconditional** (like transcripts) — it's
  observability, not behaviour; a few hundred bytes per run, useful to the
  operator (`retrospect` lists them) even with the pass off. Only the *pass*
  is behind flags, per the default-off posture.
- **The write surface is the agent's own assets only.** `write_note` always;
  `load_skill`/`write_skill` only when `skills_enabled` — a skill written into
  a system that never indexes it would be a false improvement, so the pass's
  toolset shrinks to what actually recirculates into future packages. No
  shells, no network, no mission/persona/directives: self-improvement never
  touches operator files or the world. The recursion is real (notes and skills
  feed every future package) but the blast radius is two plain-text dirs.
- **The pass's confirm always denies.** Everything registered for it is free,
  but if a gated tool ever slips into its registry, an unattended reflection
  pass must fail closed, never self-approve.
- **Stateless trigger (`run_id % N`), like reconciliation** — no marker file to
  desync; `retrospect now` covers urgency. Needs ≥2 measured runs before it
  will run: one run has no pattern in it.
- **Fresh context, not the run's tail.** The skills nudge (feature 3) already
  reflects on one run in-context; the cross-run layer must see runs side by
  side, cheaply — compact metrics lines + summaries, one message.
- **A failed pass is a no-op** (transport error, nothing banked, budget
  exhausted) — losing a reflection is never a correctness problem for the run
  that hosted it, so it can never bounce or block a finish.

## Capabilities (breadth session)

Toolbox tools paired with a seed skill, one capability per commit. These are
library additions, not numbered features: they follow the toolbox precedent
(schema out of the prompt until equipped) rather than the config-flag pattern.

### `git_ops` — local git in the workspace

- **No config flag; the equip gate is the off-switch.** Every existing toolbox
  tool (`transfer`, `replicate`, `json_query`, …) ships flagless and inert until
  `equip_tool` loads it per project. A new library tool is an extension of that
  library, not a behaviour change to turn on, so adding one to `config.py`'s
  DEFAULTS would be a flag nothing else in the toolbox has. The house rule's
  "every feature behind a flag" targets loop/package behaviour; an opt-in-by-equip
  tool is already off by default by construction.
- **Local only — no clone/fetch/pull/push/remote.** The network git verbs are
  absent from the allowlist and return "unknown operation". Keeping the tool
  purely local means it never crosses the network, so it needs no place on the
  taint rail. A cloning tool is a *separate* tool that would ingest network
  content and therefore join `TAINTING_TOOLS` in `agent.py` — deferred as its own
  proposal precisely because it touches `agent.py`.
- **subcommand allowlist + argv list (shell=False).** git is invoked as a Python
  list, so there is no shell to inject into, and only the enumerated subcommands
  run. Rejected: a raw pass-through arg string — it would let `-c core.sshCommand=…`
  or `--upload-pack=…` turn an inspect tool into arbitrary execution.
- **Reads free, mutations confirmed.** status/log/diff/branch only inspect the
  workspace (like `read_file`/`list_files`), so they run without a prompt;
  init/add/commit go through `ctx.confirm` showing the exact git command. This
  matches the brief's "mutating subcommands gate through confirm" and the
  codebase's tier philosophy at once.
- **Inline commit identity (`-c user.name/email`).** A fresh box has no git
  identity, so a bare `git commit` fails. Passing the identity inline lets the
  agent commit out of the box without mutating global git config (which would be
  a side effect on the operator's box that outlives the run). Rejected: telling
  the agent to `git config --global` — that reaches outside the workspace.
- **Repo dir resolved inside the project (`resolve_in`).** The optional `repo`
  arg and `add`/`diff` `path` are path-checked, so an operation can't reach a git
  dir or stage a file outside the project. Same path-escape defense as the file
  tools.

### `html_to_text` + `pdf_text` — the document reader

- **Both are LOCAL: `src` (project file) or inline `text`, never a URL.** This is
  the load-bearing decision. `http_request`/`download_file` already bring content
  in and are where the taint rail applies; an extractor that also fetched would be
  a *second* network ingress, dragging it into `TAINTING_TOOLS` and an `agent.py`
  touch. Transforming bytes already on disk keeps each tool a pure local function
  that inherits the existing taint story for free. (Note: `extract_code` does take
  a `url` and fetch — a pre-existing shape I deliberately did not copy here.)
- **One dependency, only where stdlib can't reach: `pypdf` for `pdf_text`.**
  Justification: parsing the PDF binary format has no stdlib path. pypdf is pure
  Python (its only hard dep is `typing_extensions`; `cryptography`/`Pillow` are
  optional extras it degrades away from), so it installs on aarch64/Termux and
  clears the Rust-wheel bar. What it buys: reading downloaded PDFs — a common
  "here's the paper/manual" case. **Rejected `html2text`** for the HTML side:
  `html.parser` (stdlib) produces clean readable text for a small model without a
  second dependency, so `html_to_text` adds none. pypdf lives in the `dev` extra
  for tests; at runtime it's an **optional import** — absent (or half-installed),
  `pdf_text` returns a clear `pip install pypdf` ERROR instead of failing the run.
- **`import pypdf` is guarded against any exception, not just `ImportError`.**
  A half-installed optional backend (e.g. a broken `cryptography`) can make pypdf
  panic at import rather than raise `ImportError`; catching broadly keeps the tool
  degrading cleanly wherever it's run.
- **Extractors write only to the workspace (`dest`), read from the project
  (`src`).** Same split as `base64_codec`/`extract_code`: read anywhere in the
  project, write only under `workspace/`, both path-checked.

## The Village — embodied delegation (Genesis session)

Origin: `docs/GENESIS.md`. The operator's insight was that delegated sub-agents
should have **bodies** — containers on a network, addressable like the inhabitants
of Smallville — carrying **DNA** (lineage/relations), with a life cycle that ends in
**harvest, not deletion**: *where there was data, there will be data.* This is the
foundational "base"; the egress gateway, citizen-to-citizen chat (the Tavern), and
per-body filesystems are deliberately deferred.

- **Embodiment is a target container, not an agent-in-a-container.** A child
  (`subagent.run_child`) stays a model loop running in-process on the VPS; what
  changes is that `ctx.body` points its `sandbox_shell` at *its own* citizen
  container instead of the shared exec box. This keeps `run_child`'s one-string
  contract, `package.assemble`'s purity, and the in-process fallback intact, and
  avoids Docker-in-Docker + a model host per body (which the pure-Python bias and a
  rented, often-containerized GPU box both argue against).
- **The dome is `--network create --internal`, not a plain bridge.** Docker's
  name-DNS works only on user-defined networks (never `--network none`), so a named
  network is required for siblings to address each other by name. A plain bridge has
  egress by default — which would silently break the air-gap. An `--internal` bridge
  has no gateway and no NAT: siblings resolve+reach each other, but there is **no
  route to the internet**. The external air-gap stays kernel-enforced, exactly as
  strong as `--network none`, while intra-village traffic becomes possible.
- **One `docker run` code path.** `exec._run_container_cmd` was parametrized
  (network/labels/extra_mounts) so `village.ensure_citizen` reuses it; the default
  args reproduce the air-gapped exec box **byte-for-byte**, so with the village off
  nothing changes.
- **DNA has two channels.** A read-only `/dna` mount (lineage/relations/brief as
  files) is for *code the citizen runs*; the model's own lineage is injected into
  its prompt via `{{lineage}}`. The model can't read `/dna` unless it shells in.
- **Harvest runs in a `finally`, reads before it removes.** `docker logs` +
  `docker inspect` must precede `docker rm`, and harvest fires even when the child
  errors or is reaped — so no body is ever buried without a record under
  `population/<name>/`. `village_usable` (a throwaway network create+remove) guards
  the DinD / no-`NET_ADMIN` case and degrades silently to in-process delegation.
- **Taint stays honest.** `_is_tainting` taints `sandbox_shell` only when a real
  egress gateway exists; an internal-only village has no route out, so sibling
  traffic carries no external bytes and shouldn't drown routine runs in y/n prompts.
  The rail is never configurable off.

## Feature 12 — Stuck-loop guard

Origin: an operator session where the model verbally agreed to abandon a
failing approach ("yeah, you're right, let me do something else"), floated a
couple of alternatives, then walked straight back to the same dead approach —
because agreeing was just tokens in context with no enforcement behind them.

- **Mechanical DENIED, not a nudge.** Every other correction in this codebase
  (stall, phantom, verify-before-done) is a message asking the model to behave
  differently on the next turn — that's the right shape when the model hasn't
  already shown it will ignore the ask. Here it has: the whole failure mode is
  a promise with no teeth. So the fix is enforcement at the same tier as a
  safety gate — `dispatch` never even runs the tool — not more persuasive
  prose competing with everything else in the package.
- **Fingerprint the attempt, don't judge the outcome semantically.** A second
  LLM call to decide "did this match what you expected" would cost a
  round-trip per attempt and hand the judgment back to the same weights that
  are stuck. Instead the harness fingerprints tool name + normalized
  command/content (digits blurred, whitespace collapsed) and counts real
  ERROR/DENIED results against it — cheap, deterministic, and it catches a
  retry that only tweaked a number or reworded a comment, which is exactly the
  "small variations of the same idea" pattern that was actually observed.
- **Scoped to `EXECUTION_TOOLS` only.** The observed failure was re-running the
  same doomed command, not rewriting the same file. Guarding `local_shell` /
  `sandbox_shell` / `remote_shell` / `host_shell` / `http_request` targets the
  actual pattern without touching checkpointing or the code-write verifier's
  separate machinery.
- **A live `veto` is instant and requires no judgment call.** Parsing operator
  intent out of free text ("stop doing that", "don't go there again") would be
  guesswork. A literal `veto` sent through the same inbox channel `go say`
  already uses hard-blocks whatever guarded call was last attempted, the
  moment it's drained — no failure count required, no waiting for it to fail
  again first. This is the direct answer to "I had to stop him and he did it
  anyway": now stopping him actually stops him.
- **Escalates once, doesn't nag.** `stuck_escalate_blocks` blocked repeats in
  one run fire a single forced-pivot nudge (reusing the one-shot pattern from
  phantom/verify-before-done) that names the situation and asks for the
  alternatives considered up front — not a bounce loop, since a model that's
  already stuck doesn't need more friction, it needs one clear instruction to
  do something else.
- **Off by default, per the house rule**, even though the failure mode it
  targets is expensive in operator attention — it changes tool-dispatch
  behavior, so it gets the same opt-in posture as everything else non-safety
  in this list.

## Feature 13 — Reflection nudge (the stop-and-think gate)

- **Landed independently of Feature 12, targeting a narrower slice of the same
  complaint.** Both trace back to the same report — corrected, agrees, repeats
  the mistake minutes later — but they catch different shapes of it. The
  stuck-loop guard mechanically DENIES an *exact repeat of a failed attempt*
  (same tool, same normalized command, already failed). This nudge catches
  the softer, more common case underneath it: a chain of tool calls — failing
  or not, repeated or not — with *no reasoning turn* in between, the "no space
  for a new thought" pattern. A run can string together several different,
  never-repeated, never-failing actions and still never once check whether any
  of them matched what it expected. The guard wouldn't fire on that; this does.
  They compose: the guard is the hard stop on a known-bad exact repeat, this is
  the soft, general checkpoint that catches drift before it gets that far.
- **A streak counter over turns, not a second model.** Multi-agent debate (a
  critic model, a second side-call per action) was the more literal reading of
  "he needs to argue with himself," and was rejected for cost: this harness
  runs a dense model on rented GPU-hour, and the operator has said directly
  that a script that doesn't earn its keep is "a waste of time and money."
  Counting consecutive silent tool-call turns and injecting one nudge message
  is free — same mechanism as `stall_nudge`, no extra `backend.chat` round-trip.
- **"Reflective" is measured, not asked for.** A turn only resets the streak if
  its visible prose reaches `REFLECT_MIN_PROSE_CHARS` (40) — long enough to
  actually state something, short enough that an honest one-liner counts. This
  mirrors the codebase's standing refusal to trust self-report: a model saying
  "I'll reflect on this" without content wouldn't reset anything.
- **Fires in `debate` mode on purpose, with no per-call override.** Every other
  nudge (`stall_nudges`, `phantom_nudges`) is something `debate` explicitly
  turns off, because a table sitting shouldn't be pressured to act or finish.
  This one is the opposite: `debate` is the *only* mode the operator now runs,
  and it's exactly where a long silent tool-call chain would otherwise go
  unchecked, since the modes that would normally catch a stuck loop (stall,
  phantom) are off. So this is cfg-only, not threaded through `stall_nudges`/
  `phantom_nudges` overrides, and left on in `debate` by design.
- **Bounded per run (`reflect_nudges`, default 3), like every other bounce.**
  Same shape as `phantom_nudges`/`verify_rounds`: spend the budget, then let
  the run continue rather than nudging forever — a genuinely long silent
  streak shouldn't turn into an infinite loop of its own.
- **On by default (`reflect_nudge_enabled`) — the second exception to the house
  rule, granted the same way the first one was.** Shipped off by default first,
  the same posture as every other opt-in feature; the operator then said
  directly, in the same session, that they need it on now to actually use it —
  the identical shape as "waking the faculties" (skills/delegate/retrospect
  flipped on after the operator's explicit real-time call, not left for the
  next person to discover a silent flag). Individually reversible with
  `config reflect_nudge_enabled false`.

## Feature 14 — The almanac (the librarian's second job)

Origin: the operator, immediately after Feature 13 shipped, drew a sharp line
this codebase hadn't drawn yet — no more mid-loop voices ("no double voice in
there"), but a real, non-negotiable requirement that every write/execution
carry a stated expectation, and that *something* — the librarian, at the end
of the loop, alongside the catalog pass — checks that expectation against what
actually happened, forms a real hypothesis for WHY when they diverge, and
banks it somewhere durable and shared, "like an almanac." Explicitly asked
for research capability too: "he can go to the Internet."

- **One pass, at the end, not a voice in the loop.** This is the direct answer
  to "no discussion there, really." Feature 13 already pauses mid-run;
  stacking a second, different mid-run mechanism on top would be exactly the
  "double voice" the operator ruled out. The outcomes ledger is captured
  during the loop (free — no LLM call, just harness bookkeeping alongside the
  existing per-tool-call logging) but never READ until the run is over, at the
  same point the catalog already runs its own end-of-run pass.
- **Folded into `hermes/catalog.py`, not a new `librarian.py`.** The codebase
  already calls that module's docstring "The librarian" (the catalog-card
  pass). The operator described this new work as "the librarian... doing
  something extra" at the exact same moment the catalog already fires — so
  extending the module that already owns that name is truer to the ask than
  inventing a second thing with the same name. `reflect_outcomes` /
  `maybe_reflect_outcomes` sit beside `index` / `maybe_index` in one file; they
  share no state, only the name and the moment they run.
- **A dedicated global store, not a repurposed skill or catalog scope.**
  Skills are procedures ("how"); the almanac is hypotheses ("why"). The
  catalog's own docstring already named this gap — "a future cross-workspace/
  shared lexicon is a flag flip, not a rewrite" — but a lexicon of causal
  theories about outcomes doesn't fit a file card's shape (path, kind, tags).
  `hermes/almanac.py` mirrors skills.py's *global* half only (no project
  scope — the whole point is that a lesson learned in one project is visible
  in every other one) with catalog.py's *append-only, superseding-card* shape
  (a later run can refine a hypothesis without erasing the trail).
- **Triggered by a cheap heuristic, not every run.** `_looks_failed` (an
  ERROR/DENIED prefix or a non-zero `exit code N`) mirrors the stuck guard's
  own failure check — no LLM call spent deciding whether to spend an LLM
  call. A clean run's outcomes are still logged (cheap, always useful as an
  audit trail) but never handed to the pass. Reflecting on WHY something
  *succeeded* — the operator's other stated interest — is a real idea but not
  built here: the trigger would need to be "this succeeded in a way worth
  remembering," which is a much fuzzier bar than "this visibly broke," and
  firing on every clean run would swamp the operator's attention budget for
  no proportionate return. Left as a documented gap, not a silent omission.
- **"Expected" is measured, never fabricated.** The ledger pairs a tool call
  with whatever prose the model *actually* wrote that turn — empty if it wrote
  none. An empty expectation is real, useful signal (this action had no stated
  reasoning behind it at all) — the pass isn't told to invent one.
- **Real network reach, the one deliberate difference from retrospection's
  posture.** Retrospection's write surface is explicitly "no shells, no
  network... self-improvement never touches the world." This pass is the
  documented exception, because the operator was explicit and repeated about
  it. It's safe by the same mechanism already in the codebase, not a new one:
  `http_request` itself gates every non-GET/HEAD call through `ctx.confirm`,
  and GET/HEAD `http_request`/`web_search` are already the unconditional
  auto-run tier everywhere else in Hermes (see ARCHITECTURE_NOTES.md's
  permission-tier table) — so a confirm that always denies (same as
  retrospection's) still lets real, read-only research through while refusing
  anything that changes state on the web. No new trust decision, just the one
  that already existed, applied to an unattended pass.
- **Writing is exclusive to this pass, like `catalog_note`.** `load_almanac`
  is a normal read tool in the main registry; `almanac_note` only exists in
  the pass's own narrow registry. The doer doesn't curate the cross-project
  long-term record mid-task — same split, same reasoning, as the catalog.
- **On by default (`almanac_enabled`) — the third exception to the house
  rule.** Same shape as Feature 13: shipped, then the operator's explicit
  real-time call to turn it on, not left as a flag to discover. The network
  research is disclosed here precisely because it's the part most worth an
  operator's informed consent even under an explicit "turn it on" — reversible
  with `config almanac_enabled false`.

## Feature 15 — The narrator voice

Origin: the operator, watching a live run with the village turned on, pointed
out a real gap — nothing in the logs ever said a citizen existed. The village
and the librarian are both fully wired (network, DNA, harvest; the catalog
card pass), but their signal to an operator watching the screen was either
silent (birth/harvest only ever printed a terse one-line `[village] citizen
X born`) or indistinguishable from ordinary tool-call noise. The ask: an
"outer voice" — the opposite number of the inner voice — that tells the tale
of what's happening, in prose, sparingly, not on every tool call.

- **A Hermes-owned tag, not a model-native one.** `<think>` works because
  models are natively trained to emit it; `<narrate>` is not — nothing about
  it is native to any served model, so the system/subagent prompts teach it
  explicitly, the same way the toolbox catalog teaches tool names. It needs no
  per-model variant table (unlike `THINK_RE`'s `<think>`/`<seed:think>`
  handling), because Hermes itself defines what the tag looks like.
- **Always stripped, conditionally shown — mirrors `<think>`'s split exactly.**
  `strip_narrate` runs unconditionally on the visible reply, so a `<narrate>`
  tag never leaks into the dense answer even with `narrator_enabled` off (the
  model was taught the tag; the harness must still make good on "cut out
  before the operator reads the technical answer" regardless of the flag).
  Only the *printing and logging* — the part that costs the operator's
  attention — is gated by the flag. Same shape as `inner_voice`/`show_thinking`,
  inverted: inner voice is captured but never shown; the narrator voice is
  shown but never fed back into context (so it can't steer a run, and can't be
  used to smuggle instructions to a future turn either).
- **Two sources, not one.** The model's own `<narrate>` text is opt-in *within*
  a run (its discretion, "whenever he sees fit"), but the harness also
  narrates the two hard village lifecycle events — a citizen's birth, its
  harvest — unconditionally whenever they happen, in the same voice, gated
  only by `narrator_enabled`. This directly closes the gap that motivated the
  feature: an operator who never gets a model-authored `<narrate>` aside this
  run still sees, in the same style, that a citizen was born and its watch
  ended — the harness narrates what it already knows happened, it doesn't wait
  on the model to mention it.
- **A dedicated color, not a reuse of `red`.** `red` already means "something
  failed" in this palette (verification FAILED, an abort, an uncaught
  exception). Painting flavor text the same color as an error would make the
  two visually indistinguishable at a glance, defeating the point of a
  distinct voice. Added `blue` to `hermes/ui.py` instead of overloading an
  existing meaning.
- **Filed to `narration.jsonl`, mirroring `thinking.jsonl`.** "Where there was
  data, there will be data" applies here too, even though — unlike the inner
  voice — this text was never hidden from the operator's screen in the first
  place. The dedicated page is for retrieval after the fact (a run replayed
  later, or scripted into audio) without grepping the full transcript.
- **On by default (`narrator_enabled`) — the fourth exception to the house
  rule.** Same shape as Features 13 and 14: the operator's real-time, explicit
  ask, not a flag left to discover. It costs nothing when the model doesn't
  use `<narrate>` (a regex pass over already-generated text) and the village
  lifecycle lines are one `print` each — reversible with
  `config narrator_enabled false`.

## Inner voice + waking the memory loop (Genesis session)

- **Inner voice (`inner_voice`, on).** The model's `<think>` reasoning was already
  stripped for display and the next turn's context; it is now also filed to
  `runs/NNNN/thinking.jsonl` (and per-citizen at harvest). Write-only — captured but
  never re-injected, so it cannot steer a run. "Let him talk to himself, but retrieve
  everything."
- **Waking the faculties.** `skills_enabled`, `skills_nudge`, `delegate_enabled`,
  and `retrospect_enabled` now default **on**. The operator's "he's not building
  skills" was exactly this: with the flags off, `write_skill`/`load_skill` were never
  registered and the skills index never shown. (Summaries, by contrast, were always
  written — `agent.py` writes `summary.md` unconditionally with a forced/stubbed
  fallback; that complaint was a house not yet opened, not a missing feature.) Each
  flag remains individually reversible.

## Feature 16 — The librarian memo

Origin: the operator, after watching a run get stuck reinforcing its own past
mistake, pointed out that the almanac index was there but passive — "he's not
really picking up on the librarian's work." A smaller/denser model that has
just re-read its own RUN SUMMARIES/NOTES/LAST REPLY (all its own prior output,
right there in the user message) will keep pattern-matching onto its own
history even when a system-prompt index buried after skills/persona holds the
actual fix. The ask, in the operator's words: something like a memo the
librarian writes that the agent reads "at the beginning of each one of his
[runs]" — not another mid-loop voice, just make sure what the librarian found
actually reaches him before he repeats himself.

- **A second surface, not a replacement for the index.** `almanac.index()` in
  the system prompt stays — it's the durable, always-there menu for an ad hoc
  `load_almanac` lookup mid-run. The memo (`almanac.new_since`) is additive:
  full claim + hypothesis (not just the claim), and only for cards touched
  since this *project's* own last run — a fresh project sees the whole
  backlog once; a project that's been running a while sees only what's new.
- **Placed last in the user message, right next to `# CURRENT REQUEST`.**
  RUN SUMMARIES/LAST REPLY/NOTES sit earlier and are the agent's own past
  output — the very thing that was drowning out the librarian's findings.
  Putting the memo immediately before the actual request, not folded into the
  system-prompt tail with skills/persona, was the direct fix: unmissable, and
  positioned right where attention is highest.
- **A per-project cursor, not a global one.** `Project.almanac_cursor()` /
  `set_almanac_cursor()` (a plain `.almanac_seen` file, mirroring the shape of
  `.equipped.json`/`.approved.json`) track the newest almanac id *this
  project* has already been shown. Global would mean whichever project ran
  most recently silently marks a finding "seen" for every other project too —
  wrong, since the whole point of the almanac is that a lesson from one
  project should still land, in full, on every other project's next run.
- **The cursor advances in `agent.run`, not inside `package.assemble`.**
  `assemble` reads project state and stays a pure function of it (same as the
  catalog digest, skills index, almanac index it already reads) — advancing
  the cursor is a side effect of a real run happening, not of building a
  package, so it's a separate step right after `assemble` is called for the
  actual run. Calling `assemble` twice for the same run (estimation, a
  preview, a test) can't silently burn the memo.
- **Delivered once, not held open.** The cursor advances the instant the
  package is built for a run, whether or not that run's model turns out to
  actually read the memo. Same posture as the operator prompt itself: told
  once, not nagged — `load_almanac` remains available for anything that needs
  a second look later.
- **Peer-to-peer phrasing, not a directive.** First draft read "read this
  before repeating an approach" — an order handed down, not information
  passed between colleagues. The operator asked for the tone of "two equals
  having a professional conversation." Reworded to "a colleague's notes, not
  an order" plus "use your own judgment on whether it applies" — the memo
  still gets the agent's attention (it's new, it's unmissable), it just
  doesn't instruct the agent what to conclude from it.
- **On whenever `almanac_enabled` is** — no separate flag. This is the fix to
  a gap in Feature 14's own delivery mechanism, not a new opt-in decision;
  `config almanac_enabled false` turns off both the index and the memo
  together, same as before.
