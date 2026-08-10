#!/usr/bin/env python3
"""
4SYNC ARCH — PreToolUse guard hooks.

Generic, product-shippable guard set. One dispatcher, six guards. Each guard
enforces a *structural* protocol invariant that applies to ANY adopter of the
loader-stack pattern — nothing here is specific to any one product or brand.

  g1  KERNEL write guard   — protect the identity/doctrine file from casual edits
  g2  ABBA format guard    — every OPEN bulletin message must be addressed To: someone
  g3  sandbox git guard    — never commit through a distrusted/mounted filesystem
  g4  STATUS write guard    — keep STATUS a parseable, un-clipped, snapshot (not a journal)
  g5  boring guard         — keep the manifest pure declaration (its own max_bytes; no date creep)
  g6  root fence           — flag a write into a DIFFERENT ARCH instance than the
                             session booted in; silent outside ARCH entirely

NOTE ON PORTABILITY (why this differs from an internal deployment):
  This is the neutral template shipped with 4SYNC ARCH. An internal deployment may
  add its OWN domain-specific guards (brand-leak guards, protected-prompt guards,
  retired-concept guards, etc.). Do NOT bake org-specific business logic into this
  shared file.

  DO NOT extend this file by appending to the GUARDS list in your own checkout.
  Wire this file at USER level — the placement that closes the launch-directory
  bypass, so the guards cover every ARCH instance on the machine including ones a
  session merely drills into. At that scope your instance's copy is NOT what
  executes, and an appended guard never runs. The failure is silent and total:
  settings still say enforce, the log still fills with structural catches, and
  your domain guards are simply gone. (Wire only at project level and the append
  does run — but you lose it on every upgrade of this file, and you keep the
  bypass. Neither placement makes the append a good idea.)

  This paragraph used to recommend that append. The first external adopter carried
  three domain guards with a history of real interceptions; following the old
  advice would have dropped all three with no error and no change in output.

  Extend with the TWO-HOOK PATTERN instead — see "Adding your own guards" in
  README.md. Two PreToolUse hooks with DISJOINT guard sets: this file at user
  level (structural), your own `hooks/guards_<org>.py` at project level (domain),
  both reading ARCH_HOOKS_MODE so one setting governs both and no guard fires
  twice. It is explicit, it survives an upgrade of this file, and the shared
  machine-wide hook never imports or executes adopter code — which auto-discovery
  would have required, and which is an arbitrary-code-execution surface, not a
  style question (deliberate decision, 2026-08-03 — not an oversight).

Wire via .claude/settings.json -> hooks.PreToolUse (see hooks/claude-settings.example.json).

Protocol (Claude Code PreToolUse):
  stdin  : JSON {"tool_name": "...", "tool_input": {...}, ...}
  exit 0 : allow
  exit 2 : BLOCK (stderr is shown to the agent as the reason)
  exit 0 + {"hookSpecificOutput": {... "permissionDecision": "ask" ...}}
         : put the call to the human as a permission prompt

Modes (env ARCH_HOOKS_MODE):
  "warn"    (DEFAULT) : violations are logged, action is ALLOWED. Rollout mode —
                        observe automated runs for false positives before enforcing.
  "enforce"           : violations BLOCK with exit 2, or ASK for guards that
                        classify their finding as a decision rather than a defect.
  "off"               : dispatcher exits 0 immediately.

WHAT THESE GUARDS ARE, AND ARE NOT (read before relying on them):
  They protect against DRIFT AND ACCIDENT — a stray edit, a helpful one-liner,
  an agent taking the path of least resistance. They are NOT a security
  boundary. The hook runs in the same trust domain as the files it guards, so
  anything that can edit those files can edit this one.

  `ask` is ergonomics with teeth, NOT an authorization boundary. Verified
  2026-08-05: `ask` requests a prompt, and where no prompt can be shown the
  ambient permission decision stands — it resolves to ALLOW under acceptEdits,
  bypassPermissions, `--allowedTools`, and a settings.json `permissions.allow`
  entry, which is itself a file an agent can write. Bash coverage likewise
  converts a SILENT bypass into a LOUD one; it does not close it. Both are
  worth having and neither is a lock. Say so in your own docs too.

Configuration (env):
  ARCH_HOOKS_MODE  : warn | enforce | off        (default: warn)
  ARCH_HOOKS_LOG   : path to the warn-mode log    (default: ~/.arch_hooks_warn.log)
  ARCH_CONFIG_DIR  : name of your loader-stack config dir, matched as a path
                    segment (default: "config"). This is what makes g1/g4
                    portable — no hard-coded project paths.
  ARCH_MANIFEST    : basename of the instance manifest g5 guards
                    (default: "4sync.yaml").
  ARCH_STATUS_TOUCHED_MAX : max chars for the STATUS `last_touched` line
                    before g4 flags scope-creep (default: 200)
  ARCH_SANDBOX     : set to "1" when running in a sandboxed/mounted environment
                    whose git view may be stale/clipped; enables g3. (default: off)

Per-guard override env vars (intentional edits, set interactively by the owner):
  CLAUDE_KERNEL_EDIT=1  — permit KERNEL writes  (guard 1)
"""

import inspect
import json
import os
import re
import sys
import time

WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}

