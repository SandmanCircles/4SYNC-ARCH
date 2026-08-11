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
BOOT_HOOK_REL = "hooks/session_start.py"
SETTINGS_REL = os.path.join(".claude", "settings.local.json")
LOG_REL = ".claude/arch_hooks_warn.log"
MATCHER = "Write|Edit|MultiEdit|NotebookEdit|Bash"
NOTE = ("Local machine-specific wiring for the 4SYNC ARCH guard + session-debt hook "
        "(gitignored). Flip ARCH_HOOKS_MODE to enforce after a clean stretch, off to disable.")


DEFAULT_MANIFEST = "4sync.yaml"


def instance_root():
    """This script lives at <root>/scripts/wire_hooks.py, so the root is two up.
    Derived, never guessed — the path being wrong is the whole failure mode here."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def git_root(path):
    """The git repository root containing `path`, or None if there isn't one."""
    try:
        p = subprocess.run(["git", "-C", path, "rev-parse", "--show-toplevel"],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=15)
    except Exception:  # noqa: BLE001 — no git, or it hung; treat as "cannot tell"
        return None
    if p.returncode != 0:
        return None
    out = p.stdout.decode("utf-8", "replace").strip()
    return os.path.abspath(out) if out else None


def settings_root(instance):
    """Where Claude Code will ACTUALLY read settings for a session in this instance.

    THE DEFECT THIS EXISTS FOR (MP#64, field-reported 2026-08-10).
    This script used to write `.claude/settings.local.json` at the INSTANCE root and
    stop there. That is right only when the instance is also the repository root.
    Put ARCH in a subfolder of an existing codebase — `myapp/ops/`, the shape of
    every adoption that adds ARCH to a project rather than starting from an empty
    folder — and the file lands somewhere nothing ever reads. The tool reported
    success, the adopter believed they were wired, and no guard ever fired.

    THE RESOLUTION IS THE GIT REPOSITORY ROOT, NOT THE "PROJECT ROOT". That
    distinction is the whole reason this is mechanical rather than a guess: Claude
    Code resolves settings to the root of the git repository (through worktrees to
    the main checkout), so one file covers sessions started in any subdirectory.
    "Project root" has no definition a script can test; a repository root does, and
    `git rev-parse --show-toplevel` answers it exactly.

    It also classifies the awkward case correctly without a special rule: a nested
    repo that is its own repository — like the product repo inside this silo — is
    its own settings root, so it is left alone rather than being treated as a
    misplaced instance.

    Two documented exceptions keep the file where the instance is: outside a git
    repository, and when the repository root is the user's home directory."""
    instance = os.path.abspath(instance)
    top = git_root(instance)
    if top is None:
        return instance, "not a git repository — settings stay with the instance"
    if os.path.normcase(top) == os.path.normcase(instance):
        return instance, "the instance is the repository root"
    if os.path.normcase(top) == os.path.normcase(os.path.abspath(os.path.expanduser("~"))):
        return instance, ("the repository root is your home directory — Claude Code keeps "
                          "settings where the session starts")
    return top, ("the instance is nested inside this repository, and Claude Code reads "
                 "settings from the repository root")


def find_manifest(instance):
    """The instance manifest's filename, or None if it cannot be identified.

    A manifest is a root-level `*.yaml` that declares `sync_version:` and `boot:`.
    Matched by CONTENT rather than by name because genesis renames it per project
    (`CRM.yaml`), which is precisely the case that needs ARCH_MANIFEST set."""
    try:
        names = sorted(os.listdir(instance))
    except OSError:
        return None
    for name in names:
        if not name.lower().endswith((".yaml", ".yml")):
            continue
        try:
            with open(os.path.join(instance, name), encoding="utf-8") as fh:
                head = fh.read(4096)
        except Exception:  # noqa: BLE001
            continue
        if "sync_version:" in head and "\nboot:" in head:
            return name
    return None


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


