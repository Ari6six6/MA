# Quickstart — the whole thing in one page

You open Hermes and you want to get work done. Here's everything you actually
need, and nothing you don't.

## The one verb

```
go <what you want done>
```

That starts a **work session**: a run, up to 42 minutes long (a ceiling, not a
target — small asks finish in seconds). It runs in the background, so closing
your phone doesn't kill it, and you watch it work live as it goes.

While it's working, it's a conversation, not a black box:

- **You can steer it any time.** Type `go <more>` (or `go say <more>`) and what
  you say gets folded into what it's doing.
- **It can ask you back.** When it hits a real fork — a decision that turns on
  what *you* want — it stops and asks, and waits for your answer. You reply the
  same way: `go <your answer>`.
- **Stepping out doesn't stop it.** Ctrl-C leaves the live view; the session
  keeps running in the background. `go` (or `go attach`) drops you back in.
  `go status` shows what's alive.

That's the loop. Start something, talk to it while it runs, come back to the
result.

> Prefer to sit with it the whole time instead of backgrounding it? `session`
> (alias `s`) is the same thing but foreground — it stays in front of you and
> hands you the turn after each step. Same 42-minute budget, same back-and-forth.

## The GPU (once per work day)

The model runs on a GPU you rent on demand. Two commands bring it up:

```
gpu attach     connect to a GPU box (rents/finds one)
gpu serve      load the model onto it
```

`gpu status` checks it, `gpu down` releases it when you're done. You only do this
once at the start of a working session, not per `go`.

## The mission

```
mission          show the standing brief the agent always sees
mission edit      change it
```

`mission.md` is a short brief that rides along in **every** session — who you
are, what you're building, standing preferences. Set it once and every `go`
inherits it. It's the difference between re-explaining yourself each time and
just saying "go".

## Where your work actually lives

Everything is plain files on the VPS. No database, nothing hidden.

```
~/.hermes/                  config.json · persona.md · gpu state   (settings)
~/hermes-projects/<space>/  ← all your work lives under here
   mission.md               the standing brief (mission edit)
   notes.md                 facts the agent chose to remember
   workspace/               the files the agent creates and edits — its desk
   runs/0001/ 0002/ ...      one folder per `go`: transcript + a summary
   history.jsonl            every prompt you've sent
```

A **"space"** is just that folder — a named workbench holding one line of work.
You don't have to think about it: your first `go` auto-creates the default one
and everything lands there. If you ever want a clean, separate workbench (a
different project entirely), `space new <name>` makes one; `space list` shows
them. Most of the time you never touch this.

**Runtime thinking:** each `go` is one runtime (≤42 min). Its work piles up in
`workspace/`, and each run leaves a summary the next one reads — so a day of
work is just a series of `go`s in the same space, each picking up where the last
left off. You think in *sessions*, not in project bureaucracy.

## That's it

```
go <text>      start / steer a work session
go status      what's running
gpu attach     ·  gpu serve       bring the model up (once per day)
mission edit   the standing brief
help           these essentials  ·  help more   everything else
```

Everything else the program can do — skills, checkpoints, managed servers,
delegation, self-review — is under `help more` when you want it. You don't need
any of it to work.
