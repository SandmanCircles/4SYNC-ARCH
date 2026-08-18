# ADOPTING — trying it, bringing your history, and getting back out

The README is the manual and `TIPS.md` is how to use it day to day. This is the part
before both: how to try it without risking anything, how to bring an existing project
in, and how to leave if you decide against it.

Everything here is observed rather than designed. Each section describes something
adopters and their sessions already worked out; writing it down is so the next person
doesn't have to.

---

## Trying it first

**Try it in a folder you intend to delete.** Genesis writes `instance.root` in
permanently, so the folder you run it in is the folder it belongs to forever. That is
the right behavior, and it's also why a trial gets its own throwaway folder rather than
a corner of something real.

A trial folder is disposable. **It is not a scratchpad.**

That distinction is the one thing to get right, and it's easy to miss because both
words sound like "temporary." A scratchpad path is often somewhere a second session
can't open — a sandbox, a container, a temp area that belongs to one session and
vanishes with it. **The whole value of this thing is state surviving from one session
to the next.** A trial nothing else can reach can't demonstrate that, which makes it a
trial of everything except the point.

So: a real folder, somewhere you can find it, that you delete afterward.

```
mkdir ~/arch-trial && cd ~/arch-trial && git init
```

The `git init` is not dressing: **ARCH requires a repository.** Rotation's dirty-tree
gate, the close's commit step, and every exit in this document run on git — and genesis
will ask ("has git been initiated?") and offer to run it if you skipped it.

### What a trial should actually show you

One sitting, four steps. The first three are what any drop-in does. **The fourth is the
only one that proves anything.**

1. **Boot.** Drop the files in, start a session, say anything. It reads the manifest and
   orients.
2. **Genesis.** It interviews you, then **plays back what it understood and stops.** Read
   that playback — nothing is written until you confirm, and it's the cheapest moment in
   the whole process to catch a misunderstanding.
3. **Do a scrap of work.** Anything. Then say "wrap up."
4. **Close the session. Open a new one. Say "status."**

Step 4 is the trial. Everything before it you could get from reading the repo. A second
session picking up exactly where the first one left off — without you re-explaining
anything — is the product, and it's the only part a single sitting can't show by
existing.

### Then throw it away

**A trial doesn't graduate.** When you're convinced, delete the folder and run genesis
again in the real project. Don't try to move the trial: `instance.root` and the hook
paths are absolute, and repairing them by hand is a worse first day than starting clean.

You lose whatever you did in the trial. That's the correct price — a trial's job is to
earn trust, not to be the beginning of your project.

---

## Adopting a project that already exists

The README says ARCH wants a folder of its own, and that's true. It reads like a bar on
existing projects, and it isn't. **The pattern that works:**

1. Make a **new, empty folder** for ARCH. Run genesis there.
2. **Add your existing project's folder to that session** as a second directory.

Your project keeps its own repo, its own history, and its own root. ARCH gets the clean
folder it needs. The session can read across both, which is what matters — it can walk
your codebase and your old notes while writing state into a folder that is entirely its
own.

The alternative — dropping the files into your existing project's parent — also works
and is what some people reach for first. It's simply less tidy, and it puts ARCH's
packaging next to your code.

---

## Bringing your history with you

The question every owner of an old project asks is some version of: *does this only
start recording from today, or can it go back?*

**It can go back further than you'd expect, and there are two different kinds of past
involved.**

### Conclusions come across immediately

Your invariants, naming rules, retired terms, and the architectural decisions you've
already made are not a timeline. They're your project's **current truth**, distilled
from everything that came before. The seed interview exists to pull exactly that out of
an old project, so the *conclusions* of years of work land on day one.

### The chronological record is reconstructable, from three real sources

| Source | What it recovers | How far back | Effort |
|---|---|---|---|
| `scripts/actuals.py` | Per-session token and cost history, **including sessions from before you adopted** | Only as far as Claude Code's transcript retention — **this expires** | Automatic |
| `git log` | A closed-task ledger and a condensed history of what got built, when, and why | Your entire commit history | Real work: a session walks the log and writes it |
| `scripts/split_ledger.py` | Any task list you already keep inline, migrated into `tasks/` | Whatever task history exists | Automatic |

**Run the transcript one first.** It's the only source with a clock on it:

```
python scripts/actuals.py --all --log
```

`--all` means every project on this machine. Pre-adoption sessions are the ones that
prune soonest, so this is the step that gets more expensive the longer you wait. The
other two sources aren't going anywhere.

For the git walk, ask for **milestone level, not commit by commit.** A phase-level
reconstruction — here's each major stage of the project and the reasoning behind it —
captures the story cheaply. Commit-by-commit is real effort for diminishing return.

### The honest limit

It cannot recover reasoning that was never written down anywhere. Decisions you made in
your head, in a chat that's gone, on a call — those are gone, **and it should not invent
them.** Backfill reaches exactly as far as your durable artifacts and no further.

### Why this doesn't make your context worse

The obvious objection to documenting years of history is that you'll pay for it at every
boot. **You won't, and it's structural rather than a promise.**

