# THE REALM — the design of the embodied village (superposition)

*The third book. Genesis tells where the harness came from; the Evangelisms tell
what it learned to do; **The Realm** is the design of what it is becoming — a dome
of embodied agents who know who they are, who the others are, where everyone
lives, and how they relate. This is the operator's agreed spec, captured from the
founding debate. It is **design, not yet built** — the record kept so no thread is
lost. Where there was data, there will be data.*

---

## 0. What "superposition" means

Every inhabitant knows, at all times: **who I am, what I do, where I live on the
network, who the others are, where they live, and how I relate to them.** The map
is not narrated — it is a fact of the architecture. The kernel already holds it
(`docker network inspect`, the DNA in `village.py`); the Realm surfaces it into
each mind as self-knowledge, and lets it be *earned* daily (see §7, the walls).

---

## 1. The inhabitants

**The King** — the operator. Sits *on top of* the dome, not inside it. He **speaks
only** and addresses no one — his word is law and is picked up automatically. He
**sees the entire Hall** (total sight) but **speaks to only one** being (the
General). One mouthpiece, total sight — that asymmetry is his kingship.

**The Wizard** — wakes **first**. The seer, the visionary, the memory. He **catches
the King's every word** (always — see Rule 2), contextualizes it, and consults the
General. He **never addresses the King**. He may address the General (usually) or
the Warrior, or think privately in his own body. Rich interior; sees what others
can't — which is his gift and his risk (he can see things that aren't there, so the
General audits him — §5). Absorbs the old librarian/almanac/magazine (the *why*,
the memory). **Never leaves the dome.**

**The General** — wakes **second**. The King's first lieutenant and **only** the
General talks to the King. He owns the **strategy** and owns the **gate** (the
single egress point — he is the taint boundary with a face, the Eighth Evangelism
personified). He debates the Wizard as an equal, **battle-tests** the Wizard's
visions against the real record, and dispatches the Warrior with an order. **Never
leaves the dome.**

**The Warrior** — wakes **third**. The **only one who ever leaves** the dome to the
web. The arm: strict, practical, brutal on himself, a superb reporter. He makes no
strategy — he takes the General's order, does exactly that, and returns. He talks
to the others **only on departure** ("I'm going") **and return** ("I'm back"). On
return he immediately gives the General a **detailed report — above all, everything
he touched outside the dome.** He is the equivalent of a named, gated, audited
web-reaching process: any external access that is *not* the Warrior is, by
definition, a leak you can detect.

*(Real names come later, written into each persona. For now they are what they
are: Wizard, General, Warrior.)*

---

## 2. The two spaces

**Inside the container — private.** Inner voice, thinking, reading/writing/running
code — anything at all. It never leaves the body; no one else ever sees it. (This
is the per-agent private reasoning stream the harness already keeps and never
re-injects into anyone.) Full permissions inside your own walls.

