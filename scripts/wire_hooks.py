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
  python scripts/wire_hooks.py --status        # is THIS machine wired for THIS instance?
  python scripts/wire_hooks.py --python PATH   # wire a different interpreter than this one
  python scripts/wire_hooks.py --mode enforce  # default: warn
"""

import argparse
import json
import os
import re
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


# How much of a candidate file to read when testing whether it is a manifest.
# 64 KB is four times the 16,384-byte cap every instance declares for its own
# manifest, so a legal manifest always sits wholly inside the window; the bound
# exists only to stop an unrelated multi-megabyte root-level YAML being slurped.
MANIFEST_HEAD_BYTES = 65536


def _declares_manifest(path):
    """Does this file declare itself an ARCH instance manifest?

    The test is the two top-level keys `sync_version:` and `boot:`, each anchored
    with (?m)^ — the same anchoring mail.py's peer-detect already uses.

    TWO BLIND SPOTS THIS CLOSES (SYN-090), and both had one symptom: a real
    manifest reading as NO manifest, which is the silence SYN-088 closed one
    layer up and the reason that resolver exists at all.
      - The old test was `"\nboot:" in head`, requiring a PRECEDING newline, so a
        manifest whose FIRST line is `boot:` could never match. Nothing in the
        format requires a key above it.
      - The old window was a fixed 4,096 bytes, which UNDERCUTS the 16,384-byte
        manifest cap it is meant to serve: a prologue well within the declared
        limit could push both marker keys past the read. This repo's own manifest
        runs ~1,500 B of prologue, so the margin was three files' worth of
        comments, not a safe multiple.

    DUPLICATED DELIBERATELY in scripts/rotate.py. Machinery modules never import
    one another — each is copied and wired standalone — so a shared module would
    have to join MACHINERY and could be copied without its dependents. Keep the
    two CODE-identical; only the sibling named above legitimately differs."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            head = fh.read(MANIFEST_HEAD_BYTES)
    except OSError:          # an unreadable candidate is not the manifest
        return False
    return bool(re.search(r"(?m)^sync_version:", head)
                and re.search(r"(?m)^boot:", head))


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
        if _declares_manifest(os.path.join(instance, name)):
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


def _load_settings(path):
    """A settings file's parsed content, None if ABSENT, or "unreadable".

    Absent means the path (or its .claude directory) does not exist. Anything
    else that stops a read — permissions, a directory where a file should be,
    JSON that does not parse — is "unreadable": the file EXISTS and may hold
    the wiring, so treating it as absent would send the user to --write
    against a file the tool could not even open."""
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, NotADirectoryError):
        return None
    except Exception:  # noqa: BLE001 — exists but cannot be read or parsed
        return "unreadable"


def _hooks_malformed(settings):
    """True when a settings dict has a 'hooks' key that is not a JSON object —
    the natural paste error (an entry ARRAY dropped in directly). Merging into
    that shape corrupts it, and iterating it crashes; both callers must treat
    it as fix-by-hand, never as absent."""
    return (isinstance(settings, dict) and "hooks" in settings
            and not isinstance(settings.get("hooks"), dict))


def _hook_command(settings, event, basename):
    """The command string wiring `basename` under `event`, or None. Tolerant of
    every malformed shape — a status report must survive what a hand edit can
    produce."""
    if not isinstance(settings, dict):
        return None
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return None
    entries = hooks.get(event)
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        inner = entry.get("hooks")
        if not isinstance(inner, list):
            continue
        for h in inner:
            if isinstance(h, dict) and basename in h.get("command", ""):
                return h.get("command", "")
    return None


def _command_tokens(cmd):
    """Tokens of a hook command — double-quoted, single-quoted, or unquoted.

    wire_hooks writes double quotes, but hand-written wirings legitimately use
    single quotes (POSIX) or none at all; assuming one format made --status
    report a working single-quoted wiring as a missing interpreter and skip
    the hook-path check entirely for unquoted ones."""
    found = re.findall(r'"([^"]+)"|\'([^\']+)\'|(\S+)', cmd or "")
    return [a or b or c for a, b, c in found]


