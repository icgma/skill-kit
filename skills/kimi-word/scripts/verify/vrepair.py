"""WHAT we mechanically fix: repairs provably safe from the XML alone,
baseline-budgeted so an edit gate never touches pre-existing defects."""
from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

from lxml import etree

sys.path.insert(0, str(Path(__file__).parent))
import opc  # noqa: E402  (the one relationship resolver + atomic IO)
import walker  # noqa: E402  (the one rendered-text rule / xml:space)
from vpkg import Pkg, W, W15, W16CID, W16CEX  # noqa: E402

_XSP = walker.XML_SPACE


# ---------- Repair (--repair) ----------

def _defect_budget(baseline: Path) -> Counter:
    """Pre-existing-defect signatures in the baseline, as a spending
    budget: repair skips exactly as many matching instances as the
    original document already carried. This is the repair-side mirror
    of validate's baseline subtraction -- 检查只报你改的,修复也只修
    你改的 -- and it is what makes even the xml:space fix safe on an
    edit gate: a pre-existing unpreserved trailing space (whose
    disappearance the author's rendering already shows) stays exactly
    as it was; the same defect in text YOU added is yours and gets
    fixed."""
    from copy import deepcopy
    from ooxml_order import SORTABLE, sort_children
    bud: Counter = Counter()
    try:
        pkg = Pkg(Path(baseline))
    except (SystemExit, OSError):
        return bud
    for name in pkg.names:
        if not (name.startswith("word/") and name.endswith(".xml")):
            continue
        try:
            root = etree.fromstring(pkg.read(name))
        except etree.XMLSyntaxError:
            continue
        pn = name.rsplit("/", 1)[-1]
        for ppr in root.iter(f"{{{W}}}pPr"):
            if any(isinstance(c.tag, str) and c.tag.split("}")[-1] in
                   ("top", "left", "bottom", "right", "between", "bar")
                   for c in ppr):
                bud[("pbdr", pn)] += 1
        for body_el in root.iter(f"{{{W}}}body"):
            kids_b = list(body_el)
            sect = [c for c in kids_b if isinstance(c.tag, str)
                    and c.tag == f"{{{W}}}sectPr"]
            if sect and kids_b[-1] is not sect[-1]:
                bud[("sectpr", pn)] += 1
        for tag_ in SORTABLE:
            for el in root.iter(f"{{{W}}}{tag_}"):
                cp = deepcopy(el)
                if sort_children(cp):
                    bud[("order", pn, etree.tostring(el))] += 1
        for t_el in root.iter(f"{{{W}}}t"):
            s = (t_el.text or "").replace("\r\n", "\n").replace("\r",
                                                                "\n")
            if "\n" in s or "\t" in s:
                bud[("lit", pn, s)] += 1
        for an_ in root.iter(f"{{{W}}}abstractNum"):
            for lv_ in an_.findall(f"{{{W}}}lvl"):
                if not lv_.get(f"{{{W}}}ilvl"):
                    bud[("ilvl", pn)] += 1
        for z_ in root.iter(f"{{{W}}}zoom"):
            if not z_.get(f"{{{W}}}percent"):
                bud[("zoom", pn)] += 1
        for sh_ in root.iter(f"{{{W}}}shd"):
            if sh_.get(f"{{{W}}}val") == "solid" and \
                    sh_.get(f"{{{W}}}color") in (None, "auto") and \
                    sh_.get(f"{{{W}}}fill") not in (None, "auto"):
                bud[("shd", pn, sh_.get(f"{{{W}}}fill"))] += 1
        for t in root.iter(f"{{{W}}}t", f"{{{W}}}delText",
                           f"{{{W}}}instrText", f"{{{W}}}delInstrText"):
            s = t.text or ""
            if s != s.strip(" \t\n\r") and t.get(_XSP) != "preserve":
                bud[("xsp", pn, t.tag.split("}")[-1], s)] += 1
    return bud


