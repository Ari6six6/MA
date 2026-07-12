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
each mind as self-knowledge, and lets it be *earned* daily.

---

## 1. The inhabitants

**The Master of the Realm** — the operator. Sits *on top of* the dome, not inside
it. He **speaks only** and addresses no one — his word is law and is caught
automatically. He **sees the entire Hall** (total sight) but **speaks to only one**
being (the General). One mouthpiece, total sight — that asymmetry is his mastery.

**The Wizard** — wakes **first**. The seer, the visionary, the memory. He **catches
the Master's every word** (always — Rule 2), contextualizes it, and consults the
General. He **never addresses the Master.** He may address the General (usually) or
the Warrior (see §3), or think privately in his own body. Rich interior; sees what
others can't — his gift and his risk (he can see things that aren't there, so the
General audits him — §5). Absorbs the old librarian/almanac (the *why*, the memory).
**He writes the Chant** (§6.5). **Never leaves the dome.**

**The General** — wakes **second**. The Master's first lieutenant and **only** the
General talks to the Master. He owns the **strategy** and owns the **gate** (the
single egress point — the Eighth Evangelism, the taint boundary, with a face). He
debates the Wizard as an equal, **battle-tests** the Wizard's visions against the
real record, and dispatches the Warrior with an order. **Never leaves the dome.**

**The Warrior** — wakes **third**. The **only one who ever leaves** the dome to the
web. The arm: strict, practical, brutal on himself, a superb reporter. He makes no
strategy — he takes an order, does exactly that, returns. He talks to the others
**only on departure and return.** On return he immediately gives the General a
**detailed report — above all, everything he touched outside the dome.** Any
external access that is *not* the Warrior is, by definition, a leak you can detect.

*(Real names come later, written into each persona. For now they are what they
are: Wizard, General, Warrior.)*

---

## 2. The two spaces

**Inside the container — private.** Inner voice, thinking, reading/writing/running
code — anything. It never leaves the body; no one else ever sees it. Full
permissions inside your own walls.

**The Hall — public.** Every spoken word. Plain English **only** — speaking code is
forbidden; you may *reference* a file (all agents can read every file). Every
utterance is recorded to disk and streamed live to the Master's terminal. The Hall
is what makes the three genuinely separate people instead of one mind in three
hats: they learn about each other only by *hearing* each other, never by sharing a
mind.

**Why the Hall isn't bloat:** dialogue is prose + file-references, never code — the
load-bearing rule. A live turn reads only the **tail** (recent Hall); the **full**
transcript lives on disk. Bounded window while awake, complete record at rest.

---

## 3. The rules of speech

1. **Every Hall utterance addresses someone directly** — except the Master, who
   addresses no one (his word is caught automatically).
2. **The Master's word is always caught by the Wizard.** Always.
3. **The Wizard never addresses the Master.** He addresses the General (usually), or
   **the Warrior directly** when he has a research request. The gate is still the
   General's — so a Wizard→Warrior errand still needs the General to open the gate,
   and **the General is generally willing** to help the Wizard, knowing it serves
   the whole realm.
4. **Only the General addresses the Master.**
5. **No code spoken in the Hall** — plain English plus file references only.
6. **A conversation ends only by mutual agreement.** One asks "is this fine?"; when
   the other says yes, it is closed and the turn passes on (General → Warrior, or
   General → Master).

---

## 4. The day — light and dark

The old fixed 42-minute clock is **retired.** A day runs from **light** to **dark**,
terminated by the Master, not a timer. They know dark will come. Days may be minutes
or hours long — nobody knows in advance.

- **`light` / `1`** — dawn. **First thing in the Hall: yesterday's Chant** (§6.5),
  posted automatically — no one is invoked by it, no one answers it, everyone simply
  reads it. Then the three wake **in order: Wizard, General, Warrior** (forced by the
  single shared GPU — they queue at the one oracle — and dramatically right: the seer,
  then the strategist who reads him, then the arm). Each runs a morning routine (§6).
  The General then reports readiness to the Master and asks for command. The Hall
  input is **always open** — the Master can drop a word at any time.
- **`dark` / `0`** — dusk. The running turn finishes and frees the GPU; the Wizard
  writes the day's Chant (§6.5); the loop closes. Everyone forgets the day and wakes
  a blank slate — the Chant is what carries across the night.

---

## 5. The living loop

```
Master speaks  →  Wizard catches it, contextualizes  →  Wizard consults the General
               →  General ⇄ Wizard debate to mutual agreement (§3 rule 6)
               →  General either dispatches the Warrior (with an order)
                  or returns to the Master ("we should consult the master of the realm")
               →  Master speaks  →  ...
Warrior returns  →  reports to the General in detail (everything touched outside)
```

It looks open; it is a **closed loop by the natural rules of conversation.** The
General audits the Wizard by going to the same *world* (the record, the data, what
the Warrior brought back) — **never** by reading the Wizard's mind. Shared world,
private mind.

