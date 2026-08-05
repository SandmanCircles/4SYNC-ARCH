#!/usr/bin/env python3
"""
actuals.py — measured session cost, from Claude Code's own transcripts.

WHY THIS EXISTS (and how it differs from meter.py)
  meter.py measures the DECLARED boot stack: file sizes on disk, before any
  session runs. It answers "what will boot cost, and which file is responsible."
  That is an ESTIMATE (bytes // 4) and it is per-file.

  This script measures what a session ACTUALLY spent, from the usage records
  Claude Code already writes for every session. It answers "what did it cost."
  It cannot attribute cost to a file — the API sees one undifferentiated
  prefix — so the two tools are complements, not substitutes. Report them side
  by side and LABEL WHICH IS WHICH, always.

THE NUMBER THAT MATTERS, AND WHY IT IS NOT A RATIO
  An earlier spec for this tool proposed cache-hit ratio (cache_read / total
  input) as the headline "proof the loader stack works." It is not. Cache-hit
  ratio measures PREFIX STABILITY, not DEFERRAL. A single-monolith boot is one
  immutable blob and would cache at least as well as a split stack — plausibly
  better, since the stack's prefix contains the task ledger, which changes
  every close. A monolith could post a HIGHER ratio while costing more tokens.
  Publishing the ratio as evidence hands a reviewer the rebuttal.

  So this reports ABSOLUTE tokens, plus the two numbers that actually carry
  the argument:
    - peak turn      : the largest single-turn input (fresh + cache writes +
                       cache reads). Boot is a FIXED overhead paid against
                       this, so boot's share tells you the ceiling on what any
                       boot optimisation can ever buy. Called "peak turn" and
                       not "context window" deliberately: it is what was
                       PRESENTED to the model on the heaviest turn, which is
                       an honest upper bound on resident context but is not
                       proven equal to it. Measured max here is 855,465 cache
                       reads on one Opus 4.8 turn; do not quietly reinterpret
                       that as a window size.
                       (A hypothesis that this figure double-counted multi-pass
                       turns was CHECKED AND KILLED: 0 of 19,557 usage records
                       carry more than one entry in `iterations`, so the sum
                       aggregates nothing. Recorded because the next reader
                       will wonder the same thing.)
    - cache_creation : full-price cache writes. This is the direct measure of
                       prefix churn, and it is the number a boot-ORDER change
                       (stable files first, volatile last) should move. It is
                       the falsifiable one.

  Turn count is the multiplier nobody counts: the API is stateless, so every
  turn resends the whole prefix. A boot stack of N tokens is paid N x turns,
  not N. That is the real case for a lean boot — and also the reason to stop
  optimising it once the numbers say the remaining headroom is small.

PRIVACY — READ-ONLY, USAGE FIELDS ONLY
  Transcripts contain the FULL VERBATIM conversation: every user message,
  every assistant message, and every tool result (which means file contents
  and command output). This script reads the `usage` object on assistant
  records and the `cwd` / `gitBranch` / `version` metadata. It NEVER reads,
  stores, or emits message content, and its output is a few integers per
  session. That is deliberate: it makes the evidence safe to keep and safe to
  commit, and it means the transcripts themselves can expire on schedule
  without losing anything the product needs.

RETENTION — WHY RUNNING THIS EARLY MATTERS
  Claude Code prunes old transcripts (see `.last-cleanup`; tune with
  `cleanupPeriodDays` in ~/.claude/settings.json). Sessions are deleted
  oldest-first, so the baseline you most want — what cost looked like BEFORE
  a project adopted the loader stack — is exactly what expires first. Once
  extracted, these rows are permanent and tiny; the source is disposable.

Usage:
  python scripts/actuals.py                  # this instance (cwd), table
  python scripts/actuals.py --dir /path      # a specific instance root
  python scripts/actuals.py --all            # every project on this machine
  python scripts/actuals.py --json           # machine-readable
  python scripts/actuals.py --log            # append rows to metrics/actuals_series.jsonl
"""

import argparse
import json
import os
import sys
from datetime import datetime

# Cache reads bill at roughly a tenth of fresh input. Used only to report a
# price-WEIGHTED figure alongside the raw one, never instead of it — a raw
# cache_read sum is cumulative across turns and overstates cost badly if you
# read it as tokens-you-paid-for.
CACHE_READ_WEIGHT = 0.1

SERIES_REL = os.path.join("metrics", "actuals_series.jsonl")


def transcript_root():
    """Claude Code's per-project transcript directory."""
    return os.path.join(os.path.expanduser("~"), ".claude", "projects")


