#!/usr/bin/env python3
"""Stdlib-only suite for scripts/split_ledger.py — the one-time ledger migration.

Run:  python scripts/test_split_ledger.py

The cases that matter are the REFUSALS. This script restructures a whole ledger
once, irreversibly; a bug that drops content has no second run to catch it. So
the suite spends most of its weight proving the script declines to run on the
shapes that would produce a silent partial migration — above all the real one it
was rewritten for: description blocks sitting ABOVE the `## Task descriptions`
heading, which the heading-bound scan skipped without a word.
"""

import io
import os
import shutil
import sys
import tempfile
import contextlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import split_ledger as S  # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}   {detail}")


def run(root, *argv):
    """Invoke main() in a temp root; return (exit_code, stdout+stderr)."""
    buf = io.StringIO()
    argv = ["split_ledger.py", "--dir", root] + list(argv)
    old = sys.argv
    sys.argv = argv
    code = 0
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            S.main()
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        if isinstance(e.code, str):
            buf.write(e.code)
    finally:
        sys.argv = old
    return code, buf.getvalue()


LEDGER = """# Ledger

## Summary table

| ID | Status | Subject | Blocked by |
|---|---|---|---|
| 1 | ✅ | Done thing | — |
| 2 | ⏳ | Open thing | — |
{extra_rows}
### #1 — Done thing ✅

Body of one.

### #2 — Open thing ⏳

Body of two.
{extra}
---

*Pattern from 4SYNC ARCH — this silo is patient zero.*
"""


def make(tmp, extra="", extra_rows="", ledger=None):
    root = tempfile.mkdtemp(dir=tmp)
    text = ledger if ledger is not None else LEDGER.format(extra=extra, extra_rows=extra_rows)
    with open(os.path.join(root, "MERGE_PLAN.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return root


def main():
    S._utf8_stdout()
    tmp = tempfile.mkdtemp()
    try:
        print("collect_descriptions — the defect this was rewritten for")
        # blocks ABOVE '## Task descriptions' must still be collected
        interleaved = (
            "# L\n\n## Summary table\n\n| ID | Status | Subject | B |\n|---|---|---|---|\n"
            "| 1 | ✅ | a | — |\n| 2 | ✅ | b | — |\n\n"
            "### #1 — a ✅\n\nabove the heading.\n\n"
            "## Task descriptions\n\n### #2 — b ✅\n\nbelow the heading.\n")
        got = S.collect_descriptions(interleaved)
        check("collects blocks ABOVE the '## Task descriptions' heading",
              sorted(t for t, *_ in got) == [1, 2], f"got {[t for t, *_ in got]}")
        check("body captured for the above-heading block",
              any(t == 1 and "above the heading." in b for t, _s, b, *_ in got))

        check("heading with no dash still parses",
              [t for t, *_ in S.collect_descriptions("### #7 Subject here\n\nbody\n")] == [7])
        check("trailing status mark stripped from subject",
              S.collect_descriptions("### #7 — Subject ✅\n\nb\n")[0][1] == "Subject")

        print("\nparse_table")
        t = S.parse_table(LEDGER.format(extra="", extra_rows=""))
        check("statuses read from the table, not the heading emoji",
              t == {1: "completed", 2: "pending"}, str(t))
        check("no summary table → None", S.parse_table("# nothing\n") is None)

        print("\nrefusals (each must exit non-zero and write nothing)")
        root = make(tmp, extra="\n### #2 — dupe ⏳\n\nsecond body.\n")
        code, out = run(root, "--apply")
        check("duplicate description id is FATAL", code != 0 and "TWO descriptions" in out, out[:120])
        check("  … and no tasks/ dir was created", not os.path.exists(os.path.join(root, "tasks")))

        root = make(tmp, extra="\n### #9 — orphan ⏳\n\nno row for this.\n")
        code, out = run(root, "--apply")
        check("description with no table row is FATAL", code != 0 and "NO table row" in out, out[:120])

        root = make(tmp, extra_rows="| 3 | ⏳ | Undocumented open row | — |\n")
        code, out = run(root, "--apply")
        check("OPEN row with no description is FATAL", code != 0 and "OPEN row" in out, out[:120])

        root = make(tmp, extra_rows="| 4 | ✅ | Closed, never documented | — |\n")
        code, out = run(root)
        check("TERMINAL row with no description is NOT fatal",
              code == 0 and "terminal rows with no description: 1" in out, out[:200])

        no_nl = LEDGER.format(extra="", extra_rows="").rstrip("\n")
        root = make(tmp, ledger=no_nl)
        code, out = run(root, "--apply")
        check("ledger with no final newline is FATAL (truncation signature)",
              code != 0 and "TRUNCATED" in out, out[:120])
        code, out = run(root, "--apply", "--allow-no-final-newline")
        check("  … and the override lets it through", code == 0, out[:160])

        print("\ndry run writes nothing")
        root = make(tmp)
        code, out = run(root)
        check("dry run exits 0", code == 0, out[:160])
        check("dry run creates no tasks/", not os.path.exists(os.path.join(root, "tasks")))
        check("dry run leaves the ledger byte-identical",
              S.read(os.path.join(root, "MERGE_PLAN.md")) == LEDGER.format(extra="", extra_rows=""))

        print("\napply")
        root = make(tmp)
        code, out = run(root, "--apply")
        live = os.path.join(root, "tasks", "MP-002.md")
        closed = os.path.join(root, "tasks", "closed", "MP-001.md")
        check("apply exits 0", code == 0, out[:200])
        check("terminal row's doc → tasks/closed/", os.path.exists(closed))
        check("open row's doc → tasks/", os.path.exists(live))
        check("doc carries the body", "Body of two." in S.read(live))
        check("doc header names the row", S.read(live).startswith("# MP#2 — Open thing"))
        new = S.read(os.path.join(root, "MERGE_PLAN.md"))
        check("descriptions removed from the ledger", "Body of two." not in new)
        check("summary table survives", "| 2 | ⏳ | Open thing" in new)
        check("footer survives and is last",
              new.rstrip().endswith("*Pattern from 4SYNC ARCH — this silo is patient zero.*"),
              repr(new[-80:]))
        check("no '## Task descriptions' heading left dangling", "## Task descriptions" not in new)
        check("ledger got smaller", len(new) < len(LEDGER.format(extra="", extra_rows="")))

        print("\nempty '## Task descriptions' heading is dropped")
        led = ("# L\n\n## Summary table\n\n| ID | Status | Subject | B |\n|---|---|---|---|\n"
               "| 1 | ✅ | a | — |\n\n---\n\n## Task descriptions\n\n### #1 — a ✅\n\nbody.\n")
        root = make(tmp, ledger=led)
        code, out = run(root, "--apply")
        new = S.read(os.path.join(root, "MERGE_PLAN.md"))
        check("heading removed once emptied", code == 0 and "## Task descriptions" not in new, new)
        check("table still intact", "| 1 | ✅ | a | — |" in new)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
# ═══ EOF test_split_ledger.py ═══