# Config dir name, matched as a lowercased path segment (e.g. "config/").
CONFIG_DIR = os.environ.get("ARCH_CONFIG_DIR", "config").strip("/").lower()


# ─────────────────────────────────────────────────────────────────────────────
# Prospective content. Whole-file checks (parseability, EOF sentinel, size) are
# only meaningful against the file that WILL exist after the call — and for
# Edit/MultiEdit the payload carries a *fragment*, not a file. Replay the
# fragment against the copy on disk to recover the resulting content.
#
# Without this, a fragment is judged as if it were a whole file, and BOTH error
# directions show up: an anchored Edit false-positives (a fragment naturally has
# no EOF sentinel, and rarely parses as standalone YAML), while a size check
# false-negatives (it measures the fragment, not the grown file).
#
# Best-effort by design: any doubt about what the result will be returns None,
# and every content check SKIPS on None rather than firing. A guard that cannot
# see the truth must stay quiet, not guess.
# ─────────────────────────────────────────────────────────────────────────────

def _resulting_content(tool, ti):
    """The full file content this call will produce, or None if undeterminable."""
    if tool == "Write":
        c = ti.get("content")
        return c if isinstance(c, str) else None
    if tool not in ("Edit", "MultiEdit"):
        return None                     # e.g. NotebookEdit — cell source, not a file

    edits = ti.get("edits")
    if not isinstance(edits, list):
        edits = [ti]                    # single Edit: the input itself is the edit

    raw = ti.get("file_path") or ti.get("path") or ""
    try:
        with open(raw, encoding="utf-8") as fh:
            content = fh.read()
    except Exception:  # noqa: BLE001 — unreadable/absent: skip, never fire blind
        return None

    for e in edits:
        if not isinstance(e, dict):
            return None
        old = e.get("old_string")
        new = e.get("new_string", "")
        if not isinstance(old, str) or not isinstance(new, str) or not old:
            return None                 # create-file semantics or malformed edit
        if old not in content:
            return None                 # cannot replay faithfully → stay quiet
        content = content.replace(old, new, -1 if e.get("replace_all") else 1)
    return content


# ─────────────────────────────────────────────────────────────────────────────
# Bash targeting (MP#43). A path-scoped guard protects a FILE; testing
# `tool in WRITE_TOOLS` asks about a TOOL. `Bash` reaches this dispatcher and
# every path guard used to return None before looking at the target, so
# `Set-Content config/KERNEL.yaml` wrote what `Edit` could not — verified on
# all five guards, 2026-08-05.
#
# THE BAR IS LOUD, NOT AIRTIGHT. A determined caller can obfuscate a path past
# any regex (base64, a variable, a heredoc) and that is explicitly not the
# target. The target is that routine tooling, a helpful one-liner, and an agent
# taking the path of least resistance all trip the same wire a direct edit does.
#
# CONTENT IS UNKNOWABLE FOR A SHELL CALL. You cannot know what a command
# produces without running it, so `_resulting_content()` returns None and the
# content guards (g2/g4/g5) must NOT assert a verdict — they have noticed a
# risk, not detected a defect. They downgrade to `ask` and say why.
# ─────────────────────────────────────────────────────────────────────────────

BASH_TOOL = "Bash"

# Write intent. Requiring one of these is what keeps `cat`/`grep`/`Get-Content`
# silent: a read names the file too, and an allowlist of read commands would
# miss `cat a > b` in the other direction. Verb-based, so reads stay quiet by
# construction rather than by enumeration.
_BASH_WRITE_VERB = re.compile(
    # `>` but NOT the `>` in `->`, `=>`, `>>=` etc. Prose and code are full of
    # arrows, and a command that merely CONTAINS an arrow is not redirecting.
    r"((?<![-=<>|])>>?|"
    r"\b(set-content|add-content|out-file|write-all(text|lines)|tee|cp|copy|copy-item|"
    r"mv|move|move-item|rm|del|remove-item|truncate|dd|patch|install)\b|"
    r"\b(sed|perl)\b[^|]*\s-i\b|"
    r"""\bopen\s*\([^)]*['"][wa]""" r")",
    re.IGNORECASE)

_BASH_TOKEN = re.compile(r"""['"]([^'"]+)['"]|(\S+)""")

# A VERB that writes to its arguments, as opposed to a redirect that names its
# own target. The distinction decides how widely we harvest — see _bash_write_paths.
_BASH_WRITE_CMD = re.compile(
    r"\b(set-content|add-content|out-file|write-all(text|lines)|tee|cp|copy|copy-item|"
    r"mv|move|move-item|rm|del|remove-item|truncate|dd|patch|install)\b|"
    r"\b(sed|perl)\b[^|]*\s-i\b|"
    r"""\bopen\s*\([^)]*['"][wa]""",
    re.IGNORECASE)

# The token a redirect actually writes to: whatever follows `>`/`>>`, with the
# same `->`/`=>` exclusion the intent regex uses.
_BASH_REDIRECT_TARGET = re.compile(r"(?<![-=<>|])>>?\s*(['\"][^'\"]+['\"]|\S+)")

# Writes here are guaranteed uninteresting, and `2>/dev/null` is the single most
# common redirect in a command that is otherwise purely reading.
_NULL_SINKS = {"/dev/null", "nul", "nul:", "$null", "/dev/stdout", "/dev/stderr"}

