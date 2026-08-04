#!/usr/bin/env python3
"""
4SYNC ARCH — SessionStart hook. Makes boot non-voluntary.

THE FAILURE THIS EXISTS FOR. Boot is enforced by prose: CLAUDE.md tells a session
to read the manifest and load the `boot:` stack, and nothing checks that it did.
On 2026-08-04 a session was asked point-blank what it had loaded and answered:
"I never ran the boot sequence this session. I have not loaded the KERNEL, STATUS,
MERGE_PLAN, NAMING_CONVENTIONS, ABBA, or DEFECTS." It had oriented on the CLAUDE.md
files and a folder listing. No code noticed, because no code was watching.

What surfaced it was a human probing and the model answering honestly — the only
layer wired at the time, and the one layer that does not scale. This hook is the
layer that does.

MODES (env ARCH_BOOT_MODE):
  announce  : (default) inject a boot RECEIPT — which instance this is, the
              ordered boot list with its measured cost, EOF-sentinel status, and
              the session-debt reading. The session still does the reading; what
              changes is that it can no longer not know it was supposed to.
  inject    : additionally inject the CONTENTS of every boot file. Boot stops
              being a thing a session chooses to do. Costs the boot budget on
              every session in every ARCH instance on the machine, including ones
              a session merely drills into — which is the point, and also the
              reason it is not the default.
  off       : emit nothing.

SCOPE. Resolves the instance from cwd, STRICTLY: outside an ARCH instance this
hook prints nothing and exits 0. That matters because the placement that fixes
the launch-directory bypass is USER level (~/.claude/settings.json), where this
runs for every session on the machine — including sessions in unrelated repos.

KNOWN LIMIT, which must travel with this file: a CLOUD Cowork session gets NO
hooks at all — its tool calls run in an Anthropic container and bytes arrive as a
file transfer, so there is no local event to hook. This cannot fix that surface.
There, the standing spot-check is the only instrument: ask the session to
summarise what it loaded and to look nothing up. That probe is cheap, it is what
caught the failure above, and it is worth running on any surface.

NEVER BLOCKS. Every error is swallowed and the session proceeds. A boot receipt
is not worth failing a session over, and a hook that can break session start is a
hook that gets uninstalled.
"""

import json
import os
import re
import sys
import time

CONFIG_DIR = "config"
DEBT_FILENAME = ".session_debt.tsv"
MANIFEST_DEFAULT = "4SYNC.yaml"
BYTES_PER_TOKEN = 4
LIVE_WITHIN_DEFAULT_MIN = 15


def _instance_root(cwd):
    """Nearest ancestor of cwd holding the loader-stack config dir, or None.

    Strict by design — see the SCOPE note above. At user-level placement a cwd
    fallback would announce a 'boot stack' for every unrelated project on the
    machine."""
    cur = os.path.abspath(cwd or ".")
    while True:
        if os.path.isdir(os.path.join(cur, CONFIG_DIR)):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def _manifest_name():
    return (os.environ.get("ARCH_MANIFEST") or MANIFEST_DEFAULT).strip()


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def parse_boot_list(manifest_text):
    """Ordered `boot:` entries. Line-based on purpose: this hook must run on a
    bare interpreter with no third-party packages, the same constraint meter.py
    and rotate.py carry. Stops at the next top-level key, so `bootstrap:` cannot
    leak its nested items in — a real bug meter.py hit and fixed."""
    m = re.search(r"(?m)^boot:[ \t]*(?:#[^\n]*)?$", manifest_text)
    if not m:
        return []
    out = []
    for line in manifest_text[m.end():].splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith((" ", "\t", "-")):
            break                                   # next top-level key
        item = re.match(r"^\s*-\s*([^#\s]+)", line)
        if item:
            out.append(item.group(1).strip().strip('"\''))
    return out


def parse_bulletin_at_boot(manifest_text):
    """Bulletin file IF the manifest says it is checked at boot, else None.

    MP#17 IN A NEW TOOL. The bulletin is read at boot but is NOT in `boot:` — it
    is declared under `close.bulletin` with `check_at_boot: true`. meter.py
    under-counted boot for exactly this reason and was fixed; a receipt that reads
    only `boot:` reproduces the same undercount, and then the meter and the
    receipt disagree about what boot IS. Two tools measuring one thing must agree
    or neither is trusted."""
    m = re.search(r"(?ms)^\s{2}bulletin:[^\n]*\n(.*?)(?=^\s{0,2}\S|\Z)", manifest_text)
    if not m:
        return None
    block = m.group(1)
    if not re.search(r"(?m)^\s*check_at_boot:\s*true\b", block):
        return None
    f = re.search(r"(?m)^\s*file:\s*[\"']?([^\"'\s#]+)", block)
    return f.group(1).strip() if f else None


def parse_live_within_minutes(manifest_text):
    """session_debt.live_within, e.g. '15m' / '2h'. Default 15 minutes."""
    m = re.search(r"(?m)^\s*live_within:\s*[\"']?(\d+)\s*([mh])?", manifest_text)
    if not m:
        return LIVE_WITHIN_DEFAULT_MIN
    n = int(m.group(1))
    return n * 60 if (m.group(2) or "m") == "h" else n


