"""WHAT we check: every semantic rule beyond XSD, one function per
domain -- split from the god-file so a check's blast radius is visible."""
from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path, PurePosixPath

from lxml import etree

sys.path.insert(0, str(Path(__file__).parent))
import measure  # noqa: E402  (the one measurement value model)
import opc  # noqa: E402  (the one relationship resolver + atomic IO)
import revreg  # noqa: E402  (the one revision taxonomy + id canon)
import walker  # noqa: E402  (the one logical WML traversal)
import effctx  # noqa: E402  (the one effective-context model)
from findings import Finding  # noqa: E402
from vpkg import (Pkg, MC, W, R, W15, W16CID, W16CEX,  # noqa: E402
                  CT_NS, REL_NS, _guarded, _ids)


# ---------- Semantic checks ----------

def _table_width_findings(pkg: Pkg, trees: dict) -> list:
    """-> warns: table-width analysis, container-aware, WARN-ONLY.

    Model (review #9, precedence #11, effective context #13):
    occupied() follows the fixed-width algorithm's source precedence
    -- effective tblW[dxa] (direct or style-inherited) dominates, else
    pct, else grid / per-column constraints over all logical rows; +
    effective tblInd under §17.4.50 alignment semantics. Containers:
    body tables get their OWNING section's text width divided by its
    columns; nested tables the host cell's tcW[dxa]; header/footer
    tables the text width of sections USING that part (reference OR
    §17.10.5 inheritance); foot/endnote tables any section's. Unequal
    columns are flow-dependent, so exceeding the WIDEST column is
    overflow, exceeding only the narrowest a may-overflow; floating
    tables (tblpPr) escape column flow.

    EVERY finding here is a warning, --gen included (blocker D): the
    solver is an estimator, not Word's layout algorithm, and engines
    demonstrably diverge (LibreOffice rescales case-15's 2784pt fixed
    table into the page; Word overflows it). Estimator output must
    never feed a hard failure -- the render gate owns layout verdicts.
    Hard failures in this validator are reserved for locally PROVABLE
    facts (broken pairs, duplicate ids, dangling relationships)."""
    W_ = f"{{{W}}}"
    warns: list = []

    def _i(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    def sect_tw(sp):
        # page size / margins are ST_(Signed)TwipsMeasure: universal
        # measures ("8.5in") are legal lexicals -- measure.twips, not
        # int() (blocker A: no scattered measurement parsing)
        sz, mar = sp.find(W_ + "pgSz"), sp.find(W_ + "pgMar")
        if sz is None or mar is None:
            return None
        w = measure.twips(sz.get(W_ + "w"))
        lm = measure.twips(mar.get(W_ + "left")
                           or mar.get(W_ + "start"))
        rm = measure.twips(mar.get(W_ + "right")
                           or mar.get(W_ + "end"))
        if None in (w, lm, rm):
            return None
        return w - lm - rm - (measure.twips(mar.get(W_ + "gutter"))
                              or 0)

    def col_span(sp, tw):
        """-> (narrowest, widest) column width of the section."""
        cols = sp.find(W_ + "cols")
        if cols is None or tw is None:
            return (tw, tw)
        explicit = [x for x in (measure.twips(c.get(W_ + "w"))
                                for c in cols.findall(W_ + "col")) if x]
        if explicit:
            return (min(explicit), max(explicit))
        num = _i(cols.get(W_ + "num")) or 1
        if num > 1:
            space = measure.twips(cols.get(W_ + "space"))
            each = (tw - (720 if space is None else space) * (num - 1)) \
                // num
            return (each, each)
        return (tw, tw)

    def _grid_cols(tbl):
        """Column-constraint estimate over ALL rows (review #12: the
        max-row-sum shortcut missed staggered constraints -- rows
        [5000,1000] and [1000,5000] both sum 6000, but the SHARED grid
        columns need max(5000,1000)+max(1000,5000) = 10000). Single-
        column cells raise their column's running max; spanning
        constraints (gridSpan cells, wBefore over the gridBefore
        columns, wAfter) then distribute any deficit across their
        span. An estimate, not a layout engine -- a lower bound is
        what an overflow detector needs."""
        cols: dict = {}
        spans: list = []
        for tr in walker.rows(tbl):    # LOGICAL rows: an sdt-wrapped
            #                            w:tr is still a row (blocker B)
            trpr = tr.find(W_ + "trPr")
            gb = 0
            if trpr is not None:
                g = trpr.find(W_ + "gridBefore")
                gb = _i(g.get(W_ + "val")) or 0 if g is not None else 0
                wb = measure.dxa(trpr.find(W_ + "wBefore"))
                if gb > 0 and wb:   # wBefore is the WIDTH OF the
                    #                 gridBefore columns; without them
                    #                 it constrains nothing (a max(gb,1)
                    #                 pseudo-column collided with the
                    #                 row's real first column)
                    spans.append((0, gb, wb))
            cur = gb
            for c in walker.cells(tr):
                gs = c.find(f"{W_}tcPr/{W_}gridSpan")
                k = (_i(gs.get(W_ + "val")) or 1) if gs is not None \
                    else 1
                k = max(k, 1)
                w = measure.dxa(c.find(f"{W_}tcPr/{W_}tcW"))
                if w:
                    if k == 1:
                        cols[cur] = max(cols.get(cur, 0), w)
                    else:
                        spans.append((cur, k, w))
                cur += k
            if trpr is not None:
                ga = trpr.find(W_ + "gridAfter")
                na = _i(ga.get(W_ + "val")) or 0 if ga is not None else 0
                wa = measure.dxa(trpr.find(W_ + "wAfter"))
                if na > 0 and wa:           # same rule as wBefore
                    spans.append((cur, na, wa))
        for c0, k, w in sorted(spans, key=lambda s: s[2]):
            got = sum(cols.get(i, 0) for i in range(c0, c0 + k))
            if got < w:
                add, rem = divmod(w - got, k)
                for i in range(c0, c0 + k):
                    cols[i] = cols.get(i, 0) + add
                cols[c0] = cols.get(c0, 0) + rem
        return sum(cols.values())

    styles_t = None
    if "word/styles.xml" in pkg.names:
        try:
            styles_t = pkg.xml("word/styles.xml")
        except etree.XMLSyntaxError:
            styles_t = None

    def occupied(tbl):
        """-> (base, pct, ind).
        base: dxa width claim EXCLUSIVE of indent (0 = no dxa claim);
        pct:  fraction of the container (1.5 == 150%) or None. The
              CALLER converts against ITS container -- a pct claim has
              no absolute width until a container is known (dropping
              it lost the 100%-plus-indent overflow, review #12);
        ind:  tblInd twips, ALREADY ZEROED unless the final alignment
              is leading-edge -- §17.4.50: tblInd is ignored when jc
              is center/right/end (a centered tblW=6000 with a stale
              3000 indent hard-failed at 9000 while LibreOffice
              centers it at exactly 6000).
        Source precedence per the fixed-width algorithm (#11), widths
        via the shared measure parser (#12), and every property is
        the EFFECTIVE one -- direct tblPr or inherited through the
        tblStyle basedOn chain (blocker C: a style-inherited
        jc=center used to validate differently from the identical
        direct attribute): explicit tblW[dxa] -> tblW[pct] -> grid /
        per-column constraints from ALL logical rows."""
        ind = measure.dxa(effctx.tbl_prop(tbl, styles_t, "tblInd"))
        jc = effctx.tbl_prop(tbl, styles_t, "jc")
        if jc is not None and jc.get(W_ + "val") in (
                "center", "right", "end"):
            ind = 0
        if ind:
            # row-level alignment (trPr/jc, tblPrEx/jc -- effctx.
            # row_prop): when EVERY row's effective alignment is
            # non-leading, no row is pushed by tblInd (review #14:
            # Word centers such tables; we warned 10000 > 9360)
            rws = list(walker.rows(tbl))
            if rws and all(
                    (j := effctx.row_prop(tr, tbl, styles_t, "jc"))
                    is not None and j.get(W_ + "val") in
                    ("center", "right", "end") for tr in rws):
                ind = 0
        kind, val = measure.parse(
            effctx.tbl_prop(tbl, styles_t, "tblW"))
        if kind == "dxa" and val > 0:       # 1. explicit total width
            return (val, None, ind)
        if kind == "pct":                   # 2. fraction of container
            return (0, val, ind)
        grid = sum(x for x in (measure.twips(g.get(W_ + "w")) for g in
                               tbl.findall(f"{W_}tblGrid/{W_}gridCol"))
                   if x)
        return (max(grid, _grid_cols(tbl)), None, ind)

    def is_floating(tbl):
        return tbl.find(f"{W_}tblPr/{W_}tblpPr") is not None

    def report(part, where, occ, mn, mx):
        # WARN only, --gen included (blocker D): the solver is an
        # ESTIMATOR -- it does not implement Word's full layout
        # algorithm, and engines demonstrably diverge (case-15's
        # official 2784pt fixed table renders inside the page in
        # LibreOffice). An estimator's output must never feed a hard
        # failure; the render gate owns the verdict.
        # identity excludes the MEASURED numbers: they move whenever an
        # unrelated edit shifts a column, so a string-keyed warning read
        # as brand new every time and baseline monotonicity never closed
        # (dxv2-4 review P2). Identity = (part, normalized site, kind).
        # identity keeps the table's OWN declared width (a property of
        # the table, stable under unrelated edits) and drops only the
        # CONTEXTUAL measurements (container/column widths move when
        # page setup or neighbours change). Dropping the width too made
        # two different overwide tables one finding, so a baseline table
        # masked a brand-new one.
        site = re.sub(r"body\[\d+\]", "body[*]", str(where))
        if mx and occ > mx:
            warns.append(Finding(
                "TBLW_OVER", (part, site, "over-container", occ),
                f"{part} table {where}: declared width {occ} dxa "
                f"(tblW/ΣgridCol+tblInd) exceeds its container "
                f"({mx} dxa) -- engines may rescale or overflow; "
                "CONFIRM via render", severity="warn"))
        elif mn and mx and mn < mx and occ > mn:
            warns.append(Finding(
                "TBLW_OVER", (part, site, "over-narrowest", occ),
                f"{part} table {where}: declared width {occ} dxa fits "
                f"the widest column ({mx} dxa) but not the narrowest "
                f"({mn} dxa) of its unequal-column section -- column "
                "placement is flow-dependent; CONFIRM via render",
                severity="warn"))

    # ---- sections of document.xml (in document order) ----
    doc_t = trees.get("word/document.xml")
    body_el = doc_t.find(W_ + "body") if doc_t is not None else None
    sect_marks: list = []           # (block index, sectPr element)
    if body_el is not None:
        for bi, el in enumerate(body_el):
            if el.tag == W_ + "sectPr":
                sect_marks.append((bi, el))
                continue
            # pPr/sectPr anywhere under this direct child: a legally
            # SDT-wrapped section-break paragraph must not shift
            # section ownership (review #14). sectPr is only legal on
            # body-level paragraphs, so an el.iter scan cannot pick up
            # cell-level noise.
            for sp in el.iter(W_ + "sectPr"):
                if sp.getparent() is not None and \
                        sp.getparent().tag == W_ + "pPr":
                    sect_marks.append((bi, sp))
    all_tw = [t for t in (sect_tw(sp) for _, sp in sect_marks)
              if t is not None]

    # ---- unified table iterator (review #10). Walking body DIRECT
    # children skipped sdt/customXml-wrapped tables entirely; the
    # nested-in-cell check ran only for document.xml; pct>100% and
    # nested overflow reached warns but not gen_bad. One classifier
    # for every w:tbl in every story tree:
    #   * inside w:txbxContent -> skip (a text box's extent is the
    #     shape's, statically unknowable -- render gate's job);
    #   * nearest w:tc ancestor -> NESTED: host cell's dxa tcW governs;
    #   * else TOP-LEVEL: in document.xml the owning section of the
    #     BODY-CHILD ANCESTOR (penetrates any wrapper); elsewhere the
    #     part's container.
    def classify(t_root):
        for el in t_root.iter(W_ + "tbl"):
            if next((a for a in el.iterancestors(W_ + "txbxContent")),
                    None) is not None:
                continue
            host = next((a for a in el.iterancestors(W_ + "tc")), None)
            yield el, host

    def check_pct(part, where, pct):
        if pct and pct > 1:
            warns.append(f"{part} table {where}: tblW="
                         f"{measure.fmt_pct(pct)} of its container "
                         "(pct > 100%) -- CONFIRM via render")

    def eff_width(base, pct, ind, container):
        """The table's absolute claim against a KNOWN container: a pct
        claim converts here (100% + a leading-edge indent overflows,
        review #12), a dxa claim just adds the indent."""
        if pct is not None:
            return int(pct * container) + ind if container else 0
        return base + ind if base else 0

    def check_nested(part, el, host):
        cw = measure.dxa(host.find(f"{W_}tcPr/{W_}tcW"))
        base, pct, ind = occupied(el)
        check_pct(part, "(nested)", pct)
        if not cw:
            return
        occ = eff_width(base, pct, ind, cw)
        if occ and occ > cw:
            warns.append(
                f"{part} nested table: declared width {occ} dxa "
                f"exceeds its host cell (tcW={cw} dxa) -- CONFIRM "
                "via render")

    if body_el is not None:
        for el, host in classify(body_el):
            if host is not None:
                check_nested("word/document.xml", el, host)
                continue
            node = el                   # ascend to the body direct child
            while node.getparent() is not None \
                    and node.getparent() is not body_el:
                node = node.getparent()
            if node.getparent() is not body_el:
                continue
            bi = body_el.index(node)
            base, pct, ind = occupied(el)
            check_pct("word/document.xml", f"at body[{bi}]", pct)
            sp = next((s for j, s in sect_marks if bi <= j), None)
            tw = sect_tw(sp) if sp is not None else None
            if tw is None:
                continue
            mn, mx = (tw, tw) if is_floating(el) else col_span(sp, tw)
            occ = eff_width(base, pct, ind, mx)
            if not occ:
                continue
            report("word/document.xml", f"at body[{bi}]", occ, mn, mx)

    # ---- header/footer parts: sections that reference them ----
    ref_of: dict = {}               # part name -> [text widths]
    rels = None
    try:
        if "word/_rels/document.xml.rels" in pkg.names:
            rels = pkg.xml("word/_rels/document.xml.rels")
    except etree.XMLSyntaxError:
        rels = None
    if rels is not None:
        # rel targets resolve through opc (percent-decode, escape
        # guard) -- part NAMES carry no meaning of their own: a header
        # renamed to word/whatever.xml with its relationship intact
        # must validate identically (blocker C)
        rid_to = {}
        for rel in rels.iter("{*}Relationship"):
            tgt = rel.get("Target")
            if not tgt:
                continue
            try:                # KEYWORD-ONLY: this exact call once had
                #                 the two strings reversed and resolved
                #                 every header to document.xml itself
                #                 (review #14) -- masked by a fallback
                #                 that is now also gone
                rid_to[rel.get("Id")] = opc.rel_target(
                    target=tgt, base_part="word/document.xml")
            except ValueError:
                continue        # escape/malformed: the rels pass's find
        # §17.10.5 inheritance: a section WITHOUT its own reference of
        # a type uses the previous section's part -- effctx owns the
        # rule (a narrow later section with no headerReference used to
        # escape its inherited header's width check entirely)
        ref_of = effctx.headers_for(
            [(sp, sect_tw(sp)) for _, sp in sect_marks], rid_to)
    kinds = story_kinds(pkg)
    for n, t in trees.items():
        if kinds.get(n) == "document":
            continue
        if kinds.get(n) in ("header", "footer"):
            # NO fallback (review #14): "or all_tw" made a broken
            # relationship resolution invisible -- an unresolvable or
            # unreferenced header has no known container, and guessing
            # one masks resolver bugs (it hid a reversed-argument call
            # completely). Orphanhood is its own finding elsewhere.
            tws = ref_of.get(n)
        else:                               # footnotes/endnotes: any
            tws = all_tw
        container = min(tws) if tws else None
        for el, host in classify(t):
            if host is not None:
                check_nested(n, el, host)
                continue
            base, pct, ind = occupied(el)
            check_pct(n, "", pct)
            if container is None:
                continue
            occ = eff_width(base, pct, ind, container)
            if occ and occ > container:
                warns.append(
                    f"{n} table: declared width {occ} dxa exceeds the "
                    f"text width ({container} dxa) of a section using "
                    "this part -- CONFIRM via render")
    # ---- invalid measurements: SURFACED, never silently dropped
    # (blocker A totality: an unparseable width is a finding, not a
    # skip -- "abc" in tblW used to vanish from every report)
    for n, t in trees.items():
        seen_raw: set = set()
        for tag in ("tblW", "tcW", "tblInd", "wBefore", "wAfter",
                    "tblCellSpacing"):
            for el in t.iter(f"{{{W}}}{tag}"):
                kind, val = measure.parse(el)
                if kind == "invalid" and val[0] not in seen_raw:
                    seen_raw.add(val[0])
                    warns.append(
                        f"{n} {tag}: unparseable measurement "
                        f"{val[0]!r} ({val[1]}) -- ignored by width "
                        "analysis; fix the value")
    return warns


def story_kinds(pkg: Pkg) -> dict:
    """{part name: kind} for WML story parts, kind in {document,
    header, footer, footnotes, endnotes}.

    PRIMARY source: the OPC relationship graph (review #14) --
    /_rels/.rels names the document part, whose own rels type every
    header/footer/notes part. Part names and extensions carry no
    semantics: a header stored as word/anything.custom with its
    relationship intact is still a header, and must be read,
    redlined and width-checked as one. [Content_Types].xml overrides
    and the classic name pattern are additive fallbacks for broken
    packages only."""
    kinds: dict = {}
    R = ("http://schemas.openxmlformats.org/officeDocument/2006/"
         "relationships/")

    def rels_of(part):
        rn = ("_rels/.rels" if part == "" else str(
            PurePosixPath(part).parent / "_rels"
            / (PurePosixPath(part).name + ".rels")))
        if rn not in pkg.names:
            return []
        try:
            return list(pkg.xml(rn).iter("{*}Relationship"))
        except etree.XMLSyntaxError:
            return []

    doc = None
    for rel in rels_of(""):
        if (rel.get("Type") or "").endswith("/officeDocument"):
            try:
                doc = opc.rel_target(target=rel.get("Target"),
                                     base_part="")
            except ValueError:
                doc = None
    if doc and doc in pkg.names:
        kinds[doc] = "document"
        for rel in rels_of(doc):
            ty = rel.get("Type") or ""
            for kind in ("header", "footer", "footnotes", "endnotes",
                         "comments"):
                if ty == R + kind:
                    try:
                        name = opc.rel_target(target=rel.get("Target"),
                                              base_part=doc)
                    except ValueError:
                        continue
                    if name and name in pkg.names:
                        kinds.setdefault(name, kind)
    suffix_kind = ((".document.main+xml", "document"),
                   (".header+xml", "header"), (".footer+xml", "footer"),
                   (".footnotes+xml", "footnotes"),
                   (".endnotes+xml", "endnotes"))
    if "[Content_Types].xml" in pkg.names:
        try:
            ct = pkg.xml("[Content_Types].xml")
            for ov in ct.iter("{*}Override"):
                pn = (ov.get("PartName") or "").lstrip("/")
                c = ov.get("ContentType") or ""
                for suf, kind in suffix_kind:
                    if c.endswith(suf) and pn in pkg.names:
                        kinds.setdefault(pn, kind)
        except etree.XMLSyntaxError:
            pass
    for n in pkg.names:         # last resort: bare unpacked trees
        m = re.fullmatch(r"word/(document|header\d+|footer\d+|"
                         r"footnotes|endnotes)\.xml", n)
        if m and n not in kinds:
            kinds[n] = re.sub(r"\d+$", "", m.group(1))
    return kinds


def _part_tree(pkg, name):
    """None when absent OR malformed. Every name passed here is a
    word/*.xml part, whose parse failure the XSD pass has already
    recorded as a violation -- so degrade silently instead of
    tracebacking past the gate (or double-reporting)."""
    if name not in pkg.names:
        return None
    try:
        return pkg.xml(name)
    except etree.XMLSyntaxError:
        return None


def check_comment_integrity(pkg, kinds, trees, bad):
    # Comments (all four related parts). The MAIN comments part is
    # resolved via the relationship graph, not hardcoded: a renamed
    # comments part made read.py show the body while the validator
    # reported "comment does not exist" -- reader and validator must
    # agree on where it lives (dxv2-5 review P1.3).
    c_ids = set()
    _cmt_name = next((n for n, k in kinds.items() if k == "comments"),
                     "word/comments.xml")
    ct_comments = _part_tree(pkg, _cmt_name)
    if ct_comments is not None:
        lst = [revreg.canon_id(v) for v in _ids(ct_comments, "comment")]
        dup = [k for k, v in Counter(lst).items() if v > 1]
        if dup:
            bad.append(Finding(
                "CMT_DUP_ID", ("comments.xml",),
                "comments.xml duplicate comment ids: "
                f"{sorted(dup, key=str)}", count=len(dup)))
        c_ids = set(lst)
    for n, t in trees.items():
        refs = {revreg.canon_id(v)
                for v in _ids(t, "commentReference")}
        # Multiset comparison: a plain set would treat malformed duplicate
        # ids ("two starts paired with one end") as successfully paired
        rs = Counter(revreg.canon_id(v)
                     for v in _ids(t, "commentRangeStart"))
        re_ = Counter(revreg.canon_id(v)
                      for v in _ids(t, "commentRangeEnd"))
        for v in sorted(set(rs) | set(re_), key=str):
            if rs[v] != re_[v]:
                bad.append(Finding(
                    "CMT_RANGE_UNPAIRED", (n, str(v)),
                    f"{n} commentRange id={v}: {rs[v]} start(s) / "
                    f"{re_[v]} end(s)", count=rs[v] + re_[v]))
        for miss in (refs | set(rs)) - c_ids:
            bad.append(f"{n} references nonexistent comment id={miss}")
    # Four-part chain consistency: comments.xml paraIds are the hub --
    # commentsExtended/commentsIds must point at them, and every
    # commentsExtensible durableId must exist in commentsIds. Four
    # internally-plausible but mutually-inconsistent parts used to pass.
    W14_ = "http://schemas.microsoft.com/office/word/2010/wordml"
    if ct_comments is not None:
        para_ids = {p.get(f"{{{W14_}}}paraId")
                    for c in ct_comments.iter(f"{{{W}}}comment")
                    for p in c.iter(f"{{{W}}}p")} - {None}
        ct_ext = _part_tree(pkg, "word/commentsExtended.xml")
        if ct_ext is not None and para_ids:
            for ce in ct_ext.iter(f"{{{W15}}}commentEx"):
                v = ce.get(f"{{{W15}}}paraId")
                if v and v not in para_ids:
                    bad.append("commentsExtended paraId="
                               f"{v} matches no comment paragraph")
        dur_ids = set()
        ct_cid = _part_tree(pkg, "word/commentsIds.xml")
        if ct_cid is not None:
            seen_d: set = set()

            def _dcanon(x):
                try:
                    return int(x, 16)
                except (TypeError, ValueError):
                    return x
            for ci in ct_cid.iter(f"{{{W16CID}}}commentId"):
                v = ci.get(f"{{{W16CID}}}paraId")
                d = ci.get(f"{{{W16CID}}}durableId")
                if v and para_ids and v not in para_ids:
                    bad.append(f"commentsIds paraId={v} matches no "
                               "comment paragraph")
                if d:
                    dc = _dcanon(d)          # 'A' == 'a' == 0x0A to Word
                    if dc in seen_d:
                        bad.append(f"commentsIds duplicate durableId={d}"
                                   " (case-insensitive hex; Word merges "
                                   "the two comments)")
                    seen_d.add(dc)
                    dur_ids.add(d)
        ct_cex = _part_tree(pkg, "word/commentsExtensible.xml")
        if ct_cex is not None and dur_ids:
            for ce in ct_cex.iter(f"{{{W16CEX}}}commentExtensible"):
                d = ce.get(f"{{{W16CEX}}}durableId")
                if d and _dcanon(d) not in {_dcanon(x) for x in dur_ids}:
                    bad.append(f"commentsExtensible durableId={d} "
                               "not in commentsIds (compared as "
                               "case-insensitive hex)")
    for part, tag, ns in (("word/commentsIds.xml", "commentId", W16CID),
                          ("word/commentsExtensible.xml",
                           "commentExtensible", W16CEX)):
        pt = _part_tree(pkg, part)
        if pt is not None:
            for e in pt.iter(f"{{{ns}}}{tag}"):
                d = e.get(f"{{{ns}}}durableId")
                try:
                    if d and int(d, 16) > 0x7FFFFFFF:
                        bad.append(f"{part} durableId={d} exceeds "
                                   "0x7FFFFFFF (Word cannot open it)")
                except ValueError:   # not hex: report violation, don't crash
                    bad.append(f"{part} durableId={d!r} is not valid hex")


def check_revision_semantics(trees, bad, warn):
    # Revision semantics (XSD-valid but Word misbehaves; formerly
    # gotchas-doc knowledge, now machine-checkable)
    for n, t in trees.items():
        for wrap in ("del", "moveFrom"):
            for el in t.iter(f"{{{W}}}{wrap}"):
                if el.getparent().tag == f"{{{W}}}rPr":
                    continue                # paragraph-mark del has no text
                loc = f" (w:id={el.get(f'{{{W}}}id')})" \
                    if el.get(f"{{{W}}}id") else ""
                k = sum(1 for _ in el.iter(f"{{{W}}}t"))
                if k:
                    bad.append(Finding(
                        "DEL_WT", (n, wrap, loc, "t"),
                        f"{n} w:{wrap}{loc} contains w:t (must be "
                        "w:delText, otherwise deleted text still "
                        "displays)", count=k))
                ki = sum(1 for _ in el.iter(f"{{{W}}}instrText"))
                if ki:
                    bad.append(Finding(
                        "DEL_WT", (n, wrap, loc, "instr"),
                        f"{n} w:{wrap}{loc} contains w:instrText "
                        "(must be w:delInstrText)", count=ki))
        # w:delText inside w:ins that is NOT inside a nested w:del:
        # insertion of already-deleted text makes no sense; Word treats
        # it as corrupt structure
        for ins in t.iter(f"{{{W}}}ins"):
            if ins.getparent().tag == f"{{{W}}}rPr":
                continue
            for dt in ins.iter(f"{{{W}}}delText"):
                anc = dt.getparent()
                nested_del = False
                while anc is not None and anc is not ins:
                    if anc.tag == f"{{{W}}}del":
                        nested_del = True
                        break
                    anc = anc.getparent()
                if not nested_del:
                    bad.append(f"{n} w:delText inside w:ins without a"
                               " nested w:del")
                    break
        for tt in t.iter(f"{{{W}}}t", f"{{{W}}}delText"):
            s = tt.text or ""
            if s != s.strip("\x20\t") and \
                    tt.get("{http://www.w3.org/XML/1998/namespace}space") \
                    != "preserve":
                warn.append(f"{n} text {s[:20]!r} has leading/trailing "
                            "whitespace without xml:space=\"preserve\", "
                            "Word will strip it")
                break                       # one report per part is enough


def check_vmerge_geometry(trees, bad):
    # vMerge chain geometry: every row of one vertical merge must span
    # the SAME grid columns. XSD cannot express this (it is a cross-row
    # constraint), yet a 2-column head merged onto 1-column continuations
    # is a table Word mis-renders -- and the continuation content is
    # typically already gone. Chains are keyed by starting grid column;
    # a row that does not continue a chain closes it.
    for n, t in trees.items():
        for tbl in t.iter(f"{{{W}}}tbl"):
            active: dict = {}
            for tr in walker.rows(tbl):     # LOGICAL rows: an sdt-
                #                             wrapped tr is still a row
                col = 0
                gb = tr.find(f"{{{W}}}trPr/{{{W}}}gridBefore")
                if gb is not None and gb.get(f"{{{W}}}val"):
                    try:
                        col = int(gb.get(f"{{{W}}}val"))
                    except ValueError:
                        bad.append(f"{n} gridBefore w:val="
                                   f"{gb.get(f'{{{W}}}val')!r} not an "
                                   "integer")
                        col = 0
                nxt: dict = {}
                for tc in walker.cells(tr):
                    g = tc.find(f"{{{W}}}tcPr/{{{W}}}gridSpan")
                    try:
                        span = int(g.get(f"{{{W}}}val")) \
                            if g is not None and g.get(f"{{{W}}}val") \
                            else 1
                    except ValueError:
                        bad.append(f"{n} gridSpan w:val="
                                   f"{g.get(f'{{{W}}}val')!r} not an "
                                   "integer")
                        span = 1
                    vm = tc.find(f"{{{W}}}tcPr/{{{W}}}vMerge")
                    if vm is not None:
                        val = vm.get(f"{{{W}}}val") or "continue"
                        if val == "restart":
                            nxt[col] = span
                        elif col in active:
                            if active[col] != span:
                                bad.append(
                                    f"{n} vMerge chain at grid column "
                                    f"{col}: head spans {active[col]} "
                                    f"column(s), continuation spans "
                                    f"{span} (invalid merge geometry)")
                            nxt[col] = active[col]
                    col += span
                active = nxt


def check_marker_pairing(trees, bad):
    # Bookmark pairing / field balance / footnote-endnote references.
    # PER-ID structured findings with canonical ids (review #14: the
    # dict-in-message form broke baseline monotonicity -- {'7': 2}
    # improving to {'7': 1} read as a NEW violation -- and lexical ids
    # let a legal 044/44 bookmark pair read as unpaired).
    for n, t in trees.items():
        bs = Counter(revreg.canon_id(v) for v in
                     _ids(t, "bookmarkStart"))
        be = Counter(revreg.canon_id(v) for v in _ids(t, "bookmarkEnd"))
        for v in sorted(set(bs) | set(be), key=str):
            if bs[v] != be[v]:
                bad.append(Finding(
                    "WML_BOOKMARK_UNPAIRED", (n, str(v)),
                    f"{n} bookmark id={v}: {bs[v]} start(s) / "
                    f"{be[v]} end(s)", count=bs[v] + be[v]))
        for label, cnt in (("bookmarkStart", bs),
                           ("commentRangeStart",
                            Counter(revreg.canon_id(v) for v in
                                    _ids(t, "commentRangeStart")))):
            for v, k in sorted(cnt.items(), key=lambda kv: str(kv[0])):
                if k > 1 and (label != "bookmarkStart" or bs[v] == be[v]):
                    bad.append(Finding(
                        "WML_DUP_" + label.upper(), (n, str(v)),
                        f"{n} duplicate {label} id={v} ×{k}", count=k))
        # Stack-based field-char balancing: plain counting misses
        # end-before-begin reversals and cross-nesting
        depth, seen_sep = 0, []
        fld_bad = None
        for e in t.iter(f"{{{W}}}fldChar"):
            ty = e.get(f"{{{W}}}fldCharType")
            if ty == "begin":
                depth += 1
                seen_sep.append(False)
            elif ty == "separate":
                if depth == 0 or seen_sep[-1]:
                    fld_bad = "separate without owning begin, or duplicated"
                    break
                seen_sep[-1] = True
            elif ty == "end":
                if depth == 0:
                    fld_bad = "end before begin"
                    break
                depth -= 1
                seen_sep.pop()
        if fld_bad is None and depth:
            fld_bad = f"{depth} begin(s) without end"
        if fld_bad:
            bad.append(Finding("WML_FIELD_UNBALANCED", (n,),
                               f"{n} unbalanced field chars: {fld_bad}"))


def check_note_definitions(pkg, kinds, trees, bad):
    # Definitions parts are located by relationship/content-type KIND,
    # never by filename. The comments part was fixed this way in dxv2-5;
    # footnotes/endnotes were left hardcoded, so a package with
    # word/fn2.xml rendered correctly in read.py while the validator
    # reported "footnoteReference id=2 has no definition" -- reader and
    # validator disagreeing about the same package (dxv2-6 review B4).
    for src_tag, kind, dflt in (
            ("footnoteReference", "footnotes", "word/footnotes.xml"),
            ("endnoteReference", "endnotes", "word/endnotes.xml")):
        part = next((n for n, k in kinds.items() if k == kind), dflt)
        pt = _part_tree(pkg, part)
        have = set(_ids(pt, kind.rstrip("s"))) if pt is not None else set()
        if pt is None and part in pkg.names:
            continue    # malformed definitions part: cannot judge refs
        for n, t in trees.items():
            for miss in set(_ids(t, src_tag)) - have:
                bad.append(f"{n} {src_tag} id={miss} has no definition")


def check_mce_ignorable(pkg, bad):
    # mc:Ignorable prefixes must be declared (a listed-but-undeclared
    # prefix is invalid MCE; consumers reject the part)
    for n in pkg.names:
        if not (n.startswith("word/") and n.endswith(".xml")) \
                or "/_rels/" in n:
            continue
        root = _part_tree(pkg, n)
        if root is None:
            continue
        for el in root.iter(etree.Element):
            v = el.get(f"{{{MC}}}Ignorable")
            if v:
                undeclared = [p for p in v.split() if p not in el.nsmap]
                if undeclared:
                    bad.append(f"{n} mc:Ignorable lists undeclared "
                               f"prefixes: {undeclared}")


def check_annotation_ids(pkg, bad):
    # paraId range (hex, < 0x80000000) across all parts; durableId in
    # numbering.xml is DECIMAL unlike the hex one in comments parts.
    # DEFINITION uniqueness is checked too (MS-DOCX: unique per doc):
    # a repair that mapped two same-value illegal ids to one fresh id
    # sailed through here (Ultra-review repro)
    W14 = "http://schemas.microsoft.com/office/word/2010/wordml"
    pid_defs: Counter = Counter()
    for n in pkg.names:
        if not (n.startswith("word/") and n.endswith(".xml")) \
                or "/_rels/" in n:
            continue
        pt = _part_tree(pkg, n)
        if pt is None:
            continue
        for el in pt.iter(etree.Element):
            v = el.get(f"{{{W14}}}paraId")
            if v:
                try:
                    canon = int(v, 16)
                    if canon >= 0x80000000:
                        bad.append(f"{n} paraId={v} >= 0x80000000")
                    else:
                        pid_defs[f"{canon:08X}"] += 1
                except ValueError:
                    bad.append(f"{n} paraId={v!r} is not valid hex")
    for v, k in sorted(pid_defs.items()):
        if k > 1:
            bad.append(Finding(
                "PARAID_DUP", ("paraId", v),
                f"paraId={v} defined more than once (must be unique "
                "per document; Word regenerates, threads/replies may "
                "mis-anchor)", count=k - 1))
    numbering = _part_tree(pkg, "word/numbering.xml")
    if numbering is not None:
        for el in numbering.iter(etree.Element):
            v = el.get(f"{{{W16CID}}}durableId")
            if v:
                try:
                    if int(v, 10) >= 0x7FFFFFFF:
                        bad.append(f"word/numbering.xml durableId={v} "
                                   ">= 0x7FFFFFFF")
                except ValueError:
                    bad.append("word/numbering.xml durableId must be "
                               f"decimal there, got {v!r}")


def check_relationships(pkg, bad, warn):
    # Known parts whose relationship Type is fixed: a wrong Type keeps the
    # part reachable but Word stops recognizing what it IS (threads and
    # resolved-state silently die). Verified against real Word output.
    EXPECTED_REL_TYPE = {
        "word/comments.xml":
            R + "/comments",
        "word/commentsExtended.xml":
            "http://schemas.microsoft.com/office/2011/relationships/"
            "commentsExtended",
        "word/commentsIds.xml":
            "http://schemas.microsoft.com/office/2016/09/relationships/"
            "commentsIds",
        "word/commentsExtensible.xml":
            "http://schemas.microsoft.com/office/2018/08/relationships/"
            "commentsExtensible",
    }
    # r:id reference integrity + rels targets exist (ALL rels, package
    # root included) + duplicate rel Ids + orphan parts + officeDocument
    # entry point
    #: The exact entry-point relationship Types (ECMA transitional + ISO
    #: strict). endswith("/officeDocument") also matched look-alike URIs.
    _ENTRY_TYPES = {
        R + "/officeDocument",
        "http://purl.oclc.org/ooxml/officeDocument/relationships/"
        "officeDocument",
    }
    referenced: set = set()
    entry_targets: list = []
    for rels_name in [x for x in pkg.names if x.endswith(".rels")]:
        rt = _guarded(pkg, rels_name, bad)
        if rt is None:
            continue
        owner = opc.rels_owner(rels_name)
        seen_ids: set = set()
        for rel in rt.iter(f"{{{REL_NS}}}Relationship"):
            rid = rel.get("Id")
            if rid in seen_ids:
                bad.append(f"{rels_name} duplicate relationship "
                           f"Id={rid}")
            seen_ids.add(rid)
            target = rel.get("Target")
            if not target:
                bad.append(f"{rels_name} Relationship missing Target")
                continue
            try:               # ONE resolver project-wide (opc.rel_target)
                tgt = opc.rel_target(target=target, base_part=owner,
                                     target_mode=rel.get("TargetMode"))
            except ValueError:
                bad.append(f"{rels_name} Target={target} "
                           "escapes package root")
                continue
            if tgt is None:                       # external
                continue
            referenced.add(tgt)
            if tgt not in pkg.names:
                bad.append(f"{rels_name} points to nonexistent part {tgt}")
                continue
            exp = EXPECTED_REL_TYPE.get(tgt)
            if exp and rel.get("Type") != exp:
                bad.append(f"{rels_name} {tgt} has wrong relationship "
                           f"Type {rel.get('Type')} (expected {exp})")
            if rels_name == "_rels/.rels" and \
                    rel.get("Type") in _ENTRY_TYPES:
                entry_targets.append(tgt)
    # Entry point: existence used to be the whole check -- an entry
    # pointing at a PNG validated PASSED (high-risk false negative).
    # Word needs exactly one entry whose target is a WordprocessingML
    # main document: exact Type URI, main+xml content type, w:document
    # root.
    if "_rels/.rels" not in pkg.names:
        bad.append("missing _rels/.rels (package has no entry point)")
    elif not entry_targets:
        bad.append("_rels/.rels has no officeDocument relationship "
                   "resolving to an existing part (Word cannot open this)")
    else:
        if len(entry_targets) > 1:
            bad.append(f"_rels/.rels declares {len(entry_targets)} "
                       "officeDocument entry points (must be exactly 1)")
        tgt = entry_targets[0]
        ct_map_ct = _part_tree(pkg, "[Content_Types].xml")
        ctype = None
        if ct_map_ct is not None:
            for o in ct_map_ct.iter(f"{{{CT_NS}}}Override"):
                if o.get("PartName") == "/" + tgt:
                    ctype = o.get("ContentType")
        if ctype is not None and not (
                "wordprocessingml" in ctype and ctype.endswith("main+xml")):
            bad.append(f"entry point {tgt} content type {ctype!r} is not "
                       "a WordprocessingML main document")
        try:                       # not part_tree: for the ENTRY, "not
            entry_root = pkg.xml(tgt)   # parseable XML" (a PNG!) is
        except etree.XMLSyntaxError:    # itself the violation, not a
            entry_root = None           # silent skip
            bad.append(f"entry point {tgt} is not an XML part -- Word "
                       "cannot open this package")
        if entry_root is not None and entry_root.tag != f"{{{W}}}document":
            bad.append(f"entry point {tgt} root element is "
                       f"<{entry_root.tag}>, not w:document -- Word "
                       "cannot open this package")
    for n in pkg.names:
        if n.endswith(".rels") or n == "[Content_Types].xml" \
                or n in referenced:
            continue
        warn.append(f"{n} is referenced by no relationship (orphan part:"
                    " leftover from a deletion, or a missing rels entry)")

    for n in pkg.names:
        if not (n.startswith("word/") and n.endswith(".xml")) \
                or "/_rels/" in n:
            continue
        rels_name = str(PurePosixPath(n).parent / "_rels" /
                        (PurePosixPath(n).name + ".rels"))
        rel_ids = set()
        if rels_name in pkg.names:
            try:            # malformed: already a violation from the
                rt = pkg.xml(rels_name)   # all-rels loop above; here we
            except etree.XMLSyntaxError:  # just cannot judge r:id refs
                continue
            for rel in rt.iter(f"{{{REL_NS}}}Relationship"):
                rel_ids.add(rel.get("Id"))
                target = rel.get("Target")
                if not target:
                    bad.append(f"{rels_name} Relationship missing Target")
                    continue
                try:
                    tgt = opc.rel_target(
                        target=target, base_part=n,
                        target_mode=rel.get("TargetMode"))
                except ValueError:
                    bad.append(f"{rels_name} Target={target} "
                               "escapes package root")
                    continue
                if tgt is not None and tgt not in pkg.names:
                    bad.append(f"{rels_name} points to nonexistent "
                               f"part {tgt}")
        t = _part_tree(pkg, n)
        if t is None:
            continue
        for el in t.iter(etree.Element):
            for k, v in el.attrib.items():
                if k.startswith("{" + R + "}") and v and v not in rel_ids:
                    bad.append(f"{n} {etree.QName(el).localname}"
                               f"@{k.split('}')[1]}={v} not found in rels")


def check_content_types(pkg, bad) -> bool:
    """-> False when [Content_Types].xml is present but malformed: the
    remaining checks cannot judge such a package (same early exit as
    before the split)."""
    # [Content_Types] coverage
    if "[Content_Types].xml" in pkg.names:
        ct = _guarded(pkg, "[Content_Types].xml", bad)
        if ct is None:
            return False
        dfl = set()
        for e in ct.iter(f"{{{CT_NS}}}Default"):
            ext = e.get("Extension")     # attribute is required; a hand-
            if ext is None:              # broken file omitting it must be
                bad.append("[Content_Types].xml Default without "
                           "Extension attribute")   # a violation, not an
            else:                                   # AttributeError
                dfl.add(ext.lower())
        ovr = {e.get("PartName") for e in ct.iter(f"{{{CT_NS}}}Override")}
        for n in pkg.names:
            if n == "[Content_Types].xml":
                continue
            ext = n.rsplit(".", 1)[-1].lower() if "." in n else ""
            if ext not in dfl and "/" + n not in ovr:
                bad.append(f"[Content_Types].xml does not cover {n}")
    else:
        bad.append("missing [Content_Types].xml")
    return True


def check_dangling_refs(pkg, trees, warn):
    # Dangling references and duplicate revision ids: warning level
    # (Word falls back silently, but it is usually unintended)
    style_ids = set()
    styles = _part_tree(pkg, "word/styles.xml")
    if styles is not None:
        style_ids = {e.get(f"{{{W}}}styleId")
                     for e in styles.iter(f"{{{W}}}style")}
    numbering = _part_tree(pkg, "word/numbering.xml")  # memoized parse
    num_ids = {"0"}
    if numbering is not None:
        num_ids |= {e.get(f"{{{W}}}numId")
                    for e in numbering.iter(f"{{{W}}}num")}
    styles_ok = not ("word/styles.xml" in pkg.names and styles is None)
    nums_ok = not ("word/numbering.xml" in pkg.names and numbering is None)
    for n, t in trees.items():
        # - {None}: a ref missing w:val contributes None to the set and
        # sorted(mixed) is a TypeError that killed the whole report; the
        # missing attribute itself is the XSD pass's finding
        dangling = {e.get(f"{{{W}}}val")
                    for tag in ("pStyle", "rStyle", "tblStyle")
                    for e in t.iter(f"{{{W}}}{tag}")} - style_ids - {None}
        if dangling and styles_ok:      # malformed styles.xml: cannot judge
            warn.append(Finding(
                "DANGLING_STYLE", (n,),
                f"{n} dangling style refs (Word falls back to "
                f"Normal): {sorted(dangling)[:8]}",
                severity="warn", count=len(dangling)))
        d2 = {e.get(f"{{{W}}}val")
              for p in t.iter(f"{{{W}}}numPr")
              for e in p.iter(f"{{{W}}}numId")} - num_ids - {None}
        if d2 and nums_ok:
            warn.append(Finding(
                "DANGLING_NUMID", (n,),
                f"{n} dangling numId (list numbering not "
                f"rendered): {sorted(d2)[:8]}",
                severity="warn", count=len(d2)))


def check_revision_id_uniqueness(trees, bad):
    # Duplicate revision ids: HARD failure, aggregated across ALL story
    # parts (per-part scans missed a document.xml/header collision).
    # Taxonomy and id canonicalization come from revreg -- the SHARED
    # registry (review #10: this scan's hand-copied list omitted
    # numberingChange/tblPrExChange while revisions.py processed them,
    # so a PASSED document still double-processed on --id; and lexical
    # keying let '44'/'044' -- one ST_DecimalNumber value -- evade).
    # ECMA-376 (§17.13.5, the id attribute) wants annotation ids unique
    # per document. v11 exempted "same tag + identical content" as
    # split pieces of one logical revision -- reviewer counterexample
    # killed it: two far-apart INDEPENDENT revisions can collide on
    # both, and the XML carries no provenance to tell them apart. No
    # exemption: split paths assign fresh ids instead.
    rev_ids: dict = {}
    for n, t in trees.items():
        for tag, e in revreg.iter_point_revisions(t):
            v = e.get(f"{{{W}}}id")
            if v:
                rev_ids.setdefault(revreg.canon_id(v), []).append((tag, n))
    for v, els in sorted(rev_ids.items(), key=lambda kv: str(kv[0])):
        if len(els) < 2:
            continue
        tags = "+".join(sorted({tg for tg, _ in els}))
        parts = ",".join(sorted({p for _, p in els}))
        bad.append(Finding(
            "REV_DUP_ID", ("id", str(v)),
            f"duplicate revision w:id={v} on {len(els)} elements "
            f"({tags}) in {parts} -- ECMA-376 wants annotation "
            "ids unique document-wide; reassign (split clones "
            "must take fresh ids)", count=len(els)))


def check_range_markers(trees, bad):
    # Range-marker integrity as an ORDERED state machine (review #12:
    # bare Counter equality only found orphans -- duplicate pairs, an
    # End before its Start and payload-free customXml ranges all
    # passed; and a message without multiplicity let "one orphan
    # became two" subtract as pre-existing under --baseline). Per
    # part, per (base, canonical id): exactly one Start, exactly one
    # End, Start first; customXml* ranges must actually wrap some
    # w:customXml markup (that is what they annotate).
    rng_tags = {f"{{{W}}}{b}{h}": (b, h)
                for b in revreg.RANGE_BASES for h in ("Start", "End")}
    cx_tag = f"{{{W}}}customXml"
    for n, t in trees.items():
        marks: dict = {}
        payload: list = []          # doc-order positions of customXml
        for i, el in enumerate(t.iter()):
            if el.tag == cx_tag:
                payload.append(i)
                continue
            bh = rng_tags.get(el.tag)
            if bh:
                b, h = bh
                v = revreg.canon_id(el.get(f"{{{W}}}id"))
                marks.setdefault((b, v), ([], []))[h != "Start"].append(i)
        for (b, v), (ss, ee) in sorted(
                marks.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))):
            ident = (n, b, str(v))
            if len(ss) != 1 or len(ee) != 1:
                bad.append(Finding(
                    "WML_RANGE_UNPAIRED", ident,
                    f"{n} {b}: id={v} has {len(ss)} Start / "
                    f"{len(ee)} End -- exactly one ordered pair "
                    "per id", count=len(ss) + len(ee)))
                continue
            if ee[0] < ss[0]:
                bad.append(Finding(
                    "WML_RANGE_ORDER", ident,
                    f"{n} {b}: id={v} End precedes its Start"))
                continue
            if b.startswith("customXml") and not any(
                    ss[0] < p < ee[0] for p in payload):
                bad.append(Finding(
                    "WML_RANGE_EMPTY", ident,
                    f"{n} {b}: id={v} wraps no w:customXml "
                    "markup between Start and End"))