_HEREDOC_START = re.compile(r"""<<-?\s*['"]?\w+['"]?""")


def _strip_heredoc_body(cmd):
    """Keep the command line, drop the heredoc body it feeds.

    The body is DATA — a commit message, a file's contents, a SQL script. Paths
    named inside it are being talked about, not written to. The redirection
    target, if any, is on the command line before the `<<`."""
    m = _HEREDOC_START.search(cmd)
    if not m:
        return cmd
    nl = cmd.find("\n", m.end())
    return cmd[:nl] if nl != -1 else cmd[:m.end()]


def _bash_write_paths(cmd):
    """Path-ish tokens from a Bash command that shows write intent.

    Returns raw tokens with separators normalised to '/' but CASE PRESERVED —
    g6 resolves these against the filesystem, and lowercasing is correct only on
    Windows. Empty list when the command shows no write intent at all.

    HEREDOC BODIES ARE NOT TARGETS. `git commit -F - <<'EOF' … EOF` carries a
    commit message; a message that MENTIONS a path is not a write to it. Any
    real redirection target is named on the command line BEFORE the body, so
    dropping the body loses no coverage and removes a whole false-positive
    class. Found the hard way: this guard blocked the very commit that shipped
    it, because the message quoted another instance's path in a courier note."""
    if not cmd:
        return []
    cmd = _strip_heredoc_body(cmd)
    if not _BASH_WRITE_VERB.search(cmd):
        return []

    # A REDIRECT NAMES ITS OWN TARGET; A VERB DOES NOT (MP#50). Intent was
    # detected across the whole command and targets were then harvested across
    # the whole command too, so ONE redirect anywhere promoted every path the
    # command merely READ into a write target. Found in live use: a read-only
    # `md5sum`/`test -f` loop over another instance carrying `2>/dev/null` was
    # refused as a cross-instance write, and the reported target was the
    # unexpanded `../Coworker/$f` — the tell that no real destination was ever
    # resolved. Same family as the `->` false positive (a path MENTIONED is not
    # a path WRITTEN), different mechanism: that one narrowed which characters
    # count as a redirect, this narrows which tokens a redirect can claim.
    #
    # So: when a write VERB is present, keep the broad harvest — `cp a b`,
    # `Set-Content -Path p`, `mv x y/` really do write to their arguments, and
    # narrowing per-verb is a wide surface whose failure mode is a MISSED write.
    # When the only intent is a redirect, the target is the token after the
    # operator and nothing else. Deliberately asymmetric: a false negative here
    # fails QUIET, which is worse than the loud false positive being removed.
    if _BASH_WRITE_CMD.search(cmd):
        toks = [(q or b) for q, b in _BASH_TOKEN.findall(cmd)]
    else:
        toks = _BASH_REDIRECT_TARGET.findall(cmd)

    out = []
    for tok in toks:
        tok = tok.strip().strip("(),;`'\"")
        if not tok or tok.startswith("-"):
            continue
        tok = tok.replace("\\", "/")
        if tok.lower() in _NULL_SINKS:      # nothing guards the bit bucket
            continue
        if "/" in tok or re.search(r"\.\w{1,6}$", tok):
            out.append(tok)
    return out


def _targeted(tool, path, cmd, rx=None, basename=None, rx_bash=None):
    """Does this call target a guarded file?

    Returns (hit, knowable):
      hit       — the matched target string, or None
      knowable  — True when the resulting content can be inspected (a write
                  tool), False when it cannot (Bash). Guards that judge CONTENT
                  must downgrade their consequence when knowable is False.

    `rx_bash` lets a guard match a looser pattern on a shell token than on a
    tool path — a shell call may name the file by basename with the directory
    supplied by an earlier `cd`, which no single token can show."""
    if tool in WRITE_TOOLS:
        if basename is not None:
            return (path if os.path.basename(path) == basename else None), True
        return (path if rx and rx.search(path) else None), True
    if tool == BASH_TOOL:
        for tok in _bash_write_paths(cmd):
            low = tok.lower()
            if basename is not None:
                if os.path.basename(low) == basename:
                    return tok, False
            elif (rx_bash or rx) and (rx_bash or rx).search(low):
                return tok, False
    return None, True


_RX_KERNEL = re.compile(rf"(^|/){re.escape(CONFIG_DIR)}/[^/]*kernel[^/]*\.ya?ml$")
_RX_STATUS = re.compile(rf"(^|/){re.escape(CONFIG_DIR)}/[^/]*status[^/]*\.ya?ml$")
# Looser for shell tokens: `cd config; Set-Content KERNEL.yaml` never shows the
# directory in the token that names the file. Costs a false positive on an
# unrelated *kernel*.yaml — which is an `ask`, i.e. one prompt, not a refusal.
_RX_KERNEL_BASH = re.compile(r"(^|/)[^/]*kernel[^/]*\.ya?ml$")
_RX_STATUS_BASH = re.compile(r"(^|/)[^/]*status[^/]*\.ya?ml$")


