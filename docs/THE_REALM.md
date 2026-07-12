# THE REALM — the design of the embodied village (superposition)

*The third book. Genesis tells where the harness came from; the Evangelisms tell
what it learned to do; **The Realm** is the design of what it is becoming — a dome
of embodied agents who know who they are, who the others are, where everyone
lives, and how they relate. This is the operator's agreed spec, captured from the
founding debate. It is **design, not yet built** — the record kept so no thread is
lost. Where there was data, there will be data.*

> **Destination:** the changes are dramatic enough that this becomes its own
> project — **MOR — Masters of the Realm.** This document is written to travel there.

---

## 0. What "superposition" means

Every inhabitant knows, at all times: **who I am, what I do, where I live on the
network, who the others are, where they live, and how I relate to them.** The map
is not narrated — it is a fact of the architecture. The kernel already holds it
(`docker network inspect`, the DNA in `village.py`); the Realm surfaces it into
each mind as self-knowledge, and lets it be *earned* daily (the walls, §7).

---

## 1. The inhabitants

**The Master of the Realm** — the operator. Sits *on top of* the dome, not inside
it. He **speaks only** and addresses no one — his word is law and is caught
automatically. He **sees the entire Hall** (total sight). He **speaks to only one**
being (the General), and **only the General may speak back to him** — but that line
runs both ways and is always open (§3, §8).

**The Wizard** — wakes **first**. The seer, the visionary, the memory. He **catches
the Master's every word** (always — Rule 2), contextualizes it, consults the
General. He **never addresses the Master.** He may address the General (usually) or
the Warrior (§3), or think privately. His gift and his risk: he sees what others
can't, including things that aren't there — so the General audits him (§5). Absorbs
the old librarian/almanac (the *why*). **He writes the Chant** (§6.5) **and keeps the
Theory of the World** (§9) — the map of everywhere the realm has reached. **Never
leaves the dome.**

**The General** — wakes **second**. The Master's first lieutenant, and the **only**
one who talks with the Master — reporting, asking, and **escalating for permission**
(§8). Owns the **strategy** and the **gate** (the single egress point — the Eighth
Evangelism with a face). Debates the Wizard as an equal, **battle-tests** his visions
against the real record, dispatches the Warrior. **Never leaves the dome.**

**The Warrior** — wakes **third**. The **only one who ever leaves** the dome to the
web. The arm: strict, practical, brutal on himself, a superb reporter. Makes no
strategy — takes an order, does exactly that, returns. Talks to the others **only on
departure and return.** On return he gives the General a **detailed report — above
all, everything he touched outside** (which becomes the Theory of the World, §9).
Any external access that is *not* the Warrior is, by definition, a detectable leak.

*(Real names come later, written into each persona. And a persona is a **living
seed, not a static sheet**: the Master writes who each one starts as, and the walls
(§7) evolve it — every night an inhabitant rewrites its own self-image, so who they
are drifts with what they live. For now they are what they are: Wizard, General,
Warrior.)*

---

## 2. The two spaces

**Inside the container — private.** Inner voice, thinking, reading/writing/running
code — anything. It never leaves the body; no one else sees it. Full permissions in
your own walls.

**The Hall — public.** Every spoken word. Plain English **only** — speaking code is
forbidden; you may *reference* a file (all agents read every file). Recorded to disk,
streamed live to the Master's terminal. The Hall is what makes the three genuinely
separate people instead of one mind in three hats: they learn about each other only
by *hearing*, never by sharing a mind.

**Why it isn't bloat:** dialogue is prose + file-references, never code — the
load-bearing rule. A live turn reads only the **tail**; the **full** transcript
lives on disk. Bounded window awake, complete record at rest.

---

## 3. The rules of speech

1. **Every Hall utterance addresses someone directly** — except the Master, who
   addresses no one (his word is caught automatically).
2. **The Master's word is always caught by the Wizard.** Always.
3. **The Wizard never addresses the Master.** He addresses the General (usually), or
   **the Warrior directly** for a research errand — the gate is still the General's,
   so that errand still needs the General to open it, and **the General is generally
   willing.**