def semantic_checks(pkg: Pkg, bad: list, warn: list) -> None:
    """Orchestrator: the per-domain checks above, in the exact
    pre-split order (findings print in append order)."""
    kinds = story_kinds(pkg)
    trees = {}
    for n in kinds:             # malformed XML is already a violation from
        try:                    # the XSD pass; don't traceback on it here
            trees[n] = pkg.xml(n)
        except etree.XMLSyntaxError:
            continue
    check_comment_integrity(pkg, kinds, trees, bad)
    check_revision_semantics(trees, bad, warn)
    # Table width vs container: WARNING level, always. Static XML
    # cannot PROVE overflow -- case-15's official table declares 2784pt
    # (fixed layout!) on a ~415pt page and LibreOffice renders it
    # scaled INSIDE the page while Word overflows the same shape.
    # Cross-engine divergence means every finding here is a pointer at
    # the render gate, never a verdict (--gen is the one exception:
    # gen_checks elevates declared-width overflow to a failure).
    warn.extend(_table_width_findings(pkg, trees))
    check_vmerge_geometry(trees, bad)
    check_marker_pairing(trees, bad)
    check_note_definitions(pkg, kinds, trees, bad)
    check_mce_ignorable(pkg, bad)
    check_annotation_ids(pkg, bad)
    check_relationships(pkg, bad, warn)
    if not check_content_types(pkg, bad):
        return          # cannot judge the rest of the package
    check_dangling_refs(pkg, trees, warn)
    check_revision_id_uniqueness(trees, bad)
    check_range_markers(trees, bad)