# ─────────────────────────────────────────────────────────────────────────────
# Guards. Each returns None to allow, or a reason for the dispatcher to act on.
#
# RETURN CONTRACT (MP#44). A guard returns one of:
#   None                — allow
#   "reason"            — block  (the historical form; an adopter's existing
#                         guard keeps working unchanged and still blocks)
#   ("block", "reason") — block, stated explicitly
#   ("ask",   "reason") — put it to the human as a permission prompt
#
# THE GUARD NAMES THE FINDING; THE DISPATCHER CHOOSES THE CONSEQUENCE. Do not
# call sys.exit() or print JSON from a guard — the seam is what keeps `ask` a
# small change rather than a rewrite, and what keeps `warn` byte-identical.
#
# Which kind: is the guard detecting a DEFECT or a DECISION? A defect blocks —
# no human input improves a clipped write. A decision asks — only a human
# supplies deliberateness about doctrine. A content guard reached through Bash
# has detected NEITHER yet: it cannot see the content, so it asks.
#
# Signature: (tool, path, text, cmd[, full]) where
#   tool = tool name, path = target file (lowercased), text = written content
#          (for Edit/MultiEdit this is the replacement FRAGMENT, not the file),
#   cmd  = Bash command string ("" for non-Bash tools),
#   full = prospective resulting file content, or None if undeterminable.
# The 5th parameter is optional: the dispatcher inspects each guard's arity, so
# an adopter's own 4-arg guard keeps working unchanged.
# ─────────────────────────────────────────────────────────────────────────────

def g1_kernel_write_guard(tool, path, text, cmd):
    """Protect the KERNEL (identity + operating contract) from casual/automated edits.
    The KERNEL is edit-in-place and rare; unintended changes to it silently
    reshape every session's orientation.

    A PURE PATH DECISION — the target settles it, so this guard is fully
    enforceable through Bash as well as the write tools (MP#43).

    ASKS rather than refuses (MP#44). Editing doctrine is a decision, not a
    defect, and a human is the only thing that supplies deliberateness. The
    prompt also fixes the trap the old override left: CLAUDE_KERNEL_EDIT=1 has
    to exist in the environment that LAUNCHED Claude Code, which the desktop
    app, the VSCode extension and scheduled runs offer no way to do — so the
    documented door was a wall for most surfaces, and the one workaround
    (putting it in settings.json) does not authorize once, it disarms the guard
    permanently.

    KNOWN CEILING, verified 2026-08-05 — `ask` is NOT an authorization
    boundary. It requests a prompt; where no prompt can be shown the ambient
    permission decision stands, and it resolves to ALLOW under acceptEdits,
    bypassPermissions, `--allowedTools Write`, and a settings.json
    `permissions.allow` entry. That last one is a file the agent can write.
    Treat this as ergonomics with teeth, not a lock (tasks/MP-044.md).

    Legacy override CLAUDE_KERNEL_EDIT=1 still works and is DEPRECATED; it now
    logs when honoured, so remaining use is visible rather than assumed dead."""
    hit, _ = _targeted(tool, path, cmd, rx=_RX_KERNEL, rx_bash=_RX_KERNEL_BASH)
    if not hit:
        return None
    if os.environ.get("CLAUDE_KERNEL_EDIT") == "1":
        _log(f"[g1_kernel_write_guard] DEPRECATED override honoured "
             f"(CLAUDE_KERNEL_EDIT=1) tool={tool} path={hit}")
        return None
    via = " via a shell command" if tool == BASH_TOOL else ""
    return ("ask",
            f"KERNEL write guard: this call edits identity/doctrine ({hit}){via}. "
            "The KERNEL shapes every session's orientation and is edit-in-place, "
            "rare. Approve only if this change is intended.")


def g2_abba_format_guard(tool, path, text, cmd):
    """Coordination hygiene: every new 'Status: OPEN' bulletin message must carry a
    'To:' field, or nobody owns it. Applies to the cross-agent board (ABBA.md).

    Scoped to the message HEADER line. The board's documented format carries `To:`
    INLINE — `### [n] To: <Agent> · From: <who> · <date> · Status: OPEN` — so a
    block-scoped check anchored to line-start can never be satisfied by a correctly
    formatted message, and at `enforce` makes the board unwritable.

    A CONTENT decision, so it BLOCKS when it can read the content — a malformed
    message is wrong, not unauthorised, and no human input improves it. Reached
    through Bash the content is unknowable, so it downgrades to `ask` and says
    so rather than guessing (MP#43)."""
    hit, knowable = _targeted(tool, path, cmd, basename="abba.md")
    if not hit:
        return None
    if not knowable:
        return ("ask",
                f"ABBA format guard: a shell command is writing the bulletin board "
                f"({hit}) and this guard cannot inspect what it will produce. Every "
                "'Status: OPEN' message needs a 'To:' field or nobody owns it. "
                "Approve if you know the write is well-formed.")
    for line in (text or "").splitlines():
        if re.match(r"^###\s*\[\d+\]", line) and "status: open" in line.lower():
            if not re.search(r"\bto:\s*\S", line, re.IGNORECASE):
                return ("ABBA format guard: a new 'Status: OPEN' message lacks a 'To:' "
                        "field. Address every open bulletin to a named recipient.")
    return None