def iter_usage(path):
    """Yield (usage_dict, meta_dict) for each assistant record in a transcript.

    Tolerant by construction: a malformed or partial line is skipped rather
    than fatal, because the newest transcript is the SESSION CURRENTLY RUNNING
    and its last line can be half-written at the moment we read it."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if not isinstance(rec, dict):
                    continue
                msg = rec.get("message")
                usage = msg.get("usage") if isinstance(msg, dict) else None
                if not isinstance(usage, dict):
                    continue
                yield usage, {
                    "ts": rec.get("timestamp"),
                    "cwd": rec.get("cwd"),
                    "branch": rec.get("gitBranch"),
                    "version": rec.get("version"),
                    "sidechain": bool(rec.get("isSidechain")),
                }
    except OSError:
        return


def summarise(path):
    """One row per transcript file. Returns None when the file carries no usage
    records at all (an aborted session, or a format this version cannot read —
    both are 'nothing to say', not an error)."""
    fresh = cc = cr = out = turns = 0
    peak = 0
    first_ts = last_ts = None
    meta = {}
    # Prefix size at fixed turn numbers. This is the field a before/after
    # comparison actually needs, and the reason session TOTALS cannot answer it:
    # totals are dominated by how long the session ran, not by what it loaded.
    # A 40-turn session and a 600-turn session differ ~15x regardless of boot
    # cost, so comparing medians of any total is measuring session length.
    # Sampling the same turn index on both sides controls for that.
    # Turn 1 is pre-boot (system + CLAUDE.md, before any file is read); by turn
    # ~10 the boot stack is resident in any regime. Keeping several samples
    # rather than one lets a later analysis pick its own comparison point
    # instead of inheriting a choice made here, before anyone knew what mattered.
    trace_at = (1, 5, 10, 20, 40)
    trace = {}
    for usage, m in iter_usage(path):
        turns += 1
        i = int(usage.get("input_tokens") or 0)
        c = int(usage.get("cache_creation_input_tokens") or 0)
        r = int(usage.get("cache_read_input_tokens") or 0)
        fresh += i
        cc += c
        cr += r
        out += int(usage.get("output_tokens") or 0)
        # Context actually resident on this turn. The MAX across turns is how
        # big the window got — the denominator boot cost is measured against.
        peak = max(peak, i + c + r)
        if turns in trace_at:
            trace[str(turns)] = i + c + r
        ts = m.get("ts")
        if ts:
            first_ts = first_ts or ts
            last_ts = ts
        for k in ("cwd", "branch", "version"):
            if m.get(k) and not meta.get(k):
                meta[k] = m[k]

    if not turns:
        return None

    name = os.path.basename(path)
    return {
        "session": os.path.splitext(name)[0][:8],
        "kind": "agent" if name.startswith("agent-") else "main",
        "cwd": meta.get("cwd"),
        "branch": meta.get("branch"),
        "version": meta.get("version"),
        "first_ts": first_ts,
        "last_ts": last_ts,
        "turns": turns,
        "input_fresh": fresh,
        "cache_creation": cc,
        "cache_read_raw": cr,
        "output": out,
        "peak_turn": peak,
        "prefix_trace": trace,
        # Price-weighted input: fresh + creation at full, reads at ~0.1x. An
        # ESTIMATE of relative cost, not a bill — labelled as such everywhere.
        "input_weighted_est": int(fresh + cc + cr * CACHE_READ_WEIGHT),
    }


def project_dirs(all_projects, instance_root):
    """Which transcript directories to read.

    Matches on the `cwd` recorded INSIDE the transcripts rather than by
    reimplementing Claude Code's directory-name encoding. The encoding is
    undocumented and version-dependent; the cwd field is the ground truth the
    encoding was derived from."""
    root = transcript_root()
    if not os.path.isdir(root):
        return []
    dirs = [os.path.join(root, d) for d in os.listdir(root)
            if os.path.isdir(os.path.join(root, d))]
    if all_projects:
        return sorted(dirs)

    want = os.path.normcase(os.path.abspath(instance_root))
    hits = []
    for d in dirs:
        for f in sorted(os.listdir(d)):
            if not f.endswith(".jsonl"):
                continue
            for _usage, m in iter_usage(os.path.join(d, f)):
                cwd = m.get("cwd")
                if cwd and os.path.normcase(os.path.abspath(cwd)).startswith(want):
                    hits.append(d)
                break
            if d in hits:
                break
    return sorted(set(hits))


def collect(all_projects, instance_root):
    rows = []
    for d in project_dirs(all_projects, instance_root):
        for f in sorted(os.listdir(d)):
            if f.endswith(".jsonl"):
                row = summarise(os.path.join(d, f))
                if row:
                    row["project"] = os.path.basename(d)
                    rows.append(row)
    rows.sort(key=lambda r: (r.get("first_ts") or ""))
    return rows


def render(rows):
    if not rows:
        return ("4SYNC ARCH — session actuals\n\n"
                "  No usable transcripts found.\n"
                "  Looked in: %s\n"
                "  This is not an error: transcripts may be pruned (see\n"
                "  cleanupPeriodDays), or this Claude Code version may write a\n"
                "  format this script does not recognise.\n" % transcript_root())

    out = ["4SYNC ARCH — session actuals (MEASURED, from Claude Code transcripts)",
           "estimates from meter.py are a SEPARATE number — never merge the two",
           ""]
    hdr = ("  session   turns  peak turn   cache write   cache read   output"
           "   weighted~")
    out.append(hdr)
    out.append("  " + "-" * (len(hdr) - 2))
    main = [r for r in rows if r["kind"] == "main"]
    for r in main:
        out.append("  {:<8}{:>7}{:>11,}{:>14,}{:>13,}{:>9,}{:>12,}".format(
            r["session"], r["turns"], r["peak_turn"], r["cache_creation"],
            r["cache_read_raw"], r["output"], r["input_weighted_est"]))

    agents = [r for r in rows if r["kind"] == "agent"]
    tot_turns = sum(r["turns"] for r in rows)
    tot_cc = sum(r["cache_creation"] for r in rows)
    tot_out = sum(r["output"] for r in rows)
    tot_w = sum(r["input_weighted_est"] for r in rows)
    peaks = sorted(r["peak_turn"] for r in main)

    out += ["",
            "  sessions      : {} main, {} subagent".format(len(main), len(agents)),
            "  turns         : {:,}  (every turn resends the whole prefix —".format(tot_turns),
            "                  this is the multiplier on every boot-stack token)",
            "  cache written : {:,}  (full price; the prefix-churn number)".format(tot_cc),
            "  output        : {:,}".format(tot_out),
            "  input ~weighted: {:,}  (ESTIMATE: fresh + writes + reads x {})".format(
                tot_w, CACHE_READ_WEIGHT)]
    if peaks:
        mid = peaks[len(peaks) // 2]
        out += ["  peak turn     : median {:,} · max {:,}  (largest single-turn".format(mid, peaks[-1]),
                "                  input; an upper bound on resident context, not a window size)",
                "",
                "  A boot stack of N tokens is a FIXED overhead against the peak turn,",
                "  paid on every turn. Measure it with meter.py, then read its share",
                "  here as the CEILING on what any boot optimisation can return."]
    return "\n".join(out) + "\n"


def append_series(root, rows):
    """Append one JSON object per session. Append-only and keyed by session id
    so re-running is safe: rows already present are skipped rather than
    duplicated. JSONL, not TSV — the fields will change as the tool learns
    what matters, and fixed columns break exactly then."""
    path = os.path.join(root, SERIES_REL)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    seen = set()
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    seen.add((json.loads(line).get("project"),
                              json.loads(line).get("session")))
                except (ValueError, TypeError):
                    continue
    new = [r for r in rows if (r.get("project"), r.get("session")) not in seen]
    if new:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            if os.path.isfile(path):
                with open(path, encoding="utf-8") as old:
                    fh.write(old.read())
            for r in new:
                fh.write(json.dumps(r, sort_keys=True) + "\n")
        os.replace(tmp, path)
    return len(new), path


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description="Measured session cost from Claude Code transcripts.")
    ap.add_argument("--dir", default=os.getcwd(), help="instance root (default: cwd)")
    ap.add_argument("--all", action="store_true", help="every project on this machine")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    ap.add_argument("--log", action="store_true", help="append to metrics/actuals_series.jsonl")
    args = ap.parse_args()

    root = os.path.abspath(args.dir)
    rows = collect(args.all, root)

    if args.json:
        print(json.dumps({"generated": datetime.now().isoformat(timespec="seconds"),
                          "instance": root, "sessions": rows}, indent=2, sort_keys=True))
    else:
        print(render(rows))

    if args.log and rows:
        n, path = append_series(root, rows)
        print("actuals: appended {} new row(s) to {}".format(n, path))
    sys.exit(0)


if __name__ == "__main__":
    main()
# ═══ EOF actuals.py ═══

