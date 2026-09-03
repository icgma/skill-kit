"""Effective-context model (review #13, blocker C).

Rules must not read a LOCAL attribute and call it the truth: a
table's final jc/tblInd/tblW may come from its table style's basedOn
chain, a row may override table properties through tblPrEx, and a
section without a headerReference INHERITS the previous section's
headers. Every consumer that guessed its own context produced a
false split: `jc=center` written directly was honored while the
same jc inherited from a style was invisible, so two documents that
Word lays out identically validated differently.

Centralized here:
- tbl_prop():   tblPr direct -> tblStyle basedOn chain (cycle-safe);
- row_prop():   tblPrEx (row-scoped table-property exceptions) ->
                tbl_prop fallback;
- headers_for(): section -> header/footer part names with the
                previous-section inheritance rule applied.
"""
from __future__ import annotations

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R = ("{http://schemas.openxmlformats.org/officeDocument/2006/"
     "relationships}")


def _style_by_id(styles_root, sid):
    if styles_root is None or not sid:
        return None
    for st in styles_root.iter(W + "style"):
        if st.get(W + "styleId") == sid:
            return st
    return None


def tbl_prop(tbl, styles_root, localname):
    """The EFFECTIVE tblPr child `localname` for this table: the
    direct property if present, else the first hit walking the
    tblStyle basedOn chain (cycle-guarded). None if nowhere."""
    tp = tbl.find(W + "tblPr")
    if tp is not None:
        el = tp.find(W + localname)
        if el is not None:
            return el
    sid = None
    if tp is not None:
        st_ref = tp.find(W + "tblStyle")
        if st_ref is not None:
            sid = st_ref.get(W + "val")
    seen = set()
    while sid and sid not in seen:
        seen.add(sid)
        st = _style_by_id(styles_root, sid)
        if st is None:
            return None
        stp = st.find(W + "tblPr")
        if stp is not None:
            el = stp.find(W + localname)
            if el is not None:
                return el
        based = st.find(W + "basedOn")
        sid = based.get(W + "val") if based is not None else None
    return None


def row_prop(tr, tbl, styles_root, localname):
    """Row-effective table property: trPr (row-level, e.g. row
    alignment jc per CT_TrPrBase) -> tblPrEx (the row's
    table-property exceptions, §17.4.61) -> table level."""
    trpr = tr.find(W + "trPr")
    if trpr is not None:
        el = trpr.find(W + localname)
        if el is not None:
            return el
    ex = tr.find(W + "tblPrEx")
    if ex is not None:
        el = ex.find(W + localname)
        if el is not None:
            return el
    return tbl_prop(tbl, styles_root, localname)


def headers_for(sect_list, rid_to_part):
    """[(sectPr, text_width)] in document order + {rId: part name} ->
    {part name: [text widths of every section USING that part]},
    applying §17.10.5 inheritance: a section without its own
    headerReference/footerReference of a given type uses the previous
    section's part of that type."""
    current: dict = {}
    uses: dict = {}
    for sp, tw in sect_list:
        for kind in ("headerReference", "footerReference"):
            for ref in sp.findall(W + kind):
                part = rid_to_part.get(ref.get(R + "id"))
                if part:
                    current[(kind, ref.get(W + "type") or "default")] \
                        = part
        if tw is None:
            continue
        for part in set(current.values()):
            uses.setdefault(part, []).append(tw)
    return uses
