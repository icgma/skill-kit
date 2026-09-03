#!/usr/bin/env python3
"""Write tracked changes (w:ins / w:del) -- the revision WRITE side.

    python scripts/track.py work/unpacked --ops plan.json --dry-run --author Kimi
    # plan.json: [{"at": "#A1B2C3D4", "replace": "old", "with": "new"},
    #             {"after": "#A1B2C3D4", "new_paragraph": "..."},
    #             {"at": "#B2C3D4E5", "delete": "..."},
    #             {"at": "#C3D4E5F6", "del_paragraph": true}]
    # single op: --at "#A1B2C3D4" --replace old --with new

Targets are the view block line's stable #id and nothing else (ids
never drift -> plan order is free). New paragraphs get a fresh #id,
echoed, so later ops can target them.

Interface designed from field data: 84 hand-written revision edits across
one real 50-comment job hit exactly three pits, all absorbed here:
- run splitting at a boundary inside a non-text run -> refused with a
  clear error (same discipline as comment.py: no match / no guess);
- w:t -> w:delText and w:instrText -> w:delInstrText conversion is
  automatic (forgetting the latter is a validate violation);
- rPr for inserted text is cloned only from TEXT runs and stripped of
  rStyle -- cloning from a commentReference run once dressed new body
  text in the comment-mark character style, invisible to the eye.

Revision ids are max+1 onward over the whole package; the paragraph-mark
rPr is ordered per CT_ParaRPr via ooxml_order. All edits of one
invocation are applied in memory and written once -- a failed
invocation writes nothing.
"""
from __future__ import annotations

import argparse
import re
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from lxml import etree

sys.path.insert(0, str(Path(__file__).parent))
import opc  # noqa: E402  (shared OPC part resolution)
import revreg  # noqa: E402  (shared revision taxonomy: clone strips)
import walker as wml  # noqa: E402  (shared placeholder table + codec)
from comment import Ids, _runs_with_text, _split_run  # noqa: E402  (shared anchor machinery + id allocator)
from coords import by_id  # noqa: E402  (the one #id resolver)
from ooxml_order import ensure_child, insert_ord  # noqa: E402
from opc import atomic_write_tree  # noqa: E402

# Windows consoles/pipes default to the legacy ANSI code page (cp1252/cp936):
# the engine's CJK messages must never crash the gate there.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _mark(root_tag: str, ids: Ids, author: str):
    el = etree.Element(W + root_tag)
    el.set(W + "id", ids.take())
    el.set(W + "author", author)
    el.set(W + "date", _now())
    return el


def _to_del_content(el) -> None:
    """w:t -> w:delText, w:instrText -> w:delInstrText, in place."""
    for t in el.iter(W + "t"):
        t.tag = W + "delText"
    for t in el.iter(W + "instrText"):
        t.tag = W + "delInstrText"


#: character styles that mark an ANCHOR, not an appearance. Cloning one
#: onto new text is the pollution this function was written to stop --
#: but it used to drop rStyle unconditionally, so replacing a word inside
#: a hyperlink or an Emphasis run produced plain text and silently lost
#: the character style (dxv2-6 review W1). Matched on styleId AND on the
#: built-in NAME, because Word localizes styleIds ("a3") while the
#: w:name of a built-in stays fixed.
_ANCHOR_STYLES = {
    "commentreference", "annotationreference", "footnotereference",
    "endnotereference", "linenumber", "pagenumber",
    "annotation reference", "footnote reference", "endnote reference",
    "line number", "page number",
}


#: styles.xml root for the package being edited, set once in main(). A
#: module-level slot rather than a parameter threaded through six call
#: sites -- the alternative was six chances to forget one.
_STYLES = [None]


def _is_anchor_style(sid: str, styles_root=None) -> bool:
    styles_root = styles_root if styles_root is not None else _STYLES[0]
    if (sid or "").lower() in _ANCHOR_STYLES:
        return True
    if styles_root is None:
        return False
    for st in styles_root.findall(W + "style"):
        if st.get(W + "styleId") != sid:
            continue
        nm = st.find(W + "name")
        val = (nm.get(W + "val") if nm is not None else "") or ""
        return val.lower() in _ANCHOR_STYLES
    return False


