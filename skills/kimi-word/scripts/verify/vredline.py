"""Tracked-changes integrity vs a baseline: undo only the NEW revisions;
any text residue is an edit made without tracking."""
from __future__ import annotations

import sys
from pathlib import Path

from lxml import etree

sys.path.insert(0, str(Path(__file__).parent))
import walker  # noqa: E402  (the one rendered-text rule)
from findings import Finding, _dump_lines  # noqa: E402
from vchecks import story_kinds  # noqa: E402  (one-way: no cycle)
from vpkg import Pkg, W  # noqa: E402

# ---------- Redlining check (--redline) ----------

def _rendered_join(el, tags) -> str:
    """Concatenated text as Word renders it (non-preserved edges trimmed):
    walker.rendered is the one xml:space trim rule project-wide."""
    return "".join(walker.rendered(t) for t in el.iter(*tags))


def _rev_key(e):
    """A revision is recognized by (tag, author, date, rendered text) --
    ids are not reliable across editors."""
    W_ = f"{{{W}}}"
    return (e.tag, e.get(W_ + "author"), e.get(W_ + "date"),
            _rendered_join(e, (W_ + "t", W_ + "delText")))


def _body_text(root, mask_caches: bool = False) -> str:
    """Rendered body text, paragraph per line.

    mask_caches=True drops every w:t that lies in FIELD-RESULT territory
    (between fldChar separate..end, or inside fldSimple). Cache text is
    content Word recomputes on refresh -- rewriting it (e.g. backfilling
    real TOC page numbers before delivery) is maintenance, not a body
    edit, yet the redline gate flagged it as untracked and agents spent
    ~0.5M tokens per case proving the residue harmless (dxv2-11
    trajectory evidence). Comparing the MASKED texts tells cache-only
    residue apart from real dark edits, mechanically."""
    W_ = f"{{{W}}}"
    out = []
    for p in root.iter(W_ + "p"):
        if not mask_caches:
            s = _rendered_join(p, (W_ + "t",))
            if s:
                out.append(s)
            continue
        pieces = []
        depth_field = 0     # fldChar separate..end nesting
        for el in p.iter():
            tag = el.tag
            if tag == W_ + "fldChar":
                ty = el.get(f"{{{W}}}fldCharType")
                if ty == "separate":
                    depth_field += 1
                elif ty == "end" and depth_field:
                    depth_field -= 1
            elif tag == W_ + "t":
                inside_simple = False
                anc = el.getparent()
                while anc is not None and anc is not p:
                    if anc.tag == W_ + "fldSimple":
                        inside_simple = True
                        break
                    anc = anc.getparent()
                if not depth_field and not inside_simple:
                    # SAME text rule as the unmasked pass (walker.rendered:
                    # non-preserved edges trimmed). Raw el.text here made
                    # "what is text" mode-dependent: pass 1 ignored an
                    # invisible edge space, then the masked re-compare
                    # counted it -- a cache refresh plus one invisible
                    # space read as a dark edit (v3.4, user-confirmed fix)
                    pieces.append(walker.rendered(el))
        s = "".join(pieces)
        if s:
            out.append(s)
    return "\n".join(out)


def redline_check(pkg: Pkg, opkg: Pkg, bad: list) -> tuple:
    """TEXT-LEVEL redline gate over every story part present in both
    packages: undo only the revisions that are NEW relative to the
    baseline, then compare rendered text. Any residue is an edit made
    without tracking. Works on pre-redlined documents: baseline revisions
    are matched by (tag, author, date, text) -- including a fallback for
    a baseline revision legitimately split into pieces that still spell
    the same text -- and left in place. -> (count of new revisions,
    informational notes for the caller to print, cache-only residue
    warnings).

    Scope contract (stated, not implied): this gate covers TEXT. Untracked
    format/structure changes (bold a run, resize a table) do not move text
    and are gate 2's job -- read.py --diff reports every one of them."""
    import copy
    import difflib
    total_new = 0
    notes: list = []    # informational lines; the caller prints them
    #: cache-only redline residues: reported as warnings, not failures
    #: (see _body_text mask_caches).
    cache: list = []
    # UNION of story parts, not intersection: a header ADDED during the
    # edit carries text explained by no revision -- comparing only the
    # common parts let a whole new story of untracked text sail through
    # the gate (measured escape). A part missing on either side reads as
    # empty content on that side.
    # COMMENTS ARE NOT BODY TEXT. A comment or a reply is margin
    # discussion: Word itself records no revisions inside comment bodies,
    # so "new text with no revision explaining it" is their NORMAL state.
    # Running them through the body gate made every comment-reply task
    # fail redline with a false alarm the caller had to talk themselves
    # past -- and a gate that cries wolf stops guarding anything (dxv2-9
    # field report). Comment additions are reported separately, as facts.
    kinds_a, kinds_b = story_kinds(pkg), story_kinds(opkg)
    story = sorted(set(kinds_a) | set(kinds_b))
    for part in story:
        kind = kinds_a.get(part) or kinds_b.get(part)
        if kind == "comments":
            _comment_delta(pkg, opkg, part, bad, notes)
            continue
        total_new += _redline_part(pkg, opkg, part, bad, cache,
                                   copy=copy, difflib=difflib)
    return total_new, notes, cache