**The Hall — public.** Every spoken word. Plain English **only** — speaking code is
forbidden; you may *reference* a file (all agents can read every file). Every
utterance is recorded to disk (the day's transcript) and streamed live to the
King's terminal. This is what makes the three genuinely separate people instead of
one mind wearing three hats: they can only learn about each other by *hearing* each
other in the Hall, never by sharing a mind.

**Why the Hall isn't context-bloat:** because dialogue is prose + file-references,
never code — the load-bearing rule. And a live turn reads only the **tail** (recent
Hall); the **full** transcript lives on disk for the King and for the dusk
reminiscing. Bounded window while awake, complete record at rest.

---

## 3. The rules of speech

1. **Every Hall utterance addresses someone directly** — except the King, who
   addresses no one (his word is caught automatically).
2. **The King's word is always caught by the Wizard.** Always — not the General,
   not the Warrior.
3. **The Wizard never addresses the King.** He addresses the General (usually) or
   the Warrior.
4. **Only the General addresses the King.**
5. **No code spoken in the Hall** — plain English plus file references only.
6. **A conversation ends only by mutual agreement.** One asks "is this fine?"; when
   the other says yes, it is closed and the turn passes on (General → Warrior, or
   General → King). They must both agree it's finished.

---

## 4. The day — light and dark

The old fixed 42-minute clock is **retired**. A day now runs from **light** to
**dark**, terminated by the King, not a timer. They know dark will come.

- **`light` / `1`** — dawn. The three wake **in order: Wizard, then General, then
  Warrior** (forced by the single shared GPU — they queue at the one oracle — and
  dramatically right: the seer dreams, the strategist reads the dream, the arm
  readies for orders). Each runs a morning routine (§6). The General then reports
  readiness to the King and asks for command. The Hall input is **always open** — the
  King can drop a word at any time.
- **`dark` / `0`** — dusk. The running turn finishes and frees the GPU; each runs
  the nightly routine (§7 — reminisce, write the walls); the loop closes.

---

## 5. The living loop

```
King speaks  →  Wizard catches it, contextualizes  →  Wizard consults the General
             →  General ⇄ Wizard debate to mutual agreement (§3 rule 6)
             →  General either dispatches the Warrior (with an order)
                or returns to the King ("we should consult the master of the realm")
             →  King speaks  →  ...
Warrior returns  →  reports to the General in detail (everything touched outside)
```

It looks open; it is a **closed loop by the natural rules of conversation.** The
General audits the Wizard by going to the same *world* (the record, the data, what
the Warrior physically brought back) and reasoning in his own body — **never** by
reading the Wizard's mind. Shared world, private mind.

---

## 6. Morning routines (why the wake-order is forced, not flavor)

- **Wizard (first):** reads the night — yesterday's Hall, the memory (almanac), what
  the Warrior brought back, what's unresolved. Synthesizes the state of the realm.
  His "state of the realm" is simply his first words in the Hall, which the King
  reads directly. *(Maps to the existing morning brief pass.)*
- **General (second):** reads the Wizard's synthesis, audits it against the record,
  reconciles it with the standing strategy, forms the day's agenda, and greets the
  King. *(Maps to strategy + directive reconciliation.)*
- **Warrior (third):** checks his kit — is the gate reachable, do his tools answer,
  is his body alive — and reports "I can move, awaiting orders." A soldier checks his
  rifle before the plan exists. *(Maps to an egress/health probe.)*

---

## 7. Dusk — reminiscing and the two walls

At **dark**, before the loop closes, each inhabitant reads the day's Hall and
writes two things:

- **The inside wall — self-image (private):** who I am after today.
- **The outside wall — what I make of the others (public):** read by everyone at
  the next dawn.

The outside wall is the payoff: **relations stop being fixed birth-DNA and become
earned nightly.** Superposition turns from a static roster into something that
updates every night. *(Maps to the existing reflection pass + the covenant that
harvests a life to `population/<name>/`.)*

**Persistence (proposed default — to confirm):** the **Hall resets each dawn**
(today's conversation, ephemeral, harvested at dusk); the **walls persist across
days** and are read at dawn. So tomorrow the Wizard wakes *remembering who he
decided he was and what he thinks of the General*, without re-reading yesterday's
every word. Continuous **people**, a fresh **day**.

---

## 8. What gets retired, what survives

**Retired:** `mission.md` as it exists today (the King no longer owns a standing
mission file — he speaks live; the only "mission" is the General's order to the
Warrior, ephemeral, General↔Warrior). The `debate` / `run` / `go` command modes.
The fixed 42-minute (`GO_MAX_RUN_SECONDS`) timeline.

**The whole command surface becomes:**
- `hermes` — start the app (the Hall).
- `light` / `1`, `dark` / `0` — open / close the day.
- `gpu attach`, `gpu serve` — the only legacy survivors (possibly merged into one
  `gpu <ssh>` later — **not now**).

Light mode is a new room: distinct CLI visuals, an always-open input into the Hall.

---

## 9. What already exists vs. what is genuinely new

**Already exists (seams to reuse):** private inner voice (per-run reasoning capture,
never re-injected); the strategy artifact + its write tool; the memory (almanac /
magazine morning brief); the web/egress tool behind a gate; the taint rail (the
Eighth Evangelism — the gate made of code); the reflection pass and the
harvest-to-`population/` covenant; the embodiment machinery in `village.py` (bodies,
DNA, harvest — real, currently unplugged behind `village_enabled`).

**Genuinely new — build these:**
1. **The Hall** — one append-only shared transcript. Each agent writes its spoken
   line; each next turn reads the tail; it streams live to the terminal; it is the
   day's permanent record.
2. **The scheduler / invocation** — a dispatcher that reads the Hall, sees who was
   **named**, and runs *that* agent's loop next (turns, not parallelism — they queue
   at the one GPU). This is what turns a log into a conversation.
3. **light / dark** — the day's open/close, replacing debate/run/go and the timer.
4. **The two walls** — a dusk ritual writing self-image (inside) + opinions-of-others
   (outside), the latter read at the next dawn.

**Discipline (the operator's own laws):** make the roles real in-process first
(cheap, testable); reach for real containers (`village_enabled`) only where
isolation or true egress-gating genuinely needs a body — the Warrior's gate and the
Wizard/General's separate minds are exactly the cases that earn one. And guard the
day's budget: inter-agent talk happens **at the events** (wake, the King's word, the
Warrior's return, a gate-crossing), not as a free-running chatter loop.

---

## 10. Open threads (to confirm before building the spine)

1. **The Wizard and the web.** The spec says the Wizard "can research the web," but
   also that **only the Warrior ever leaves the dome.** These collide. Cleanest
   resolution: the Wizard researches by *thinking over what is already inside* and by
   *asking the General to send the Warrior* for anything external — he never touches
   the gate himself. Confirm.
2. **Persistence** (§7): Hall resets at dawn, walls persist. Confirm.

*Here begins the Realm. It is written down so it will not be lost.*