# ---------- From-scratch generation lint (--gen) ----------

def gen_checks(pkg: Pkg, bad: list, warn: list) -> list:
    """Machine-checkable subset of the from-zero generation pitfalls.
    Stack-agnostic: python-docx and docx-js produce the same broken XML
    shapes. OPT-IN (--gen): several patterns are legitimate in wild
    human-authored files but near-certain bugs in a generated one.
    Each check is grounded: G1 measured (renders as one line), G2 per
    spec (solid paints w:color, auto=black -- LibreOffice masks it, so
    even the render gate cannot catch this one).

    -> the gen checks that actually ran, in order -- the summary prints
    the count so "PASSED" cannot be mistaken for "--gen did nothing"
    (dxv2-3 C6: agents kept re-running it to be sure)."""
    W_ = f"{{{W}}}"
    ran = ["G1 literal newline/tab in w:t",
           "G2 solid shading without color",
           "G3 hand-typed bullet characters",
           "G4 CJK run missing eastAsia in its style chain",
           "G5 table without any width information",
           "G6 TOC field with no source headings"]
    trees = {}
    for n in story_kinds(pkg):      # content-type driven, like the
        try:                        # main gate (renamed parts count)
            trees[n] = pkg.xml(n)
        except etree.XMLSyntaxError:
            continue
    # G1: literal newline/tab inside w:t
    for n, t in trees.items():
        k = sum(1 for el in t.iter(W_ + "t")
                if "\n" in (el.text or "") or "\t" in (el.text or ""))
        if k:
            # WARN (review #14): renders as a space -- pixel-identical
            # to an intentional space; "the author meant a break" is
            # an intent INFERENCE, not a provable file error
            warn.append(Finding(
                "GEN_LITERAL_BREAK", (n,),
                f"{n} {k} w:t contain a literal newline/tab -- "
                "renders as a space in Word; use w:br / w:tab "
                "(--repair converts them)", severity="warn", count=k))
    # G2: solid shading without explicit color
    for n, t in trees.items():
        k = sum(1 for s in t.iter(W_ + "shd")
                if s.get(W_ + "val") == "solid"
                and s.get(W_ + "color") in (None, "auto"))
        if k:
            # ERROR: locally provable -- §17.18.85 'solid' paints the
            # pattern foreground color, absent color = auto = black;
            # deterministic per spec, and LibreOffice MASKS it so the
            # render gate cannot catch it
            bad.append(Finding(
                "GEN_SOLID_SHD", (n,),
                f'{n} {k} w:shd val="solid" without explicit '
                "color -- solid paints the pattern FOREGROUND "
                '(auto = black), not the fill; use val="clear" '
                "+ fill (--repair converts them)", count=k))
    # G3: hand-typed bullet characters
    for n, t in trees.items():
        k = 0
        for p in t.iter(W_ + "p"):
            ppr = p.find(W_ + "pPr")
            if ppr is not None and ppr.find(W_ + "numPr") is not None:
                continue
            txt = "".join(x.text or "" for x in p.iter(W_ + "t")).lstrip()
            # startswith, not `txt[:1] in "...":` -- an EMPTY paragraph
            # gives txt[:1] == "" and "" is `in` every string (measured
            # false positive on a field-only paragraph)
            if txt.startswith(("•", "●", "▪", "◦", "‣")):
                k += 1
        if k:
            # WARN (blocker D): heuristic -- a literal bullet CAN be
            # intentional; not locally provable as a bug
            warn.append(Finding(
                "GEN_FAKE_BULLET", (n,),
                f"{n} {k} paragraph(s) start with a hand-typed "
                "bullet character -- use a numbering definition, not "
                "literal text (indent/wrap will be wrong)",
                severity="warn", count=k))
    # G4: a CJK run whose EFFECTIVE style chain sets an explicit
    # ascii/hAnsi font but supplies eastAsia nowhere -- the exact
    # python-docx style.font.name shape: the intended font applies to
    # Latin only, CJK silently falls to the theme font. Scoped to the
    # chains of runs that actually CARRY CJK: scanning every rFonts in
    # styles.xml flagged never-used styles (python-docx's default
    # MacroText) and failed a perfectly fine minimal document.
    cjk = re.compile("[\u3040-\u30ff\u3400-\u9fff"
                     "\uf900-\ufaff\uac00-\ud7af]")
    st_rf: dict = {}
    st_based: dict = {}
    dd_rf = None
    if "word/styles.xml" in pkg.names:
        try:
            st_root = pkg.xml("word/styles.xml")
            dd_rf = st_root.find(f"{W_}docDefaults/{W_}rPrDefault/"
                                 f"{W_}rPr/{W_}rFonts")
            for s_el in st_root.iter(W_ + "style"):
                sid = s_el.get(W_ + "styleId")
                st_rf[sid] = s_el.find(f"{W_}rPr/{W_}rFonts")
                b = s_el.find(W_ + "basedOn")
                if b is not None:
                    st_based[sid] = b.get(W_ + "val")
        except etree.XMLSyntaxError:
            pass

    def chain_rf(sid):
        out, seen = [], set()
        while sid and sid not in seen:
            seen.add(sid)
            if st_rf.get(sid) is not None:
                out.append(st_rf[sid])
            sid = st_based.get(sid)
        return out
    k = 0
    for t in trees.values():
        for r_el in t.iter(W_ + "r"):
            txt = "".join(x.text or "" for x in r_el.findall(W_ + "t"))
            if not cjk.search(txt):
                continue
            cands = []
            rpr = r_el.find(W_ + "rPr")
            if rpr is not None:
                rf = rpr.find(W_ + "rFonts")
                if rf is not None:
                    cands.append(rf)
                rs = rpr.find(W_ + "rStyle")
                if rs is not None and rs.get(W_ + "val"):
                    cands += chain_rf(rs.get(W_ + "val"))
            host = r_el.getparent()
            while host is not None and host.tag != W_ + "p":
                host = host.getparent()
            if host is not None:
                ps = host.find(f"{W_}pPr/{W_}pStyle")
                if ps is not None and ps.get(W_ + "val"):
                    cands += chain_rf(ps.get(W_ + "val"))
            if dd_rf is not None:
                cands.append(dd_rf)
            # cands is most-specific-first; each font SLOT resolves
            # independently along it. The pitfall shape: a specific
            # level overrides ascii/hAnsi (font.name="黑体") while
            # eastAsia stays inherited from a LESS specific level --
            # the Latin font changed, the CJK didn't. So: explicit
            # latin at index i is fine only if eastAsia is defined at
            # some index <= i ("ea anywhere in the chain" passed the
            # broken shape whenever docDefaults carried a theme ea).
            lat_i = next((i for i, c in enumerate(cands)
                          if c.get(W_ + "ascii") or c.get(W_ + "hAnsi")),
                         None)
            ea_i = next((i for i, c in enumerate(cands)
                         if c.get(W_ + "eastAsia")
                         or c.get(W_ + "eastAsiaTheme")), None)
            if lat_i is not None and (ea_i is None or ea_i > lat_i):
                k += 1
    if k:
        # WARN (blocker D): the slot resolution is deterministic but
        # the CONSEQUENCE (which font the fallback picks, whether it
        # looks wrong) is renderer-dependent -- not a provable bug
        warn.append(Finding(
            "GEN_CJK_FONT", ("package",),
            f"{k} run(s) carry CJK text where a style level sets an "
            "explicit ascii/hAnsi font without eastAsia at that level "
            "-- the Latin font changes, the CJK silently keeps the "
            "inherited one (set w:eastAsia alongside)",
            severity="warn", count=k))
    # G5: tables carrying no width information at all
    for n, t in trees.items():
        k = 0
        for tbl in t.iter(W_ + "tbl"):
            kind, val = measure.parse(tbl.find(f"{W_}tblPr/{W_}tblW"))
            fixed_w = kind in ("dxa", "pct") and bool(val)
            grid_w = any(g.get(W_ + "w") for g in
                         tbl.findall(f"{W_}tblGrid/{W_}gridCol"))
            tc_w = any(tc.find(f"{W_}tcPr/{W_}tcW") is not None
                       for tc in tbl.iter(W_ + "tc"))
            if not (fixed_w or grid_w or tc_w):
                k += 1
        if k:
            # INFO (blocker D): compatibility/quality advice, not a
            # provable defect -- autofit without widths is legal
            warn.append(Finding(
                "GEN_WIDTHLESS", (n,),
                f"{n} {k} table(s) carry no width information "
                "(no gridCol@w, no tcW, no fixed tblW) -- renders "
                "unpredictably in Google Docs/older Word; set column "
                "widths AND per-cell width",
                severity="info", count=k))
    # G6: TOC field with nothing to collect
    # space-joined: bare concatenation glued "...\o" + "TC ..." into
    # "\oTC", destroying the word boundary the TC-source scan needs
    instr = " ".join(it.text or "" for t in trees.values()
                    for it in t.iter(W_ + "instrText"))
    instr += " ".join(fs.get(W_ + "instr") or "" for t in trees.values()
                      for fs in t.iter(W_ + "fldSimple"))
    if re.search(r"\bTOC\b", instr):
        has_outline = any(True for t in trees.values()
                          for _ in t.iter(W_ + "outlineLvl"))
        heading_used = False
        if "word/styles.xml" in pkg.names and not has_outline:
            try:
                st = pkg.xml("word/styles.xml")
                has_outline = st.find(f".//{W_}outlineLvl") is not None
                hids = {s.get(W_ + "styleId")
                        for s in st.iter(W_ + "style")
                        if (lambda nm: nm is not None and
                            (nm.get(W_ + "val") or "").lower()
                            .startswith("heading"))(s.find(W_ + "name"))}
                used = {ps.get(W_ + "val") for t in trees.values()
                        for ps in t.iter(W_ + "pStyle")}
                heading_used = bool(hids & used)
            except etree.XMLSyntaxError:
                pass
        has_tc = re.search(r"\bTC\b", instr) is not None
        if not has_outline and not heading_used and not has_tc:
            # WARN (review #14): a TOC can also collect TC fields,
            # captions/SEQ and custom \t styles -- this check does not
            # implement full field-switch semantics, so "will render
            # empty" is an estimate, not a proof
            warn.append(Finding(
                "GEN_TOC_NO_SOURCES", ("package",),
                "TOC field present but no outline sources found (no "
                "outlineLvl, no built-in heading style in use, no TC "
                "fields) -- the TOC LIKELY renders empty; check its "
                "\\t/\\c switches against your sources",
                severity="warn"))
    # G7 (former): declared-width overflow was ELEVATED to a --gen
    # failure here. REMOVED by blocker D (review #13): the width
    # solver is an estimator, and estimator output must never feed a
    # hard failure -- the same overwide table now warns identically in
    # and out of --gen, and the render gate owns the verdict. Hard
    # --gen failures remaining below/above are the PROVABLE ones (G1
    # literal newlines, G2 solid shading, G6 empty TOC), each locally
    # decidable from the spec with no renderer dependence.
    # G8: fixed layout + auto tblW is a LEGAL algorithm branch (width
    # then comes from row/cell settings -- ECMA-376 fixed-width
    # algorithm; official documents use it), so never a failure. But a
    # GENERATED table in that shape with no dxa tcW in its first
    # LOGICAL row has left its width entirely to engine guesses: warn.
    for n, t in trees.items():
        k = 0
        for tbl in t.iter(W_ + "tbl"):
            lay = tbl.find(f"{W_}tblPr/{W_}tblLayout")
            if lay is None or lay.get(W_ + "type") != "fixed":
                continue
            kind, val = measure.parse(tbl.find(f"{W_}tblPr/{W_}tblW"))
            if kind == "dxa" and val > 0:   # incl. the typeless form:
                continue                    # w:type defaults to dxa
            tr0 = walker.first_row(tbl)
            has_tcw = tr0 is not None and any(
                measure.dxa(c.find(f"{W_}tcPr/{W_}tcW")) > 0
                for c in walker.cells(tr0))
            if not has_tcw:
                k += 1
        if k:
            warn.append(f"{n} {k} fixed-layout table(s) with auto tblW "
                        "and no dxa tcW in the first row: width is "
                        "left to engine guesses -- set explicit widths "
                        "(rule 12)")
    return ran