def repair(root_dir: Path, gen: bool = False,
           baseline: Path | None = None) -> tuple:
    """Fix mechanical issues: element order in property containers,
    out-of-range durableId/paraId
    (deterministic rewrite, consistent across parts), literal newlines,
    colorless solid shading, zoom missing its required percent.
    Accepts an unpacked directory (fixed in place) OR a .docx (unpacked
    to a temp dir, fixed, atomically repacked over the original --
    needed because the mandated new-document flow runs on a saved
    .docx, and python-docx's own template ships XSD-invalid settings).

    `gen=True` additionally adds the missing xml:space="preserve" on text
    with edge whitespace. That one is NOT a mechanical fix on a foreign
    document: a trailing space with no `preserve` means "this space is
    not significant", and adding the attribute INVENTS a visible space.
    On a document WE just produced it is the right fix.

    `baseline=` scopes EVERY fix to content the edit introduced: defects
    the baseline already carried are skipped instance-for-instance (see
    _defect_budget). With a baseline, gen=True is safe on wild documents
    -- pre-existing quirks stay untouched by budget, new ones are ours.

    -> (fixed count, (part, what) -> count Counter breakdown; the CLI
    prints it under the summary line)."""
    import hashlib
    import shutil as _sh
    import tempfile as _tf
    from copy import deepcopy
    from ooxml_order import SORTABLE, ensure_child, insert_ord, \
        sort_children
    W14 = "http://schemas.microsoft.com/office/word/2010/wordml"
    if root_dir.is_file():
        tmp = Path(_tf.mkdtemp(prefix="rep"))
        try:
            opc.unpack(root_dir, tmp)
            k, detail = repair(tmp, gen=gen, baseline=baseline)
            if k:
                opc.atomic_zip_dir(tmp, root_dir)
            return k, detail
        finally:
            _sh.rmtree(tmp, ignore_errors=True)
    if not root_dir.is_dir():
        raise SystemExit(f"--repair: {root_dir} is neither a directory "
                         "nor a file")
    bud = _defect_budget(baseline) if baseline else None

    def covered(*sig) -> bool:
        """True = this defect instance pre-exists in the baseline:
        spend one budget token and leave the instance alone."""
        if bud is None:
            return False
        if bud[sig] > 0:
            bud[sig] -= 1
            return True
        return False
    n = 0
    detail: Counter = Counter()  # (part, what) -> count, for the report
    renames: dict = {}
    used: set = set()            # every id already in the package
    multi_def: set = set()       # illegal values defined >1 time (w14)
    _W14A = f"{{{W14}}}paraId"
    _defs: Counter = Counter()
    for f0 in sorted((root_dir / "word").rglob("*.xml")):
        try:
            t0 = etree.parse(str(f0))
        except etree.XMLSyntaxError:
            continue
        for el in t0.iter(etree.Element):
            for a0, dec0 in ((f"{{{W14}}}paraId", False),
                             (f"{{{W15}}}paraId", False),
                             (f"{{{W15}}}paraIdParent", False),
                             (f"{{{W16CID}}}paraId", False),
                             (f"{{{W16CID}}}durableId",
                              f0.name == "numbering.xml"),
                             (f"{{{W16CEX}}}durableId",
                              f0.name == "numbering.xml")):
                v0 = el.get(a0)
                if not v0:
                    continue
                try:
                    int(v0, 10 if dec0 else 16)
                    used.add(v0.upper())
                except ValueError:
                    pass
                if a0 == _W14A:
                    _defs[v0] += 1
    multi_def = {v for v, k in _defs.items() if k > 1}
    _refs_to: Counter = Counter()
    for f0 in sorted((root_dir / "word").rglob("*.xml")):
        try:
            t0 = etree.parse(str(f0))
        except etree.XMLSyntaxError:
            continue
        for el in t0.iter(etree.Element):
            for a0 in (f"{{{W15}}}paraId", f"{{{W15}}}paraIdParent",
                       f"{{{W16CID}}}paraId"):
                v0 = el.get(a0)
                if v0 and v0 in multi_def:
                    _refs_to[v0] += 1
    _defseq: Counter = Counter()
    _amb_skipped: set = set()
    _amb_reported: set = set()

    def fresh(old: str, decimal: bool) -> str:
        """Deterministic old->new, but NEVER colliding with an id the
        package already uses (md5(old) alone once mapped two same-value
        definitions AND an unrelated existing id onto one value)."""
        key = (old, decimal)
        if key not in renames:
            salt = 0
            while True:
                v = int(hashlib.md5(f"{old}#{salt}".encode())
                        .hexdigest()[:8], 16) & 0x7FFFFFFE
                cand = str(v or 1) if decimal else f"{v or 1:08X}"
                if cand.upper() not in used:
                    break
                salt += 1
            used.add(cand.upper())
            renames[key] = cand
        return renames[key]
    word_dir = root_dir / "word"
    if not word_dir.is_dir():
        raise SystemExit(f"{root_dir} has no word/ (not an unpacked docx)")
    for f in sorted(word_dir.rglob("*.xml")):
        try:
            tree = etree.parse(str(f))
        except etree.XMLSyntaxError:
            continue
        changed = False
        # ---- element-order repair (property containers ONLY) ----
        # Wild files' single most common XSD violation is a misordered
        # property bag (WPS/LO reorder freely; Word tolerates on open
        # but our gate rightly flags it). Sorting is safe exactly where
        # children are properties; content models are NOT touched --
        # position is semantics there (see ooxml_order.SORTABLE).
        from ooxml_order import SORTABLE, ensure_child, insert_ord, \
            sort_children
        root_el = tree.getroot()
        # bare border children directly in pPr (a hand-written-XML
        # mistake): wrap into pBdr at its schema position first
        for ppr in root_el.iter(f"{{{W}}}pPr"):
            bare = [c for c in ppr if isinstance(c.tag, str)
                    and c.tag.split("}")[-1] in
                    ("top", "left", "bottom", "right", "between", "bar")]
            if bare and not covered("pbdr", f.name):
                pbdr = ensure_child(ppr, "pBdr")
                for b in bare:
                    ppr.remove(b)
                    pbdr.append(b)
                changed = True
                n += len(bare)
                detail[(f.name, "bare pPr borders wrapped into pBdr")] \
                    += len(bare)
        for body_el in root_el.iter(f"{{{W}}}body"):
            kids_b = list(body_el)
            sect = [c for c in kids_b if isinstance(c.tag, str)
                    and c.tag == f"{{{W}}}sectPr"]
            if sect and kids_b[-1] is not sect[-1] \
                    and not covered("sectpr", f.name):
                body_el.append(sect[-1])      # append moves: sectPr last
                changed = True
                n += 1
                detail[(f.name, "body sectPr moved last")] += 1
        for tag_ in SORTABLE:
            for el in root_el.iter(f"{{{W}}}{tag_}"):
                if bud is not None:
                    # scope test needs the PRE-fix serialization: an
                    # untouched misordered bag is byte-identical to its
                    # baseline twin and stays as it was
                    cp = deepcopy(el)
                    if not sort_children(cp):
                        continue
                    if covered("order", f.name, etree.tostring(el)):
                        continue
                if sort_children(el):
                    changed = True
                    n += 1
                    detail[(f.name, f"w:{tag_} children reordered "
                            "to schema sequence")] += 1
        # literal \n / \t inside w:t -> w:br / w:tab siblings inside the
        # SAME run (multiple w:t/w:br children of one w:r are legal).
        # Measured: "line1\nline2" renders as ONE line in Word/LO.
        for t_el in list(tree.iter(f"{{{W}}}t")):
            s = (t_el.text or "").replace("\r\n", "\n").replace("\r", "\n")
            if "\n" not in s and "\t" not in s:
                continue
            if covered("lit", f.name, s):
                continue
            run = t_el.getparent()
            pos = list(run).index(t_el)
            new_els = []
            for piece in re.split(r"([\n\t])", s):
                if piece == "\n":
                    new_els.append(run.makeelement(f"{{{W}}}br", {}))
                elif piece == "\t":
                    new_els.append(run.makeelement(f"{{{W}}}tab", {}))
                elif piece != "":
                    te = run.makeelement(f"{{{W}}}t", {})
                    te.text = piece
                    if piece != piece.strip(" "):
                        te.set(_XSP, "preserve")
                    new_els.append(te)
            run.remove(t_el)
            for k2, ne in enumerate(new_els):
                run.insert(pos + k2, ne)
            changed = True
            n += 1
            detail[(f.name, "literal newline/tab in w:t -> w:br/w:tab")] += 1
        # w:lvl without its schema-REQUIRED w:ilvl: numbering.xml written
        # by hand/by generators routinely omits it; the level's position
        # IS its index, so the value is recoverable, not guessed
        # (dxv2-3 C3: the gate failed a file it could have repaired)
        for an_ in tree.iter(f"{{{W}}}abstractNum"):
            for i_, lv_ in enumerate(an_.findall(f"{{{W}}}lvl")):
                if not lv_.get(f"{{{W}}}ilvl") \
                        and not covered("ilvl", f.name):
                    lv_.set(f"{{{W}}}ilvl", str(i_))
                    changed = True
                    n += 1
                    detail[(f.name, "w:lvl missing w:ilvl -> position "
                            "index")] += 1
        # w:zoom without its schema-REQUIRED percent attribute:
        # python-docx's default template ships exactly this, so the
        # mandated new-document flow failed on a pristine save. 100 is
        # the spec default view scale.
        for z_ in tree.iter(f"{{{W}}}zoom"):
            if not z_.get(f"{{{W}}}percent") \
                    and not covered("zoom", f.name):
                z_.set(f"{{{W}}}percent", "100")
                changed = True
                n += 1
                detail[(f.name, "w:zoom missing required w:percent "
                        "-> 100")] += 1
        # solid shading with no explicit color: Word paints auto=black
        # over the fill -- val="clear" keeps the intended fill
        for sh_ in tree.iter(f"{{{W}}}shd"):
            if sh_.get(f"{{{W}}}val") == "solid" and \
                    sh_.get(f"{{{W}}}color") in (None, "auto") and \
                    sh_.get(f"{{{W}}}fill") not in (None, "auto") and \
                    not covered("shd", f.name, sh_.get(f"{{{W}}}fill")):
                sh_.set(f"{{{W}}}val", "clear")
                changed = True
                n += 1
                detail[(f.name, "solid shading without color "
                        "-> val=clear")] += 1
        for t in (tree.iter(f"{{{W}}}t", f"{{{W}}}delText",
                            f"{{{W}}}instrText", f"{{{W}}}delInstrText")
                  if gen else ()):
            s = t.text or ""
            if s != s.strip(" \t\n\r") and t.get(_XSP) != "preserve" \
                    and not covered("xsp", f.name,
                                    t.tag.split("}")[-1], s):
                t.set(_XSP, "preserve")
                changed = True
                n += 1
                detail[(f.name, "missing xml:space=preserve")] += 1
        # durableId in numbering.xml is DECIMAL; everywhere else these ids
        # are hex. Parsing decimal as hex declares legal values broken and
        # "repairs" them into letters -- active data corruption (measured:
        # one pass rewrote 17 legal ids).
        decimal = f.name == "numbering.xml"
        for el in tree.iter(etree.Element):
            # EVERY spelling of the id, definitions AND references:
            # w14:paraId is the definition, but commentsExtended points
            # at it via w15:paraId/w15:paraIdParent and commentsIds via
            # w16cid:paraId. Rewriting only the definition side turned
            # "id out of range" into "cross-part reference broken" --
            # active damage. The deterministic old->new mapping (keyed
            # by the old value) is what keeps all parts converging on
            # the same replacement.
            for attr in (f"{{{W14}}}paraId",
                         f"{{{W15}}}paraId",
                         f"{{{W15}}}paraIdParent",
                         f"{{{W16CID}}}paraId",
                         f"{{{W16CID}}}durableId",
                         f"{{{W16CEX}}}durableId"):
                v = el.get(attr)
                if not v:
                    continue
                is_dec = decimal and attr.endswith("durableId")
                cap = 0x80000000 if "paraId" in attr else 0x7FFFFFFF
                try:
                    ok = int(v, 10 if is_dec else 16) < cap
                except ValueError:
                    ok = False
                if not ok:
                    if v in multi_def:
                        if attr != _W14A or v in _amb_reported:
                            # references to an ambiguous value, or a
                            # repeat: cannot know which definition was
                            # meant -- refuse rather than guess
                            _amb_skipped.add(v)
                            continue
                        # first/again definition occurrence: give EACH
                        # its own fresh id (no reference points here,
                        # else the branch above fired)
                        if _refs_to.get(v):
                            _amb_skipped.add(v)
                            continue
                        el.set(attr, fresh(f"{v}#{_defseq[v]}", is_dec))
                        _defseq[v] += 1
                        changed = True
                        n += 1
                        detail[(f.name, "ambiguous illegal paraId "
                                "given distinct fresh ids")] += 1
                        continue
                    el.set(attr, fresh(v, is_dec))
                    changed = True
                    n += 1
                    detail[(f.name, f"out-of-range {attr.split('}')[1]}"
                            " -> fresh id")] += 1
        if changed:
            opc.atomic_write_tree(tree, f)
    for v in sorted(_amb_skipped):
        print(f"  ! paraId={v!r}: multiple definitions AND references "
              "share this illegal value -- ownership is undecidable, "
              "not auto-repaired (fix by hand via --raw)")
    return n, detail
