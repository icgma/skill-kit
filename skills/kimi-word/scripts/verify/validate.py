#!/usr/bin/env python3
"""docx validity check (lxml + ISO/ECMA standard XSDs). First of three gates.

    python scripts/validate.py file.docx [--baseline before.docx]
    python scripts/validate.py work/unpacked/ [--baseline before.docx]   # directory works too

Two check layers:
1) XSD: each XML part gets MCE preprocessing first (strip extension
   namespaces declared via mc:Ignorable, take the Fallback of
   mc:AlternateContent -- the compatibility mechanism defined by the OOXML
   standard, present in every Word file), then validate against the schema
   selected by root-element namespace.
2) Semantics (invisible to XSD, but Word fails to open or silently breaks):
   - comments: unique ids, Range/Reference pairing, durableId < 0x7FFFFFFF
   - bookmark pairing, field-char begin/end balance
   - footnote/endnote references have matching definitions
   - r:id/r:embed exist in .rels; rels targets point to existing parts
   - [Content_Types].xml covers every part
   - dangling numId/style references (-> warning: Word silently falls
     back, not fatal)

--baseline BEFORE-file: report **collateral damage** (lost parts / lost
paraIds / lost fields / lost media). This is the mechanism behind
"the whole layout broke" incidents; warn only, never fail -- deleting
parts may be intentional.

Exit code: 0 = PASSED (warnings do not affect it), 1 = FAILED.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
# The former god-file, split along its natural boundaries (layering:
# findings <- vpkg <- vchecks/vrepair/vredline <- validate, no cycles).
# This module keeps the CLI: arg parsing, baseline subtraction, output
# rendering. Everything below re-exports so `validate.X` callers
# (tests, the external harness, older notes) keep working.
from findings import (Finding, Findings, _dump_lines,  # noqa: E402,F401
                      _key)
from vpkg import (Pkg, ROOT_SCHEMA, _schema,           # noqa: E402,F401
                  mce_strip, run_checks)
from vchecks import (collateral, gen_checks,           # noqa: E402,F401
                     semantic_checks, story_kinds)
from vrepair import repair                             # noqa: E402,F401
from vredline import redline_check                     # noqa: E402,F401

# Windows consoles/pipes default to the legacy ANSI code page (cp1252/cp936):
# the engine's CJK messages must never crash the gate there.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


class Report:
    """One validation run (the structured verify<->validate API, dxv2-11):
    findings AFTER baseline subtraction plus everything render() needs
    to reproduce the old CLI stdout byte for byte -- verify.py reprints
    it, so a second renderer would drift."""
    __slots__ = ("bad", "warn", "n_xsd", "notes", "rc", "base_note",
                 "gen_ran", "gen", "max_errors")

    def __init__(self, bad, warn, n_xsd, notes, rc, base_note,
                 gen_ran, gen, max_errors):
        self.bad, self.warn, self.n_xsd = bad, warn, n_xsd
        self.notes, self.rc, self.base_note = notes, rc, base_note
        self.gen_ran, self.gen = gen_ran, gen
        self.max_errors = max_errors

    def render(self) -> str:
        out = list(self.notes)
        shown = Counter(self.bad)
        printed = 0
        for b, n in shown.items():
            if printed >= self.max_errors:
                break
            out.append(f"  ✗ {b}" + (f"  ×{n}" if n > 1 else ""))
            printed += 1
        if len(shown) > self.max_errors:
            # the elided kinds go to disk with the vdiff wording: a grep
            # target, not an invitation to Read the whole dump (agents
            # paid twice on the old "full diff ->" phrasing, dxv2-9
            # measured)
            fp = _dump_lines("validate-fail",
                             [f"✗ {b}" + (f"  ×{n}" if n > 1 else "")
                              for b, n in shown.items()])
            out.append(f"  ... {len(shown) - self.max_errors} more kinds"
                       + (f"; all violations written to {fp} (grep to "
                          "locate; do not read in full)"
                          if fp else ""))
        for w in self.warn:
            sev = w.severity if isinstance(w, Finding) else "warn"
            out.append(f"  {'ℹ' if sev == 'info' else '⚠'} {w}")
        if self.bad:
            gen_note = (f"; gen: {len(self.gen_ran)} checks ran"
                        if self.gen and self.gen_ran else "")
            out.append(f"FAILED: {len(self.bad)} violations "
                       f"({self.n_xsd} parts passed XSD"
                       f"{self.base_note}{gen_note})")
        else:
            gen_note = (f"; gen: {len(self.gen_ran)} checks clean"
                        if self.gen and self.gen_ran else "")
            n_info = sum(1 for w in self.warn
                         if isinstance(w, Finding)
                         and w.severity == "info")
            n_warn = len(self.warn) - n_info    # ℹ 是确认项不是告警,摘要分开数
            out.append(f"PASSED ({self.n_xsd} parts XSD + semantic checks"
                       + (f", {n_warn} warnings" if n_warn else "")
                       + (f", {n_info} info" if n_info else "")
                       + self.base_note + gen_note + ")")
        return "\n".join(out)


def run(src, baseline=None, redline=False, gen=False,
        max_errors=20) -> Report:
    """ONE structured entry for both CLIs (main below, verify.py):
    check src, subtract the baseline, redline-check. No printing here
    -- Report.render() owns the presentation, per-run state lives in
    the Report (no module global survives a run)."""
    pkg = Pkg(Path(src))
    bad, warn, n_xsd = run_checks(pkg)
    gen_ran: list = []
    if gen or baseline:
        # generation lint runs in BOTH modes now: standalone --gen for
        # from-scratch products (everything is yours), and baseline mode
        # where the same checks run on both sides and subtract -- a
        # hand-typed bullet the wild file always had stays silent, the
        # one your edit just added is yours (检查只报你改的)
        gen_ran = gen_checks(pkg, bad, warn)

    notes: list = []
    base_note = ""
    if baseline:
        opkg = Pkg(Path(baseline))
        obad, owarn, _ = run_checks(opkg)
        gen_checks(opkg, obad, owarn)

        def subtract(items, olds):
            """Baseline subtraction, monotonic (blocker E). Structured
            Findings compare per (code, identity) COUNTS: only a
            positive delta survives, reported as a delta. Plain
            strings keep the position-independent multiset keys."""
            old_n: dict = {}
            olds_s = []
            for x in olds:
                if isinstance(x, Finding):
                    k = (x.code, x.identity)
                    old_n[k] = old_n.get(k, 0) + x.count
                else:
                    olds_s.append(x)
            okeys = Counter(_key(x) for x in olds_s)
            kept = []
            for x in items:
                if isinstance(x, Finding):
                    k = (x.code, x.identity)
                    avail = old_n.get(k, 0)
                    if avail >= x.count:
                        old_n[k] = avail - x.count
                    else:
                        old_n[k] = 0
                        d = x.count - avail
                        kept.append(Finding(
                            x.code, x.identity,
                            x.msg + (f" (+{d} vs baseline)"
                                     if avail else ""),
                            x.severity, d))
                    continue
                k = _key(x)
                if okeys[k] > 0:
                    okeys[k] -= 1
                else:
                    kept.append(x)
            return kept
        kept = subtract(bad, obad)
        if len(bad) > len(kept):
            base_note = f", {len(bad) - len(kept)} pre-existing not counted"
        bad = Findings("error", kept)   # stay auto-wrapping: redline
        #                                 appends after this point
        # warnings too: a wild file's pre-existing width/style warnings
        # would otherwise reprint on every gate run
        warn = Findings("warn", subtract(warn, owarn))
        collateral(pkg, opkg, warn)
        has_revs = b"<w:ins " in pkg.read("word/document.xml") or \
            b"<w:del " in pkg.read("word/document.xml") \
            if "word/document.xml" in pkg.names else False
        if redline:
            n_new, rl_notes, cache_notes = redline_check(pkg, opkg, bad)
            notes.extend(rl_notes)
            warn.extend(cache_notes)
            base_note += (f", {n_new} new revisions redline-checked "
                          "(text-level; format changes: read --diff)")
        elif has_revs:
            notes.append("  note: document has tracked changes; pass "
                         "--redline to verify every edit is tracked "
                         "against the baseline")
    return Report(bad, warn, n_xsd, notes, 1 if bad else 0, base_note,
                  gen_ran, gen, max_errors)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src", help=".docx file or unpacked directory")
    ap.add_argument("--baseline",
                    help="pre-edit file: report only **new** violations + "
                         "collateral damage. Wild files often ship with "
                         "violations (WPS/LO are lax about ordering; Word "
                         "tolerates it); the gate asks 'did the edit break "
                         "anything', not 'is the file perfect'")
    ap.add_argument("--max-errors", type=int, default=20)
    ap.add_argument("--redline", action="store_true",
                    help="with --baseline: undo only the NEW tracked "
                         "changes and compare body text -- any residue is "
                         "an untracked edit. Works on pre-redlined files")
    ap.add_argument("--repair", action="store_true",
                    help="fix mechanical issues in an unpacked dir first: "
                         "missing xml:space, out-of-range paraId/"
                         "durableId, literal newline/tab in w:t, "
                         "colorless solid shading")
    ap.add_argument("--gen", action="store_true",
                    help="from-scratch generation lint: literal newlines,"
                         " solid shading, hand-typed bullets, CJK font "
                         "fallback, widthless tables, empty-TOC shapes "
                         "(near-certain bugs in generated files; opt-in "
                         "because legitimate in wild documents)")
    a = ap.parse_args(argv)

    if a.redline and not a.baseline:
        ap.error("--redline requires --baseline")
    if a.repair:
        k, log = repair(Path(a.src), gen=a.gen,
                        baseline=Path(a.baseline) if a.baseline else None)
        print(f"repaired {k} issue(s)")
        for (part, what), c_ in sorted(log.items()):
            # WHAT was repaired WHERE: the bare count left every agent
            # re-diffing the tree to find out (dxv2-3 C2)
            print(f"  · {part}: {what} ×{c_}")

    rep = run(a.src, baseline=a.baseline, redline=a.redline,
              gen=a.gen, max_errors=a.max_errors)
    print(rep.render())
    return rep.rc


if __name__ == "__main__":
    sys.exit(main())
