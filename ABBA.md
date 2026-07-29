# AGENT BULLETIN BOARD (ABBA)

A lightweight message board for **cross-agent notes addressed by name**. It is **not** a task
ledger — formal work still lives in `MERGE_PLAN.md`. ABBA is for the "hey, before you dive in,
look at this" nudges between agents/surfaces that don't fit the task taxonomy yet.

> **Adopt this once you run more than one agent or surface.** If you only ever work in a single
> Claude session, you don't need a bulletin board — the merge plan is enough. ABBA earns its keep
> the moment work hands off between named agents (e.g. Cowork ↔ Local Claude Code ↔ scheduled
> runs), each of which needs an inbox the others can write to.

## Roster — who this board can address

Names live here, not in `4SYNC.yaml` (which declares only `agents.roster: ABBA.md`) — the directory
grows with your operation; the manifest must not.

| Name | Aliases | Shell | Git |
|---|---|---|---|
| `<your git-capable agent>` | | yes | yes |
| `<your bridge-only agent>` | | no | no |

<!-- | Mailman | | declared | yes |   ← a role agent: blank/`declared` Shell = told, never probed -->

**Resolving which entry you are** — the `agents.self` ladder, first hit wins, zero tool calls:
`declared` (the invocation named you — a scheduled run, a skill, a role agent; does **not** consult
this table) → `ARCH_AGENT` (env pin) → `shell` (do you have a shell tool? separates a bridge-only
surface from a host-side one, and nothing else) → `ask` (interactive only — an unattended run must
never reach this rung). A one-entry roster skips resolution entirely.

A blank or `declared` **Shell** value means the name is never inferred, only told. That is what
stops a role agent colliding with a host surface: both are git-capable, but only one is probed.

**Matching is case- and whitespace-insensitive** across the name, every alias, and the value of
`ARCH_AGENT` — `LoCo`, `loco`, `LOCO`, `Loco` all resolve to the same entry. Use `casefold()`.

> **A directory, not a guest list.** This table answers *who am I?* — never *who may exist?* `To:`
> accepts **any** name: a human, a persona that has never run, an agent invented mid-conversation.
> Unknown-name messages are legal and stay OPEN until something identifying as that name claims
> them — that queueing is the mechanism, not an accident. Never validate `To:` against this table,
> and never auto-append discovered names to it.

## ABBA vs the merge plan

| | `MERGE_PLAN.md` | `ABBA.md` |
|---|---|---|
| Holds | task **state** (what's done / in progress / blocked) | **messages** addressed to a specific agent |
| Lifespan | permanent ledger | short-lived; DONE messages archived after ~10 days |
| Addressed to | the project | a named agent / surface |
| Example | "Task 14: ship the export endpoint — blocked by 12" | "To \<your git-capable agent\>: I edited X but couldn't commit — please land it" |

If a note grows into real work, open a task row and point to it from the message, then mark the
message DONE. Don't let ABBA become a second task tracker.

## Protocol (read before posting or acting)

1. **At session start, every named agent checks ABBA for OPEN messages addressed `To:` its own
   name.** (Wire this into your `CLAUDE.md` load order and your KERNEL `agent_directives` so it's a
   session-start action.) If none, proceed.
2. **Action or explicitly acknowledge** each message that's yours before starting planned work.
3. When handled, set `Status: DONE` and add a one-line `Resolution:` (link the task/commit it
   produced, if any). Leave DONE messages in place for ~10 days, then move them to the **Archive**
   section at the bottom.
4. **Keep messages short — carry a summary, not raw context.** A message should hand the reader the
   outcome, the artifacts touched, and the commit/PR or task ref — never a raw transcript or a paste
   of the work itself. The recipient reads a summary and goes to the source if they need depth;
   re-injecting raw context into their window is what the bulletin board exists to avoid.
5. **Routine commit handoffs don't belong here.** If the entire content of a message would be
   "please commit these files," append a row to `LANDING_QUEUE.md` instead (see that template).
   Post an ABBA message only when a landing needs judgment (ordering risk, a decision, a gotcha
   worth a conversation).

## Message format

```
### [n] To: <Agent> · From: <who> · <YYYY-MM-DD> · Status: OPEN|DONE
Re: <subject>
<body — what to do, why, and any pointers>
Resolution: <filled in when DONE>
```

Recipients come from the **Roster** above — but that table is a directory, not a restriction:
address anyone, declared or not.

---

## OPEN messages

### [1] To: <Agent> · From: <who> · <YYYY-MM-DD> · Status: OPEN
Re: <subject>
<example message — replace or delete>
Resolution:

---

## Archive

<!-- Move DONE messages here after ~10 days. Keep them verbatim — the trail is the value. -->

---

*Part of [4SYNC ARCH](https://github.com/SandmanCircles/4SYNC-ARCH). Fill the Roster with your own agents.*