def read_debt(root, live_within_min):
    """Return (live_rows, stale_rows) — the manifest's TWO readings of one file.

    Same rows, opposite meanings: recent means a session is working RIGHT NOW and
    the shared ledgers are contested; older means somebody never wrapped. A boot
    that reports only one of them is why the other keeps being missed."""
    path = os.path.join(root, DEBT_FILENAME)
    if not os.path.isfile(path):
        return [], []
    live, stale = [], []
    now = time.time()
    for line in _read(path).splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        sid, started, last = parts[0], parts[1], parts[2]
        try:
            age_min = (now - time.mktime(time.strptime(last, "%Y-%m-%dT%H:%M:%S"))) / 60.0
        except (ValueError, OverflowError):
            age_min = None
        row = (sid[:8], last, parts[3] if len(parts) > 3 else "")
        (live if age_min is not None and age_min <= live_within_min else stale).append(row)
    return live, stale


def check_sentinel(path):
    """True/False for a loader YAML's EOF sentinel, None when not applicable.

    A missing sentinel means the read was CLIPPED (stale mount, partial read).
    Reporting it at boot is strictly better than a session discovering it after
    it has already reasoned on half a file."""
    if not path.lower().endswith((".yaml", ".yml")):
        return None
    try:
        tail = _read(path).rstrip().splitlines()
        return bool(tail) and tail[-1].lstrip().startswith("# ═══ EOF")
    except OSError:
        return None


def build_receipt(root, manifest_name, manifest_text, mode):
    """The context block. Returns (text, boot_files)."""
    boot = parse_boot_list(manifest_text)
    # The manifest itself is read to START boot, so it is part of the cost —
    # the same accounting meter.py uses, and the reason the two agree.
    stack = [manifest_name] + boot
    bulletin = parse_bulletin_at_boot(manifest_text)
    live, stale = read_debt(root, parse_live_within_minutes(manifest_text))

    lines = [
        "═══ 4SYNC ARCH — BOOT RECEIPT ═══",
        f"Instance root: {root}",
        f"Manifest: {manifest_name}",
        "",
        "BOOT IS NOT OPTIONAL, and it is not satisfied by having read CLAUDE.md.",
        f"This instance declares {len(stack)} file(s) to load, in this order:",
        "",
    ]

    total = 0
    for i, rel in enumerate(stack, 1):
        p = os.path.join(root, rel)
        if not os.path.isfile(p):
            lines.append(f"  {i}. {rel} — MISSING")
            continue
        n = os.path.getsize(p)
        total += n
        sent = check_sentinel(p)
        flag = "" if sent is not False else "  ⚠ EOF SENTINEL ABSENT — read may be CLIPPED"
        lines.append(f"  {i}. {rel}  ({n:,} B, ~{n // BYTES_PER_TOKEN:,} tok){flag}")
    if bulletin:
        p = os.path.join(root, bulletin)
        n = os.path.getsize(p) if os.path.isfile(p) else 0
        total += n
        lines += ["",
                  f"  + {bulletin} — SCAN the '### [n] … To: … Status:' headers and open only "
                  f"the bodies addressed to you ({n:,} B if read whole; do not).",
                  "    On a header-count mismatch, fall back to a full read AND SAY SO."]
    lines += ["", f"Boot stack: {total:,} B (~{total // BYTES_PER_TOKEN:,} tokens)."]

    if live:
        lines += ["",
                  f"⚠ {len(live)} OTHER SESSION(S) LIVE in this instance — shared ledgers are "
                  "CONTESTED. Re-read any ledger immediately before editing it, make small "
                  "anchored edits, and check your write survived."]
        for sid, last, _ in live:
            lines.append(f"    · {sid} last wrote {last}")
    if stale:
        lines += ["",
                  f"{len(stale)} session(s) holding UNDEPOSITED state (never wrapped):"]
        for sid, last, _ in stale:
            lines.append(f"    · {sid} last wrote {last}")
    if live or stale:
        lines.append("  NOTE: last_activity tracks FILE WRITES only — every git command after "
                     "one is invisible. 'Not live' means probably idle, never gone.")

    if mode == "inject":
        lines += ["", "═══ BOOT CONTENT (injected — you have already loaded these) ═══"]
        for rel in boot:
            p = os.path.join(root, rel)
            if not os.path.isfile(p):
                continue
            try:
                lines += ["", f"───── {rel} ─────", _read(p)]
            except OSError:
                lines.append(f"───── {rel} — UNREADABLE ─────")
        lines += ["", "Every boot file above is now in context. Do not re-read them; "
                      "proceed to the work, and open task documents on demand only."]
    else:
        lines += ["", "Read the files above now, in order, before any other work. "
                      "Then check the bulletin board per the KERNEL directive."]

    return "\n".join(lines), boot


def main():
    try:
        raw = sys.stdin.read()
    except (OSError, ValueError):
        raw = ""
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except ValueError:
        payload = {}

    mode = (os.environ.get("ARCH_BOOT_MODE") or "announce").strip().lower()
    if mode == "off":
        return 0

    root = _instance_root(payload.get("cwd") or os.getcwd())
    if root is None:
        return 0                      # not an ARCH instance — say nothing

    manifest_name = _manifest_name()
    manifest_path = os.path.join(root, manifest_name)
    if not os.path.isfile(manifest_path):
        return 0                      # config/ without a manifest is not our instance

    text, _ = build_receipt(root, manifest_name, _read(manifest_path), mode)
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": text,
    }}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 — a boot receipt never fails a session start
        sys.exit(0)
# ═══ EOF session_start.py ═══
