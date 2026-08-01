# [PROJECT NAME] — Findings

Durable operational facts that belong to no task. **Never loaded at boot** — declared under `on_demand:` in `4SYNC.yaml`, and reached by **grepping `Trigger:` lines**, not by reading the file.

---

## Protocol — read before adding an entry

This file exists because the same class of knowledge otherwise ends up either **overpriced** — parked in `MERGE_PLAN.md`, the file every session reads, costing tokens forever — or **lost**, written into a journal block that ages out of the recent window and effectively disappears. Identical facts, opposite fates, decided by nothing more than which hand wrote them down.

Be honest that this is **one more surface in a system whose entire pitch is fewer, better-placed files.** It earns that only if the three rules below are enforced rather than aspirational. Without them it is a junk drawer, which is the disease the `tasks/` split exists to cure.

**1. `Trigger:` is mandatory.** If you cannot say *when* a future session needs this, it is not a finding — it is a thought. Thoughts go in the journal and age out. The trigger is also what makes this file loopable into agent flows: an agent greps triggers and reads only the matching entry, so the standing cost is the trigger index, not the file. (The general rule: *addressed files get scanned, unaddressed files get capped.* This file is addressed by trigger.)

**2. Every entry has an `Exit:`.** It becomes a hook rule (if mechanically catchable), becomes a task row (if actionable), or is retired when it stops being true. Nothing lives here permanently by default. **A findings file with no eviction path is a junk drawer with a nice name.**

**3. It is measured.** `scripts/rotate.py` reports this file's size alongside the ledger and the journal, and flags any entry missing a `Trigger:`. A bucket nobody measures is exactly how a ledger becomes most of your boot cost.

**What does NOT belong here:** answers to questions asked later — *why this licence, why this architecture* — belong in `config/REFERENCE.yaml`. Deep canon is not a finding. Task substance belongs in `tasks/MP-0NN.md`.

**If a future session finds this file full of entries with no trigger and no exit, the correct move is to DELETE THE FILE, not reorganise it.** The experiment will have failed, and saying so is the honest outcome.

### Entry format

```
### <one-line title>
Trigger: <the moment a session needs this — the grep target>
<the finding, as short as it can honestly be>
Exit: <hook rule | task row | retire-when>
```

Findings sort naturally into three kinds, and the distinction matters more than the file does:

- **Fire at the moment of the mistake** — an environment trap, a command that silently does the wrong thing. *A document cannot prevent these*; nobody reads a file before typing a command. These want a **hook rule**, and the entry exists only until one is written.
- **Change a conclusion before reasoning starts** — a fact that, if unknown, makes a session confidently report a defect that isn't one. These genuinely need reading, and the load-bearing ones earn a **one-line pointer in `config/KERNEL.yaml`** — a pointer, never the content.
- **Answer a question asked later** — these already have a home in `REFERENCE.yaml`. Do not migrate them here.

---

## Findings

### [Example] A command that silently resolves to the wrong binary
Trigger: running that command from the shell tool on this machine
[What actually happens, and what to do instead. Keep it to what a session needs at the moment it is about to make the mistake.]
Exit: candidate hook rule — mechanically catchable

---

*Pattern from 4SYNC ARCH.*