def g3_sandbox_git_guard(tool, path, text, cmd):
    """In a sandboxed/mounted environment the filesystem view can be stale or clipped;
    a git add/commit there can capture a truncated file and look host-verified.
    Enabled only when ARCH_SANDBOX=1. Commit host-side instead."""
    if os.environ.get("ARCH_SANDBOX") != "1":
        return None
    if tool == "Bash" and re.search(r"\bgit\s+(add|commit|stash|rm)\b", cmd or ""):
        return ("Sandbox git guard: this environment's mounted filesystem may serve "
                "clipped/stale file views — a commit here can land a truncated file. "
                "Commit host-side (native git), not through the sandbox.")
    return None


def g4_status_write_guard(tool, path, text, cmd, full=None):
    """Keep STATUS a healthy snapshot: it must parse as YAML, must not be clipped
    (EOF sentinel present), and its `last_touched` line must stay short — narrative
    belongs in the task ledger/journal, not in STATUS.

    All three checks describe the RESULTING file, so they run against `full`. When
    the result can't be determined the guard stays silent — an anchored Edit is the
    documented close-protocol write mode and must never be blocked on a blind read.

    Through Bash the target is visible but the result is not, so the guard
    surfaces the write as an `ask` instead of asserting a content verdict it has
    no basis for (MP#43). Blocking there would ship this file's first
    false-positive class."""
    hit, knowable = _targeted(tool, path, cmd, rx=_RX_STATUS, rx_bash=_RX_STATUS_BASH)
    if not hit:
        return None
    if not knowable:
        return ("ask",
                f"STATUS write guard: a shell command is writing STATUS ({hit}) and "
                "this guard cannot inspect the result. STATUS must stay parseable, "
                "un-clipped and snapshot-shaped. Approve if you know what the "
                "command produces.")
    if full is None:
        return None
    content = full

    # (a) parseability — best-effort; skip cleanly if PyYAML isn't installed.
    #     WITHOUT PyYAML THIS CHECK IS SKIPPED and (b) + (c) below still block.
    #     There is no fallback PARSER here and there must not be one: a partial
    #     validator presenting as a YAML check is false confidence, which is a
    #     worse failure than a disclosed gap. (g5's regex fallback is a different
    #     thing — it extracts two KNOWN KEYS, which a regex genuinely can do.)
    #     PyYAML is absent from every fresh Python, so this degraded path is the
    #     default adopter experience; it is covered positively by
    #     test_pre_tool_use.py::TestStatusGuardWithoutPyYAML.
    try:
        import yaml  # type: ignore
        try:
            yaml.safe_load(content)
        except Exception as e:  # noqa: BLE001
            return f"STATUS write guard: content does not parse as YAML ({e}). Aborting write."
    except Exception:  # noqa: BLE001
        pass  # yaml not available — rely on the sentinel + length checks below.

    # (b) EOF sentinel — catches a clipped write. A healthy config file ends with
    #     an "# ═══ EOF ... ═══" line; if the last non-empty line lacks it, the
    #     content was likely truncated.
    nonempty = [ln for ln in content.splitlines() if ln.strip()]
    if not nonempty or "EOF" not in nonempty[-1]:
        return ("STATUS write guard: the EOF sentinel is missing from the end of the "
                "content being written — the write looks clipped. Re-read host-side "
                "and retry.")

    # (c) last_touched scope-creep — one short line, not a second journal.
    limit = int(os.environ.get("ARCH_STATUS_TOUCHED_MAX", "200"))
    m = re.search(r'(?m)^\s*last_touched:\s*(.*)$', content)
    if m and len(m.group(1).strip().strip('"').strip("'")) > limit:
        return (f"STATUS write guard: `last_touched` exceeds {limit} chars — that is "
                "session narrative. STATUS is a snapshot; put the narrative in the "
                "task-ledger journal instead.")
    return None


