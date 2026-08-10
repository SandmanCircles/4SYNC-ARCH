# TIPS — turning vibes into builds

> **Turn vibes into builds that you can stand on.**

The README is the manual. This is the part you'd otherwise learn by running it for a month.

## Starting out

- Give it a folder of its own. Genesis writes that path in permanently.
- At genesis it plays back what it understood before writing anything. Read that playback — it's the cheapest moment to fix a misunderstanding.
- If you'd rather not fill in the seed, just start a session. It'll interview you.
- Nothing to install. If you moved the folder somewhere, say so — that breaks it quietly.

## Opening a session — say anything

It boots either way. What you open with just aims it.

- **"Hi"** — oriented and waiting. Perfectly fine.
- **"Load the task list"** — shows you everything open before you decide anything.
- **"Open #22"** — that one task, nothing else loaded.
- **"Status"** — where things stand: what's live, what's blocked, what's in flight.
- **"What changed since yesterday?"** — reads back the last sessions' journal.
- **"Pick up the ball and run"** — and see what happens. Might want to keep an eye on it.

There's no command vocabulary to learn, and no wrong opener. Plain English gets there.

## Every session

- Say "wrap up" when you're done. Don't just close the window — an unclosed session leaves its work undeposited.
- Silence doesn't end a session. Walk away and come back; it's still where you left it.
- Don't paste your own files into the chat. Tell it which file and let it read.
- Ask "what's open?" instead of re-explaining. If it can't answer, the ledger needs attention, not you.
- New work you want remembered? Say "put that in the ledger." Chat is not storage.

## Getting good answers

- Correct it out loud. "That's wrong, we call it X" is worth more than a rewrite you do silently.
- When you rule on something, say you're ruling. It records decisions differently from suggestions.
- Don't let it write your positions for you. If it drafts your opinion and you nod, that becomes canon.
- Ask "what did you assume?" when something looks off. Usually faster than debugging the output.
- Ask how it knows. Anything it can't source, treat as a guess.

## When it asks permission

- The prompt names what it's about to do and why something objected. Read it; it's short.
- If it says a file would lose lines — that's another session's work about to disappear. Say no and ask what changed.
- Approving is fine when you know the answer. The prompt exists so you get the chance, not to slow you down.

## Running more than one

You can open several and put them all to work. That's the shape this is built for, not a hazard it tolerates.

The dividing line is where the session actually runs, not what it's called. On your machine it has git and the hooks; in a cloud container it has neither, whatever folder you launched it from.

|  | Claude Code, on your machine | Cowork, in the cloud |
|---|---|---|
| **Git** | Commits, tags, pushes | None |
| **Hooks** | Fire on every tool call | Never |
| **In the debt file** | Yes — the others can see it | No — invisible to the others |
| **Finishes work by** | Committing it | Writing a landing-queue row |
| **Reach for it when** | Work needs to land, or you're closing out | You want to think out loud without the technical register |

Two of the same kind is just two of the same row. The pairing that does the most is one of each: the cloud session works, the machine session lands it. Two hands is normal. Six is a busy afternoon.

- **Ask for the handoff by name** if a cloud session forgets it. "Queue that for landing" is the whole instruction.
- Tell each session the others exist. They're careful neighbors, but only once they know they have neighbors.
- Expect more permission prompts when they overlap. That's them noticing each other, which is the feature.
- Two sessions editing the same file is fine and happens constantly. Two sessions editing it from stale copies is the thing to avoid — say "re-read it first" and you've handled it.

## Checking on it

- "What build am I running?" — it'll tell you, computed live.
- "Am I current?" — compare against the published version on the site.
- "What did this session cost?" — it measures rather than estimates.
- Ask for the support report when something feels wrong. It's read-only, and running it for yourself is usually worth the five minutes.

## Watch for

- Numbers in your own docs that nobody recomputes. They drift.
- A session agreeing with you too readily on something you're unsure about.
- Work you did in chat that never landed in a file. If it isn't written down, it didn't happen.

---

None of this is a workflow you have to adopt. It's what the folder already does — these are just the parts nobody tells you.

---

*Part of [4SYNC ARCH](https://github.com/SandmanCircles/4SYNC-ARCH). Genesis moves this to `archive/ARCH_TIPS.md` — it's the product's documentation, not your project's.*
