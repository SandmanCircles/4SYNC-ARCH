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

# Per-file boot growth since the last logged close. BOTH gates must trip, so a
# 40-byte file that doubled is not news and a large file drifting up by a line
# is not either. Env overrides keep the knob discoverable next to the others.
SERIES_REL = os.path.join("metrics", "roc_series.jsonl")
GROWTH_MIN_PCT = 10.0
GROWTH_MIN_BYTES = 1024
GROWTH_PCT_ENV = "ARCH_BOOT_GROWTH_PCT"
GROWTH_NAMED_MAX = 3


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


def _block_under(text, key):
    """The body of a nested `key:` block at ANY indent, or None if absent.

    INDENT-AGNOSTIC ON PURPOSE (MP#73). This replaced `^\\s{2}<key>:` — an anchor on
    EXACTLY two spaces, which is simply what this project's manifests happen to use.
    Reindent a manifest to four spaces (any YAML formatter, most editor defaults) and
    every lookup built on that anchor silently returned its built-in default: a
    declared `journal.max_bytes` of 16384 came back as 12288, and a bulletin declared
    `check_at_boot: true` came back as "no bulletin", with no error anywhere.

    That is the failure class this project has already named twice — a
    parsed-but-not-honored manifest key is "worse than an absent one, because it is
    trusted". And it is not a rare path: PyYAML is absent from every fresh Python,
    which this codebase calls "the modal fresh install", so for most adopters these
    regexes ARE the parser. In THIS file there is no PyYAML path at all, so it is
    the only parser for everybody.

    Requires at least one space of indent, preserving the original intent that these
    keys are nested (under `close:`) rather than top-level.

    DUPLICATED DELIBERATELY in scripts/meter.py and scripts/rotate.py. Machinery
    modules never import one another — each is copied and wired standalone — so a
    shared module would have to join MACHINERY and could be copied without its
    dependents. Keep the three byte-identical."""
    ind = None
    body = []
    for line in text.splitlines():
        if ind is None:
            m = re.match(r"^([ \t]+)" + re.escape(key) + r":", line)
            if m:
                ind = len(m.group(1).expandtabs(8))
            continue
        if not line.strip():
            body.append(line)
            continue
        depth = len(re.match(r"^[ \t]*", line).group(0).expandtabs(8))
        if depth <= ind:
            break
        body.append(line)
    return "\n".join(body) if ind is not None else None


def parse_bulletin_at_boot(manifest_text):
    """Bulletin file IF the manifest says it is checked at boot, else None.

    MP#17 IN A NEW TOOL. The bulletin is read at boot but is NOT in `boot:` — it
    is declared under `close.bulletin` with `check_at_boot: true`. meter.py
    under-counted boot for exactly this reason and was fixed; a receipt that reads
    only `boot:` reproduces the same undercount, and then the meter and the
    receipt disagree about what boot IS. Two tools measuring one thing must agree
    or neither is trusted."""
    block = _block_under(manifest_text, "bulletin")
    if block is None:
        return None
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


def read_last_series(root):
    """Per-file boot bytes from the last logged close, or None.

    `meter.py --log` appends one row per close to metrics/roc_series.jsonl,
    carrying a per-file `files` map. NOTHING HAS EVER READ IT AT BOOT — the
    series was built to answer "which file grew?" after the fact, and the
    session best placed to act on that answer is the one that just PAID for the
    growth, not the one trying to leave.

    Returns ({relpath: bytes}, ts) or None. Silent on every failure: an adopter
    who has never run the meter has no series, and a check an adopter cannot
    satisfy is a false alarm they learn to ignore."""
    path = os.path.join(root, SERIES_REL)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            last = ""
            for line in fh:                     # the series is append-only and small
                if line.strip():
                    last = line
        if not last:
            return None
        row = json.loads(last)
        files = row.get("files")
        if not isinstance(files, dict) or not files:
            return None
        clean = {k: v for k, v in files.items() if isinstance(v, int)}
        return (clean, row.get("ts")) if clean else None
    except (OSError, ValueError, TypeError):
        return None