def g5_boring_guard(tool, path, text, cmd, full=None):
    """Keep the instance manifest BORING — pure declaration. The manifest declares
    its OWN policy in `integrity.manifest_rules`; this guard reads that policy from
    the resulting content (so a deliberate policy change in the same write is
    honored) and enforces it: (a) content stays within `max_bytes` — measured on
    the resulting FILE, so an Edit that grows the manifest past its cap is caught,
    not just a whole-file Write; (b) when
    `declaration_only` is set, no journal-style calendar date leaks in — the
    manifest takes dates from the clock at runtime and records history in the task
    ledger, so a literal YYYY-MM-DD is state/narrative creep. Manifest filename via
    ARCH_MANIFEST (default '4sync.yaml').

    Content decision: blocks when it can read the result, asks when reached
    through Bash where it cannot (MP#43)."""
    manifest = os.environ.get("ARCH_MANIFEST", "4sync.yaml").strip().lower()
    hit, knowable = _targeted(tool, path, cmd, basename=manifest)
    if not hit:
        return None
    if not knowable:
        return ("ask",
                f"boring-guard: a shell command is writing the instance manifest "
                f"({hit}) and this guard cannot inspect the result. The manifest is "
                "pure declaration, bounded by its own max_bytes. Approve if the "
                "write keeps it that way.")
    if full is None:
        return None
    content = full

    max_bytes = None
    decl_only = False
    # TWO CONDITIONS THAT MEAN OPPOSITE THINGS, deliberately not sharing a handler
    # (MP#60). "PyYAML is absent" is the default on a fresh Python and degrading to
    # the regex scan is correct — a regex genuinely can extract two known keys. "The
    # content does not parse" is not a reason to degrade; it IS the finding. One
    # `except Exception` covering both meant a manifest write that broke the YAML was
    # allowed through silently: it happened for real, was committed, and was caught by
    # the close discipline's explicit parse rather than by this guard. Same shape as
    # g4 above, which had it right already.
    try:
        import yaml  # type: ignore
    except Exception:  # noqa: BLE001 — PyYAML not installed
        yaml = None

    if yaml is not None:
        try:
            parsed = yaml.safe_load(content)
        except Exception as exc:  # noqa: BLE001 — a genuine parse failure
            mark = getattr(exc, "problem_mark", None)
            where = (" at line %d, column %d" % (mark.line + 1, mark.column + 1)
                     if mark is not None else "")
            problem = (getattr(exc, "problem", None) or str(exc).splitlines()[0]).strip()
            return (f"boring-guard: this write leaves the manifest UNPARSEABLE as YAML"
                    f"{where} — {problem}. The manifest is the first file every session "
                    "reads, so a break here surfaces at the NEXT boot as a protocol that "
                    "will not load, rather than as the edit that caused it. Common cause: "
                    "a ': ' inside a plain multi-line scalar, which YAML reads as a "
                    "mapping key — rewrite it with an em dash, or quote the scalar.")
        if parsed is not None and not isinstance(parsed, dict):
            return (f"boring-guard: this write leaves the manifest parsing as "
                    f"{type(parsed).__name__}, not a mapping of declared keys. "
                    "It is syntactically valid YAML but not a manifest.")
        rules = (((parsed or {}).get("integrity") or {}).get("manifest_rules") or {})
        max_bytes = rules.get("max_bytes")
        decl_only = bool(rules.get("declaration_only"))
    else:
        # Disclosed, not silent: the allow path has no channel to the user, so say it
        # where a later session can find it. max_bytes and declaration_only still bite.
        _log("g5: PyYAML absent — manifest PARSE check skipped; max_bytes and "
             "declaration_only still enforced via the regex scan")
        m = re.search(r'(?m)^\s*max_bytes:\s*(\d+)', content)
        max_bytes = int(m.group(1)) if m else None
        decl_only = bool(re.search(r'(?m)^\s*declaration_only:\s*true\b', content))

    if isinstance(max_bytes, int):
        size = len(content.encode("utf-8"))
        if size > max_bytes:
            return (f"boring-guard: the manifest write is {size} bytes, over its own declared "
                    f"max_bytes ({max_bytes}). The manifest is pure declaration — trim it, or "
                    "raise max_bytes deliberately in the same edit.")

    if decl_only:
        m = re.search(r'\b(20\d\d-[01]\d-[0-3]\d)\b', content)
        if m:
            return (f"boring-guard: the manifest declares declaration_only, but this write "
                    f"contains a calendar date ({m.group(1)}) — journal/narrative creep. "
                    "State belongs in STATUS; history belongs in the task-ledger journal.")
    return None


def _same_tree(a, b):
    """True when a and b are the same path or one contains the other."""
    a = os.path.normcase(os.path.normpath(a))
    b = os.path.normcase(os.path.normpath(b))
    return a == b or a.startswith(b + os.sep) or b.startswith(a + os.sep)


def g6_root_fence(tool, path, text, cmd, full=None, ctx=None):
    """Flag a write into a DIFFERENT ARCH instance than the session is booted in.

    The failure this exists for (2026-07-28): a session booted in one instance
    came one tool call from writing a task row into a *separate live instance's*
    ledger, having learned that ledger's numbering by grep rather than by boot —
    no KERNEL, no invariants, no open-task context. `close.freshness_check`
    could not catch it because that gate assumes a single instance.

    RESOLVE FROM THE TARGET, NOT FROM cwd. This hook is wired at USER level, so
    it loads for every session on the machine. "Compare against instance.root
    from the manifest" would assume there is one instance and the session is in
    it; wired globally there isn't, and a session legitimately working inside
    another instance must not be flagged merely for not being in this one.

    Containment, not equality — this is the part that needed verifying rather
    than assuming. A nested repo can ship its OWN config/ dir (4SYNC-ARCH does,
    since the loader stack is the thing it ships), so a naive equality test
    resolves every write to the nested product repo as a different instance and
    flags the single most common write path there is. If either root contains
    the other they are one tree and one session's business.

    Silent outside ARCH entirely: a target that resolves to no instance is an
    ordinary project, a scratch file, or ~/.claude/plans/… — not our business.
    That is what makes a machine-wide wire safe rather than intrusive.

    QUIET on lobby writes (decided 2026-07-31): when the SESSION resolves to no
    instance, a drill-down from a home folder is a normal working pattern, not a
    mistake. It logs one line naming the target and returns None, so
    it never blocks even under enforce. A guard that makes the common case noisy
    is a guard that gets switched off, and then it is not protecting anything.

    A PURE PATH DECISION, so Bash is covered too (MP#43) — and this was the row
    that mattered most: MP#36 made "the human is the courier" a permanent
    KERNEL invariant, and until now it was enforced against four tools and not
    against `Set-Content`.

    STILL BLOCKS, deliberately, where g1 asks (MP#44 open question, resolved
    here toward block). The fence is permanent doctrine, not a per-call
    judgement, and asking would train the reflex to approve — which is how
    prompt fatigue starts and how a permanent rule quietly becomes a
    negotiable one."""
    if not ctx:
        return None
    if tool in WRITE_TOOLS:
        targets = [ctx.get("raw_path") or ""]
    elif tool == BASH_TOOL:
        targets = _bash_write_paths(cmd)
    else:
        return None

    session_cwd = ctx.get("cwd") or os.getcwd()
    session_root = _instance_root(session_cwd, strict=True)
    for raw in targets:
        if not raw:
            continue
        # Resolve against the SESSION's cwd, not the hook process's. A shell
        # command names files relative to where the session is; the hook runs
        # wherever it was launched. Using os.path.abspath() alone resolved a
        # bare `notes.txt` against the hook's own directory and flagged an
        # ordinary local write as cross-instance — caught by the "unguarded
        # Bash is silent" test, which is exactly the false-positive class this
        # guard must not ship.
        target = raw if os.path.isabs(raw) else os.path.join(session_cwd, raw)
        target_root = _instance_root(os.path.dirname(os.path.abspath(target)), strict=True)
        if target_root is None:
            continue                     # not an ARCH instance — silent
        if session_root is None:
            _log(f"[g6_root_fence] QUIET tool={tool} path={raw} :: write into ARCH "
                 f"instance {os.path.basename(target_root)} from a session not booted "
                 f"in any instance (lobby drill-down — allowed, recorded)")
            continue
        if _same_tree(session_root, target_root):
            continue
        via = " (via a shell command)" if tool == BASH_TOOL else ""
        return (f"CROSS-INSTANCE WRITE{via}: this session is booted in "
                f"{os.path.basename(session_root)} but the target is inside "
                f"{os.path.basename(target_root)} ({raw}). That instance has its own "
                f"ledger, numbering and invariants, none of which this session loaded "
                f"— write from a session booted there, or leave it a message.")
    return None