def _command_exe(cmd):
    """The interpreter path out of a hook command string."""
    toks = _command_tokens(cmd)
    return toks[0] if toks else ""


def _command_hook_path(cmd):
    """The hook-script path out of a hook command string, or ""."""
    toks = _command_tokens(cmd)
    return toks[1] if len(toks) > 1 else ""


def status(root, user_settings=None, check_interpreter=True):
    """Report whether THIS machine is wired for THIS instance. Returns exit code.

    THE GAP THIS CLOSES (SYN-089, field-reported): wiring is machine-local by
    correct design — absolute interpreter and hook paths in gitignored settings
    — so git-syncing an instance to a second machine carries the protocol and
    none of the enforcement layer. The clone boots CLAUDE.md-only, silently:
    no guards, no session-debt recorder, and no SessionStart receipt, which is
    the one channel that would have announced the absence. Until this existed,
    the only detector was a session noticing that nothing had announced itself.

    Exit codes: 0 = the PreToolUse guards are verifiably wired somewhere Claude
    Code will read for a session in this instance; 1 = unwired, or wired with
    problems (missing hook script, dead interpreter); 2 = cannot tell — no
    clean wiring found AND some settings file exists but is not readable JSON.
    An unreadable file never aborts the report: the remaining sources are
    still checked, because a trailing comma in one file must not hide valid
    wiring in another. The boot receipt is reported from every source but
    never moves the exit code — recommended, not required. All three settings
    sources Claude Code reads are checked: the shared project settings.json,
    the project settings.local.json, and the user settings.json.
    `check_interpreter=False` skips the on-disk path checks and interpreter
    execution (the report then says paths were not verified)."""
    root = os.path.abspath(root)
    sroot, why_root = settings_root(root)
    hook_base = os.path.basename(HOOK_REL)
    boot_base = os.path.basename(BOOT_HOOK_REL)
    sources = [
        ("project", os.path.join(sroot, ".claude", "settings.json")),
        ("project-local", os.path.join(sroot, SETTINGS_REL)),
        ("user", user_settings or os.path.join(
            os.path.expanduser("~"), ".claude", "settings.json")),
    ]

    print("instance root : %s" % root)
    print("settings root : %s  (%s)" % (sroot, why_root))

    wired = []        # (label, command)
    receipt_at = []
    unreadable = []

    for label, path in sources:
        blob = _load_settings(path)
        if blob == "unreadable":
            unreadable.append(label)
            print("%-14s: %s — EXISTS BUT IS NOT READABLE (bad JSON, permissions, "
                  "or a directory)" % (label, path))
            continue
        if _hooks_malformed(blob):
            unreadable.append(label)
            print("%-14s: %s — 'hooks' is not a JSON object (a pasted entry array?); "
                  "fix by hand" % (label, path))
            continue
        cmd = _hook_command(blob, "PreToolUse", hook_base)
        if cmd:
            wired.append((label, cmd))
            mode = (blob.get("env") or {}).get("ARCH_HOOKS_MODE")
            print("%-14s: guards wired in %s%s"
                  % (label, path, ("  (ARCH_HOOKS_MODE=%s)" % mode) if mode else ""))
        else:
            print("%-14s: %s — %s"
                  % (label, path, "no guard entry" if blob is not None else "absent"))
        if _hook_command(blob, "SessionStart", boot_base):
            receipt_at.append(label)

    if receipt_at:
        print("receipt       : wired at %s level" % " + ".join(receipt_at))
        if "user" not in receipt_at:
            print("                CAVEAT: a project-level receipt reaches only sessions")
            print("                launched inside this repo. The sessions that skip boot")
            print("                are launched OUTSIDE it and read only user settings —")
            print("                the user-level paste is still recommended.")
    else:
        print("receipt: NOT wired — a session that skips boot announces nothing.")
        print("                `--write` does NOT wire the receipt: run this script and")
        print("                paste its SessionStart block into ~/.claude/settings.json")
        print("                yourself (user level, by hand).")

    # The stranded pre-v1.0.7 shape: settings written where nothing reads them.
    if os.path.normcase(sroot) != os.path.normcase(root):
        stranded = os.path.join(root, SETTINGS_REL)
        if os.path.isfile(stranded):
            print("STRANDED      : %s" % stranded)
            print("                This instance is nested, so Claude Code reads settings")
            print("                at the repository root; this file is never read. Merge")
            print("                anything you set in it upward, then delete it.")

    # Verify what each wired command points at — BOTH halves, PER SOURCE. A
    # wiring whose hook script is gone (the machine-migration aftermath) fails
    # exactly as silently as a dead interpreter. Per-source matters because the
    # shared project settings.json is COMMITTED and necessarily carries one
    # machine's absolute paths: on every other machine those paths fail, and a
    # global problems list turned that expected condition into a false
    # unwired-verdict over a perfectly healthy local wiring.
    problems_by = {}
    if check_interpreter:
        exe_runs = {}
        for label, cmd in wired:
            probs = []
            hook = _command_hook_path(cmd)
            if hook and not os.path.isfile(hook):
                probs.append("missing hook script: %s" % hook)
            exe = _command_exe(cmd)
            if not os.path.isfile(exe):
                probs.append("missing interpreter: %s" % exe)
            else:
                if exe not in exe_runs:
                    exe_runs[exe] = interpreter_works(exe)
                if not exe_runs[exe]:
                    probs.append("interpreter does not execute: %s" % exe)
            if probs:
                problems_by[label] = probs

    print()
    for label, probs in problems_by.items():
        for p in probs:
            print("PROBLEM (%s): %s" % (label, p))
    if "project" in problems_by and len(problems_by) < len(wired):
        print("NOTE          : the shared project settings.json carries absolute paths")
        print("                from whichever machine wrote it — problems there are")
        print("                expected on other machines and do not affect this")
        print("                machine's verdict while a local wiring is healthy.")
    healthy = [label for label, _ in wired if label not in problems_by]
    if healthy:
        note = ""
        if len(healthy) > 1:
            note = " — Claude Code dedupes identical hook commands, so a guard fires once"
        if not check_interpreter:
            note += "  (paths not verified)"
        if unreadable:
            note += "  (could not read: %s)" % " + ".join(unreadable)
        print("VERDICT       : WIRED (%s)%s" % (" + ".join(healthy), note))
        return 0
    if wired:
        print("VERDICT       : wired (%s) but no source verifies on THIS machine —"
              % " + ".join(label for label, _ in wired))
        print("                treat as unwired until fixed. A hook on a broken")
        print("                interpreter or a missing script fails silently, which")
        print("                is worse than no hook.")
        return 1
    if unreadable:
        print("VERDICT       : CANNOT TELL — no wiring found in the readable files,")
        print("                and the unreadable/malformed one(s) above may hold it.")
        print("                Fix that file first; refusing to call this machine")
        print("                unwired.")
        return 2
    print("VERDICT       : UNWIRED on this machine. The protocol still runs —")
    print("                CLAUDE.md + the manifest travel with the folder — but no")
    print("                guards, no boot receipt, no session-debt recorder. Fix:")
    print("                python scripts/wire_hooks.py --write   (once per machine,")
    print("                per instance root), then reload the session.")
    return 1


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
    ap.add_argument("--status", action="store_true",
                    help="report whether THIS machine is wired for THIS instance "
                         "(exit 0 wired, 1 unwired or unverifiable wiring, 2 cannot "
                         "tell — a settings file exists but cannot be read) — "
                         "writes nothing")
    args = ap.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    root = os.path.abspath(args.dir) if args.dir else instance_root()

    if args.status:
        if args.write:
            print("NOTE: --status is read-only; --write is ignored in this run. Wire")
            print("      first, then verify: run --write and --status as two commands.")
            print()
        return status(root)
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

    if _hooks_malformed(existing):
        print("ERROR: %s has a 'hooks' key that is not a JSON object (a pasted entry\n"
              "       array?). Merging into that shape would corrupt it — fix the file\n"
              "       by hand first." % settings_path)
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