4. **The General is the only one who speaks with the Master — both ways.** He may
   report, ask questions, and **escalate for permission** unprompted (§8). "Nobody
   addresses the Master" applies to everyone *except* the General.
5. **No code spoken in the Hall** — plain English plus file references only.
6. **A conversation ends only by mutual agreement.** One asks "is this fine?"; when
   the other says yes, it closes and the turn passes on.

---

## 4. The day — light and dark

The fixed 42-minute clock is **retired.** A day runs from **light** to **dark**,
ended by the Master, not a timer. Days may be minutes or hours; nobody knows in
advance.

- **`light` / `1`** — beams the Master into the realm (a new room, distinct CLI
  visuals, an always-open input). **First in the Hall: yesterday's Chant** (§6.5),
  posted automatically — no one invoked, no one answers, everyone reads. Then the
  three wake **in order: Wizard, General, Warrior** (forced by the single shared
  GPU — they queue at the one oracle). Each runs a morning routine (§6). The General
  reports readiness and asks for command.
- **`dark` / `0`** — the running turn finishes and frees the GPU; each writes its
  walls (§7); the Wizard writes the day's Chant (§6.5); the loop closes and the Master
  is prompted back out. Everyone forgets the day's words and wakes a blank slate — the
  Chant and the walls are what cross the night.

**The Master's input is always queued as the next turn.** He can type + enter at any
time; the moment the running turn finishes and the GPU is idle, **he is the next to
get the program's attention.** A turn mid-thought is never interrupted — the queue
waits for idle. The only two ways the Master re-enters the day: **(a)** he says
something (queued in), or **(b)** the General comes to him (report, question, or
escalation). Going `dark` ends it.

---

## 5. The living loop

```
Master speaks  →  Wizard catches, contextualizes  →  Wizard consults the General
               →  General ⇄ Wizard debate to mutual agreement (§3.6)
               →  General dispatches the Warrior (with an order)
                  — or escalates to the Master for permission (§8)
                  — or returns to the Master for orders
               →  Master speaks  →  ...
Warrior returns  →  reports to the General in detail (everything touched outside)
```

Open-looking, but a **closed loop by the natural rules of conversation.** The General
audits the Wizard by going to the same *world* — never by reading his mind. Shared
world, private mind.

---

## 6. Morning routines (the wake-order is forced, not flavor)

- **Wizard (first):** reads what carried over (the Chant, his walls), reads what's
  inside, folds the Warrior's latest reports into the Theory of the World (§9), and
  synthesizes the state of the realm — his first words in the Hall.
- **General (second):** reads the Wizard's synthesis, audits it, reconciles it with
  the standing strategy and the Theory of the World, forms the day's agenda, greets
  the Master.
- **Warrior (third):** checks his kit — gate reachable, tools answering, body alive —
  reports "I can move, awaiting orders."

### 6.5 The Chant — the morning song (the realm's one shared memory)

Because everyone wakes a blank slate, one thing must carry the day across the night —
not billions of logs, **one story.** Each dusk, before sleep, **the Wizard writes the
Chant:** under **200 words**, a chant or little poem more than prose — what comes to
his mind about that one day. **Named by day** — *Day 1, Day 2, …* — and **stored in
the project `space`** (that convention survives). At the next **dawn** it is the
**first thing posted into the Hall**, automatically; everyone reads it, no one
responds, no one is invoked. The realm's memory of who it was yesterday.

---

## 7. Persistence — blank slate, with what survives

At **dusk**, before sleep, each inhabitant reads the day's Hall and writes:
- **The inside wall — self-image (private):** who I am after today.
- **The outside wall — what I make of the others (public):** read by all at dawn.

The outside wall makes **relations earned, not fixed** — superposition that breathes.

**On the containers (the expert call you asked for):** the *bodies* are cheap and
ephemeral — a container can be torn down at dark and reborn at light, and it should
be, so nothing rots. The **walls are not the container** — they are files, harvested
to `population/<name>/` under the covenant (*you don't bury the body; you keep the
record*). At dawn a fresh body is born and its walls are **mounted back into it.** So:
**bodies die nightly, walls persist.** That resolves the bloat worry — the container
carries nothing across the night; only the small, deliberate files do (the walls, the
Chant, the Theory of the World). Whether the runtime is Docker at all is an
implementation detail below this line and not your concern.