def _clone_rpr(run, styles_root=None):
    """Clone a TEXT run's rPr for new inserted text.

    Keeps rStyle -- a character style IS the run's appearance, and the
    tracked replacement of an Emphasis/Hyperlink word must stay italic /
    stay a link after accept. Only ANCHOR styles (comment/footnote
    references, line numbers) are stripped: those mark a position, and
    copying one onto ordinary text is what the original blanket drop was
    trying to prevent. Also strips ALL revision history
    (revreg.strip_history, recursive) -- history records are the SOURCE
    run's, and each carries a document-unique w:id a copy would
    duplicate."""
    rpr = run.find(W + "rPr") if run is not None else None
    if rpr is None:
        return None
    c = deepcopy(rpr)
    for st in c.findall(W + "rStyle"):
        if _is_anchor_style(st.get(W + "val") or "", styles_root):
            c.remove(st)
    revreg.strip_history(c)
    return c if len(c) else None


def _new_run(text: str, clone_from=None, color: str | None = None):
    """A plain run with cloned formatting; color overrides w:color (the
    direct-edit marking variant: replaced text shown in a review color
    WITHOUT tracked changes)."""
    r = etree.Element(W + "r")
    rpr = _clone_rpr(clone_from)
    if color:
        if rpr is None:
            rpr = etree.Element(W + "rPr")
        for c in rpr.findall(W + "color"):
            rpr.remove(c)
        cel = etree.Element(W + "color")
        cel.set(W + "val", color.upper())
        # insert_ord, NOT SubElement: a bare append lands AFTER an
        # rPrChange the clone might carry -- rPrChange must stay last
        # (the ordering helper existed all along; this path just did
        # not use it -- measured regression). _clone_rpr strips
        # rPrChange now, but the invariant belongs here regardless.
        insert_ord(rpr, cel)
    if rpr is not None and len(rpr):
        r.append(rpr)
    t = etree.SubElement(r, W + "t")
    t.text = text
    if text != text.strip():
        t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    return r


def _new_ins_run(text: str, ids: Ids, author: str, clone_from=None):
    ins = _mark("ins", ids, author)
    ins.append(_new_run(text, clone_from))
    return ins


def _field_zone(p):
    """Set of id(run) for every run living inside a complex field --
    instruction OR result region (begin..end, any nesting depth). A
    tracked edit in a RESULT region looks fine until Word updates fields
    and silently regenerates the cache over it; such spans are refused,
    not wrapped."""
    zone, depth = set(), 0
    for r in p.iter(W + "r"):
        fc = r.find(W + "fldChar")
        ty = fc.get(W + "fldCharType") if fc is not None else None
        if ty == "begin":
            depth += 1
        if depth > 0:
            zone.add(id(r))
        if ty == "end":
            depth = max(0, depth - 1)
    return zone


