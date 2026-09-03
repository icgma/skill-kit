#!/usr/bin/env python3
"""OOXML child-element order: **generated** from the wml.xsd shipped with the skill, never hand-copied.

    from ooxml_order import insert_ord, order_of
    insert_ord(pPr, jc_el)          # insert at the schema-correct position
    python scripts/ooxml_order.py pPr   # print the legal child order of pPr

## Why this exists

Children of `pPr` / `tcPr` / `sectPr` follow a mandatory xsd:sequence
order -- put `jc` before `spacing` and XSD validation rejects it outright.
In four real-task runs, **all four agents** hand-copied an order table
(one of them copied it wrong). Hand-copied tables rot; the schema does not.

## Usage conventions

- `insert_ord(parent, child)`: insert in schema order. The parent container
  is auto-detected (including pitfalls such as `w:pPr/w:rPr` being
  CT_ParaRPr, ordered differently from a plain `w:rPr`).
- Unknown children **raise an error listing the legal names** -- silently
  appending at the end would defer the ordering error until validate,
  where the message is worse. Pre-existing unknown siblings
  (mc:AlternateContent / w14:* extensions) sort after known elements,
  the conventional MCE position.
- `rPr` is a repeating choice in the transitional schema (order technically
  free; only rPrChange must come last), but this table still gives Word's
  customary order -- following it never hurts.
"""
from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

from lxml import etree

# Windows consoles/pipes default to the legacy ANSI code page (cp1252/cp936):
# the engine's CJK messages must never crash the gate there.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

XS = "{http://www.w3.org/2001/XMLSchema}"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
XSD = Path(__file__).parent / "schemas" / \
    "iso29500" / "wml.xsd"

#: container tag -> content model. Same-named elements have different types
#: under different parents (pPr inside `w:p` is CT_PPr, in styles it is
#: CT_PPrGeneral, in a numbering lvl it is CT_PPrBase -- the orders are
#: prefixes of one another, so the largest one suffices). Special key
#: "pPr/rPr": see _key_of.
_TYPE_OF = {
    "pPr": "CT_PPr",
    "rPr": "CT_RPr",
    "pPr/rPr": "CT_ParaRPr",       # paragraph-mark rPr: ins/del first, order differs
    "tblPr": "CT_TblPr",
    "tblPrEx": "CT_TblPrEx",
    "trPr": "CT_TrPr",
    "tcPr": "CT_TcPr",
    "sectPr": "CT_SectPr",
    "numPr": "CT_NumPr",
    "pBdr": "CT_PBdr",
    "tblBorders": "CT_TblBorders",
    "tcBorders": "CT_TcBorders",
    "lvl": "CT_Lvl",
    "style": "CT_Style",
    "settings": "CT_Settings",
    "p": "CT_P",                   # pPr must be the first child of w:p
    "tbl": "CT_Tbl",
    "tr": "CT_Row",
    "tc": "CT_Tc",
    "abstractNum": "CT_AbstractNum",
    "num": "CT_Num",
    "comment": "CT_Comment",
    "footnote": "CT_FtnEdn",
    "endnote": "CT_FtnEdn",
    "framePr": "CT_FramePr",
    "rPrChange": "CT_RPrChange",
    "pPrChange": "CT_PPrChange",
    # margin bags: CT_TblCellMar/CT_TcMar sequence is
    # top-(start|left)-bottom-(end|right) -- counter-intuitive enough
    # that WPS/hand-written XML routinely emits left-first, and the gate
    # failed a file it could have repaired (dxv2-3 C3)
    "tblCellMar": "CT_TblCellMar",
    "tcMar": "CT_TcMar",
}


@lru_cache(maxsize=1)
def _schema():
    t = etree.parse(str(XSD))
    return ({c.get("name"): c for c in t.iter(XS + "complexType")},
            {g.get("name"): g for g in t.iter(XS + "group")})


