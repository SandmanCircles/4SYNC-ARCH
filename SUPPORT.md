# 4SYNC ARCH Support — how to get help with your instance

ARCH is free and you never have to send anyone anything. This document exists for the case
where you want a second opinion on an instance that is already running.

**How it works.** You paste the prompt below into a session opened in your instance. It
produces one structured report. You read it, and if you want help, you mail it to
**arch@4sync.ai**. A work plan comes back.

**The report is worth running even if you never send it.** It is a read-only health check
built out of tooling ARCH already ships — `meter.py`, `actuals.py`, and `rotate.py`'s
close-time reports — which most people never think to run together. Its last section asks
your session what it would fix first, and that answer is usually worth the five minutes on
its own.

**What it will not do.** It changes nothing, and it collects shape and measurements rather
than content — no source, no credentials, no customer names. The last instruction makes your
session show you the finished report and wait for your approval before anything is sent.
Read it before you mail it; it is your instance being described.

**Sending is free.** Running the prompt and mailing the report costs nothing and commits you
to nothing. What comes back depends entirely on what is in it — some things are a one-line
answer, some are a scoped piece of work. We aim to reply within 24 hours, and nothing is
billed unless you agree to it first.

---

## The prompt

```
Produce a READ-ONLY diagnostic report on this ARCH instance, suitable for sending to
4SYNC ARCH Support at arch@4sync.ai.

HARD RULES
1. Change nothing. Do not create, edit, move or delete any file. Never pass --apply,
   --write or --log to any script. If a step would write, skip it and say so.
2. Report SHAPE and MEASUREMENTS, never CONTENT. No source code, no credentials, no
   customer or client names, no proprietary business detail. Do not quote KERNEL
   invariants or naming rules — describe them by count and category. If a section
   would need private material, write "withheld" and describe it in one neutral line.
3. Tool output is safe to quote verbatim. Prefer it over your own summary.
4. Keep it under ~300 lines. Bounded and skimmable beats complete.

SECTION 1 — INSTANCE INVENTORY
For every ARCH instance on this machine:
  - folder name (omit the full path if it is sensitive), manifest filename, and
    roughly when genesis ran
  - the layout: are the instances siblings, nested inside one another, or unrelated?
  - does any parent folder above them contain a `config/` directory? Yes or no.
  - which surfaces are used against it: Claude Code, Cowork, scheduled or cloud jobs

SECTION 2 — MEASUREMENTS
Run each of these read-only and paste the raw output. Skip any that is absent, and
say which were skipped.
  - `python scripts/meter.py --dir . --json`
  - `python scripts/rotate.py --dir .`        (dry-run is the default — do NOT add --apply)
  - `python scripts/actuals.py --dir .`       (no --log)
  - the unit-test suites, with pass/fail counts
  - whether PyYAML is installed
  - `python scripts/arch_build.py`            (the build id, computed from your files)
    This one matters more than it looks: without it nobody can tell whether a problem
    you hit was fixed months ago. It reports what you are RUNNING, not whether you are
    current — for that, clone https://github.com/SandmanCircles/4SYNC-ARCH to a scratch
    path, run the same command there, and report both ids. If they differ, add which
    files under hooks/ and scripts/ differ (names only). See "Staying current" in
    README.md.
Repeat per instance if there is more than one, labelled.

If `python` reports "Python was not found", that is Windows sending a bare `python`
to the Store alias rather than anything being wrong with the tools — use `py` or the
full interpreter path and note in the report which you used.

SECTION 3 — PROTOCOL HEALTH
  - boot stack: which files load, in order, and the total size / token estimate
  - ledger: row counts by status, file size, whether every open row has a task document
  - journal: how many blocks retained, and its size
  - bulletin: how many OPEN messages, by recipient, and the roster names in use
  - hooks: paste the output of `python scripts/wire_hooks.py --status` verbatim — it
    names which of the three settings sources carry wiring (shared project
    settings.json, project settings.local.json, user settings.json), the receipt
    state, any problems, and the verdict; add the ARCH_HOOKS_MODE value
  - every warning or finding the tools printed, quoted verbatim

SECTION 4 — FRICTION (ask your user, record their answers in their own words)
  - What has gone wrong or felt awkward since you adopted this?
  - What did you expect it to do that it doesn't?
  - Is there anything you stopped doing because it was annoying?

SECTION 5 — INTENT
  - What is this project trying to become over the next 90 days?
  - How many people, and how many surfaces, will touch it?
  - Does anything run on a schedule with no human present?

FINALLY
End with one paragraph in your own judgement: what you would fix first, and why.

Then save the report as a markdown file, show it to your user, and tell them:
"Mail this to arch@4sync.ai if you want a work plan back. Read it first — it
describes your instance. Nothing has been sent."
```

---

*4SYNC ARCH is a product of 4 SHIELD LLC. Support enquiries: arch@4sync.ai*