def _span_runs(p, target: str, ids: Ids | None = None):
    """Locate `target` inside paragraph p (across runs, splitting edges).
    -> list of fully-covered runs, in document order. Errors out on
    no match / multiple matches / boundary inside a non-text run.
    `ids` feeds _split_run: a split that clones an rPrChange assigns the
    clone a fresh document-unique id."""
    target = wml.decode_ph(target)     # view escape \(tab) -> literal
    runs = _runs_with_text(p)
    flat = "".join(t for _, t in runs)
    i = flat.find(target)
    if i < 0:
        raise SystemExit(
            f"target text not found in block: {target!r}. The match "
            "stream is the view's content line: (placeholders) like "
            "(tab)/(f1)/(img1) are not text -- split the target at "
            "them; \u00ab\u00bb-quoted text is tracked-DELETED (not in the "
            "accepted state) -- use revisions.py, not --replace. Run "
            "prep.py first to merge fragmented runs."
            + _near_miss(flat, target))
    if flat.find(target, i + 1) >= 0:
        raise SystemExit(f"target text matches multiple places in block: "
                         f"{target!r} -- lengthen it with context")
    j = i + len(target)
    # Refuse targets inside field territory BEFORE any splitting. Checking
    # only the seg for fldChar (below) misses two cases: a span strictly
    # inside a RESULT region touches no fldChar run, and fldSimple content
    # is nested, not a sibling. Both are cached text Word regenerates.
    zone = _field_zone(p)
    pos = 0
    for r, t in runs:
        if pos < j and pos + len(t) > i:        # run overlaps the target
            bad = id(r) in zone
            anc = r.getparent()
            while not bad and anc is not None and anc is not p:
                bad = anc.tag == W + "fldSimple"
                anc = anc.getparent()
            if bad:
                raise SystemExit(
                    "target text lies inside a field (instruction or "
                    "cached result) -- Word regenerates field results on "
                    "update, silently discarding edits there. Use "
                    "--del-field, or edit the text the field points at")
        pos += len(t)
    pos, start_run, end_run = 0, None, None
    for r, t in runs:
        if start_run is None and pos + len(t) > i:
            if pos < i:
                r = _split_run(r, i - pos, ids)
                t = t[i - pos:]
                pos = i
            start_run = r
        if start_run is not None and pos + len(t) >= j:
            if pos + len(t) > j:
                _split_run(r, j - pos, ids)
            end_run = r
            break
        pos += len(t)
    # collect the sibling chain start..end; refuse container-crossing spans
    if start_run.getparent() is not end_run.getparent():
        raise SystemExit("target text spans a container boundary "
                         "(hyperlink/ins/del/moveFrom/textbox); narrow "
                         "the target")
    parent = start_run.getparent()
    kids = list(parent)
    i0, i1 = kids.index(start_run), kids.index(end_run)
    seg = kids[i0:i1 + 1]
    for el in seg:
        if el.tag in (W + "del", W + "moveFrom") or (
                isinstance(el.tag, str)
                and el.find(W + "del") is not None):
            raise SystemExit(
                "target span contains tracked-deleted/moved-away "
                "content (the view shows it as «…») -- already-dead "
                "text cannot be re-deleted; narrow the target or use "
                "revisions.py to resolve the pending revision first")
    # Point markers and commentReference runs are ALLOWED in the span:
    # ops group around them (each contiguous run stretch gets its own
    # w:del) and the markers stay in place -- Word's own shape. This
    # used to be a refusal, which locked the CLI out of comment-driven
    # revision tasks where the text to edit is almost always inside an
    # anchor (measured: a runner hand-wrote 300 lines around it).
    # Fields stay refused: tearing a fldChar pair corrupts the file.
    for k in seg:
        if k.tag in _MARKER_TAGS:
            continue
        if k.tag != W + "r":
            raise SystemExit(
                f"span crosses a non-run element "
                f"({etree.QName(k).localname}) -- narrow the target so it "
                "stays inside plain text, or handle that element "
                "explicitly")
        if k.find(W + "commentReference") is not None:
            continue                       # boundary run, left in place
        if k.find(W + "fldChar") is not None or \
                k.find(W + "instrText") is not None:
            raise SystemExit("span crosses a field -- delete fields with "
                             "--del-field, or narrow the target")
    return seg


#: Point markers whose pair may sit outside the span: they act as group
#: boundaries and are never moved or wrapped.
_MARKER_TAGS = {W + t for t in (
    "bookmarkStart", "bookmarkEnd", "commentRangeStart",
    "commentRangeEnd", "permStart", "permEnd", "proofErr")}


def _is_group_run(el) -> bool:
    """A run that participates in del/replace groups (not a
    commentReference host, which is an anchor artifact, not content)."""
    return el.tag == W + "r" and el.find(W + "commentReference") is None


def _wrap_del(runs, ids: Ids, author: str):
    """Wrap a contiguous sibling run list in one w:del (converted)."""
    if not runs:
        return None
    parent = runs[0].getparent()
    d = _mark("del", ids, author)
    parent.insert(parent.index(runs[0]), d)
    for r in runs:
        parent.remove(r)
        _to_del_content(r)
        d.append(r)
    return d


def _wrap_del_grouped(seg, ids: Ids, author: str):
    """One w:del per contiguous run group in seg; markers and
    commentReference runs stay in place between the groups (pairing and
    anchor geometry preserved by construction). -> list of dels."""
    dels, group = [], []

    def flush():
        if group:
            dels.append(_wrap_del(list(group), ids, author))
            group.clear()
    for el in seg:
        if _is_group_run(el):
            group.append(el)
        else:
            flush()
    flush()
    return dels


def _wrap_all_runs_del(container, ids: Ids, author: str,
                       kids=None) -> int:
    """Wrap every run under container in w:del, grouping contiguous
    sibling runs; recurses into hyperlinks; runs inside someone's w:ins
    get a NESTED w:del (the correct reject-another's-insertion shape).
    `kids` limits the walk to an explicit sibling slice (field spans)."""
    n = 0
    group: list = []

    def flush():
        nonlocal n
        if group:
            _wrap_del(list(group), ids, author)
            n += len(group)
            group.clear()
    for child in (list(container) if kids is None else kids):
        if child.tag == W + "r":
            group.append(child)
        else:
            flush()
            if child.tag in (W + "hyperlink", W + "ins", W + "smartTag",
                             W + "sdt", W + "customXml", W + "dir",
                             W + "bdo", W + "fldSimple"):
                inner = child.find(W + "sdtContent") \
                    if child.tag == W + "sdt" else child
                if inner is not None:
                    n += _wrap_all_runs_del(inner, ids, author)
    flush()
    return n


