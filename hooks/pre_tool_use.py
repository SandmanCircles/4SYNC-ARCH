#!/usr/bin/env python3
"""
4SYNC Context Management System (4SYNC CMS) — PreToolUse guard hooks.

Generic, product-shippable guard set. One dispatcher, four guards. Each guard
enforces a *structural* CMS invariant that applies to ANY adopter of the
loader-stack pattern — nothing here is specific to any one product or brand.

  g1  KERNEL write guard   — protect the identity/doctrine file from casual edits
  g2  ABBA format guard    — every OPEN bulletin message must be addressed To: someone
  g3  sandbox git guard    — never commit through a distrusted/mounted filesystem
  g4  STATUS write guard    — keep STATUS a parseable, un-clipped, snapshot (not a journal)

NOTE ON PORTABILITY (why this differs from an internal deployment):
  This is the neutral template shipped with 4SYNC CMS. An internal deployment may
  add its OWN domain-specific guards (brand-leak guards, protected-prompt guards,
  retired-concept guards, etc.) by appending functions to the GUARDS list below.
  Keep those in a separate, clearly-labeled module in your own checkout — do NOT
  bake org-specific business logic into this shared file.

Wire via .claude/settings.json -> hooks.PreToolUse (see hooks/claude-settings.example.json).

Protocol (Claude Code PreToolUse):
  stdin  : JSON {"tool_name": "...", "tool_input": {...}, ...}
  exit 0 : allow
  exit 2 : BLOCK (stderr is shown to the agent as the reason)

Modes (env SYNC_HOOKS_MODE):
  "warn"    (DEFAULT) : violations are logged, action is ALLOWED. Rollout mode —
                        observe automated runs for false positives before enforcing.
  "enforce"           : violations BLOCK with exit 2.
  "off"               : dispatcher exits 0 immediately.

Configuration (env):
  SYNC_HOOKS_MODE  : warn | enforce | off        (default: warn)
  SYNC_HOOKS_LOG   : path to the warn-mode log    (default: ~/.sync_hooks_warn.log)
  SYNC_CONFIG_DIR  : name of your loader-stack config dir, matched as a path
                    segment (default: "config"). This is what makes g1/g4
                    portable — no hard-coded project paths.
  SYNC_STATUS_TOUCHED_MAX : max chars for the STATUS `last_touched` line
                    before g4 flags scope-creep (default: 200)
  SYNC_SANDBOX     : set to "1" when running in a sandboxed/mounted environment
                    whose git view may be stale/clipped; enables g3. (default: off)

Per-guard override env vars (intentional edits, set interactively by the owner):
  CLAUDE_KERNEL_EDIT=1  — permit KERNEL writes  (guard 1)
"""

import json
import os
import re
import sys
import time

WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}

# Config dir name, matched as a lowercased path segment (e.g. "config/").
CONFIG_DIR = os.environ.get("SYNC_CONFIG_DIR", "config").strip("/").lower()


# ─────────────────────────────────────────────────────────────────────────────
# Guards. Each returns a reason string when it wants to block, else None.
# Signature: (tool, path, text, cmd) where
#   tool = tool name, path = target file (lowercased), text = written content,
#   cmd  = Bash command string ("" for non-Bash tools).
# ─────────────────────────────────────────────────────────────────────────────

def g1_kernel_write_guard(tool, path, text, cmd):
    """Protect the KERNEL (identity + operating contract) from casual/automated edits.
    The KERNEL is edit-in-place and rare; unintended changes to it silently
    reshape every session's orientation. Owner override: CLAUDE_KERNEL_EDIT=1."""
    if tool in WRITE_TOOLS and re.search(rf"(^|/){re.escape(CONFIG_DIR)}/[^/]*kernel[^/]*\.ya?ml$", path):
        if os.environ.get("CLAUDE_KERNEL_EDIT") != "1":
            return ("KERNEL write guard: writes to the KERNEL config are blocked unless "
                    "CLAUDE_KERNEL_EDIT=1 is set. The KERNEL is identity/doctrine — "
                    "edit it deliberately, not as a side effect.")
    return None


