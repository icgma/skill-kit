#!/usr/bin/env python3
"""Accept/reject tracked changes -- pure lxml. Only revision elements are
added or removed; every other byte stays untouched. Supports per-revision
(--id) and per-author (--author) processing.

    python scripts/revisions.py in.docx out.docx --accept
    python scripts/revisions.py in.docx out.docx --reject --author Alice
    python scripts/revisions.py in.docx out.docx --accept --id 5 --id 7

## Semantics (each row is easy to get backwards -- read first)

|                | accept                        | reject                          |
|----------------|-------------------------------|---------------------------------|
| w:ins          | unwrap (content stays)        | remove entirely                 |
| w:del          | remove entirely               | unwrap + delText->t             |
| para-mark del  | merge paragraph into next     | strip the mark                  |
| para-mark ins  | strip the mark                | merge into next (reject the "new paragraph") |
| *PrChange      | strip (new props already live)| restore the stored old props    |
| row ins/del    | strip mark / delete row       | delete row / strip mark         |
| moveFrom/To    | delete / unwrap               | unwrap / delete                 |
"""
from __future__ import annotations

import argparse
import sys
import zipfile
from copy import deepcopy
from pathlib import Path

from lxml import etree

sys.path.insert(0, str(Path(__file__).parent))
import opc  # noqa: E402  (atomic write-back)
import revreg  # noqa: E402  (shared revision taxonomy + id canon)
from ooxml_order import insert_ord  # noqa: E402

# Windows consoles/pipes default to the legacy ANSI code page (cp1252/cp936):
# the engine's CJK messages must never crash the gate there.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _unwrap(el) -> None:
    """Hoist el's children to el's position, remove el. The tail is
    preserved (some documents carry indentation text)."""
    parent = el.getparent()
    i = parent.index(el)
    kids = list(el)
    for k, c in enumerate(kids):
        parent.insert(i + k, c)
    if el.tail:
        tgt = kids[-1] if kids else (parent[i - 1] if i > 0 else None)
        if tgt is not None:
            tgt.tail = (tgt.tail or "") + el.tail
        else:
            parent.text = (parent.text or "") + el.tail
    parent.remove(el)


def _deltext_to_t(el) -> None:
    """Rejected deletion content returns to live form: delText -> t AND
    delInstrText -> instrText (forgetting the latter leaves a field whose
    instruction never executes again)."""
    for dt in el.iter(W + "delText"):
        dt.tag = W + "t"
    for dt in el.iter(W + "delInstrText"):
        dt.tag = W + "instrText"


def _merge_into_next(p) -> bool:
    """The paragraph mark was resolved away -> merge this paragraph's
    content into the next one (keeping the next paragraph's pPr)."""
    nxt = p.getnext()
    if nxt is None or nxt.tag != W + "p":
        return False        # next is not a paragraph (table/section end)
    kids = [c for c in p if c.tag != W + "pPr"]
    pos = 1 if nxt.find(W + "pPr") is not None else 0
    for k, c in enumerate(kids):
        nxt.insert(pos + k, c)
    p.getparent().remove(p)
    return True


#: *Change -> the child element holding the OLD properties. All eight
#: differ; mapping two of them wrong wipes table/section properties.
#: (tblPrExChange/tblGridChange added by review #10's registry sync:
#: the validator flags their duplicate ids, so the resolver must be
#: able to dispose of them too -- same store-old-props shape.)
_PR_OF = {"pPrChange": "pPr", "rPrChange": "rPr", "tblPrChange": "tblPr",
          "trPrChange": "trPr", "tcPrChange": "tcPr",
          "sectPrChange": "sectPr", "tblPrExChange": "tblPrEx",
          "tblGridChange": "tblGrid"}


def _restore_change(pr, change) -> None:
    """reject *PrChange: replace current props with the stored old ones.
    Non-format children that must survive:
    - rPr/sectPr inside pPr (pPrChange stores CT_PPrBase, which excludes
      both);
    - ins/del/moveFrom/moveTo inside a paragraph-mark rPr (those are
      OTHER PEOPLE'S paragraph-level revisions, not formatting; per
      CT_ParaRPr they sort before all format keys)."""
    tag = etree.QName(change).localname
    old = change.find(W + _PR_OF[tag])
    if tag == "pPrChange":
        keep_tail = [c for c in pr if c.tag in (W + "rPr", W + "sectPr")]
        keep_head = []
    elif tag == "rPrChange":
        keep_tail = []
        keep_head = [c for c in pr if c.tag in
                     (W + "ins", W + "del", W + "moveFrom", W + "moveTo")]
    else:
        keep_head, keep_tail = [], []
    for c in list(pr):
        pr.remove(c)
    for c in keep_head:
        pr.append(c)
    if old is not None:
        for c in old:
            pr.append(deepcopy(c))
    for c in keep_tail:
        pr.append(c)


