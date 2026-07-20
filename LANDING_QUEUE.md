# LANDING QUEUE — commits awaiting a git-capable session

**What this is:** a machine-readable manifest of files ready to commit, written by sessions that
cannot safely run git (e.g. sandboxes that reach repos through a stale-prone mount — see the
KERNEL `MOUNTED-FILESYSTEM DISTRUST` directive). Any git-capable, host-side session **drains this
queue at wrap**: verify each row's files host-side, stage explicitly by path, commit with the
suggested message, then mark the row LANDED with the SHA.

**Why it exists:** routine "please commit these files" traffic doesn't belong on the agent
bulletin board — that's for genuine nudges needing judgment. A queue row is structured, blind-
executable, and cheap to drain in batch. Adopt this file once you have (a) more than one surface
and (b) at least one surface that can't safely run git.

**Row protocol:** append new rows at the TOP of the Queue section. Never delete rows — flip
`Status:` to LANDED + add the SHA. Move LANDED rows older than ~10 days to the Archive section.

**Row format:**

```
### Q<n> · <date> · From: <agent> · Status: QUEUED|LANDED
Repo: <org/repo @ path>
Files: <explicit paths>
Msg: "<suggested commit message>"
Order: <ordering constraints, or "none">
Gotchas: <safety conditions / verify notes, or "none">
Landed: <SHA + date, filled by the landing session>
```

---

## Queue

### Q1 · <YYYY-MM-DD> · From: <agent> · Status: QUEUED
Repo: <org/repo @ path>
Files: <example — replace or delete>
Msg: "<message>"
Order: none
Gotchas: none
Landed:

---

## Archive

<!-- LANDED rows older than ~10 days move here verbatim. -->

---

*Part of [4SYNC-CMS](https://github.com/SandmanCircles/4SYNC-CMS). Adapt agent names to your setup.*