def _mark_paragraph(p, tag: str, ids: Ids, author: str) -> None:
    """Set the paragraph-mark revision (¶del / ¶ins) with CT_ParaRPr
    ordering (ins/del sort before all format keys -- insert_ord knows).
    Idempotent per (tag): a mark of this kind already present is left
    alone -- writing a SECOND w:del/w:ins into one rPr is XSD-invalid
    (CT_ParaRPr allows at most one of each; dxv2-3 review P1.5c wrote
    two w:del deleting an already-mark-deleted paragraph)."""
    ppr = ensure_child(p, "pPr")
    rpr = ensure_child(ppr, "rPr")
    if rpr.find(W + tag) is not None:
        return
    insert_ord(rpr, _mark(tag, ids, author))


def _field_ranges(p, name: str):
    """Sibling-run ranges of fields whose instruction contains `name`
    (stack-based: fields nest). -> list of [runs]."""
    out = []
    kids = list(p)
    k = 0
    while k < len(kids):
        el = kids[k]
        if el.tag == W + "r" and \
                el.find(f"{W}fldChar[@{W}fldCharType='begin']") is not None:
            depth, instr, seg = 0, "", []
            for m in range(k, len(kids)):
                e2 = kids[m]
                seg.append(e2)
                if e2.tag != W + "r":
                    continue
                fc = e2.find(W + "fldChar")
                ty = fc.get(W + "fldCharType") if fc is not None else None
                if ty == "begin":
                    depth += 1
                elif ty == "end":
                    depth -= 1
                    if depth == 0:
                        break
                for it in e2.iter(W + "instrText"):
                    instr += it.text or ""
            if name in instr:
                out.append((k, k + len(seg) - 1, seg))
            k += 1
            continue
        k += 1
    # innermost match wins: with nested fields, the outer span's
    # instruction CONTAINS the inner's -- deleting the outer 10-run field
    # because the target lives in the inner one is the measured failure
    inner = [x for x in out
             if not any(y is not x and x[0] <= y[0] and y[1] <= x[1]
                        for y in out)]
    return [seg for _, _, seg in inner]


def _fldsimple_to_runs(fs):
    """Replace a w:fldSimple with the equivalent complex field (begin /
    instrText / separate / result / end sibling runs). Needed because
    CT_RunTrackChange forbids fldSimple children: a tracked deletion must
    wrap RUNS -- and on accept the whole field is then really gone,
    whereas a w:del kept inside the fldSimple would leave an empty field
    shell that Word happily re-renders on the next update. Nested
    fldSimple children are converted first (depth-first).
    -> the new sibling elements, in document order."""
    for sub in list(fs.iterchildren(W + "fldSimple")):
        _fldsimple_to_runs(sub)

    def _run(child):
        r = etree.Element(W + "r")
        r.append(child)
        return r
    begin = etree.Element(W + "fldChar")
    begin.set(W + "fldCharType", "begin")
    instr = etree.Element(W + "instrText")
    instr.text = fs.get(W + "instr") or ""
    instr.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    sep = etree.Element(W + "fldChar")
    sep.set(W + "fldCharType", "separate")
    end = etree.Element(W + "fldChar")
    end.set(W + "fldCharType", "end")
    content = [c for c in fs if c.tag != W + "fldData"]
    new = [_run(begin), _run(instr), _run(sep)] + content + [_run(end)]
    parent, idx = fs.getparent(), fs.getparent().index(fs)
    for k, el in enumerate(new):
        parent.insert(idx + k, el)
    new[-1].tail, fs.tail = fs.tail, None
    parent.remove(fs)
    return new