#: Range markers that must be rescued before deleting a container: their
#: other half lives outside it, so deleting them along produces orphans
#: that never pair again (Word's native resolution also collapses and
#: keeps them).
_RESCUE = tuple(W + t for t in (
    "bookmarkStart", "bookmarkEnd", "commentRangeStart", "commentRangeEnd",
    "permStart", "permEnd",
    # move-range markers pair across container boundaries too: deleting a
    # container that holds one half (while the other stays) orphaned the
    # survivor -> XSD-invalid (dxv2-3 review P1.5a)
    "moveFromRangeStart", "moveFromRangeEnd",
    "moveToRangeStart", "moveToRangeEnd",
    # customXml insert/delete ranges pair the same way; omitting them
    # left "0 Start / 1 End" after a selective reject (dxv2-4 P1.7)
    "customXmlInsRangeStart", "customXmlInsRangeEnd",
    "customXmlDelRangeStart", "customXmlDelRangeEnd",
    "customXmlMoveFromRangeStart", "customXmlMoveFromRangeEnd",
    "customXmlMoveToRangeStart", "customXmlMoveToRangeEnd"))


def _remove_keep_markers(el) -> None:
    """Remove el, but hoist bookmark/comment/permission range markers
    inside it to el's position first."""
    parent = el.getparent()
    i = parent.index(el)
    for k, m in enumerate(list(el.iter(*_RESCUE))):
        m.tail = None
        parent.insert(i + k, m)
    parent.remove(el)


