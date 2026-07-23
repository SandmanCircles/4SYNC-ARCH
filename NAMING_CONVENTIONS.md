# [PROJECT NAME] — Canonical Naming Conventions

**Version:** [X.Y]  |  **Date:** YYYY-MM-DD  |  **Authority:** [Brand Guide / Master Config / etc.]
**Supersedes:** [Prior version + one-line summary of what changed]

This file is the canonical reference for **vocabulary state** — brand marks, internal taxonomies, retired terms, and the reasoning behind each. It pairs with the merge plan (operational state) and the project config (identity state) as a third pillar of session continuity.

Every external surface — marketing, customer communications, briefs, decks, code, documentation — must use the forms in this file as written. Drift produces brand confusion, undermines vocabulary discipline, and surrenders cognitive scaffolding the project has already built.

---

## The System

| Form | Usage | Rule |
|---|---|---|
| **[Canonical product name]** | Full product name — nav, footer, formal documents | [Casing rule, punctuation rule, etc.] |
| **[Shorthand form]** | Headlines, running prose | [Casing rule, when to drop suffixes, etc.] |
| **[Domain identifier]** | URLs, domain references | [TLD treatment, lowercase rule, etc.] |
| **[Sub-product / vertical naming]** | Domain-specific extensions | [Pattern that scales across sub-products] |
| **[Parent entity name]** | Legal, investor, entity-level only | [Exact form] |
| **[Internal-only name]** | Internal docs, engineering, B2B only | NEVER in public-facing copy or customer materials. |
| **[Retired name]** | RETIRED | Internal/archive only. Do not use externally. |

---

## The Logic Behind the Convention

For each major naming rule, document *why* it exists. Without the reasoning, future sessions (or future you) will challenge the convention the first time it looks suboptimal.

**Why [convention 1]?**
[Reasoning — the specific cognitive, perceptual, or strategic logic. Pattern recognition? Audience signaling? Avoidance of an old failure mode?]

**Why [convention 2]?**
[Reasoning.]

**Why [convention 3]?**
[Reasoning. Include any data or empirical observation that locked the choice — e.g., "v3 of this convention was tested in user interviews and produced X% drop in comprehension."]

---

## Typographic / Display Rules (if applicable)

How the brand mark renders visually in product UI, marketing surfaces, or documentation:

### [Surface type 1 — e.g., nav/header/footer]
[Specific rendering instructions — color spans, weights, spacing, etc.]

### [Surface type 2 — e.g., badges, certificates]
[Rendering instructions.]

---

## Common Errors to Avoid

| ❌ Wrong | ✅ Correct | Why |
|---|---|---|
| `[wrong form 1]` | `[correct form 1]` | [The specific reason — what cognitive or visual failure the wrong form produces] |
| `[wrong form 2]` | `[correct form 2]` | [Reason] |
| `[wrong form 3]` | `[correct form 3]` | [Reason — including version note if it was retired by a specific version, e.g., "RETIRED v1.6 — collapsed two-register system"] |

This table is the most-referenced section in production. Keep it dense and explicit. Every cell is a moment where someone (human or AI) almost wrote the wrong thing — capture the correction so it doesn't have to be made again.

---

## The Brand Pattern (if applicable)

If your project's naming uses a generative pattern (e.g., a prefix or suffix system that produces multiple product names from a single root), document the pattern explicitly:

- **[Pattern element 1]** → [How it generates]
- **[Pattern element 2]** → [How it generates]

State the pattern's underlying logic — what structural distinction it encodes. Is it a brand/domain split? A product/sub-product hierarchy? An audience-targeting device? Naming the underlying logic protects the pattern from drift.

---

## Internal / Engine Nomenclature

If your project has internal taxonomies (scoring axes, classification labels, archetype codes, etc.) that need to stay precise across code, database, documentation, and communication, document them here.

### [Taxonomy 1 — e.g., "Scoring Gates"]

| Code | Label | Scope |
|---|---|---|
| [code 1] | [human-readable label] | [where this code is canonical — universal? domain-specific?] |
| [code 2] | [label] | [scope] |

**Rules:**
- [Rule 1 — e.g., "The code is canonical in databases and code; the label is canonical in customer-facing surfaces."]
- [Rule 2 — e.g., "Never rename the code; the numbering reflects research history and is part of the IP story."]

### [Taxonomy 2 — e.g., "Tier Labels"]

[Same structure.]

### Why these specific letters/numbers/etc.

[The "why" for the taxonomy choice — what alternatives were tested, what made the current form win.]

---

## Retired / Rejected — Do Not Resurrect

Items below were tried, rejected, or superseded. Documenting them prevents future sessions from re-introducing them.

| Retired form | Date retired | Why |
|---|---|---|
| `[retired form 1]` | YYYY-MM-DD | [The specific failure mode that triggered retirement. Be concrete — "X user-tested poorly" beats "X felt wrong"] |
| `[retired form 2]` | YYYY-MM-DD | [Reason] |

---

## Migration Scope Warnings (if applicable)

If a rename or vocabulary change is narrowly scoped — applies only to one namespace and not others — write the explicit scope here. This is the most common source of accidental damage when an AI agent encounters a rename.

### ⚠ THE `[NAME]` RENAME IS NARROWLY SCOPED

The string `[name]` appears in **N distinct namespaces** in the codebase. The rename touches **exactly one** of them.

| Namespace | What it identifies | Example values | Status under this rename |
|---|---|---|---|
| **[Namespace 1]** (the only one being renamed) | [description] | [examples] | **RENAMED → `[new form]`.** |
| **[Namespace 2]** | [description] | [examples] | **UNCHANGED FOREVER.** Not touched by this rename. |
| **[Namespace 3]** | [description] | [examples] | **UNCHANGED.** |

**The collision that motivated the rename was [conceptual / syntactic / both].** Be explicit about which.

---

## What Is NOT a Naming or Design Basis

If your project has frameworks, metaphors, or references that appear in R&D files but should never leak into product documents or external communications, list them here as explicit anti-references.

**[Framework/reference 1]** — [Why it's not a product rationale or naming basis. What's permitted to do with it.]

**[Framework/reference 2]** — [Same.]

---

## Version History

*Version [current] — [What changed this version. Always include the trigger — what made this change necessary. Without the trigger, future sessions can't tell whether to honor or revisit.]*
*Version [prior] — [What changed.]*
*Version [earlier] — [What changed.]*

---

## How to use this file

**On day one:** delete most of the example structure above. Lock 3–5 truly canonical conventions (your product name in its three or four most common renderings, the parent entity name, any internal-only names that must not leak externally). Add the Common Errors table even if it starts with two rows. Version it `1.0`.

**Per session:** Claude (or any agent) loads this file at session start (your CLAUDE.md should be wired up to do so). Before generating any external-facing copy, the agent should cross-check forms against this file.

**When a new convention locks:** bump the version, add a version-history line that names the trigger, and update the relevant tables. Don't silently change a rule — every rule change should be traceable to a specific moment.

**When something is retired:** move it to the "Retired / Rejected" table with the date and reason. Don't delete — the audit trail prevents resurrection.

**If this file ever conflicts with the config loader stack:** for *vocabulary / naming*, **this file is canonical** — the `KERNEL` carries only a quickref pointer to it, so fix the KERNEL pointer to match. For *identity / state* facts (entity, current status), the `KERNEL`/`STATUS` win and you update this file to match.

---

*Part of [4SYNC ARCH](https://github.com/SandmanCircles/4SYNC-ARCH). Adapt freely.*