def _walk(particle, cts, groups, out, strict):
    """Particle tree -> element names expanded in order. Choice members expand
    in listed order (= Word's customary order), but record strict=False:
    relative order inside a choice is not schema-mandated."""
    for node in particle:
        tag = node.tag
        if tag == XS + "element":
            nm = node.get("name") or (node.get("ref") or "").split(":")[-1]
            if nm:
                out.append(nm)
        elif tag == XS + "sequence":
            _walk(node, cts, groups, out, strict)
        elif tag == XS + "choice":
            strict[0] = strict[0] and len(node) <= 1
            _walk(node, cts, groups, out, strict)
        elif tag == XS + "group":
            ref = (node.get("ref") or "").split(":")[-1]
            g = groups.get(ref)
            if g is not None:
                _walk(g, cts, groups, out, strict)
        elif tag in (XS + "complexContent", XS + "extension"):
            base = node.get("base")
            if base:
                bn = base.split(":")[-1]
                if bn in cts:
                    _walk(cts[bn], cts, groups, out, strict)
            _walk(node, cts, groups, out, strict)
        elif tag in (XS + "annotation", XS + "attribute",
                     XS + "attributeGroup", XS + "anyAttribute"):
            pass
        else:
            _walk(node, cts, groups, out, strict)


def _names_under(node, groups, depth=0) -> list:
    """All element names in a particle subtree (group refs expanded)."""
    if depth > 20:
        return []
    out = []
    for c in node:
        tag = c.tag
        if tag == XS + "element":
            nm = c.get("name") or (c.get("ref") or "").split(":")[-1]
            if nm:
                out.append(nm)
        elif tag == XS + "group":
            g = groups.get((c.get("ref") or "").split(":")[-1])
            if g is not None:
                out += _names_under(g, groups, depth + 1)
        elif tag in (XS + "annotation", XS + "attribute",
                     XS + "attributeGroup", XS + "anyAttribute"):
            pass
        else:
            out += _names_under(c, groups, depth + 1)
    return out


@lru_cache(maxsize=None)
def segment_map(key: str) -> dict:
    """name -> mandated-order SEGMENT rank. Every slot of a sequence is
    its own segment; everything under one choice shares one segment.
    The schema mandates order BETWEEN segments and never inside a
    choice -- so sorting by segment enforces exactly the skeleton
    (ins/del before format keys, rPrChange last, cellMerge after tcW)
    while legal intra-choice arrangements are never touched (the strict
    flag was too coarse: one choice anywhere marked the whole container
    unsortable, skipping tcPr/sectPr -- the measured violation sites)."""
    tname = _TYPE_OF.get(key)
    if tname is None:
        return {}
    cts, groups = _schema()
    ct = cts.get(tname)
    if ct is None:
        return {}
    seg: dict = {}
    counter = [0]

    def walk(node, depth=0):
        if depth > 20:
            return
        for c in node:
            tag = c.tag
            if tag == XS + "element":
                nm = c.get("name") or (c.get("ref") or "").split(":")[-1]
                if nm:
                    counter[0] += 1
                    seg.setdefault(nm, counter[0])
            elif tag == XS + "choice":
                counter[0] += 1
                s = counter[0]
                for nm in _names_under(c, groups):
                    seg.setdefault(nm, s)
            elif tag == XS + "sequence":
                walk(c, depth + 1)
            elif tag == XS + "group":
                g = groups.get((c.get("ref") or "").split(":")[-1])
                if g is not None:
                    walk(g, depth + 1)
            elif tag in (XS + "complexContent", XS + "extension"):
                base = c.get("base")
                if base and base.split(":")[-1] in cts:
                    walk(cts[base.split(":")[-1]], depth + 1)
                walk(c, depth + 1)
            elif tag in (XS + "annotation", XS + "attribute",
                         XS + "attributeGroup", XS + "anyAttribute"):
                pass
            else:
                walk(c, depth + 1)
    walk(ct)
    return seg


@lru_cache(maxsize=None)
def order_of(key: str):
    """Container key ('pPr' / 'pPr/rPr' / ...) -> (ordered element names, whether schema-mandated)."""
    tname = _TYPE_OF.get(key)
    if tname is None:
        raise KeyError(f"no content model for {key!r}. Known containers: "
                       f"{sorted(_TYPE_OF)}")
    cts, groups = _schema()
    ct = cts.get(tname)
    if ct is None:
        raise KeyError(f"{tname} not found in wml.xsd")
    out: list = []
    strict = [True]
    _walk(ct, cts, groups, out, strict)
    seen, dedup = set(), []
    for nm in out:
        if nm not in seen:
            seen.add(nm)
            dedup.append(nm)
    return dedup, strict[0]


def _key_of(parent) -> str:
    """Element -> container key. Detects same-name-different-type cases like `w:pPr/w:rPr` (CT_ParaRPr)."""
    tag = parent.tag.split("}")[-1]
    if tag == "rPr":
        pp = parent.getparent()
        if pp is not None and pp.tag == W + "pPr":
            return "pPr/rPr"
    return tag


