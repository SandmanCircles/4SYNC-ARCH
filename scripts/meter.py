#!/usr/bin/env python3
"""
4SYNC ARCH — boot-cost meter (read-only).

Measures the token cost of a 4SYNC instance's boot load and reports how much the
loader-stack design keeps OUT of every session's context window. The whole point
of the protocol is that a session boots on a small, ordered stack and DEFERS the
bulk (deep reference canon, frozen archives) until — and only if — it is needed.
This meter puts a number on that deferral.

What it does:
  - Reads 4SYNC.yaml (the instance manifest) to learn the load lists — it does NOT
    hardcode filenames. The `boot:` list is what every session pays up front; the
    `on_demand:` + `never_load_whole:` lists are what the design keeps deferred.
    An instance that renamed its manifest points BOTH this meter and the g5
    boring-guard at the new name with one variable: ARCH_MANIFEST.
  - Sizes each referenced file on disk and converts bytes → an ESTIMATE of tokens.
  - Prints a boot-stack total, a deferred total, and a savings line: how many
    tokens the loader stack keeps out of the window, as an absolute count and as a
    share of total known canon.

Token counts are ESTIMATES, not tokenizer output — see estimate_tokens(). The
number is a design-pressure gauge, not a billing figure.

Read-only: this module never opens a file for writing and never mutates the repo.
It only stat()s and reads. Safe to run against any instance at any time.

Usage:
  python scripts/meter.py --dir /path/to/project        # text report
  python scripts/meter.py --dir /path/to/project --json  # machine-readable JSON
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime

MANIFEST_DEFAULT = "4SYNC.yaml"


def resolve_manifest(env=None):
    """Basename of the instance manifest, honoring the ARCH_MANIFEST env knob.

    Same variable the g5 boring-guard reads (hooks/pre_tool_use.py), so an
    instance that renames its manifest configures both tools once.

    One deliberate difference from the hook: the hook lowercases this, because it
    only ever COMPARES it against the basename of a path being written. The meter
    OPENS the file, so the case must survive — on a case-sensitive filesystem
    '4sync.yaml' does not open '4SYNC.yaml'.

    An unset, empty, or whitespace-only value falls back to the default.
    """
    env = os.environ if env is None else env
    return (env.get("ARCH_MANIFEST") or "").strip() or MANIFEST_DEFAULT


# The three load lists the manifest declares. Order here is display order.
LIST_KEYS = ("boot", "on_demand", "never_load_whole")

# Rough English heuristic: ~4 bytes of UTF-8 text per token. This is the standard
# back-of-envelope figure, NOT a real tokenizer count — a true count depends on
# the model's BPE vocabulary. Kept deliberately simple and dependency-free.
BYTES_PER_TOKEN = 4

# Skipped when summing a directory entry. Same set rotate.py uses — a deferred
# folder's weight is its documents, never a vendored dependency tree.
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", "dist", "build"}


# ─────────────────────────────────────────────────────────────────────────────
# Pure functions — no disk, no globals mutated. The test suite hits these directly.
# ─────────────────────────────────────────────────────────────────────────────

def estimate_tokens(nbytes):
    """Estimate token count from a byte count using the ~4-bytes-per-token English
    heuristic. This is an ESTIMATE, not a tokenizer measurement — a real count
    depends on the model's BPE vocabulary and the actual text. Monotonic in nbytes
    and returns 0 for 0 (or negative) bytes."""
    if nbytes <= 0:
        return 0
    return nbytes // BYTES_PER_TOKEN


def _strip_inline_comment(value):
    """Drop a trailing ' # ...' comment and surrounding quotes/space from a scalar.
    Filenames never contain '#', so splitting on it is safe for load-list items."""
    value = value.split("#", 1)[0].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
    return value.strip()


def _parse_load_lists_lines(manifest_text):
    """Dependency-free fallback parser: walk the manifest line by line, collecting
    the '- item' rows under each of the top-level LIST_KEYS blocks. Robust to inline
    comments, blank lines, and comment-only lines. Stops a block at the next
    top-level key (a line that starts in column 0 and contains a ':')."""
    lists = {k: [] for k in LIST_KEYS}
    current = None
    top_key = re.compile(r"^([A-Za-z_][\w-]*):")
    list_item = re.compile(r"^\s*-\s+(.+)$")
    for raw in manifest_text.splitlines():
        # A new top-level key ends whatever block we were in. 'bootstrap:' does not
        # collide with 'boot:' because the colon is part of the match.
        m = top_key.match(raw)
        if m:
            current = m.group(1) if m.group(1) in LIST_KEYS else None
            continue
        if current is None:
            continue
        im = list_item.match(raw)
        if im:
            item = _strip_inline_comment(im.group(1))
            if item:
                lists[current].append(item)
        # blank lines / indented comment lines fall through and are ignored
    return lists


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
    regexes ARE the parser rather than a fallback.

    Requires at least one space of indent, preserving the original intent that these
    keys are nested (under `close:`) rather than top-level.

    DUPLICATED DELIBERATELY in hooks/session_start.py and scripts/rotate.py. Machinery
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


def _bulletin_from_lines(manifest_text):
    """Dependency-free fallback for bulletin_boot_file — same contract, regex only.
    Split out so the suite can exercise it whether or not PyYAML is installed
    (mirrors _parse_load_lists_lines)."""
    block = _block_under(manifest_text, "bulletin")
    if block is None or not re.search(r"(?m)^\s*check_at_boot:\s*true\b", block):
        return None
    fm = re.search(r"(?m)^\s*file:\s*(\S+)", block)
    return _strip_inline_comment(fm.group(1)) if fm else None


def bulletin_scan_enabled(manifest_text):
    """True when close.bulletin declares `mode: scan_headers`.

    The board is ADDRESSED — every message carries a `To:` — so a session needs the
    header index plus its own mail, not the file. Nothing in the load path used that
    addressing until this flag existed; the addressing was honored by the reader and
    ignored by the load."""
    try:
        import yaml  # type: ignore
        data = yaml.safe_load(manifest_text)
        if isinstance(data, dict):
            b = (data.get("close") or {}).get("bulletin") or {}
            return str(b.get("mode", "")).strip() == "scan_headers"
    except Exception:  # noqa: BLE001 — yaml missing or manifest not valid yaml
        pass
    block = _block_under(manifest_text, "bulletin")
    return bool(block is not None and re.search(r"(?m)^\s*mode:\s*scan_headers\b", block))


def bulletin_boot_file(manifest_text):
    """The bulletin board is read at SESSION START but is not declared in `boot:` —
    it sits under `close.bulletin` with `check_at_boot: true`. Reading only the load
    lists therefore undercounts the boot stack by a whole file that every multi-agent
    session actually pays for.

    It is CONDITIONAL — on `check_at_boot: true`, and on nothing else.

    IT USED TO GATE ON AN `agents:` BLOCK, and that was wrong. `agents:` was a
    proxy for "is this board live", and the proxy held on the instance it was
    written against and nowhere else: a real instance was found carrying
    `bulletin.check_at_boot: true` AND a CLAUDE.md telling every session to read
    the board, but no `agents:` block — so the meter silently dropped 11,611
    tokens, 14% of that instance's true boot, on the one instance whose number
    was being used to justify a migration. The manifest already states the fact
    directly; read the fact, not a correlate of it.

    Returns the bulletin's relative path, or None. Mirrors parse_load_lists' handling —
    PyYAML when importable, regex fallback otherwise (zero third-party deps)."""
    try:
        import yaml  # type: ignore
        data = yaml.safe_load(manifest_text)
        if isinstance(data, dict):
            b = (data.get("close") or {}).get("bulletin") or {}
            if b.get("check_at_boot") and b.get("file"):
                return str(b["file"]).strip()
            return None
    except Exception:  # noqa: BLE001 — yaml missing or manifest not valid yaml
        pass
    return _bulletin_from_lines(manifest_text)


def parse_load_lists(manifest_text):
    """Extract the boot / on_demand / never_load_whole file lists from 4SYNC.yaml
    text. Best-effort: use PyYAML if importable, else fall back to a line/regex
    parser over the load-list blocks (mirrors the hook's YAML handling — the module
    must run with zero third-party deps). Returns a dict with exactly the LIST_KEYS
    keys, each a list of relative path strings (possibly empty).

    The `boot` list also picks up the conditionally-read bulletin board (see
    bulletin_boot_file) — it is read at session start, so the meter must charge for
    it even though the manifest declares it elsewhere."""
    lists = None
    try:
        import yaml  # type: ignore
        data = yaml.safe_load(manifest_text)
        if isinstance(data, dict):
            out = {}
            ok = True
            for k in LIST_KEYS:
                v = data.get(k)
                if v is None:
                    out[k] = []
                elif isinstance(v, list):
                    out[k] = [str(x).strip() for x in v if str(x).strip()]
                else:
                    ok = False
                    break
            if ok:
                lists = out
    except Exception:  # noqa: BLE001 — yaml missing or manifest not valid yaml
        pass
    if lists is None:
        lists = _parse_load_lists_lines(manifest_text)

    bulletin = bulletin_boot_file(manifest_text)
    if bulletin and bulletin not in lists["boot"]:
        lists["boot"].append(bulletin)
    return lists


# ─────────────────────────────────────────────────────────────────────────────
# Disk access — the only impure layer. Read-only; never opens for writing.
# ─────────────────────────────────────────────────────────────────────────────

def measure_file(root, relpath):
    """Return the size in bytes of root/relpath, or 0 if it is missing.

    Never raises on a missing/unreadable path (skeleton or template repos vary) —
    a missing entry measures 0 and is flagged by path_exists() at the report layer
    so it can carry a note.

    A DIRECTORY is summed, not zeroed (MP#47/D4). A manifest may legitimately defer
    a whole folder — `tasks/` on a second instance holds 98 documents — and this
    reported it as `(missing — counted as 0)`, which to an adopter reads as a broken
    install rather than as 98 files correctly kept off the boot path. getsize() on a
    directory returns the directory ENTRY's size, which is not the answer either."""
    p = os.path.join(root, relpath)
    if os.path.isdir(p):
        return measure_dir(p)
    try:
        return os.path.getsize(p)
    except OSError:
        return 0


def measure_dir(path):
    """Total bytes of every file under `path`, honouring SKIP_DIRS.

    Recursive because the split puts real weight one level down — `tasks/` holds
    `tasks/closed/`, and a top-level-only sum would under-report a deferred folder
    by most of its contents."""
    total = 0
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                continue
    return total


# One agent's own OPEN mail, allowed for on top of the header index. Measured:
# on a 46 KB board the single OPEN message for one agent was ~1,081 B.
BULLETIN_BODY_ALLOWANCE = 1200


def measure_bulletin_scan(root, relpath, allowance=BULLETIN_BODY_ALLOWANCE):
    """Price a scanned bulletin board as HEADER INDEX + one agent's own mail.

    Once the boot step greps `### [n] … To: … Status:` header lines and reads only
    the bodies addressed to this agent, charging the whole file reports a cost the
    protocol no longer pays. Measured on a real 46,445 B board: 24 header lines are
    1,701 B, and one agent's single OPEN message ~1,081 B — so the honest number is
    ~2,800 B, not 46 KB. The file is large mostly because ONE agent is not draining
    its inbox, and no other agent should be metered for that.

    Falls back to the full size when the file cannot be read or carries no parseable
    headers — the same posture as the scan itself, which reverts to a full read (and
    says so) rather than silently missing a message."""
    p = os.path.join(root, relpath)
    try:
        with open(p, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return 0
    heads = re.findall(r"(?m)^### \[\d+\][^\n]*$", text)
    if not heads:
        return len(text.encode("utf-8"))
    index = sum(len(h.encode("utf-8")) + 1 for h in heads)
    return min(index + allowance, len(text.encode("utf-8")))


def file_exists(root, relpath):
    """True if the manifest entry resolves to something measurable.

    Accepts a DIRECTORY as well as a file (MP#47/D4). The isfile() test that used
    to live here is what made a deferred `tasks/` folder of 98 documents report as
    missing. Kept under the old name because it answers the same question the
    report layer asks — 'is there anything here?'"""
    p = os.path.join(root, relpath)
    return os.path.isfile(p) or os.path.isdir(p)


# ─────────────────────────────────────────────────────────────────────────────
# Report assembly — a pure data builder (for --json) and a text renderer.
# ─────────────────────────────────────────────────────────────────────────────

def _row(root, relpath, tag=None, scanned=False):
    nbytes = (measure_bulletin_scan(root, relpath) if scanned
              else measure_file(root, relpath))
    row = {
        "path": relpath,
        "bytes": nbytes,
        "tokens": estimate_tokens(nbytes),
        "missing": not file_exists(root, relpath),
    }
    if os.path.isdir(os.path.join(root, relpath)):
        row["dir"] = True
    if scanned:
        row["scanned"] = True
    if tag is not None:
        row["tag"] = tag
    return row


def build_report_data(root, lists, manifest=None):
    """Pure-ish data builder for the meter (the only impurity is stat()/getsize via
    measure_file). Returns a dict describing the boot stack, the deferred set, their
    totals, and the savings math. The 'full boot read' is the manifest itself PLUS
    every file in the boot: list — the manifest is read to START boot, so it counts.

    manifest: basename of the instance manifest; defaults to resolve_manifest()."""
    manifest = manifest or resolve_manifest()
    # A scanned bulletin is priced as header-index + one agent's own mail, not as
    # a whole-file read — otherwise the meter keeps reporting a cost the protocol
    # stopped paying the moment `bulletin.mode: scan_headers` landed.
    scan_set = set()
    try:
        with open(os.path.join(root, manifest), encoding="utf-8") as fh:
            mtext = fh.read()
        if bulletin_scan_enabled(mtext):
            b = bulletin_boot_file(mtext)
            if b:
                scan_set.add(b)
    except OSError:
        pass

    boot_rows = [_row(root, manifest)]
    boot_rows += [_row(root, p, scanned=(p in scan_set)) for p in lists.get("boot", [])]

    deferred_rows = [_row(root, p, tag="on_demand") for p in lists.get("on_demand", [])]
    deferred_rows += [_row(root, p, tag="never_load_whole") for p in lists.get("never_load_whole", [])]

    boot_bytes = sum(r["bytes"] for r in boot_rows)
    boot_tokens = sum(r["tokens"] for r in boot_rows)
    deferred_bytes = sum(r["bytes"] for r in deferred_rows)
    deferred_tokens = sum(r["tokens"] for r in deferred_rows)

    total_tokens = boot_tokens + deferred_tokens
    deferred_pct = (deferred_tokens / total_tokens * 100.0) if total_tokens else 0.0

    return {
        "root": root,
        "manifest": manifest,
        "bytes_per_token": BYTES_PER_TOKEN,
        "boot": boot_rows,
        "deferred": deferred_rows,
        "boot_total_bytes": boot_bytes,
        "boot_total_tokens": boot_tokens,
        "deferred_total_bytes": deferred_bytes,
        "deferred_total_tokens": deferred_tokens,
        "total_bytes": boot_bytes + deferred_bytes,
        "total_tokens": total_tokens,
        "deferred_pct": deferred_pct,
    }


def _fmt_int(n):
    return f"{n:,}"


def build_report(root, lists, manifest=None):
    """Render the human-readable text report from build_report_data()."""
    data = build_report_data(root, lists, manifest)

    # Column widths — right-align the byte and token columns across all rows.
    all_rows = data["boot"] + data["deferred"]
    path_w = max([len(r["path"]) for r in all_rows] + [len("BOOT TOTAL")])
    byte_vals = [_fmt_int(r["bytes"]) for r in all_rows] + [
        _fmt_int(data["boot_total_bytes"]), _fmt_int(data["deferred_total_bytes"])]
    tok_vals = [_fmt_int(r["tokens"]) for r in all_rows] + [
        _fmt_int(data["boot_total_tokens"]), _fmt_int(data["deferred_total_tokens"])]
    byte_w = max(len(v) for v in byte_vals) if byte_vals else 1
    byte_w = max(byte_w, len("bytes"))
    tok_w = max(len(v) for v in tok_vals) if tok_vals else 1
    tok_w = max(tok_w, len("~tokens"))

    def line(path, nbytes, tokens, tag="", note=""):
        s = f"  {path:<{path_w}}  {nbytes:>{byte_w}}  {tokens:>{tok_w}}"
        if tag:
            s += f"  {tag}"
        if note:
            s += f"  {note}"
        return s

    def _row_note(r):
        if r["missing"]:
            return "(missing — counted as 0)"
        if r.get("dir"):
            return "(directory — contents summed)"
        return ""

    out = []
    out.append("4SYNC ARCH — boot-cost meter")
    out.append(f"repo: {root}")
    out.append(f"(token counts are ESTIMATES ~ bytes // {BYTES_PER_TOKEN}, not tokenizer output)")
    out.append("")
    out.append(line("BOOT STACK", "bytes", "~tokens"))
    out.append("  " + "-" * (path_w + byte_w + tok_w + 4))
    for r in data["boot"]:
        note = _row_note(r)
        out.append(line(r["path"], _fmt_int(r["bytes"]), _fmt_int(r["tokens"]), note=note))
    out.append(line("BOOT TOTAL",
                    _fmt_int(data["boot_total_bytes"]),
                    _fmt_int(data["boot_total_tokens"])))
    out.append("")
    out.append(line("DEFERRED", "bytes", "~tokens"))
    out.append("  " + "-" * (path_w + byte_w + tok_w + 4))
    if data["deferred"]:
        for r in data["deferred"]:
            note = _row_note(r)
            tag = f"[{r.get('tag', '')}]"
            out.append(line(r["path"], _fmt_int(r["bytes"]), _fmt_int(r["tokens"]),
                            tag=tag, note=note))
    else:
        out.append("  (nothing deferred — no on_demand / never_load_whole entries)")
    out.append(line("DEFERRED TOTAL",
                    _fmt_int(data["deferred_total_bytes"]),
                    _fmt_int(data["deferred_total_tokens"])))
    out.append("")
    out.append(
        f"SAVINGS: boot loads ~{_fmt_int(data['boot_total_tokens'])} tokens; "
        f"defers ~{_fmt_int(data['deferred_total_tokens'])} tokens "
        f"({data['deferred_pct']:.1f}% of ~{_fmt_int(data['total_tokens'])} tokens of "
        f"total known canon) — kept out of every session's window.")
    return "\n".join(out)


# ─────────────────────────────────────────────────────────────────────────────
# Longitudinal series (--log)
# ─────────────────────────────────────────────────────────────────────────────

SERIES_DEFAULT = os.path.join("metrics", "roc_series.jsonl")


def _git_commit(root):
    """Short HEAD sha, or None outside a repo. A row without one is still a
    valid measurement; it just cannot be re-derived later."""
    try:
        out = subprocess.run(["git", "-C", root, "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or None if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def series_row(data, commit=None, note=None, when=None):
    """One measurement, as a plain dict. Pure — the caller supplies time and sha.

    WHY PER-FILE BYTES AND NOT JUST THE TOTAL: the question the series has to
    answer later is not "did boot grow" but "WHICH FILE grew" — that is the
    wedge chart, and it is unrecoverable from a scalar. This silo has now had
    the same disease three times (inline descriptions, then Subject cells, then
    the table's own prose paragraphs), and each time the total moved for a
    reason no total could name.

    Tokens are omitted per file and kept only in the aggregates: they are
    bytes // BYTES_PER_TOKEN, so storing both invites a series where the two
    disagree after the divisor is ever tuned. Store the measurement, derive the
    estimate."""
    return {
        "ts": (when or datetime.now()).isoformat(timespec="seconds"),
        "commit": commit,
        "manifest": data.get("manifest"),
        "bytes_per_token": data.get("bytes_per_token"),
        "boot_bytes": data["boot_total_bytes"],
        "boot_tokens": data["boot_total_tokens"],
        "deferred_bytes": data["deferred_total_bytes"],
        "deferred_tokens": data["deferred_total_tokens"],
        "deferred_pct": round(data["deferred_pct"], 2),
        "files": {r["path"]: r["bytes"] for r in data["boot"] if not r.get("missing")},
        "deferred_files": {r["path"]: r["bytes"] for r in data["deferred"]
                           if not r.get("missing")},
        "note": note,
    }


def append_series(path, row):
    """APPEND one JSON line. Never rewrites, never reorders, never blocks.

    JSONL rather than TSV, deliberately: the boot stack itself changes over time
    — files are added, deferred, split, renamed — and that change IS the thing
    being measured. Fixed columns would break at exactly the moment the series
    got interesting, and the repair would be to rewrite history in the one file
    whose entire value is that it cannot be backfilled.

    Same reason this only ever appends. A measurement series has no undo: a run
    that is not logged today can never be recovered, and a bug that rewrites the
    file destroys the only copy. Duplicate rows at one commit are honest (two
    measurements really were taken) and cost a line."""
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main():
    # Windows consoles default to cp1252; the '—' and box-drawing glyphs in the
    # report would raise UnicodeEncodeError on print. Reconfigure early.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(
        description="Read-only boot-cost meter for a 4SYNC instance.")
    ap.add_argument("--dir", default=".",
                    help="project root (holds the instance manifest). Default: current dir.")
    ap.add_argument("--json", action="store_true",
                    help="emit machine-readable JSON instead of the text report")
    ap.add_argument("--log", nargs="?", const=SERIES_DEFAULT, default=None,
                    metavar="PATH",
                    help=f"append this measurement to a longitudinal series "
                         f"(default: {SERIES_DEFAULT}). The series cannot be "
                         f"backfilled — a close that skips this loses the point.")
    ap.add_argument("--note", default=None,
                    help="short label stored with the logged row (e.g. 'after ledger trim')")
    args = ap.parse_args()

    root = os.path.abspath(args.dir)

    # Resolve ONCE and thread it down, so the file we opened and the file the
    # report names can never disagree.
    manifest = resolve_manifest()

    manifest_path = os.path.join(root, manifest)
    try:
        with open(manifest_path, encoding="utf-8") as f:
            manifest_text = f.read()
    except OSError:
        # No manifest — still emit a well-formed (empty) report rather than crash.
        manifest_text = ""

    lists = parse_load_lists(manifest_text)

    data = build_report_data(root, lists, manifest)

    if args.json:
        print(json.dumps(data, indent=2))
    else:
        print(build_report(root, lists, manifest))

    if args.log:
        row = series_row(data, commit=_git_commit(root), note=args.note)
        path = args.log if os.path.isabs(args.log) else os.path.join(root, args.log)
        try:
            append_series(path, row)
        except OSError as exc:
            # A measurement is never worth failing a close over. Say so and exit 0.
            print(f"\n! could not append to the series ({exc}) — measurement NOT "
                  f"recorded. This run is unrecoverable; the series has no backfill.",
                  file=sys.stderr)
        else:
            print(f"\nlogged: {os.path.relpath(path, root)} — boot "
                  f"{row['boot_bytes']:,} B (~{row['boot_tokens']:,} tok)"
                  + (f" [{args.note}]" if args.note else ""))


if __name__ == "__main__":
    main()
# ═══ EOF meter.py ═══