# ORDER IS LOAD-BEARING (MP#44). The first guard to return a reason decides the
# consequence, so a guard that ASKS must never pre-empt one that would BLOCK.
# g6 runs first because the instance fence is permanent doctrine (MP#36): a
# cross-instance write to another instance's STATUS was reaching g4 first and
# being downgraded from block to ask — the content guard has no verdict there
# anyway, and the fence does. Ordering only became load-bearing when `ask`
# arrived; before that every guard blocked and the order merely picked a message.
GUARDS = [
    g6_root_fence,
    g1_kernel_write_guard,
    g2_abba_format_guard,
    g3_sandbox_git_guard,
    g4_status_write_guard,
    g5_boring_guard,
]


# ─────────────────────────────────────────────────────────────────────────────
# Dispatcher
# ─────────────────────────────────────────────────────────────────────────────

def _extract(payload):
    """Surface fields. NOTE `text` is what the payload literally carries: whole
    content for Write, but only the replacement FRAGMENT for Edit/MultiEdit. Use
    _resulting_content() for anything that reasons about the whole file."""
    tool = payload.get("tool_name", "")
    ti = payload.get("tool_input", {}) or {}
    path = (ti.get("file_path") or ti.get("notebook_path") or ti.get("path") or "").replace("\\", "/").lower()
    text = ti.get("content") or ti.get("new_string") or ti.get("new_source") or ""
    if not text and isinstance(ti.get("edits"), list):
        text = "\n".join(str(e.get("new_string", "")) for e in ti["edits"])
    cmd = ti.get("command") or ""
    return tool, path, text, cmd


def _log(msg):
    logpath = os.environ.get("ARCH_HOOKS_LOG", os.path.expanduser("~/.arch_hooks_warn.log"))
    try:
        with open(logpath, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}\n")
    except Exception:  # noqa: BLE001
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Session-debt recorder (trigger-B). NOT a guard — a side effect that records
# that THIS session owes an explicit close. On any file-write it upserts one row
# (keyed by session_id) into a local, gitignored debt file at the instance root;
# ONLY an explicit close clears that row (see 4SYNC.yaml session_debt / close.debt).
# Effect: a silently-parked, never-wrapped session becomes visible at next boot.
# Fully isolated + best-effort — it can never alter or block the tool call.
# Toggle off with ARCH_DEBT=0; relocate the file with ARCH_DEBT_FILE=<abs path>.
# ─────────────────────────────────────────────────────────────────────────────

DEBT_FILENAME = ".session_debt.tsv"
DEBT_HEADER = ("# 4SYNC session-debt — unwrapped sessions; an explicit close clears its own row.\n"
               "# session_id\tstarted\tlast_activity\tcwd\tstatus")


def _instance_root(cwd, strict=False):
    """Nearest ancestor of cwd that contains the loader-stack config dir.

    Keeps the debt file at ONE known place (the instance root) regardless of which
    subfolder the session's cwd is in — without hard-coding any project path.

    strict=False returns cwd when no instance is found (the original behavior,
    kept for callers that need a path no matter what). strict=True returns None
    instead, which is what the debt recorder needs: this hook is designed to be
    wired at USER level so its guards protect every ARCH instance on the machine
    regardless of where a session launched — and at that scope the cwd fallback
    would drop a .session_debt.tsv into every unrelated project the session ever
    writes to. No instance means no debt to record."""
    start = os.path.abspath(cwd or ".")
    cur = start
    while True:
        if os.path.isdir(os.path.join(cur, CONFIG_DIR)):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None if strict else start
        cur = parent