def boot_growth_lines(root, sizes):
    """Report per-file growth since the last logged close. [] when nothing to say.

    THE FAILURE THIS EXISTS TO CATCH, observed in this instance: config/STATUS.yaml
    was trimmed to 16,420 B and stood at 28,174 B five days later, +72%. A
    close-time size report fired at EVERY ONE of those closes, named the right
    file, and prescribed the right fix — and it scrolled past, because a warning
    delivered to a session that is trying to finish competes with finishing and
    loses. The same sentence at boot reaches a session with the whole session
    ahead of it. (MP#62.)

    Reports; never blocks. `sizes` is {relpath: current_bytes} for the stack."""
    prev = read_last_series(root)
    if not prev:
        return []
    before, ts = prev
    try:
        pct_min = float(os.environ.get(GROWTH_PCT_ENV) or GROWTH_MIN_PCT)
    except ValueError:                          # a junk env value must not lose the check
        pct_min = GROWTH_MIN_PCT

    grown = []
    for rel, now in sizes.items():
        was = before.get(rel)
        if not isinstance(was, int) or was <= 0 or now <= was:
            continue
        delta = now - was
        pct = delta * 100.0 / was
        if delta >= GROWTH_MIN_BYTES and pct >= pct_min:
            grown.append((delta, pct, rel, was, now))
    if not grown:
        return []
    grown.sort(reverse=True)

    total_was = sum(v for k, v in before.items() if k in sizes)
    total_now = sum(sizes[k] for k in sizes if k in before)
    stack = ""
    if total_was > 0 and total_now != total_was:
        d = total_now - total_was
        stack = (" Stack %s B (~%s tok) overall."
                 % (format(d, "+,"), format(d // BYTES_PER_TOKEN, "+,")))

    out = ["", "⚠ BOOT FILES GREW since the last logged close%s:%s"
           % (" (%s)" % ts if ts else "", stack)]
    for delta, pct, rel, was, now in grown[:GROWTH_NAMED_MAX]:
        out.append("    · %s  %s → %s B  (+%s B, +%.0f%%)"
                   % (rel, format(was, ","), format(now, ","),
                      format(delta, ","), pct))
    if len(grown) > GROWTH_NAMED_MAX:
        out.append("    · … and %d more" % (len(grown) - GROWTH_NAMED_MAX))
    out.append("  A boot file that grows every session is carrying something that "
               "belongs in an ON-DEMAND file. You pay this on arrival, every session, "
               "forever — cutting it is cheapest RIGHT NOW, before the work starts.")
    return out


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
    sizes = {}
    for i, rel in enumerate(stack, 1):
        p = os.path.join(root, rel)
        if not os.path.isfile(p):
            lines.append(f"  {i}. {rel} — MISSING")
            continue
        n = os.path.getsize(p)
        total += n
        sizes[rel] = n
        sent = check_sentinel(p)
        flag = "" if sent is not False else "  ⚠ EOF SENTINEL ABSENT — read may be CLIPPED"
        lines.append(f"  {i}. {rel}  ({n:,} B, ~{n // BYTES_PER_TOKEN:,} tok){flag}")
    if bulletin:
        p = os.path.join(root, bulletin)
        n = os.path.getsize(p) if os.path.isfile(p) else 0
        total += n
        # DELIBERATELY NOT in `sizes`: the bulletin is SCANNED, not read, and
        # meter.py logs it at its scan estimate while this figure is the whole
        # file. Comparing the two reports a ~1100% jump on a file that did not
        # change. Caught on the growth check's first live run — a dry run could
        # not have shown it, because the two numbers only meet in the series.
        lines += ["",
                  f"  + {bulletin} — SCAN the '### [n] … To: … Status:' headers and open only "
                  f"the bodies addressed to you ({n:,} B if read whole; do not).",
                  "    On a header-count mismatch, fall back to a full read AND SAY SO."]
    lines += ["", f"Boot stack: {total:,} B (~{total // BYTES_PER_TOKEN:,} tokens)."]

    try:
        lines += boot_growth_lines(root, sizes)
    except Exception:                           # noqa: BLE001 — never fail a boot over a report
        pass

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
    # Windows consoles default to cp1252; the em dash and tick/cross glyphs in the
    # output would arrive mangled or raise on print. Reconfigure early. Matches the
    # form already in meter.py, actuals.py, arch_build.py and wire_hooks.py — this
    # was the house pattern in four scripts and absent from five (MP#84).
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
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
        # DELIBERATELY UNLOGGED, unlike pre_tool_use's guard loop (MP#72). This
        # hook only PRINTS; a crash costs a receipt, never an unenforced rule —
        # and there is no session left to tell. The guard loop is the one place
        # where swallowing an exception means a check did not happen.
        sys.exit(0)
# ═══ EOF session_start.py ═══