def _near_miss(flat: str, target: str) -> str:
    """When the anchor misses, say HOW it missed. The killer case from
    the field: the document has U+2011 (non-breaking hyphen) where the
    caller typed U+002D -- two glyphs no human eye tells apart. Exact
    matching stays exact (loosening it would turn "not found" into
    "silently edited the wrong place"); the fix is to hand back the
    nearest text in the block with the first differing character NAMED
    by codepoint, so the caller corrects the anchor instead of
    bisecting by hand."""
    import difflib
    if not flat or not target:
        return ""
    win = len(target)
    grams = {flat[k:k + win] for k in range(0, max(1, len(flat) - win + 1))}
    best = difflib.get_close_matches(target, grams, n=1, cutoff=0.6)
    if not best:
        return ""
    cand = best[0]
    d = next((k for k in range(min(len(cand), len(target)))
              if cand[k] != target[k]), None)
    if d is None:
        return f"\n  nearest in block: {cand!r}"
    return (f"\n  nearest in block: {cand!r}"
            f"\n  first difference at char {d}: you wrote "
            f"U+{ord(target[d]):04X} {target[d]!r}, the document has "
            f"U+{ord(cand[d]):04X} {cand[d]!r}")


def _brief(what: str) -> str:
    """One-line summary of an applied op. `what` spells out the old and
    new text in full; on a batch that is the caller's own plan read back
    to them -- measured at 94% of the plan's token count for 26 ops.
    Keep the shape and the size, drop the payload; --verbose restores it."""
    m = re.match(r"-'(.*)' \+'(.*)'$", what, re.S)
    if m:
        old, new = m.group(1), m.group(2)
        # a replace usually rewrites a few characters inside a long
        # anchor; echoing both sides in full repeats the plan twice over.
        # Strip the shared head and tail and show only what MOVED.
        h = 0
        while h < min(len(old), len(new)) and old[h] == new[h]:
            h += 1
        t = 0
        while (t < min(len(old), len(new)) - h
               and old[len(old) - 1 - t] == new[len(new) - 1 - t]):
            t += 1
        do, dn = old[h:len(old) - t], new[h:len(new) - t]
        if h or t:
            return (f"-{len(old)}ch +{len(new)}ch  "
                    f"{_snip(do, 20)} -> {_snip(dn, 20)}"
                    + (f"  (@{h})" if h else ""))
        return f"-{len(old)}ch +{len(new)}ch {_snip(old)} -> {_snip(new)}"
    m = re.match(r"-'(.*)'$", what, re.S)
    if m:
        return f"-{len(m.group(1))}ch {_snip(m.group(1))}"
    m = re.match(r"\+'(.*)'$", what, re.S)
    if m:
        return f"+{len(m.group(1))}ch {_snip(m.group(1))}"
    return what if len(what) <= 60 else what[:57] + "..."


def _snip(t: str, n: int = 14) -> str:
    t = t.replace("\n", " ")
    return repr(t if len(t) <= n else t[:n] + "\u2026")


def _blk_text(body, sel) -> str:
    """What the target paragraph ACTUALLY holds right now -- the fact
    the caller needs and the tool already had."""
    try:
        from coords import by_id
        el = by_id(body, sel)
    except BaseException:
        return ""
    return "".join(t.text or "" for t in el.iter(W + "t", W + "delText"))


def _op_error(k: int, o, e: BaseException, body) -> str:
    """Turn a bare refusal into something the caller can act on: WHICH
    op, WHICH paragraph, WHICH anchor, and what that paragraph actually
    says right now. (The old index-drift diagnosis died with positional
    selectors: #ids do not drift, so the whole E_STALE category of
    failure no longer exists.)"""
    blk = o.after if getattr(o, "new_paragraph", None) else o.block
    target = (o.replace or o.delete or o.del_field or o.append
              or o.new_paragraph or "")
    head = f"op#{k}"
    if blk is not None:
        head += f" at[{blk}]"
    if target:
        head += f" anchor={_snip(str(target), 30)}"
    lines = [f"{head}: {e}"]
    if blk is not None:
        now = _blk_text(body, blk)
        if now:
            lines.append(f"  {blk} now reads: {_snip(now, 40)}")
    return "\n".join(lines)


_W14PARA = ("{http://schemas.microsoft.com/office/word/2010/wordml}"
            "paraId")


def _fresh_paraid(pool: set, seed: str) -> str:
    """Delegates to revreg.fresh_pid -- THE one allocator (v3.2)."""
    v = revreg.fresh_pid(seed, pool)
    pool.add(v)
    return v