def _comment_delta(pkg, opkg, part, bad, notes) -> None:
    """Comments changed vs baseline: DELETING or REWRITING someone's
    comment is still a violation (you do not silently edit another
    reviewer's words); ADDING comments/replies is normal work and is
    reported as an informational line, not a failure."""
    def texts(p):
        if part not in p.names:
            return {}
        out = {}
        for c in p.xml(part).iter(f"{{{W}}}comment"):
            cid = c.get(f"{{{W}}}id")
            out[cid] = "".join(t.text or "" for t in c.iter(f"{{{W}}}t"))
        return out
    a, b = texts(opkg), texts(pkg)
    gone = {k: v for k, v in a.items() if k not in b}
    changed = {k: (a[k], b[k]) for k in a.keys() & b.keys()
               if a[k] != b[k]}
    if gone or changed:
        det = "; ".join([f"c{k} deleted: {v[:20]!r}"
                         for k, v in sorted(gone.items())]
                        + [f"c{k} rewritten: {x[:20]!r}->{y[:20]!r}"
                           for k, (x, y) in sorted(changed.items())])
        bad.append(Finding("CMT_TAMPERED", (part,),
                           f"{part}: baseline comments altered -- {det}",
                           count=len(gone) + len(changed)))
    new = sorted(k for k in b if k not in a)
    if new:
        notes.append(f"  · comments: +{len(new)} new "
                     f"({', '.join('c' + k for k in new[:8])}) -- margin "
                     "discussion, not body text; informational")


_EMPTY_STORY = (f'<w:document xmlns:w="{W}"><w:body/></w:document>')


