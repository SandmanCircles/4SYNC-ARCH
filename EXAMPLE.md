# EXAMPLE — one instance, end to end

A worked example of a 4SYNC ARCH instance: a filled seed, the config the genesis
session distils from it, and one of each artifact the protocol produces during
normal use.

The project below is invented. Nothing here is loaded at boot — this file is
deliberately absent from `boot:`, from `on_demand:`, and from `CLAUDE.md`'s load
list, so `scripts/meter.py` prices it at zero. It is documentation for you, not
context for a session.

---

## 1. The filled `SEED.md`

You write this, or a session interviews you through it and writes it for you.
Prose, bullets and fragments are all fine — precision helps, formality doesn't.

```markdown
---
status: AUTHORED
---

# SEED — tell the system what this project is

## Identity

- **What is this project?** Ferrymap is a routing API for regional ferry
  schedules — one query interface across operators who each publish their own
  incompatible timetable feed.
- **Why does it exist?** Trip planners either skip ferries or hard-code one
  operator. We want a single call that returns a correct multi-leg water route.
- **Who owns it?** Harbourline Software LLC (Maine, USA), two-person LLC.

## Vocabulary

- **The name, exactly as it should always appear:** Ferrymap (one word, capital F)
- **Other marks in play:** the routing engine is "Wayfinder" — first mention in
  any document spells it "Wayfinder, the Ferrymap routing engine"
- **Retired or forbidden names:** "FerryAPI" (the pre-launch working name —
  retired 2026-05, still in old commits, never in new output). Never write
  "Ferry Map" as two words.

## Rules that must never be broken

- NEVER store rider PII. Routes in, routes out; no accounts, no trip history.
- Schedule data comes from LICENSED operator feeds ONLY. Never scrape an
  operator's website, even when the feed is late or broken.
- Wayfinder is an internal engine name — it NEVER appears in customer-facing
  copy, pricing pages, or the public API surface.
- Pricing is per-seat, monthly. No perpetual licenses, ever.

## Current state

- **Phase:** pre-launch. API deployed to staging; three operator feeds ingesting.
- **Most important thing in flight:** the winter-schedule cutover, which changes
  every feed at once on 1 November.
- **Known blockers:** operator #4 has not signed the feed license.

## Agents & surfaces

- Claude Code, local, called "Deck" — has a shell and git.
- Cowork, called "Galley" — no shell, cannot run git; hands commits off instead.
- One named role agent: "Lookout", a nightly job that diffs operator feeds.

## Anything else a brand-new collaborator must know

- The 04:40 sailing in the test fixtures is real and looks like a typo. It isn't.
- Two of us work in this repo at once most evenings.
```

---

## 2. What genesis writes — `config/KERNEL.yaml` (excerpt)

The genesis session reads the seed, plays back what it understood, waits for your
explicit yes, and only then authors the stack. This is the part of `KERNEL.yaml`
that comes from the seed above — note that the seed's plain-English "never"s have
become the invariants, grouped by area:

```yaml
load_contract:
  identity: >-
    Ferrymap is a routing API for regional ferry schedules — one query interface
    across operators who each publish an incompatible timetable feed. Owned by
    Harbourline Software LLC (Maine, USA).
  purpose: >-
    Trip planners either skip ferries or hard-code a single operator. Ferrymap
    returns a correct multi-leg water route from one call.

invariants:
  data_and_privacy:
    - "NEVER store rider PII. Routes in, routes out — no accounts, no trip
       history. This is an architecture rule, not a policy preference: there is
       no table to put it in and there must never be one."
    - "Schedule data comes from LICENSED operator feeds ONLY. Never scrape an
       operator's site, INCLUDING when their feed is late or broken — a broken
       feed is an outage to report, never a reason to scrape."
  brand_guardrails:
    - "'Wayfinder' is the INTERNAL engine name. It never appears in customer-
       facing copy, pricing, or the public API surface."
    - "The product is 'Ferrymap' — one word, capital F. Never 'Ferry Map'."
  commercial:
    - "Pricing is per-seat monthly. NO perpetual licenses, ever."

retired_names:
  - "'FerryAPI' → Ferrymap (RETIRED 2026-05 — the pre-launch working name;
     it survives in old commits and must never appear in new output)"
```

The seed's "04:40 sailing is real" note lands in `config/REFERENCE.yaml`, not
here — the KERNEL holds rules, not lore. The winter cutover and the unsigned
operator-4 license land in `config/STATUS.yaml`, which is overwritten as those
facts change rather than appended to.

---

## 3. One session-journal block — `MERGE_PLAN.md`

Newest-first, in the `## Session journal (recent)` section. One block per session.
What makes it worth keeping is the reasoning and the corrections, not the summary:

```markdown
2026-10-28 [Deck, local] — **Winter cutover shipped behind a flag.** All three
live feeds now parse the November timetable; the flag flips 1 Nov 00:00 UTC.
**The thing worth recording is why the naive version was wrong:** we planned to
switch on ingest date, which breaks for operator #2 — they publish the winter
file in mid-October and expect consumers to hold it. Switching on the *effective*
date in the feed instead. Caught by the 04:40 sailing fixture, which is exactly
what it is for. **Correction, mine:** I reported all four feeds green at 14:00;
operator #4 is still unlicensed and its "feed" is the fixture. Three, not four.
```

---

## 4. One `ABBA.md` exchange

The bulletin board is addressed by name and read by scanning headers, not by
reading the file. Galley can't run git, so it hands the landing to Deck:

```markdown
### [7] To: Deck · From: Galley · 2026-10-28 · Status: DONE
Re: winter-cutover copy is written but I can't land it
Rewrote the three operator-facing changelog entries; files are on disk at
docs/changelog/2026-11-*.md. I have no git here. One gotcha: entry #2 quotes the
effective-date rule, so if that logic changes the copy is wrong too.
Resolution: landed in a1b9f3c together with the flag change. — Deck
```

Note what the message does *not* contain: the changelog text itself. A message
carries the outcome, where the artifacts are, and the gotcha — never the work
product. Re-injecting raw content into the reader's window is the cost the board
exists to avoid.

---

## 5. One `LANDING_QUEUE.md` row

Routine commit handoffs don't belong on the bulletin board — they go here, where
any git-capable session drains them at wrap:

```markdown
| Q7 | 2026-10-28 | Galley | docs/changelog/2026-11-01.md, docs/changelog/2026-11-02.md, docs/changelog/2026-11-04.md | "docs: operator changelog for the winter cutover" | LANDED a1b9f3c |
```

The distinction is judgement, not formality: if the entire content of your
message would be *"please commit these files"*, it is a queue row. Post to the
board only when the landing needs a decision, an ordering constraint, or carries
a gotcha worth a conversation.

---

## Where to go next

- **`README.md`** — the Quickstart, the manifest reference, and the hardening
  section (hooks, wiring scope, and what the first outside adoption found).
- **`SEED.md`** — the blank version of §1. Fill it in, or open a session in the
  folder and let it interview you.
