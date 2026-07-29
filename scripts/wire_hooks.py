#!/usr/bin/env python3
"""
wire_hooks.py — wire the optional guard hooks into Claude Code, correctly, once.

The hooks ship inert: nothing runs until `.claude/settings.local.json` points at
them. Writing that file by hand means getting three machine-specific paths right,
and the interpreter path is the one people get wrong — on Windows a bare `python`
is often the Microsoft Store stub, which sits on PATH and does not execute scripts.
Wire that in and the hook never fires, silently, while you believe you are guarded.

This script derives both paths from its own location and its own interpreter, then
PROVES the interpreter works by running it before writing anything. It refuses to
write a path it could not execute — an unwired hook you know about beats a wired
hook that does nothing.

It is not an installer. The protocol itself needs no installation: boot, close and
genesis are declared in 4SYNC.yaml and executed by any session. This touches exactly
one file, `.claude/settings.local.json`, and nothing else.

Usage:
  python scripts/wire_hooks.py                 # DRY RUN — print the merged result
  python scripts/wire_hooks.py --write         # merge it into .claude/settings.local.json
  python scripts/wire_hooks.py --python PATH   # wire a different interpreter than this one
  python scripts/wire_hooks.py --mode enforce  # default: warn
"""

import argparse
import json
import os
import subprocess
import sys

HOOK_REL = "hooks/pre_tool_use.py"
SETTINGS_REL = os.path.join(".claude", "settings.local.json")
LOG_REL = ".claude/arch_hooks_warn.log"
MATCHER = "Write|Edit|MultiEdit|NotebookEdit|Bash"
NOTE = ("Local machine-specific wiring for the 4SYNC ARCH guard + session-debt hook "
        "(gitignored). Flip ARCH_HOOKS_MODE to enforce after a clean stretch, off to disable.")


def instance_root():
    """This script lives at <root>/scripts/wire_hooks.py, so the root is two up.
    Derived, never guessed — the path being wrong is the whole failure mode here."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def diagnose(exe):
    """A reason this interpreter is a bad choice, or None. Checked BEFORE running it
    so the Store-stub case gets a useful message instead of a bare failure."""
    if not exe:
        return "no interpreter path"
    if "windowsapps" in exe.replace("\\", "/").lower():
        return ("this is the Microsoft Store Python stub — it sits on PATH but does not "
                "execute scripts. Install Python from python.org or winget, then re-run "
                "this script with that interpreter (or pass --python <full path>).")
    if not os.path.isfile(exe):
        return "not a file on disk"
    return None


def interpreter_works(exe):
    """Actually execute it. Everything else is inference; this is evidence."""
    try:
        r = subprocess.run([exe, "-c", "print('ok')"],
                           capture_output=True, text=True, timeout=20)
        return r.returncode == 0 and r.stdout.strip() == "ok"
    except Exception:  # noqa: BLE001 — any failure to run means unusable, full stop
        return False


def build_hook_entry(exe, hook_path):
    """Quote both paths: spaces in either are the norm on Windows, not an edge case."""
    return {
        "matcher": MATCHER,
        "hooks": [{"type": "command", "command": '"%s" "%s"' % (exe, hook_path)}],
    }


def merge(existing, exe, hook_path, root, mode):
    """Merge our wiring into whatever is already there. Preserves every unrelated key,
    every unrelated hook, and any env value the user already set — this script fills
    blanks, it does not overwrite decisions."""
    out = dict(existing) if isinstance(existing, dict) else {}
    out.setdefault("//", NOTE)

    hooks = dict(out.get("hooks") or {})
    pre = list(hooks.get("PreToolUse") or [])

    # Idempotent: replace our own entry if it is already present, never append a twin.
    ours = build_hook_entry(exe, hook_path)
    basename = os.path.basename(hook_path)
    replaced = False
    for i, entry in enumerate(pre):
        cmds = " ".join(h.get("command", "") for h in (entry.get("hooks") or []))
        if basename in cmds:
            pre[i] = ours
            replaced = True
            break
    if not replaced:
        pre.append(ours)

    hooks["PreToolUse"] = pre
    out["hooks"] = hooks

    env = dict(out.get("env") or {})
    env.setdefault("ARCH_HOOKS_MODE", mode)
    env.setdefault("ARCH_HOOKS_LOG", (root + "/" + LOG_REL).replace("\\", "/"))
    out["env"] = env
    return out


def main():
    ap = argparse.ArgumentParser(description="Wire the 4SYNC ARCH guard hooks into Claude Code.")
    ap.add_argument("--dir", default=None,
                    help="instance root to wire (default: this script's own checkout)")
    ap.add_argument("--write", action="store_true",
                    help="write the merged settings (default: dry run, print only)")
    ap.add_argument("--python", dest="python", default=None,
                    help="interpreter to wire (default: the one running this script)")
    ap.add_argument("--mode", default="warn", choices=["warn", "enforce", "off"],
                    help="initial ARCH_HOOKS_MODE (default: warn — logs, never blocks)")
    args = ap.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    root = os.path.abspath(args.dir) if args.dir else instance_root()
    hook_path = os.path.join(root, *HOOK_REL.split("/")).replace("\\", "/")
    exe = (args.python or sys.executable or "").replace("\\", "/")

    if not os.path.isfile(hook_path):
        print("ERROR: no hook at %s — is this the ARCH folder?" % hook_path)
        return 2

    why = diagnose(exe)
    if why:
        print("ERROR: refusing to wire %s\n       %s" % (exe, why))
        return 2
    if not interpreter_works(exe):
        print("ERROR: refusing to wire %s\n"
              "       it did not execute a trivial script. A hook wired to a broken\n"
              "       interpreter fails silently — you would think you were guarded.\n"
              "       Pass a working one with --python <full path>." % exe)
        return 2

    settings_path = os.path.join(root, SETTINGS_REL)
    existing = {}
    if os.path.isfile(settings_path):
        try:
            with open(settings_path, encoding="utf-8") as fh:
                existing = json.load(fh)
        except Exception as exc:  # noqa: BLE001
            print("ERROR: %s exists but is not readable JSON (%s).\n"
                  "       Fix or move it — refusing to overwrite a file I cannot merge."
                  % (settings_path, exc))
            return 2

    merged = merge(existing, exe, hook_path, root, args.mode)
    rendered = json.dumps(merged, indent=2)

    print("instance root : %s" % root)
    print("interpreter   : %s  (verified: runs)" % exe)
    print("hook          : %s" % hook_path)
    print("settings      : %s%s" % (settings_path, "" if existing else "   (will be created)"))
    print()
    print(rendered)
    print()

    if not args.write:
        print("DRY RUN — nothing written. Re-run with --write to apply.")
        return 0

    os.makedirs(os.path.dirname(settings_path), exist_ok=True)
    with open(settings_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(rendered + "\n")
    print("WROTE %s" % settings_path)
    print("Now reload: open /hooks once, or restart the session. A .claude/ folder that")
    print("did not exist when the session started is not watched mid-session.")
    print("Add .claude/settings.local.json and the warn log to .gitignore — they are local.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# ═══ EOF wire_hooks.py ═══