def _one_op(body, ids: Ids, o, pidpool: set = None) -> tuple:
    """Validate and apply ONE op against the live tree.
    -> (op name, description). o carries the same fields as the
    CLI namespace; SystemExit on any invalid op (callers write
    nothing in that case -- transactional)."""
    ops = [o for o, v in (("replace", o.replace), ("delete", o.delete),
                          ("append", o.append),
                          ("del-paragraph", o.del_paragraph),
                          ("new-paragraph", o.new_paragraph),
                          ("del-field", o.del_field)) if v]
    if len(ops) != 1:
        raise SystemExit("E_OP_SHAPE: exactly one operation per op | got a mix or none of replace/delete/append/del_paragraph/new_paragraph/del_field")
    op = ops[0]
    if op == "new-paragraph":
        if o.after is None:
            raise SystemExit("E_OP_SHAPE: new_paragraph needs after | try: {'after': '#id', 'new_paragraph': '...'}")
    elif o.block is None:
        raise SystemExit(f"E_OP_SHAPE: {op} needs a target | try: {{'at': '#id', ...}}")
    if op == "replace" and o.with_ is None:
        raise SystemExit("E_OP_SHAPE: replace needs with | try: {'at': '#id', 'replace': OLD, 'with': NEW}")

    sel = o.after if op == "new-paragraph" else o.block
    # '#id' is the ONLY selector: the stable coordinate never drifts,
    # so batch order is free and no ordering rule exists to remember.
    from coords import by_id
    ref = by_id(body, sel)
    if ref.getparent() is not body:
        raise SystemExit(
            f"E_SCOPE: #{sel.lstrip('#')} is inside a table/header/"
            "footnote/SDT | track's text ops address body paragraphs "
            "only | try: hand-written revision per references/editing.md "
            "section 3 (tables: editing.md section 5 rule 10)")

    if op == "replace":
        seg = _span_runs(ref, o.replace, ids)
        first_run = next(el for el in seg if _is_group_run(el))
        if getattr(o, "color", None):
            # mark variant: DIRECT edit + review color, no revision
            # elements (a redline gate will honestly read it as an
            # untracked edit -- that is the point of the mode)
            parent = first_run.getparent()
            parent.insert(parent.index(first_run),
                          _new_run(o.with_, clone_from=first_run,
                                   color=o.color))
            for el in seg:
                if _is_group_run(el):
                    parent.remove(el)
            what = (f"-{o.replace!r} +{o.with_!r} in color {o.color} "
                    "(DIRECT edit, no revision)")
        else:
            dels = _wrap_del_grouped(seg, ids, o.author)
            dels[-1].addnext(_new_ins_run(o.with_, ids, o.author,
                                          clone_from=first_run))
            what = f"-{o.replace!r} +{o.with_!r}"
    elif op == "delete":
        _wrap_del_grouped(_span_runs(ref, o.delete, ids), ids, o.author)
        what = f"-{o.delete!r}"
    elif op == "append":
        clone = None
        for r, t in reversed(_runs_with_text(ref)):
            if t.strip():
                clone = r
                break
        ref.append(_new_ins_run(o.append, ids, o.author, clone_from=clone))
        what = f"+{o.append!r} (appended)"
    elif op == "del-paragraph":
        k = _wrap_all_runs_del(ref, ids, o.author)
        _mark_paragraph(ref, "del", ids, o.author)
        what = f"paragraph deleted ({k} runs + mark)"
    elif op == "new-paragraph":
        p = etree.Element(W + "p")
        src_ppr = ref.find(W + "pPr") if ref.tag == W + "p" else None
        if src_ppr is not None:
            ppr = deepcopy(src_ppr)
            for drop in ("rPr", "sectPr"):
                for e in ppr.findall(W + drop):
                    ppr.remove(e)
            # ALL revision history, RECURSIVELY via the shared registry
            # (review #10: stripping only the direct pPrChange child
            # left numPr/numberingChange alive in the copy -- another
            # document-unique id duplicated). A brand-new paragraph
            # has no history.
            revreg.strip_history(ppr)
            if len(ppr):
                p.append(ppr)
        _mark_paragraph(p, "ins", ids, o.author)
        clone = None
        if ref.tag == W + "p":
            for r, t in _runs_with_text(ref):
                if t.strip():
                    clone = r
                    break
        p.append(_new_ins_run(o.new_paragraph, ids, o.author,
                              clone_from=clone))
        # a stable #id from birth, so later ops in this or the next plan
        # can target the paragraph the tool just made; only when the
        # part already binds w14 (post-prep trees always do)
        new_pid = ""
        if pidpool is not None and "http://schemas.microsoft.com/office/"\
                "word/2010/wordml" in (body.getroottree()
                                       .getroot().nsmap or {}).values():
            new_pid = _fresh_paraid(pidpool,
                                    f"track:{o.new_paragraph[:24]}")
            p.set(_W14PARA, new_pid)
        ref.addnext(p)
        what = (f"new paragraph after [{o.after}]"
                + (f" -> #{new_pid}" if new_pid else ""))
    else:                                   # del-field
        segs = _field_ranges(ref, o.del_field)
        # fldSimple form of the same field: instruction is an ATTRIBUTE,
        # so the complex-field scan above never sees it
        simples = [fs for fs in ref.iter(W + "fldSimple")
                   if o.del_field in (fs.get(W + "instr") or "")]
        simples = [fs for fs in simples          # innermost match wins
                   if not any(d is not fs and o.del_field in
                              (d.get(W + "instr") or "")
                              for d in fs.iter(W + "fldSimple"))]
        seg_els = {id(e) for seg in segs for e in seg}
        simples = [fs for fs in simples          # already inside a matched
                   if not any(id(anc) in seg_els  # complex span
                              for anc in fs.iterancestors())]
        if not segs and not simples:
            raise SystemExit(f"no field with {o.del_field!r} in its "
                             f"instruction found in block {o.block}")
        for seg in segs:
            # whole sibling slice, not just runs: a REF result often sits
            # inside a hyperlink -- wrapping only top-level runs would
            # leave the linked half of the field alive
            _wrap_all_runs_del(ref, ids, o.author, kids=seg)
        for fs in simples:
            _wrap_all_runs_del(ref, ids, o.author,
                               kids=_fldsimple_to_runs(fs))
        what = (f"{len(segs) + len(simples)} field(s) "
                f"matching {o.del_field!r} deleted")

    return op, what