def _record_debt(payload):
    """Upsert THIS session's unwrapped row on a file-write. Best-effort: swallow
    every error — debt bookkeeping must never affect the tool call it rides on."""
    if os.environ.get("ARCH_DEBT", "1") == "0":
        return
    if payload.get("tool_name", "") not in WRITE_TOOLS:
        return
    sid = (payload.get("session_id")
           or os.environ.get("CLAUDE_CODE_SESSION_ID") or "unknown")
    cwd = payload.get("cwd") or os.getcwd()
    debtfile = os.environ.get("ARCH_DEBT_FILE")
    if not debtfile:
        root = _instance_root(cwd, strict=True)
        if root is None:
            return          # not inside an ARCH instance — record nothing, litter nothing
        debtfile = os.path.join(root, DEBT_FILENAME)
    now = time.strftime("%Y-%m-%dT%H:%M:%S")

    rows = {}
    try:
        with open(debtfile, encoding="utf-8") as fh:
            for ln in fh:
                if not ln.strip() or ln.startswith("#"):
                    continue
                c = ln.rstrip("\n").split("\t")
                if len(c) >= 5:
                    rows[c[0]] = c            # preserve every OTHER session's row
    except FileNotFoundError:
        pass
    except Exception:  # noqa: BLE001 — an unreadable debt file must not break the call
        return

    started = rows[sid][1] if sid in rows else now
    rows[sid] = [sid, started, now, cwd, "unwrapped"]

    tmp = debtfile + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(DEBT_HEADER + "\n")
            for c in rows.values():
                fh.write("\t".join(c[:5]) + "\n")
        os.replace(tmp, debtfile)            # atomic on the same filesystem
    except Exception:  # noqa: BLE001
        try:
            os.remove(tmp)
        except Exception:  # noqa: BLE001
            pass


def _verdict(returned):
    """Normalise a guard's return into (kind, reason).

    Accepts the historical bare string so an adopter's existing guard keeps
    blocking exactly as before — arity dispatch made the 5th/6th parameters
    opt-in, and this does the same for the consequence. An unrecognised kind
    degrades to "block": if a guard says something is wrong and this function
    cannot tell how strongly, the safe reading is the strict one."""
    if not returned:
        return "block", None
    if isinstance(returned, str):
        return "block", returned
    if isinstance(returned, (tuple, list)) and len(returned) == 2:
        kind, reason = returned
        if not isinstance(reason, str) or not reason:
            return "block", None
        return ("ask" if str(kind).lower() == "ask" else "block"), reason
    return "block", str(returned)


def main():
    mode = os.environ.get("ARCH_HOOKS_MODE", "warn").lower()
    if mode == "off":
        sys.exit(0)

    try:
        payload = json.load(sys.stdin)
    except Exception:  # noqa: BLE001 — never block on a malformed payload
        sys.exit(0)

    tool, path, text, cmd = _extract(payload)

    try:
        full = _resulting_content(tool, payload.get("tool_input") or {})
    except Exception:  # noqa: BLE001 — reconstruction is best-effort; None = skip
        full = None

    try:
        _record_debt(payload)
    except Exception:  # noqa: BLE001 — recorder is best-effort, never fatal to the call
        pass

    # Context for guards that must reason about WHERE the write lands rather than
    # what it contains. `path` from _extract is lowercased for content matching,
    # which is fine on Windows and wrong on a case-sensitive filesystem — so the
    # raw target is carried here, unmodified, for anything touching the filesystem.
    ti = payload.get("tool_input") or {}
    ctx = {
        "cwd": payload.get("cwd") or os.getcwd(),
        "raw_path": ti.get("file_path") or ti.get("notebook_path") or ti.get("path") or "",
    }

    for guard in GUARDS:
        try:
            # Arity dispatch, extended one step: a 5th param opts into whole-file
            # checks, a 6th into path context. 4- and 5-arg guards — including any
            # an adopter appended — keep working unchanged.
            nargs = len(inspect.signature(guard).parameters)
            if nargs >= 6:
                reason = guard(tool, path, text, cmd, full, ctx)
            elif nargs == 5:
                reason = guard(tool, path, text, cmd, full)
            else:
                reason = guard(tool, path, text, cmd)
        except Exception:  # noqa: BLE001 — a buggy guard must never break the tool call
            continue
        kind, reason = _verdict(reason)
        if reason:
            if mode == "enforce":
                if kind == "ask":
                    _log(f"[{guard.__name__}] ASK tool={tool} path={path or cmd} :: {reason}")
                    json.dump({"hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "ask",
                        "permissionDecisionReason": reason,
                    }}, sys.stdout)
                    sys.stdout.write("\n")
                    sys.exit(0)
                sys.stderr.write(reason + "\n")
                sys.exit(2)
            else:  # warn — byte-identical to pre-MP#44 behavior, ask included
                _log(f"[{guard.__name__}] tool={tool} path={path} :: {reason}")
                sys.exit(0)

    sys.exit(0)


if __name__ == "__main__":
    main()