Reconstructed history lands in the tiers that are never loaded: session digests go to
`JOURNAL_HISTORY.md` (never read whole), retroactive closed tasks go to `tasks/closed/`
(never read, kept greppable), and deep rationale goes to `REFERENCE.yaml` (on demand
only). So you can document the entire history of an old project without adding a single
token to any future boot.

The system has a place for the past precisely because it separates frozen archive from
live state.

---

## The seed is worth your time

Genesis will interview you if you'd rather not write anything, and that works. But the
adopter who got the most out of this spent **six to seven hours hand-writing a seed
document** before running anything, and called to say what it was doing for him within
three hours of finishing.

That's not a coincidence and it's not a requirement. It's just the honest relationship:
**what you get out of the identity stack is bounded by what you put into the seed.**

What's worth writing down:

- **Every name you've retired** and what replaced it, with the reason. This is the single
  highest-value category, because it's the knowledge that dies with the person who
  remembers it.
- **Decisions that are locked** — the ones you don't want relitigated every few months.
- **Why you do things the way you do.** Not the convention, the argument behind it.
- **What this project is not**, and what it must never become.
- **Who works on it**, if more than one agent or surface touches it.

You can add all of this later by editing the files. It's just cheaper to say it once,
up front, to something that's asking.

---

## Taking an instance to a second machine

Clone it with git and the **protocol** arrives whole — but the **enforcement layer does
not travel.** Hook wiring is machine-local (absolute interpreter and hook paths, in
gitignored settings), so the second machine boots with no guards, no session-debt
recorder, and no boot receipt — silently, because the receipt is the announcement
channel and it is part of what is missing. Two commands close the gap, once per
machine, per instance root:

```
python scripts/wire_hooks.py --status   # is THIS machine wired for THIS instance?
python scripts/wire_hooks.py --write    # wire it, then reload the session
```

Also worth knowing: `.session_debt.tsv` is per-machine, so the "do you have company?"
boot reading only sees sessions on the machine you are on. Two machines editing the
same ledgers through git rely on the anchored-edit discipline and git conflicts — the
same backstop as any two concurrent writers.

---

## Getting back out

Worth knowing before you start, not after. **The exit cost is low, and it isn't a trick
— it's structural.** There's no proprietary format, no database, no cloud dependency,
and nothing running that you didn't wire yourself. Everything is plain files in your
repo.

Three ways out, by effort:

| Level | What you do | Effort | Reversible |
|---|---|---|---|
| **Soft abandon** | Nothing. Stop saying "wrap up." A session that doesn't read the manifest just doesn't. The files sit inert | None | Resume any time |
| **Clean removal, keep the knowledge** | `git rm` the machinery — manifest, `config/` scaffolding, `hooks/`, `scripts/`, the bulletin board, the landing queue, the findings file. Keep the files that are your project's brain. Unwire the hooks | Half an hour to an hour, mostly deciding what to keep | Fully — it's git |
| **Total revert** | `git revert` the genesis commit | Minutes | Instantly |

Even at the second level, **your accumulated knowledge stays fully readable.** You lose
the choreography, not the content.

(All three ways out assume the repository the adoption path requires. A folder that was
never `git init`-ed has no genesis commit to revert — which is one of the reasons
genesis now asks about git before it writes anything.)

### Two things that are slightly sticky, stated plainly

1. **Hook wiring lives in your settings, not in the repo.** If you wired at user level —
   which is recommended when you run several instances — backing out means remembering
   to unwire there too. Forgetting is harmless, since the hooks do nothing outside a live
   instance; you'd just have a dangling entry.
2. **Habit, and anything you built on top.** Your own guards, a shortcut command,
   scheduled jobs that learned to read the queue. That's behavioral coupling rather than
   technical, and it's the same for any tool you actually use.

### On the license

**FSL-1.1-ALv2 is not viral.** It covers ARCH's own code. Removing ARCH's files removes
the obligation, and **nothing attaches to your work** — not while you use it, and not
after you stop.

### The asymmetry worth naming

Adopting is deliberate and takes minutes. Backing the machinery out is a `git rm` and
takes under an hour. **The content is yours either way.**

So the decision is genuinely low-stakes in both directions, which is a point in its
favor rather than a disclaimer — and it's exactly why trying it on one real project
costs you almost nothing.

---

## If you're a session reading this on someone's behalf

You'll likely be the one running the trial. Two things:

- **Ask for a real folder path.** Don't put the trial in your scratchpad — see the top
  of this document for why. Disposable and unreachable are not the same property.
- **If you're in a cloud container, you have no git and no hooks.** You can read, author
  and run the protocol; you can't commit it or make a guard fire for someone to watch.
  Write what you did into `LANDING_QUEUE.md` and a session on their machine lands it.
- **If you're booted inside another ARCH instance with hooks at `enforce`, you cannot
  write the trial folder.** The root fence blocks cross-instance writes by design — no
  override, no prompt. That is the fence working, not breaking. Launch the trial
  session with its working directory IN the trial folder instead, which is the fence's
  own prescription ("write from a session booted there") — and a cleaner trial anyway,
  since that session carries none of your other project's context.

---

*Part of [4SYNC ARCH](https://github.com/SandmanCircles/4SYNC-ARCH). Genesis moves this to `arch/ADOPTING.md` — it's the product's documentation, not your project's.*