def process_tree(root, accept: bool, want) -> int:
    """-> number of revisions processed. want(el) decides whether one is
    touched (id/author filtering)."""
    n = 0
    # (1) row-level revisions (first: deleting a whole row subsumes the
    #     run-level revisions inside it)
    for tr in list(root.iter(W + "tr")):
        trpr = tr.find(W + "trPr")
        if trpr is None:
            continue
        for tag in ("ins", "del"):
            m = trpr.find(W + tag)
            if m is None or not want(m):
                continue
            n += 1
            kill = (tag == "del") == accept  # accept del / reject ins -> drop row
            if kill:
                _remove_keep_markers(tr)
                break
            trpr.remove(m)
    # (1b) cell-level revisions in tcPr: cellIns/cellDel mark tracked
    #      cell insertion/deletion; cellMerge tracks a vertical-merge
    #      change (old state stored in @vMergeOrig)
    for tc in list(root.iter(W + "tc")):
        tcpr = tc.find(W + "tcPr")
        if tcpr is None:
            continue
        for tag in ("cellIns", "cellDel"):
            m = tcpr.find(W + tag)
            if m is None or not want(m):
                continue
            n += 1
            kill = (tag == "cellDel") == accept
            if kill:
                _remove_keep_markers(tc)
                break
            tcpr.remove(m)
        cm = tcpr.find(W + "cellMerge")
        if cm is not None and want(cm) and cm.getparent() is not None:
            n += 1
            if not accept:               # reject: restore pre-merge state
                vm = tcpr.find(W + "vMerge")
                orig = cm.get(W + "vMergeOrig")
                if vm is not None:
                    tcpr.remove(vm)
                if orig is not None:     # cont/rest -> continue/restart
                    nv = tcpr.makeelement(W + "vMerge", {})
                    nv.set(W + "val",
                           "continue" if orig == "cont" else "restart")
                    # insert_ord, not insert(0): CT_TcPr is a strict
                    # sequence and vMerge sorts AFTER cnfStyle/tcW/
                    # gridSpan -- a hardcoded position produced XSD-
                    # invalid output on perfectly legal input
                    insert_ord(tcpr, nv)
            tcpr.remove(cm)
    # (1c) numberingChange: Word-2003 legacy display artifact recording a
    #      superseded numbering; both dispositions converge on the current
    #      state, so the marker is simply removed
    for nc in list(root.iter(W + "numberingChange")):
        if want(nc) and nc.getparent() is not None:
            n += 1
            nc.getparent().remove(nc)
    # (2) run-level containers (before paragraph marks: when a fully
    #     inserted paragraph is rejected, its ins runs must be cleared
    #     first so the mark step can see "this paragraph is empty now")
    for tag, keep_on_accept in (("ins", True), ("del", False),
                                ("moveTo", True), ("moveFrom", False)):
        for el in list(root.iter(W + tag)):
            if el.getparent() is None or not want(el):
                continue
            if el.getparent().tag == W + "rPr":
                continue                    # paragraph-mark ones: step (3)
            n += 1
            if keep_on_accept == accept:    # content stays
                if tag in ("del", "moveFrom"):
                    _deltext_to_t(el)
                _unwrap(el)
            else:
                _remove_keep_markers(el)
    # (3) paragraph marks (para-mark del / para-mark ins)
    for p in list(root.iter(W + "p")):
        rpr = p.find(f"{W}pPr/{W}rPr")
        if rpr is None:
            continue
        for tag in ("del", "ins"):
            m = rpr.find(W + tag)
            if m is None or not want(m):
                continue
            n += 1
            join = (tag == "del") == accept  # accept mark-del / reject mark-ins -> merge
            rpr.remove(m)
            if join and not _merge_into_next(p):
                # Next block is not a paragraph, cannot merge: a rejected
                # fully-inserted paragraph leaves an empty shell -> drop it.
                # But only if the parent still has another paragraph --
                # removing the last paragraph of a table cell / text box
                # produces an empty w:tc that Word refuses to open.
                parent = p.getparent()
                if parent is not None:
                    empty = not [c for c in p if c.tag != W + "pPr"]
                    has_other_p = any(sib is not p and sib.tag == W + "p"
                                      for sib in parent)
                    if empty and has_other_p:
                        parent.remove(p)
    # (4) format revisions *Change -- the list is DERIVED from the
    #     shared registry (minus numberingChange, step 1c's special
    #     case): a class added there without a _PR_OF mapping fails
    #     loudly here instead of silently not resolving
    for tag in tuple(t for t in revreg.HISTORY if t != "numberingChange"):
        for ch in list(root.iter(W + tag)):
            if not want(ch):
                continue
            n += 1
            pr = ch.getparent()
            if accept:
                pr.remove(ch)
            else:
                pr.remove(ch)
                _restore_change(pr, ch)
    # (5) move range markers (paired bookmark-style markers, meaningless
    #     after resolution). Per CT_MarkupRange the End carries no author
    #     attribute, so it cannot pass want() by itself -- process as
    #     pairs: when a Start passes the filter, remove the same-id End
    #     with it; otherwise keep the whole pair. Base names come from
    #     the shared registry, and ids pair via canon_id: a '044' Start
    #     with a '44' End is ONE pair -- raw-string matching deleted
    #     the Start and orphaned the End (review #11).
    for base in revreg.RANGE_BASES:
        # customXml*Range track the customXml WRAPPER markup, not text:
        # accepting a del-range (or rejecting an ins-range) unwraps the
        # w:customXml elements between the pair; text inside carries its
        # own w:ins/w:del handled above
        unwrap_between = base == "customXmlDelRange" and accept \
            or base == "customXmlInsRange" and not accept
        gone_ids = set()
        for st in list(root.iter(W + base + "Start")):
            if want(st) and st.getparent() is not None:
                gone_ids.add(revreg.canon_id(st.get(W + "id")))
                st.getparent().remove(st)
                n += 1          # counts toward "was this part modified" --
                #                 a marker-only part must still be written
        for en in list(root.iter(W + base + "End")):
            if en.getparent() is None:
                continue
            if revreg.canon_id(en.get(W + "id")) in gone_ids or want(en):
                if unwrap_between:
                    prev = en.getprevious()
                    while prev is not None and prev.tag == W + "customXml":
                        nxt = prev.getprevious()
                        _unwrap(prev)
                        prev = nxt
                en.getparent().remove(en)
                n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src", help=".docx, or the unpacked/ working copy "
                                "(processed IN PLACE, dst not needed)")
    ap.add_argument("dst", nargs="?",
                    help="output .docx (file mode only)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--accept", action="store_true")
    g.add_argument("--reject", action="store_true")
    ap.add_argument("--id", action="append", default=[],
                    help="process only these w:id values (repeatable)")
    ap.add_argument("--author", help="process only this author's revisions")
    a = ap.parse_args()

    # canon_id both sides: '44' and '044' are one ST_DecimalNumber
    # value -- lexical comparison would skip a legitimately-addressed
    # revision (and mirror the validator's old duplicate-scan blindness)
    ids = {revreg.canon_id(v) for v in a.id}

    def want(el) -> bool:
        if ids and revreg.canon_id(el.get(W + "id")) not in ids:
            return False
        if a.author and el.get(W + "author") != a.author:
            return False
        return True

    # canon_id passes non-numeric strings THROUGH (so they group with
    # themselves in the taxonomy); a revision-id FILTER, though, needs a
    # real decimal or the later sorted(ids) mixes str and int and
    # tracebacks (dxv2-5 review minor). Reject anything non-integer here.
    for _v in (a.id or []):
        if not isinstance(revreg.canon_id(str(_v)), int):
            raise SystemExit(f"E_SELECTOR: --id {_v!r} is not a decimal revision id")
    total = 0
    src_dir = Path(a.src)
    if src_dir.is_dir():
        # v3.2 工作流对齐:全家都在工作副本上原地干活,本工具曾要求
        # 先 pack 再处理再 unpack 回来——三步官僚为一个动词
        if a.dst:
            raise SystemExit("E_INPUT: directory mode edits in place | "
                             "drop the dst argument")
        names = [str(p.relative_to(src_dir)).replace("\\", "/")
                 for p in src_dir.rglob("*") if p.is_file()]

        def _rd(n):
            p2 = src_dir / n
            return p2.read_bytes() if p2.is_file() else None
        stories = opc.story_parts(_rd, names)
        for name in names:
            if name not in stories and not (
                    name.startswith("word/") and name.endswith(".xml")):
                continue
            if not name.endswith(".xml"):
                continue
            data = _rd(name)
            if not any(t in data for t in
                       (b":ins", b":del", b"Change", b":moveFrom",
                        b":moveTo", b":cellIns", b":cellDel",
                        b":cellMerge", b":customXml",
                        b"<ins", b"<del", b"<cell", b"<moveFrom",
                        b"<moveTo", b"<customXml")):
                continue
            root = etree.fromstring(data)
            k = process_tree(root, a.accept, want)
            if k:
                total += k
                opc.atomic_write(src_dir / name, etree.tostring(
                    root, xml_declaration=True, encoding="UTF-8",
                    standalone=True))
        if total == 0 and (ids or a.author):
            flt = " ".join([f"--id {i}" for i in sorted(ids)]
                           + ([f"--author {a.author}"]
                              if a.author else []))
            raise SystemExit(f"E_NO_MATCH: no revision matches {flt} | "
                             "nothing processed | try: check ids via "
                             "the view's revision lines")
        print(f"{'accept' if a.accept else 'reject'}: "
              f"processed {total} revision(s) in place -> {src_dir}")
        return 0
    if not a.dst:
        raise SystemExit("E_INPUT: file mode needs dst | try: "
                         "revisions.py in.docx out.docx --accept")
    with opc.CappedZip(a.src) as zin:  # capped: zip-bomb defense
        if zin.duplicates:
            # a write path must not launder an ambiguous package: read/
            # unpack already refuse, but revisions wrote through and
            # kept BOTH document.xml entries (dxv2-5 review P1.4)
            raise SystemExit(
                "package contains duplicate entries: "
                + ", ".join(zin.duplicates[:5])
                + " -- refusing to edit an ambiguous package")
        names = zin.namelist()
        # WHICH PARTS: resolved through the shared OPC resolver, not by a
        # `word/` prefix match. A legal package whose main part lives at
        # e.g. doc/main.xml used to fall through this filter entirely and
        # produce "accept: processed 0 revisions" with exit 0 -- a write
        # path reporting success while writing the input back unchanged
        # (dxv2-6 review B4).
        stories = opc.story_parts(zin.read, names)
        touched = {}
        for name in names:
            if name not in stories and not (
                    name.startswith("word/") and name.endswith(".xml")):
                continue
            if not name.endswith(".xml"):
                continue
            data = zin.read(name)
            # The sniff is a performance hint only: match localname bytes
            # in both prefixed (":ins") and default-namespace ("<ins")
            # forms -- hardcoding "w:" silently skips legal documents and
            # reports a fake "processed 0" success. Over-matching is
            # harmless -- one extra parse.
            if not any(t in data for t in
                       (b":ins", b":del", b"Change", b":moveFrom",
                        b":moveTo", b":cellIns", b":cellDel", b":cellMerge",
                        b":customXml",
                        b"<ins", b"<del", b"<cell", b"<moveFrom",
                        b"<moveTo", b"<customXml")):
                continue
            root = etree.fromstring(data)
            k = process_tree(root, a.accept, want)
            if k:
                total += k
                touched[name] = etree.tostring(
                    root, xml_declaration=True, encoding="UTF-8",
                    standalone=True)
    # A filter that matched NOTHING is an error, not a no-op success:
    # "processed 0 -> out.docx" reads as "the rejection happened" and
    # the caller ships an untouched file (review #9's hash-invariance
    # sweep). No dst is written -- failure paths leave zero artifacts.
    if total == 0 and (ids or a.author):
        flt = " ".join([f"--id {i}" for i in sorted(ids)]
                       + ([f"--author {a.author}"] if a.author else []))
        raise SystemExit(f"no revision matches {flt} -- nothing "
                         f"processed, {a.dst} not written (check ids "
                         "via read.py view / revisions in view.txt)")
    # Atomic either way: with edits the zip is rebuilt next to dst and
    # swapped in; without, the bytes are copied whole. Never a half file.
    opc.atomic_zip_rewrite(a.src, a.dst, touched)
    print(f"{'accept' if a.accept else 'reject'}: processed {total} "
          f"revisions -> {a.dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