#: redline treats a move as a tracked edit: moveTo behaves like ins
#: (inserted at destination), moveFrom like del (removed at source).
#: Omitting them made every legit move read as an untracked edit
#: (dxv2-3 review P1.6d).
def _redline_part(pkg, opkg, part, bad, cache, copy, difflib) -> int:
    W_ = f"{{{W}}}"
    INS_T = (W_ + "ins", W_ + "moveTo")
    DEL_T = (W_ + "del", W_ + "moveFrom")
    root = (copy.deepcopy(pkg.xml(part)) if part in pkg.names
            else etree.fromstring(_EMPTY_STORY))
    oroot = (opkg.xml(part) if part in opkg.names
             else etree.fromstring(_EMPTY_STORY))

    def depth(e):
        d = 0
        while e is not None:
            d += 1
            e = e.getparent()
        return d
    def undo(e):
        parent = e.getparent()
        if parent is None:
            return
        if parent.tag == W_ + "rPr":     # paragraph-mark revision
            host = parent.getparent().getparent()
            parent.remove(e)
            if e.tag in INS_T and host is not None:
                import revisions as _rv  # rejoin the split paragraph
                _rv._merge_into_next(host)
            return
        if e.tag in INS_T:
            parent.remove(e)
        else:
            for dt in e.iter(W_ + "delText"):
                dt.tag = W_ + "t"
            for dt in e.iter(W_ + "delInstrText"):
                dt.tag = W_ + "instrText"
            i = parent.index(e)
            for k2, c in enumerate(list(e)):
                parent.insert(i + k2, c)
            parent.remove(e)
    # INNERMOST-FIRST keying with ADJUSTED text. Flat two-phase keying
    # breaks on nested revisions: a track.py edit INSIDE a baseline w:ins
    # changes the outer container's recursive text (X -> XY), so the
    # outer legal old revision reads as new -- measured false FAIL.
    # Fix: key deepest-first; once a descendant w:ins is known-new, its
    # text is EXCLUDED from every ancestor's key (undoing it removes that
    # text). A known-new w:del changes nothing textually -- undoing it
    # revives its delText, which the key counts anyway.
    # A second wrinkle: a pending ins may later be RESCUED by the
    # split-piece fallback (old content, legitimately split) -- but its id
    # already polluted new_set, so ancestors keyed with its text excluded
    # were misclassified. One reclassification pass with rescued pieces
    # pinned as old fixes that (the rescue decision itself is stable).
    new_set: set = set()
    rescued_ids: set = set()

    def adj_text(e):
        parts = []
        if e.text:
            pass                          # revision containers carry no text
        for c in e.iter(W_ + "t", W_ + "delText"):
            anc, dead = c.getparent(), False
            while anc is not None and anc is not e:
                if anc.tag in INS_T and id(anc) in new_set:
                    dead = True
                    break
                anc = anc.getparent()
            if dead:
                continue
            parts.append(walker.rendered(c))
        return "".join(parts)

    def adj_key(e):
        return (e.tag, e.get(W_ + "author"), e.get(W_ + "date"),
                adj_text(e))
    new = []
    for _attempt in range(2):
        new_set.clear()
        pool = {}
        for e in oroot.iter(*INS_T, *DEL_T):
            pool.setdefault(_rev_key(e), []).append(e)
        pending: list = []               # candidate split-pieces, by group
        for e in sorted([x for x in root.iter(*INS_T, *DEL_T)],
                        key=depth, reverse=True):
            if e.getparent() is None:
                continue
            k = adj_key(e)
            bucket = pool.get(k)
            if bucket:
                bucket.pop()
            else:
                pending.append((k, e))
                if e.tag in INS_T and id(e) not in rescued_ids:
                    new_set.add(id(e))
        # split-piece fallback BEFORE undoing: an old revision legitimately
        # split into pieces still spells the same text under
        # (tag,author,date)
        unmatched = {}
        for k, es in pool.items():
            for _ in es:
                unmatched.setdefault(k[:3], []).append(k[3])
        by_group: dict = {}
        for k, e in pending:
            by_group.setdefault(k[:3], []).append((k, e))
        new = []
        newly_rescued: set = set()
        for g, items in by_group.items():
            joined = "".join(k[3] for k, _ in items)
            if joined and joined == "".join(unmatched.get(g, [])):
                newly_rescued |= {id(e) for _, e in items
                                  if e.tag in INS_T
                                  and id(e) in new_set}
                continue
            new.extend(e for _, e in items)
        if not newly_rescued:
            break
        rescued_ids |= newly_rescued
    for e in sorted(new, key=depth, reverse=True):
        undo(e)
    new_count = len(new)
    a, b = _body_text(oroot), _body_text(root)
    if a != b:
        diff = [ln for ln in difflib.unified_diff(
                    a.split("\n"), b.split("\n"), lineterm="", n=0)
                if ln[:1] in "+-" and ln[:3] not in ("+++", "---")]
        # residue confined to FIELD CACHES is a cache rewrite, not a dark
        # edit: compare again with cache territory masked on both sides.
        am, bm = (_body_text(oroot, mask_caches=True),
                  _body_text(root, mask_caches=True))
        fp = _dump_lines(f"redline-{Path(part).stem}", diff)
        loc = (f" all residue written to {fp} (grep to locate; do not "
               "read in full)" if fp else "")
        if am == bm:
            cache.append(
                f"redline: {part}: residue is confined to field caches "
                f"({len(diff)} lines) — cache rewrite, Word recomputes "
                f"on refresh; sign off via read --diff.{loc}")
        else:
            bad.append(f"redline: {part}: after undoing only the NEW "
                       "revisions, text still differs from baseline -> "
                       f"untracked edits ({len(diff)} lines): "
                       + " | ".join(d[:70] for d in diff[:4]) + loc)
    return new_count