# ---------- Collateral damage (--baseline) ----------

def collateral(pkg: Pkg, orig: Pkg, warn: list) -> None:
    lost_parts = set(orig.names) - set(pkg.names)
    if lost_parts:
        warn.append(f"parts: {len(lost_parts)} fewer than baseline: "
                    f"{sorted(lost_parts)[:6]}")
    new_parts = sorted(set(pkg.names) - set(orig.names))
    if new_parts:
        # confirmation, not an alarm: an agent that just added footer5.xml
        # gets positive proof the part landed and registered (the checks
        # above own registration); an agent that did NOT plan a new part
        # learns of the accidental add here instead of never.
        warn.append(Finding(
            "parts-added", ("parts-added",),
            f"parts: +{len(new_parts)} vs baseline: "
            f"{', '.join(new_parts[:6])}"
            f"{' …' if len(new_parts) > 6 else ''}"
            " — expected if you added them; unexpected = accidental add",
            severity="info", count=len(new_parts)))

    def count(p, pat):
        return sum(len(re.findall(pat, p.read(n)))
                   for n in p.names
                   if n.startswith("word/") and n.endswith(".xml"))
    for pat, label in ((rb"w14:paraId", "w14:paraId (view addressing)"),
                       (rb"<w:instrText", "field codes"),
                       (rb"<w:headerReference", "header references"),
                       (rb"<w:footerReference", "footer references")):
        a, b = count(orig, pat), count(pkg, pat)
        if b < a:
            if pat == rb"<w:instrText":
                # Deleting a field AS A TRACKED CHANGE converts its
                # instrText to delInstrText -- the instruction is still
                # in the file, just marked deleted. The raw count drop
                # made this warning fire on every legitimate tracked
                # field deletion, and agents burned ~1M tokens grepping
                # this file to prove their own innocence (dxv2-11
                # trajectory evidence, case-09 s40-48). Say it here.
                da = count(orig, rb"<w:delInstrText")
                db = count(pkg, rb"<w:delInstrText")
                conv = min(a - b, max(0, db - da))
                if conv == a - b:
                    warn.append(
                        f"{label} {a}→{b}: all {conv} converted "
                        "instrText→delInstrText inside tracked "
                        "deletions — benign, no field content lost")
                    continue
                if conv:
                    warn.append(
                        f"{label} {a}→{b} ({conv} converted to "
                        "delInstrText in tracked deletions — benign; "
                        f"{a - b - conv} actually lost)")
                    continue
            warn.append(f"{label} {a}→{b} (lost {a - b})")
    ma = [n for n in orig.names if n.startswith("word/media/")]
    mb = [n for n in pkg.names if n.startswith("word/media/")]
    if len(mb) < len(ma):
        warn.append(f"media files {len(ma)}→{len(mb)}")