def merge(existing, exe, hook_path, root, mode, manifest=None):
    """Merge our wiring into whatever is already there. Preserves every unrelated key,
    every unrelated hook, and any env value the user already set — this script fills
    blanks, it does not overwrite decisions.

    `manifest` sets ARCH_MANIFEST when the instance manifest is not the default name.
    Genesis already merges it into `.claude/settings.json`, so this is not a universal
    gap — it bites when the settings file Claude Code actually loads is not the one
    genesis wrote, which is the same nested layout `settings_root` exists for. Without
    it, g5 stops guarding the manifest, the boot receipt and meter cannot find it, and
    rotate falls back to a default journal cap."""
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
    if manifest and manifest.lower() != DEFAULT_MANIFEST:
        env.setdefault("ARCH_MANIFEST", manifest)
    out["env"] = env
    return out


def boot_hook_block(exe, boot_hook_path):
    """The user-level SessionStart wiring, rendered with REAL paths.

    THIS SCRIPT DOES NOT WRITE IT, and that is a decision rather than a gap. It
    writes PROJECT-level settings, and the sessions that skip boot are precisely
    the ones launched OUTSIDE the instance — which never read project settings at
    all. Wiring the receipt here would put it exactly where it is least needed and
    let an adopter believe they were covered. `~/.claude/settings.json` is also the
    one file whose contents run on every tool call in every project on the machine,
    so it stays a deliberate paste rather than something a script does to you.

    What is removed is the step that actually goes wrong: hand-substituting an
    interpreter path and a hook path into a template of `/full/path/to/python`.
    Both are already known here and the interpreter has been EXECUTED, so they are
    printed filled in. No `matcher` — SessionStart is not a tool event."""
    return json.dumps(
        {"hooks": {"SessionStart": [
            {"hooks": [{"type": "command",
                        "command": '"%s" "%s"' % (exe, boot_hook_path)}]}]}},
        indent=2)


def print_boot_hook_guidance(root, exe):
    """Print the paste-ready SessionStart block, or say why there is none."""
    boot_hook = os.path.join(root, *BOOT_HOOK_REL.split("/")).replace("\\", "/")
    print()
    if not os.path.isfile(boot_hook):
        print("NOTE: no %s in this checkout — nothing to say about the boot receipt."
              % BOOT_HOOK_REL)
        return
    print("── The boot receipt is NOT wired by this script. Paste this yourself: ──")
    print()
    print(boot_hook_block(exe, boot_hook))
    print()
    print("Into ~/.claude/settings.json (USER level), merging into any 'hooks' block")
    print("already there — do not replace the file. Project level would not help: the")
    print("sessions that skip boot are the ones launched OUTSIDE this folder, and those")
    print("never read project settings at all. Paths above are this script's own")
    print("verified interpreter and hook, already filled in.")


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

    sroot, why_root = settings_root(root)
    manifest = find_manifest(root)
    settings_path = os.path.join(sroot, SETTINGS_REL)
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

    merged = merge(existing, exe, hook_path, sroot, args.mode, manifest)
    rendered = json.dumps(merged, indent=2)

    print("instance root : %s" % root)
    print("interpreter   : %s  (verified: runs)" % exe)
    print("hook          : %s" % hook_path)
    print("settings root : %s" % sroot)
    print("                %s" % why_root)
    print("settings      : %s%s" % (settings_path, "" if existing else "   (will be created)"))
    if manifest and manifest.lower() != DEFAULT_MANIFEST:
        print("manifest      : %s  (non-default — wiring ARCH_MANIFEST)" % manifest)
    if os.path.normcase(sroot) != os.path.normcase(root):
        print()
        print("NOTE: this writes OUTSIDE the instance, into the repository root above it.")
        print("      That is where Claude Code reads settings from, so it is the only")
        print("      place the wiring takes effect for sessions in this project.")
    print()
    print(rendered)
    print()

    if not args.write:
        print("DRY RUN — nothing written. Re-run with --write to apply.")
    else:
        os.makedirs(os.path.dirname(settings_path), exist_ok=True)
        with open(settings_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(rendered + "\n")
        print("WROTE %s" % settings_path)
        print("Now reload: open /hooks once, or restart the session. A .claude/ folder that")
        print("did not exist when the session started is not watched mid-session.")
        print("Add .claude/settings.local.json and the warn log to .gitignore — they are local.")

    # Printed in BOTH modes: it is advice, not an effect of writing.
    print_boot_hook_guidance(root, exe)
    return 0


if __name__ == "__main__":
    sys.exit(main())

# ═══ EOF wire_hooks.py ═══