def g2_abba_format_guard(tool, path, text, cmd):
    """Coordination hygiene: every new 'Status: OPEN' bulletin message must carry a
    'To:' field, or nobody owns it. Applies to the cross-agent board (ABBA.md)."""
    if tool in WRITE_TOOLS and os.path.basename(path) == "abba.md":
        for block in re.split(r"\n\s*\n", text or ""):
            if "status: open" in block.lower() and not re.search(r"(^|\n)\s*to:\s*\S", block, re.IGNORECASE):
                return ("ABBA format guard: a new 'Status: OPEN' message lacks a 'To:' "
                        "field. Address every open bulletin to a named recipient.")
    return None


def g3_sandbox_git_guard(tool, path, text, cmd):
    """In a sandboxed/mounted environment the filesystem view can be stale or clipped;
    a git add/commit there can capture a truncated file and look host-verified.
    Enabled only when SYNC_SANDBOX=1. Commit host-side instead."""
    if os.environ.get("SYNC_SANDBOX") != "1":
        return None
    if tool == "Bash" and re.search(r"\bgit\s+(add|commit|stash|rm)\b", cmd or ""):
        return ("Sandbox git guard: this environment's mounted filesystem may serve "
                "clipped/stale file views — a commit here can land a truncated file. "
                "Commit host-side (native git), not through the sandbox.")
    return None


def g4_status_write_guard(tool, path, text, cmd):
    """Keep STATUS a healthy snapshot: it must parse as YAML, must not be clipped
    (EOF sentinel present), and its `last_touched` line must stay short — narrative
    belongs in the task ledger/journal, not in STATUS."""
    if tool not in WRITE_TOOLS or not re.search(rf"(^|/){re.escape(CONFIG_DIR)}/[^/]*status[^/]*\.ya?ml$", path):
        return None
    content = text or ""

    # (a) parseability — best-effort; skip cleanly if PyYAML isn't installed.
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
    limit = int(os.environ.get("SYNC_STATUS_TOUCHED_MAX", "200"))
    m = re.search(r'(?m)^\s*last_touched:\s*(.*)$', content)
    if m and len(m.group(1).strip().strip('"').strip("'")) > limit:
        return (f"STATUS write guard: `last_touched` exceeds {limit} chars — that is "
                "session narrative. STATUS is a snapshot; put the narrative in the "
                "task-ledger journal instead.")
    return None


GUARDS = [
    g1_kernel_write_guard,
    g2_abba_format_guard,
    g3_sandbox_git_guard,
    g4_status_write_guard,
]


# ─────────────────────────────────────────────────────────────────────────────
# Dispatcher
# ─────────────────────────────────────────────────────────────────────────────

def _extract(payload):
    tool = payload.get("tool_name", "")
    ti = payload.get("tool_input", {}) or {}
    path = (ti.get("file_path") or ti.get("notebook_path") or ti.get("path") or "").replace("\\", "/").lower()
    text = ti.get("content") or ti.get("new_string") or ti.get("new_source") or ""
    if not text and isinstance(ti.get("edits"), list):
        text = "\n".join(str(e.get("new_string", "")) for e in ti["edits"])
    cmd = ti.get("command") or ""
    return tool, path, text, cmd


def _log(msg):
    logpath = os.environ.get("SYNC_HOOKS_LOG", os.path.expanduser("~/.sync_hooks_warn.log"))
    try:
        with open(logpath, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}\n")
    except Exception:  # noqa: BLE001
        pass


def main():
    mode = os.environ.get("SYNC_HOOKS_MODE", "warn").lower()
    if mode == "off":
        sys.exit(0)

    try:
        payload = json.load(sys.stdin)
    except Exception:  # noqa: BLE001 — never block on a malformed payload
        sys.exit(0)

    tool, path, text, cmd = _extract(payload)

    for guard in GUARDS:
        try:
            reason = guard(tool, path, text, cmd)
        except Exception:  # noqa: BLE001 — a buggy guard must never break the tool call
            continue
        if reason:
            if mode == "enforce":
                sys.stderr.write(reason + "\n")
                sys.exit(2)
            else:  # warn
                _log(f"[{guard.__name__}] tool={tool} path={path} :: {reason}")
                sys.exit(0)

    sys.exit(0)


if __name__ == "__main__":
    main()