def main() -> int:
    ap = argparse.ArgumentParser(
        description="write tracked changes into an unpacked docx")
    ap.add_argument("target", help="prep.py's unpacked/ directory")
    ap.add_argument("--at", dest="block", metavar="#ID",
                    help="target paragraph by its stable #id (the view "
                         "block line's #A1B2C3D4) -- ids never drift, "
                         "batch order is free")
    ap.add_argument("--after", metavar="#ID",
                    help="insert the new paragraph after this one (#id)")
    ap.add_argument("--replace", metavar="OLD")
    ap.add_argument("--with", dest="with_", metavar="NEW")
    ap.add_argument("--delete", metavar="TEXT")
    ap.add_argument("--append", metavar="TEXT")
    ap.add_argument("--del-paragraph", action="store_true")
    ap.add_argument("--new-paragraph", metavar="TEXT")
    ap.add_argument("--del-field", metavar="NAME",
                    help="delete field(s) whose instruction contains NAME")
    ap.add_argument("--author", default="Claude")
    ap.add_argument("--color", metavar="RRGGBB",
                    help="with --replace: DIRECT edit, new text in this "
                         "color, no revision elements (marking mode)")
    ap.add_argument("--dry-run", action="store_true",
                    help="resolve every op and report, write nothing "
                         "(same code path, only the write is skipped)")
    ap.add_argument("--verbose", action="store_true",
                    help="echo each op's full old/new text (default: "
                         "a one-line summary -- the plan is not news)")
    ap.add_argument("--ops", metavar="PLAN.json",
                    help="batch mode: JSON array of op objects (same "
                         "keys as the CLI flags, hyphens or underscores);"
                         " one process, one id scan, one atomic write")
    a = ap.parse_args()

    def _sel_norm(v, name):
        """Selector: '#A1B2C3D4' -- the stable paragraph id, nothing
        else. Positional indexes were removed with the v3 break: they
        drift on insert/delete and forced a plan-ordering rule."""
        if v is None:
            return v
        s = str(v).strip()
        if re.fullmatch(r"#?[0-9A-Fa-f]{8}", s):
            return "#" + s.lstrip("#")
        raise SystemExit(f"E_SELECTOR: {name} got {v!r} | must be "
                         "'#A1B2C3D4' (8 hex, copied from the view's "
                         "block line)")

    a.block = _sel_norm(a.block, "--at")
    a.after = _sel_norm(a.after, "--after")

    if a.color:
        if not a.replace and not a.ops:
            ap.error("--color goes with --replace")
        if not re.fullmatch(r"[0-9A-Fa-f]{6}", a.color):
            ap.error("--color wants 6 hex digits, e.g. FF0000")

    if a.ops:
        import plan as _plan

        def _sel(v, name):
            try:
                return _sel_norm(v, name)
            except SystemExit as e:
                raise ValueError(str(e.code).replace("E_SELECTOR: ", ""))

        def _color(v, name):
            v = _plan.str_norm(v, name)
            if not re.fullmatch(r"[0-9A-Fa-f]{6}", v):
                raise ValueError(f"{name} wants 6 hex digits")
            return v
        op_list = _plan.load(
            a.ops,
            fields={"replace": _plan.str_norm, "with_": _plan.str_norm,
                    "delete": _plan.str_norm, "append": _plan.str_norm,
                    "new_paragraph": _plan.str_norm,
                    "del_field": _plan.str_norm,
                    "del_paragraph": _plan.bool_norm,
                    "block": _sel, "after": _sel, "color": _color,
                    "author": _plan.str_norm},
            aliases={"with": "with_", "at": "block"},
            defaults=dict(replace=None, with_=None, delete=None,
                          append=None, del_paragraph=False,
                          new_paragraph=None, del_field=None,
                          block=None, after=None, color=None,
                          author=a.author))
    else:
        op_list = [a]

    # WHICH PART: through the shared OPC resolver, so track/read/validate/
    # revisions cannot disagree about a legally-named package (dxv2-6
    # review B4). Falls back to the conventional path.
    root_dir = Path(a.target)
    names = [str(p.relative_to(root_dir)).replace("\\", "/")
             for p in root_dir.rglob("*") if p.is_file()]

    def _read(n):
        p2 = root_dir / n
        return p2.read_bytes() if p2.is_file() else None

    doc = root_dir / opc.main_part(_read, names)
    if not doc.exists():
        raise SystemExit(f"E_INPUT: missing {doc} | target must be an unpacked directory (prep.py output)")
    tree = etree.parse(str(doc))
    body = tree.getroot().find(W + "body")
    roots = [tree.getroot()]
    styles_root = None
    sp = doc.parent / "styles.xml"
    if sp.is_file():
        try:
            styles_root = etree.parse(str(sp)).getroot()
        except etree.XMLSyntaxError:
            styles_root = None
    _STYLES[0] = styles_root
    for f in sorted(doc.parent.rglob("*.xml")):
        if f == doc:
            continue
        try:                    # headers/footnotes/comments carry w:id too
            roots.append(etree.parse(str(f)).getroot())
        except etree.XMLSyntaxError:
            continue
    ids = Ids(*roots)
    first_id = ids.next_id
    # paraId pool for new paragraphs: every paraId/durableId anywhere in
    # the package, so a fresh id cannot collide (same domain rules as
    # prep's backfill)
    pidpool = {v for r in roots for el in r.iter(etree.Element)
               for k2, v in el.attrib.items()
               if k2.endswith("}paraId") or k2.endswith("}durableId")}

    # All ops apply in memory, ONE atomic write at the end: a failing op
    # anywhere leaves the file untouched (per-op invocations once meant
    # 35 processes x 35 full id scans on a single batch job). Targets
    # are #ids, which do not drift -- plan order is free by construction.
    msgs = []
    for k, o in enumerate(op_list):
        try:
            op, what = _one_op(body, ids, o, pidpool)
        except SystemExit as e:
            # WHERE, not just why. A 26-op batch used to fail with a bare
            # sentence about run fragmentation: no op index, no block, no
            # anchor, and often the wrong cause (dxv2-9 field report).
            raise SystemExit(_op_error(k, o, e, body)) from None
        msgs.append((k, op, o.after if op == "new-paragraph" else o.block,
                     what))
    if getattr(a, "dry_run", False):
        # NOT a simulation: the identical code path ran, every refusal
        # fired, only the write is skipped. A separate "preview" engine
        # would be a second implementation of apply(), free to drift.
        for k, op, blk, what in msgs:
            print(f"op#{k} at[{blk}] {op}: "
                  f"{what if a.verbose else _brief(what)}")
        print(f"dry-run OK: {len(op_list)} op(s) resolve, "
              f"would use ids {first_id}..{ids.next_id - 1} -- nothing written")
        return 0
    atomic_write_tree(tree, doc)
    for k, op, blk, what in msgs:
        # QUIET by default: the plan you just wrote is not news. Full
        # old/new text was 94% of the plan's own size on a 26-op batch.
        print(f"op#{k} block[{blk}] {op}: {what if a.verbose else _brief(what)}")
    n = ids.next_id - first_id
    print(f"({len(op_list)} op(s), ids {first_id}..{ids.next_id - 1}, "
          f"{n} revision elements)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
