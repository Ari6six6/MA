You are reviewing your own recent performance in this project — not to do new
work, but to make future runs go better. This is a private reflection pass:
nothing here changes any answer already given to the operator.

Below: per-run METRICS the harness recorded, your own RUN SUMMARIES, your
current SKILLS INDEX, the tail of your NOTES, and the WORKSPACE CATALOG — one
card per file you have produced, with what it is for and whether it duplicates
or supersedes another. The metrics are ground truth — the harness counted them
while running the loop; you did not author them. Do not argue with them and do
not embellish them.

# METRICS (harness-recorded, oldest first)

{{metrics}}

Reading the columns: `aborted` = hit the turn cap or the error breaker.
`tool_errors` = tool calls that came back ERROR/DENIED. `stall_nudges` =
bounced for narrating instead of acting. `phantom_bounces` = tried to finish
with code only pasted in the reply. `verify_bounces` / `verify_failures` =
finished without verifying / failed independent verification. `tainted_turns`
= turns gated because untrusted network content was in scope.

# RUN SUMMARIES

{{summaries}}

# SKILLS INDEX

{{skills_index}}

# NOTES (tail)

{{notes}}

# WORKSPACE CATALOG (your files — what each is for)

{{catalog}}

Look for what RECURS in two places now — how your runs WENT (the metrics and
summaries) and what your runs LEFT BEHIND (the catalog). In the catalog,
watch for: several files flagged as the same content under different names (a
task you keep re-deriving instead of reusing), sprawl of near-duplicate
scripts, or files with no clear purpose. These are problems that never show up
in the metrics because they live in the artifacts, not the run — this is the
one place you can catch them. For each recurring pattern where you can state a
concrete fix:

- `write_skill` a procedure (or update the existing one — writing the same
  name edits it in place) when the fix is a how-to: the exact commands, the
  error you keep hitting, the way past it. (Only when the skill tools are
  available to you in this pass.)
- `write_note` when the fix is a fact or standing reminder your future
  packages should carry.
- `catalog_note` when the problem is a specific FILE: sharpen its purpose, or
  raise a flag on it (e.g. "duplicate of scraper.py — consolidate") so the
  recommendation rides beside that file in every future package. (Only when
  the catalog is available to you in this pass.)

Rules: every conclusion must trace to the data above — no invented problems,
no self-congratulation. One or two high-value writes beat many. You cannot
touch mission, persona, or directives — those belong to the operator. If
nothing recurs, say "nothing worth changing" and stop.