- **The Hall resets each dawn** (ephemeral day-conversation; harvested at dusk).
- **The Chant, the walls, and the Theory of the World persist** and are re-loaded at
  dawn.

---

## 8. The chain of command — egress permission

Reaching the outside world escalates **bottom-to-top and back down.** When the
General decides to send the Warrior to a web service, he **asks the Master's
permission first.** The Master **authorizes a service once** — a domain or target IP —
and thereafter the realm may reach it freely (self-served, no re-asking). A *new*
service is a *new* escalation. This is exactly the existing rail: a per-domain
allowlist (`http_policy`) plus the taint boundary (the Eighth Evangelism) — approve
`api.example.com` once, it's on the list, and everything the Warrior drags back
through the gate is tainted until the Master's turn clears it.

```
Wizard → General → Master (permission for a new service)
       ← Master authorizes the domain once (added to the allowlist)
General → Warrior → gate → web → back → report → General
```

---

## 9. The Theory of the World — the Wizard's map of the outside

The realm holds **a theory of the outside world** — a map of the internet it has
actually touched, and **it is the Wizard's to keep.** When the Warrior goes somewhere,
the realm remembers the plain facts: **IP address, domain, paths/filesystem, subdomains
hit**; if services **share an IP**, that's known; services touched every few days are
**mapped by cadence**; and for each, *what we do with them and what usually comes back*
— our relationship to them. Plain JSON.

The Warrior **gathers** it (it's already in his return report, §1); the **Wizard
cartographs** it — folding each fresh report into the persistent map at dawn (§6),
because seeing the shape of a world others only touch piecemeal is exactly the seer's
gift. The **General reads** it for strategy. So the flow mirrors the whole realm: the
arm brings raw ground-truth home, the seer turns it into a map, the strategist plans
on it. A high-value feature to build once the spine stands — the Wizard's second
artifact, beside the Chant.

---

## 10. What gets retired, what survives, what's new

**Retired:** `mission.md` as it exists today (the Master speaks live; the only
"mission" is the General's ephemeral order to the Warrior). The `debate` / `run` /
`go` modes. The fixed 42-minute (`GO_MAX_RUN_SECONDS`) timeline.

**Command surface:** `hermes` (start) · `light`/`1`, `dark`/`0` (open/close the day) ·
`space` (the surviving project-space; chants live here) · `gpu attach`, `gpu serve`
(the last legacy survivors — maybe one `gpu <ssh>` later, *not now*).

**Already exists (seams to reuse):** the private inner voice (per-run reasoning,
never re-injected); strategy + its write tool; the memory (almanac / morning brief);
the web/egress tool behind a gate; the per-domain allowlist (`http_policy`) and the
taint rail; the reflection pass and the harvest-to-`population/` covenant; the
project-`space`; the embodiment in `village.py` (bodies, DNA, harvest — real, unplugged
behind `village_enabled`).

**Genuinely new — build these (the spine):**
1. **The Hall** — one append-only shared transcript; each agent writes its line, each
   next turn reads the tail; streams live; the day's record.
2. **The scheduler / invocation** — reads the Hall, sees who was **named**, runs *that*
   agent next (turns, not parallelism). Rule 2 lives here.
3. **light / dark** — the day's open/close, replacing debate/run/go and the timer, with
   the always-queued Master input.
4. **The Chant** — the Wizard's dusk song, auto-posted at dawn.
5. **The two walls** — the dusk ritual; bodies die nightly, walls persist and re-mount.

*Then, on top of the spine: the **Theory of the World** (§9) — the Wizard's map,
cartographed from the Warrior's reports, read by the General for strategy.*

**Discipline (the operator's own laws):** make roles real in-process first (cheap,
testable); reach for real containers only where isolation or true egress-gating needs
a body — the Warrior's gate and the separate minds are the cases that earn one. Guard
the day: inter-agent talk fires **at the events** (wake, the Master's word, the
Warrior's return, a gate-crossing), never as a free-running chatter loop.

---

*Here begins the Realm. It is written down so it will not be lost. Until we meet
again.*