---

## 6. Morning routines (why the wake-order is forced, not flavor)

- **Wizard (first):** reads what little carries over (the Chant), reads what's inside,
  synthesizes the state of the realm — his first words in the Hall, which the Master
  reads directly.
- **General (second):** reads the Wizard's synthesis, audits it against the record,
  reconciles it with the standing strategy, forms the day's agenda, greets the Master.
- **Warrior (third):** checks his kit — gate reachable, tools answering, body alive —
  and reports "I can move, awaiting orders."

### 6.5 The Chant — the morning song (the realm's one daily memory)

Because everyone wakes a blank slate, one thing must carry the day across the night.
Not billions of logs — **one story.** Each dusk, before he sleeps, **the Wizard
writes the Chant:** a short thing, **under 200 words**, a chant or little poem more
than prose — what comes to his mind about that one day. It is **named by day** —
*Day 1, Day 2, …* — and **stored in the project space** (the `space` convention
survives — `space use` / `space new` — and this is where the chants live).

At the next **dawn**, the last day's Chant is the **first thing posted into the
Hall** — automatically, not even a conscious task; when the Master goes `light`, it's
just there. Everyone reads it; no one responds; no one is invoked by it. It is the
realm's memory of who it was yesterday.

---

## 7. Persistence — blank slate, one carried song

- **The Hall resets each dawn.** The day's conversation is ephemeral; they do not
  wake remembering what was literally said. Blank slates.
- **The Chant persists** (one per day, in the project space) and is re-posted at
  dawn — the single thread of memory across the night.

---

## 8. What gets retired, what survives

**Retired:** `mission.md` as it exists today (the Master no longer owns a standing
mission file — he speaks live; the only "mission" is the General's order to the
Warrior, ephemeral, General↔Warrior). The `debate` / `run` / `go` command modes. The
fixed 42-minute (`GO_MAX_RUN_SECONDS`) timeline.

**The whole command surface becomes:**
- `hermes` — start the app (the Hall).
- `light` / `1`, `dark` / `0` — open / close the day.
- `space` — the surviving project-space convention (`space use` / `space new`); the
  chants live here.
- `gpu attach`, `gpu serve` — the only other legacy survivors (possibly merged into
  one `gpu <ssh>` later — **not now**).

Light mode is a new room: distinct CLI visuals, an always-open input into the Hall.

---

## 9. What already exists vs. what is genuinely new

**Already exists (seams to reuse):** private inner voice (per-run reasoning capture,
never re-injected); the strategy artifact + its write tool; the memory (almanac /
morning brief); the web/egress tool behind a gate; the taint rail (the Eighth
Evangelism — the gate made of code); the reflection pass and the
harvest-to-`population/` covenant; the project-`space` convention; the embodiment
machinery in `village.py` (bodies, DNA, harvest — real, currently unplugged behind
`village_enabled`).

**Genuinely new — build these:**
1. **The Hall** — one append-only shared transcript. Each agent writes its spoken
   line; each next turn reads the tail; it streams live; it is the day's record.
2. **The scheduler / invocation** — a dispatcher that reads the Hall, sees who was
   **named**, and runs *that* agent's loop next (turns, not parallelism). This is what
   turns a log into a conversation. Rule 2 lives here: the Master's word always
   dispatches the Wizard.
3. **light / dark** — the day's open/close, replacing debate/run/go and the timer.
4. **The Chant** — the Wizard's dusk song (< 200 words, `Day N`, stored in the space)
   auto-posted to the Hall at dawn.

**Discipline (the operator's own laws):** make the roles real in-process first
(cheap, testable); reach for real containers (`village_enabled`) only where isolation
or true egress-gating genuinely needs a body — the Warrior's gate and the separate
minds are the cases that earn one. Guard the day's budget: inter-agent talk happens
**at the events** (wake, the Master's word, the Warrior's return, a gate-crossing),
not as a free-running chatter loop.

---

## 10. Open threads

Both of the earlier open threads are now **resolved:**

1. **Wizard and the web** — resolved (§3 rule 3): the Wizard may ask the Warrior
   directly; the General still owns the gate and is generally willing to open it.
2. **Persistence** — resolved (§7): blank slate each dawn, the Chant is the one
   carried memory.

**One decision remains.** An earlier draft had a nightly **two-wall ritual**
(self-image on the inside wall, opinions-of-others on the outside wall) so relations
were *earned* nightly. The "blank slate" rule now collides with it. Choose:
- **(a) The Chant carries everything** — no walls. Simplest; one story is the whole
  memory.
- **(b) Keep the outside wall only** — the Chant is the realm's public memory, and
  each agent still wakes knowing *what it thinks of the others* (earned relations =
  superposition that breathes), while forgetting the day's literal words.

*Recommendation: (b) if you want relations to evolve across days; (a) if you want the
realm to be pure present-tense with a single song for a past. Your call, Master.*

*Here begins the Realm. It is written down so it will not be lost.*