def rank_map(key: str) -> dict:
    names, _ = order_of(key)
    return {nm: i for i, nm in enumerate(names)}


def insert_ord(parent, child) -> None:
    """Insert child into parent at the **schema-correct position**.

    Placement rule: insert after the last known sibling whose rank <= own.
    Unknown siblings (mc:/w14: extensions) are treated as rank +inf (MCE
    convention: at the tail). If the child's name is not in the content
    model -> raise an error listing the legal names, **never append
    silently** (a silent append defers the ordering error until validate,
    where it is harder to read).
    """
    key = _key_of(parent)
    ranks = rank_map(key)
    cn = child.tag.split("}")[-1]
    if cn not in ranks:
        names, _ = order_of(key)
        raise ValueError(
            f"<w:{cn}> is not in the content model of <w:{key}>. "
            "Legal children (in order): "
            + " ".join(names))
    r = ranks[cn]
    pos = 0
    for i, sib in enumerate(parent):
        if sib is child:
            # RE-ORDERING an element already attached is the common
            # shape (SubElement(...) then insert_ord). Counting the
            # child as its own predecessor set pos to "just after
            # myself", so the move was a no-op and the element stayed
            # wherever it was appended -- every caller then hit an
            # ordering violation at validate and "fixed" it with
            # --repair, hiding the bug behind a workaround (measured on
            # a real formatting task: three insertions, three failures).
            continue
        sn = sib.tag.split("}")[-1] if isinstance(sib.tag, str) else ""
        if ranks.get(sn, 1 << 30) <= r:
            pos = i + 1
    parent.insert(pos, child)


#: Containers whose children may be SORTED into schema order (repair
#: side). Property bags only: each child is a property, order carries no
#: meaning beyond the schema sequence. Content models (w:p, w:tr, w:body,
#: w:comment...) are deliberately ABSENT -- there, position IS semantics:
#: a bookmarkStart between two runs anchors exactly there, and "fixing"
#: its order tears the range (measured flaw in a prior implementation
#: that reordered every XSD type).
SORTABLE = ("pPr", "rPr", "tblPr", "tblPrEx", "trPr", "tcPr", "sectPr",
            "numPr", "pBdr", "tblBorders", "tcBorders", "lvl", "style",
            "settings", "abstractNum", "num", "framePr",
            # margin bags: CT_TblCellMar's sequence is top-start-bottom-end,
            # which nobody writes from memory (WPS emits left-first and the
            # gate failed a file it could have fixed)
            "tblCellMar", "tcMar")


def sort_children(parent) -> bool:
    """Stable-sort a PROPERTY CONTAINER's children into schema order --
    the repair-side counterpart of insert_ord (same rank table, same
    parent-path awareness: a paragraph-mark rPr sorts as CT_ParaRPr).
    Unknown children (mc:/w14: extensions) keep their relative order
    after the known ones, the conventional MCE position. Sorting is by
    SEGMENT (see segment_map): the mandated skeleton is enforced,
    intra-choice arrangements are never rewritten -- zero churn on
    legal files, which also keeps this idempotent and repair-honest
    ("N fixed" means N real violations, not N normalizations).
    -> True when the order changed. Callers gate on SORTABLE."""
    segs = segment_map(_key_of(parent))
    if not segs:
        return False
    kids = list(parent)

    def rk(el):
        if not isinstance(el.tag, str):
            return (2, 0)                     # comments/PIs: stay last
        nm = el.tag.split("}")[-1]
        return (0, segs[nm]) if nm in segs else (1, 0)
    ordered = sorted(kids, key=rk)            # stable: ties keep order
    if ordered == kids:
        return False
    for c in ordered:                         # append MOVES in lxml
        parent.append(c)
    return True


def ensure_child(parent, tag: str):
    """Return the child if present, otherwise **create** it in schema order
    (`ensure_child(p, "pPr")` puts pPr first in w:p -- it must be the
    first child)."""
    el = parent.find(W + tag)
    if el is None:
        el = parent.makeelement(W + tag, {})
        insert_ord(parent, el)
    return el


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        print("available containers:", " ".join(sorted(_TYPE_OF)))
        return 0
    key = sys.argv[1].removeprefix("w:")
    names, strict = order_of(key)
    print(f"<w:{key}> -- "
          f"{'schema-mandated order' if strict else 'Word customary order (choice, advisory only)'}")
    for i, nm in enumerate(names):
        print(f"  {i:3d} {nm}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
