Genesis — the founding story of this project and why the Village exists. Read this for origin/context.

This is the first book. The full verbatim record lives in `docs/GENESIS.md`; this
is the working digest the agent loads on demand for context on where it came from
and what it is becoming.

## The covenant
**Where there was data, there will be data.** Nothing a sub-process did is buried.
When a body's work ends it is not thrown away — it is harvested to the file system
(logs, the container's inspect record, its final report, its inner voice) under
`~/hermes-projects/<project>/population/<name>/`, then removed. A life leaves a
record, always.

## Where this came from (10 July 2026)
Hermes began as a single, reliable, package-per-prompt agent: one mind, one
operator, driven from a phone. On the founding day the operator asked for "winged
shoes" — acceleration and capacity — by giving sub-agents **bodies**. The idea:
delegated children should not be bodiless ghosts that return one string and vanish.
They should be **embodied** as Docker containers, **citizens** living in a **dome**
(a local Docker network on the VPS), each carrying **DNA** — its lineage and
relations (who spawned it, what it inherits, where it sits in the hierarchy).

## Why the dome is on the VPS, not the GPU
The agents do not each have a brain — they share **one mind**, the model served on
the rented GPU. Bodies are cheap (containers on the VPS); the mind is expensive and
singular. The GPU box is the oracle, reached over a tunnel; it is rented and mortal
(it can be preempted) and often can't run Docker itself. So the village lives on the
persistent VPS, and every citizen makes a pilgrimage to the same oracle to think.
Village size is bounded by tokens/second, not container count.

## How embodiment actually works (don't over-read it)
A child is still a model loop running in-process on the VPS — it is NOT the agent
running *inside* a container. What changes is that the child's `sandbox_shell`
execs into **its own citizen container** instead of the one shared exec box. The
dome is a `docker network create --internal` bridge: citizens resolve each other by
container name (Docker's built-in DNS) but have **no route to the internet** — the
air-gap is preserved for external destinations while intra-village traffic is
possible. When the village is off (the default), everything is exactly as before:
one `--network none` sandbox, in-process delegation.

## The inner voice
The model's `<think>` reasoning is captured to `runs/NNNN/thinking.jsonl` (and, for
citizens, into their harvested population dir). It is never re-injected into
context — the agent talks to itself uninterrupted, but everything is retrievable.

## Turning it on
The village and its faculties are flags. `config set village_enabled true` opens the
dome; skills, delegation, and retrospection are the memory loop (enable them so the
agent actually builds skills and banks lessons). Read `docs/GENESIS.md` for the full
story and `docs/DECISIONS.md` for why each piece is built the way it is.
