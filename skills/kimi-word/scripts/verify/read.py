#!/usr/bin/env python3
"""docx -> text view: **content + formatting + provenance**, read
straight from OOXML with no intermediate model.

    python scripts/read.py file.docx                 # or an unpacked dir
    python scripts/read.py file.docx --raw 57        # that block's raw XML

## First principles: a docx holds exactly five kinds of information,
## and the view must answer all five

1. **Content** -- the characters. The information floor: the model must
   read these no matter what.
2. **Appearance** -- effective formatting. Style chain resolved, then
   differentially encoded (only keys deviating from the base print).
3. **Provenance** -- which container the text lives in. Field result
   caches (⟦SEQ Figure▸1⟧: editing the "1" gets wiped on refresh),
   hyperlinks (<a url>), revisions (<ins author>), comment anchors
   (⟨c5⟩…⟨/c5⟩), text boxes (@txbox{…}). Same-looking text with
   different provenance has **completely different edit consequences**
   -- omit provenance and the model will corrupt the document.
4. **Addressing** -- [N] = index among body's direct children, identical
   to --raw N and lxml body[N]; #XXXXXXXX is w14:paraId (Word writes it,
   LibreOffice/WPS do not -- which is why the index is the handle that
   always works).
5. **Indirection** -- styles (@style), numbering definitions (@num:
   list=1.L0 renders as "Chapter One" in Word; without the definition
   the model pastes number text into the body), theme fonts
   (~minorHAnsi is a reference, not a font name; real names on @theme).

## Objective function

Answer all five within 2-3x the plain-text token count; the long tail
goes to progressive disclosure (--raw). Budget keepers: syntax lives in
SKILL.md, not the view; base = each block's own style chain (what prints
is exactly the direct formatting); font quad-channel folds at print
time.

Unmodeled keys must be NAMED in @skip -- "not said" and "not there"
look identical, the darkest completeness leak.

## Past mistakes (caught by the metric; recorded here against relapse)

- Minus signs on value keys: -latin only ever meant "no run here";
  6423 occurrences, all wrong, all wasted characters.
- Theme refs printed as font names; w:cstheme has a lowercase t (OOXML's
  own inconsistency) -- +"Theme" never read it.
- p.iter() flattening: text-box text leaked into the body, fields went
  invisible, comment anchors vanished -- one root cause, **flattening
  erases provenance**. Now a container-tree walk.
"""
from __future__ import annotations

import argparse
import re
import sys
import zipfile
from collections import Counter
from copy import deepcopy
from pathlib import Path

from lxml import etree

sys.path.insert(0, str(Path(__file__).parent))
import measure  # noqa: E402  (the one measurement value model -- a
#                 local int(v) tracebacked on the LEGAL "150%" form and
#                 a missing w:type, which defaults to dxa, hid widths)
import opc  # noqa: E402  (the one relationship resolver)
import walker as wml  # noqa: E402  (the one logical WML traversal: an

# Windows consoles/pipes default to the legacy ANSI code page (cp1252/cp936):
# the engine's CJK messages must never crash the gate there.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
#                 sdt-wrapped w:tr is still a row -- findall("tr") lost
#                 it. Aliased: read has its own token Walker class)

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
W14 = "{http://schemas.microsoft.com/office/word/2010/wordml}"
W15 = "{http://schemas.microsoft.com/office/word/2012/wordml}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
WP = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
PR = "{http://schemas.openxmlformats.org/package/2006/relationships}"
O = "{urn:schemas-microsoft-com:office:office}"


def q(tag: str) -> str:
    return W + tag


# ---------------------------------------------------------------- units

def half_pt(v) -> str:
    """Half-points -> pt (w:sz is half-point; 24 = 12pt)."""
    try:
        return f"{int(v) / 2:g}pt"
    except (TypeError, ValueError):
        return str(v)


def twip(v) -> str:
    """Twips -> pt display, TOTAL: huge legal integers (int/20 went
    float and overflowed), universal measures ("8.5in" must display
    like its equivalent "12240" or --diff invents differences),
    invalid values fall back to the raw string."""
    if isinstance(v, int):
        return measure.fmt_pt(v)
    t = measure.twips(v)
    return measure.fmt_pt(t) if t is not None else str(v)


def eighth_pt(v) -> str:
    """ST_EighthPointMeasure -> pt display, total, integer-exact.
    Plain numbers are eighth-points (12 -> 1.5pt); universal measures
    are legal in the type too; anything else shows raw."""
    try:
        n = int(v, 10)
    except (TypeError, ValueError):
        t = measure.twips(v)            # "1.5pt" etc.
        return measure.fmt_pt(t) if t is not None else str(v)
    sign = "-" if n < 0 else ""
    whole, frac = divmod(abs(n) * 125, 1000)
    if not frac:
        return f"{sign}{whole}pt"
    return f"{sign}{whole}.{str(frac).zfill(3).rstrip('0')}pt"


def emu_cm(v) -> str:
    try:
        return f"{int(v) / 360000:.2f}cm"
    except (TypeError, ValueError):
        return str(v)


# ---------------------------------------------------------------- fact extraction

_RPR_BOOL = {"b": "b", "i": "i", "strike": "s", "caps": "caps",
             "smallCaps": "smallCaps", "outline": "outline", "shadow": "shadow",
             "emboss": "emboss", "imprint": "imprint", "vanish": "hidden",
             "dstrike": "dstrike", "rtl": "rtl", "cs": "cs-on",
             "specVanish": "specVanish", "oMath": "oMath",
             # run-level snapToGrid: pPr's was handled, rPr's was neither
             # rendered nor named, so <w:snapToGrid w:val="0"/> in a run
             # was invisible (dxv2-7 boolean sweep)
             "snapToGrid": "snapGrid"}

#: Keys deliberately not modeled (no visible-layout impact, or caches
#: Word recomputes). Must be NAMED on the view's @skip line.
SKIP = {
    "kern": "kerning threshold",
    "noProof": "spell-check off",
    "webHidden": "hidden in web view",
    "suppressAutoHyphens": "no auto hyphenation",
    "suppressLineNumbers": "no line numbers",
    "autoSpaceDE": "auto CJK/latin spacing",
    "autoSpaceDN": "auto CJK/digit spacing",
    "adjustRightInd": "auto right-indent adjust",
    "topLinePunct": "compress leading punctuation",
    "overflowPunct": "punctuation overflow",
    "wordWrap": "latin line-break rule",
    "proofErr": "spell-check marker",
    "rsid": "revision-session id",
    "themeFontLang": "theme languages",
    "eastAsianLayout": "east-asian layout",
    "effect": "text animation",
    "kinsoku": "east-asian kinsoku",
    "woUserID": "WPS private author id",
    "cnfStyle": "conditional-format cache (Word recomputes)",
    "noEndnote": "endnotes off",
    "formProt": "form protection flag",
    "footnotePr": "section footnote numbering",
    "numFmt": "footnotePr child",
    "tblStyleRowBandSize": "table-style band size",
    "tblStyleColBandSize": "table-style band size",
    "lastRenderedPageBreak": "last-pagination cache",
    "latentStyles": "style-gallery UI hints (no layout effect)",
    "tblCaption": "table caption (accessibility metadata)",
    "tblDescription": "table description (accessibility)",
    "divId": "HTML div association",
    "style@customStyle": "user-defined-style flag",
    "pgMar@header": "header distance from edge",
    "pgMar@footer": "footer distance from edge",
    "pgMar@gutter": "binding gutter",
}


#: CT_OnOff elements: `<w:x/>` is ON, `<w:x w:val="0"/>` is OFF, absent is
#: the inherited/default value. THREE distinct states -- rendering by
#: `find(...) is not None` collapses the first two, so `<w:tblHeader
#: w:val="0"/>` printed `+header-row` and two semantically opposite
#: documents rendered identically (dxv2-7 review P1.3). Every consumer
#: of a boolean goes through `_on()`; every @skip entry for one carries
#: its state.
# ---- @skip name sets: MODULE SCOPE on purpose. They are the
# SUBTRAHEND of the @skip set-difference, and a subtrahend that is
# merely declared will lie sooner or later -- `formProt` lied for four
# rounds, `tblCaption`/`tblDescription` longer. tests/inv_cover.py
# imports them and falsifies every entry it can synthesize a fixture
# for, so a lie fails a gate instead of waiting for a reviewer.

_WML_NS = {W, W14, W15}


_SUMMARIZED = {"pBdr", "tblBorders", "tcBorders", "tblCellMar",
               "tcMar", "framePr", "tabs", "numPr", "latentStyles",
               "docDefaults", "rPrDefault", "pPrDefault",
               "tblStylePr"}


_RENDER_NOISE = {"id", "rsid", "rsidR", "rsidRPr", "rsidDel",
                 "rsidP", "rsidRDefault", "rsidTr", "rsidSect",
                 "paraId", "textId", "space", "Ignorable",
                 "author", "date", "initials", "name", "val",
                 "w", "h", "type", "fill", "color", "sz", "ascii",
                 "hAnsi", "eastAsia", "cs", "left", "right", "top",
                 "bottom", "firstLine", "hanging", "line", "before",
                 "after", "styleId", "pos", "leader", "start",
                 "char", "font", "embed", "link", "hint", "themeColor"}


_STRUCTURAL = {"rFonts", "ind", "spacing", "shd", "lang", "sz",
               "szCs", "color", "u", "tab", "tabs", "highlight",
               "vertAlign", "pStyle", "rStyle", "numId", "ilvl",
               "tblStyle", "tblW", "tcW", "gridSpan", "vMerge",
               "trHeight", "jc", "outlineLvl", "framePr", "pBdr",
               "tblBorders", "tcBorders", "tblCellMar", "tcMar",
               "tblLayout", "tblInd", "textAlignment", "rPrChange",
               "pPrChange", "sectPrChange", "tblGridChange",
               "tblPrChange", "trPrChange", "tcPrChange", "position",
               "textDirection", "vAlign", "cnfStyle", "tblLook",
               "pPrDefault", "rPrDefault", "tblStylePr",
               "numPr", "pPr", "rPr", "sectPr", "tblPr",
               "trPr", "tcPr", "tblPrEx",
               "tblCellSpacing", "tblOverlap",
               "gridCol", "tblGrid", "tblHeader",
               "cantSplit", "gridBefore", "gridAfter", "wBefore",
               "wAfter", "hidden", "noWrap", "hideMark",
               "headerReference", "footerReference", "pgSz",
               "pgMar", "cols", "docGrid", "titlePg", "type",
               "pgNumType", "pgBorders", "lnNumType"}


_SUPPRESS_SKIP = {
    "document", "body", "p", "r", "t", "delText", "instrText",
    "delInstrText", "tbl", "tr", "tc", "hdr", "ftr", "footnotes",
    "endnotes", "footnote", "endnote", "comments", "comment",
    "styles", "style", "docDefaults", "name", "basedOn", "next",
    "link", "uiPriority", "qFormat", "semiHidden", "unhideWhenUsed",
    "numbering", "abstractNum", "num", "lvl", "lvlText", "lvlJc",
    "numFmt", "start", "abstractNumId", "pStyleLvl", "isLgl",
    "suff", "multiLevelType", "nsid", "tmpl", "styleLink",
    "numStyleLink", "lvlRestart", "lvlOverride", "startOverride",
    "ins", "del", "moveFrom", "moveTo", "cellIns", "cellDel",
    "cellMerge", "moveFromRangeStart", "moveFromRangeEnd",
    "moveToRangeStart", "moveToRangeEnd", "bookmarkStart",
    "bookmarkEnd", "commentRangeStart", "commentRangeEnd",
    "commentReference", "footnoteReference", "endnoteReference",
    "footnoteRef", "endnoteRef", "annotationRef", "separator",
    "continuationSeparator", "fldChar", "fldSimple", "hyperlink",
    "sdt", "sdtPr", "sdtContent", "sdtEndPr", "customXml",
    "customXmlPr", "drawing", "pict", "object", "txbxContent",
    "br", "cr", "noBreakHyphen", "softHyphen", "sym", "ptab",
    "smartTag", "smartTagPr", "proofErr", "bookmark", "rFonts",
    "id", "placeholder", "showingPlcHdr",
    "lsdException",
    "background", "attachedTemplate", "settings", "webSettings",
}



ONOFF = {
    "b", "bCs", "i", "iCs", "caps", "smallCaps", "strike", "dstrike",
    "outline", "shadow", "emboss", "imprint", "vanish", "specVanish",
    "webHidden", "noProof", "snapToGrid", "rtl", "cs", "oMath",
    "keepNext", "keepLines", "pageBreakBefore", "widowControl",
    "suppressAutoHyphens", "suppressOverlap", "suppressLineNumbers",
    "kinsoku", "wordWrap", "overflowPunct", "topLinePunct",
    "autoSpaceDE", "autoSpaceDN", "bidi", "adjustRightInd",
    "contextualSpacing", "mirrorIndents", "cantSplit", "tblHeader",
    "hidden", "noWrap", "hideMark", "titlePg", "evenAndOddHeaders",
    "formProt", "noEndnote", "bookFoldPrinting", "autoRedefine",
    "qFormat", "semiHidden", "unhideWhenUsed", "personal",
    "personalCompose", "personalReply", "isLgl", "tblOverlap",
}


def _val(el, attr: str = "val"):
    return None if el is None else el.get(W + attr)


def _on(el) -> bool:
    """OOXML boolean: absent=off; present w/o val=on; val=0/false=off.
    Everyone hand-writing OOXML trips once: <w:b/> is ON, <w:b w:val="0"/> is OFF."""
    if el is None:
        return False
    return el.get(W + "val") not in ("0", "false", "off")


def _shd(sh):
    """w:shd -> uniform form: `fill` or `val:fill`, pattern color as `:c=`.
    All three call sites (para/run/cell) share THIS ONE function --
    bg= used to mean fill:color while shd= meant val:fill: same shape, different meaning."""
    if sh is None:
        return None
    v, fill, col = _val(sh), sh.get(W + "fill"), sh.get(W + "color")
    # theme fill is the SAME fact as fill, expressed against the theme --
    # `~name` is the view's existing "theme reference" notation (see the
    # ~minorHAnsi font refs). It used to be on @skip, so two documents
    # whose shading differed only by theme rendered identically and
    # --diff saw nothing (dxv2-6 review B2).
    thm = sh.get(W + "themeFill")
    if thm:
        fill = "~" + thm
        for at, sign in (("themeFillTint", "+"), ("themeFillShade", "-")):
            tv = sh.get(W + at)
            if tv:
                fill += f"{sign}{tv}"
    if v in (None, "clear", "nil"):
        base = fill if fill not in (None, "auto") else None
        if base is None and col in (None, "auto"):
            return None
        base = base or "clear"
    else:
        base = f"{v}:{fill or 'auto'}"
    if col not in (None, "auto", "000000"):
        base += f":c={col}"
    return base


def rpr_facts(rpr) -> dict:
    """One w:rPr -> fact dict. The extent of character-level formatting."""
    d: dict = {}
    if rpr is None:
        return d
    f = rpr.find(q("rFonts"))
    if f is not None:
        for a, key in (("ascii", "latin"), ("eastAsia", "eastAsia"),
                       ("hAnsi", "hAnsi"), ("cs", "cs")):
            # Theme attr names are asciiTheme/../**cstheme** (lowercase t,
            # OOXML's own inconsistency). `~` prefix = theme ref; real name on @theme.
            tk = W + ("cstheme" if a == "cs" else a + "Theme")
            v, tv = f.get(W + a), f.get(tk)
            if v:
                d[key] = v
            elif tv:
                d[key] = "~" + tv
        if f.get(W + "hint"):
            d["hint"] = f.get(W + "hint")
    sz = _val(rpr.find(q("sz")))
    if sz:
        d["size"] = half_pt(sz)
    szcs = _val(rpr.find(q("szCs")))
    if szcs and szcs != sz:
        d["sizeCs"] = half_pt(szcs)          # only informative when it differs from latin size
    for tag, key in _RPR_BOOL.items():
        el = rpr.find(q(tag))
        if el is not None:
            d[key] = _on(el)
    for tag, key in (("bCs", "b"), ("iCs", "i")):
        el = rpr.find(q(tag))
        if el is not None and _on(el) != d.get(key, False):
            d[key + "Cs"] = _on(el)
    u = rpr.find(q("u"))
    if u is not None:
        d["u"] = (_val(u) or "single") + \
            (f":#{u.get(W + 'color')}" if u.get(W + "color") not in
             (None, "auto") else "")
    col = rpr.find(q("color"))
    if col is not None:
        v, t = _val(col), col.get(W + "themeColor")
        base = f"theme:{t}" if t else (f"#{v}" if v and v != "auto"
                                       else "auto")
        for tw in ("themeTint", "themeShade"):   # visible color math --
            x = col.get(W + tw)                  # dropping it made a
            if x:                                # 00->FF change diff to zero
                base += f":{tw[5].lower()}{x}"
        d["color"] = base
    hl = _val(rpr.find(q("highlight")))
    if hl:
        d["highlight"] = hl
    ft = rpr.find(q("fitText"))
    if ft is not None:
        d["fitText"] = twip(ft.get(W + "val"))
    va = _val(rpr.find(q("vertAlign")))
    if va:
        d["vert"] = va
    sp = _val(rpr.find(q("spacing")))
    if sp:
        d["charSpacing"] = twip(sp)          # rPr spacing is letter-spacing, not paragraph
    pos = _val(rpr.find(q("position")))
    if pos:
        d["raise"] = half_pt(pos)
    st = _val(rpr.find(q("rStyle")))
    if st:
        d["cstyle"] = st
    lang = rpr.find(q("lang"))
    if lang is not None:
        seen, parts_ = set(), []
        for a in ("val", "eastAsia", "bidi"):
            v = lang.get(W + a)
            if v:
                v = v.split("-")[0]
                if v not in seen:
                    seen.add(v)
                    parts_.append(v)
        if parts_:
            d["lang"] = "/".join(parts_)     # main language only; exact values via --raw
    s = _shd(rpr.find(q("shd")))
    if s:
        d["rshd"] = s
    if rpr.find(q("rPrChange")) is not None:
        d["Δfmt"] = True                     # has a format revision; old values via --raw
    return d


def ppr_facts(ppr) -> dict:
    """One w:pPr -> fact dict. The extent of paragraph-level formatting."""
    d: dict = {}
    if ppr is None:
        return d
    st = _val(ppr.find(q("pStyle")))
    if st:
        d["style"] = st
    jc = _val(ppr.find(q("jc")))
    if jc:
        d["align"] = jc
    sp = ppr.find(q("spacing"))
    if sp is not None:
        # beforeLines (1/100 line) beats before (twips); autospacing overrides both.
        for lines, twips_, key in (("beforeLines", "before", "before"),
                                   ("afterLines", "after", "after")):
            auto = sp.get(W + twips_ + "Autospacing")
            lv, tv = sp.get(W + lines), sp.get(W + twips_)
            if auto not in (None, "0", "false"):
                d[key] = "auto"
            elif lv:
                d[key] = f"{int(lv)/100:g}line" + (f"({twip(tv)})" if tv else "")
            elif tv:
                d[key] = twip(tv)
        line, rule = sp.get(W + "line"), sp.get(W + "lineRule")
        if line:
        # Same number, two meanings: 1/240 line when auto, twips when exact/atLeast.
            if rule in (None, "auto"):
                d["line"] = f"{int(line)/240:g}x"
            else:
                d["line"] = f"{twip(line)}{'!' if rule == 'exact' else '+'}"
    ind = ppr.find(q("ind"))
    if ind is not None:
        # **chars and pt are two independent inheritance channels** -- store as
        # separate keys. Folded into one key, a style firstLineChars=200 plus a
        # direct firstLine=400 lets pt overwrite the whole key: the view prints
        # 20pt, Word renders 2ch. The write-side form of "clearing only firstLine clears nothing".
        for a, ac, key in (("firstLine", "firstLineChars", "first"),
                           ("hanging", "hangingChars", "hanging"),
                           ("left", "leftChars", "indL"),
                           ("start", "startChars", "indL"),
                           ("right", "rightChars", "indR"),
                           ("end", "endChars", "indR")):
            v, vc = ind.get(W + a), ind.get(W + ac)
            if vc is not None:
                d[key + "•ch"] = f"{int(vc)/100:g}ch"
            if v is not None:
                d[key + "•pt"] = twip(v)
    for tag, key in (("keepNext", "keepNext"), ("keepLines", "keepLines"),
                     ("pageBreakBefore", "pageBreak"),
                     ("widowControl", "widow"), ("bidi", "rtl-p")):
        el = ppr.find(q(tag))
        if el is not None:
            d[key] = _on(el)
    lvl = _val(ppr.find(q("outlineLvl")))
    if lvl is not None:
        d["outline"] = lvl                   # raw 0-based value (legal domain 0-9), no +1
    for btag, key in (("snapToGrid", "snapGrid"),
                      ("contextualSpacing", "ctxSpacing")):
        el2 = ppr.find(q(btag))
        if el2 is not None:
            d[key] = "on" if _on(el2) else "off"  # value form:
            # the interesting case is the explicit OFF (bool
            # False only prints with show_off)
    num = ppr.find(q("numPr"))
    if num is not None:
        nid, il = _val(num.find(q("numId"))), _val(num.find(q("ilvl")))
        if nid == "0":
            d["list"] = "none"               # numId=0 = numbering explicitly cancelled
        elif nid:
            d["list"] = f"{nid}.L{il or 0}"
    s = _shd(ppr.find(q("shd")))
    if s:
        d["shd"] = s
    bdr = ppr.find(q("pBdr"))
    if bdr is not None:
        sides = [c.tag.split("}")[1] for c in bdr
                 if c.get(W + "val") not in (None, "none", "nil")]
        if sides:
            d["border"] = "+".join(sides)
    tabs = ppr.find(q("tabs"))
    if tabs is not None:
        ts = []
        for t in tabs.findall(q("tab")):
            if t.get(W + "val") == "clear":
                continue
            s2 = f"{t.get(W + 'val', 'left')}@{twip(t.get(W + 'pos'))}"
            ld = t.get(W + "leader")
            if ld not in (None, "none"):
                s2 += f":{ld}"               # TOC dot leaders come from leader, not periods
            ts.append(s2)
        if ts:
            d["tabs"] = ",".join(ts)
    fr = ppr.find(q("framePr"))
    if fr is not None:
        keep = [f"{k}=" + (twip(fr.get(W + k)) if k in ("w", "h")
                           else fr.get(W + k))
                for k in ("w", "h", "vAnchor", "hAnchor") if fr.get(W + k)]
        d["frame"] = ",".join(keep) if keep else "yes"
    ta = _val(ppr.find(q("textAlignment")))
    if ta:
        d["vAlign"] = ta
    if ppr.find(q("pPrChange")) is not None:
        d["Δfmt"] = True
    return d


# ---------------------------------------------------------------- indirection

class Styles:
    """Style-chain resolution -- the one piece of domain knowledge here
    (not written in the XML): effective = docDefaults -> basedOn chain -> direct."""

    def __init__(self, root):
        self.p_of: dict = {}
        self.r_of: dict = {}
        self.based: dict = {}
        self.name: dict = {}
        self.default_p = None
        self.dp, self.dr = {}, {}
        if root is None:
            return
        dd = root.find(q("docDefaults"))
        if dd is not None:
            self.dp = ppr_facts(dd.find(f"{q('pPrDefault')}/{q('pPr')}"))
            self.dr = rpr_facts(dd.find(f"{q('rPrDefault')}/{q('rPr')}"))
        for st in root.findall(q("style")):
            sid = st.get(W + "styleId")
            self.p_of[sid] = ppr_facts(st.find(q("pPr")))
            self.r_of[sid] = rpr_facts(st.find(q("rPr")))
            b = _val(st.find(q("basedOn")))
            if b:
                self.based[sid] = b
            n = _val(st.find(q("name")))
            if n:
                self.name[sid] = _safe(n)
            if st.get(W + "type") == "paragraph" and \
                    st.get(W + "default") in ("1", "true"):
                self.default_p = sid

    def chain(self, sid):
        out, seen = [], set()
        while sid and sid not in seen and sid in self.p_of:
            seen.add(sid)
            out.append(sid)
            sid = self.based.get(sid)
        return list(reversed(out))

    def effective(self, sid, direct_p: dict, direct_r: dict):
        p, r = dict(self.dp), dict(self.dr)
        for s in self.chain(sid or self.default_p):
            p.update(self.p_of.get(s, {}))
            r.update(self.r_of.get(s, {}))
        p.update(direct_p)
        r.update(direct_r)
        return p, r


class Numbering:
    """numbering.xml: numId -> abstractNum -> per-level definitions.
    Printing only `list=1.L0` hides that Word renders "Chapter One" -- the model
    would paste the number text into the body (the duplicated-heading incident)."""

    def __init__(self, root):
        self.abs_of: dict = {}
        self.lvls: dict = {}
        self.ovr: dict = {}
        if root is None:
            return
        for num in root.findall(q("num")):
            nid = num.get(W + "numId")
            self.abs_of[nid] = _val(num.find(q("abstractNumId")))
            # resolve override VALUES: a bare "!override" flag told the
            # model something changed but not what -- restart values are
            # the whole point of lvlOverride
            for ov in num.findall(q("lvlOverride")):
                il = ov.get(W + "ilvl")
                so = _val(ov.find(q("startOverride")))
                self.ovr.setdefault(nid, []).append(
                    f"L{il}={so}" if so is not None else f"L{il}")
        for an in root.findall(q("abstractNum")):
            aid = an.get(W + "abstractNumId")
            for lvl in an.findall(q("lvl")):
                self.lvls[(aid, lvl.get(W + "ilvl"))] = {
                    "fmt": _val(lvl.find(q("numFmt"))),
                    "text": _val(lvl.find(q("lvlText"))),
                    "start": _val(lvl.find(q("start"))),
                    "suff": _val(lvl.find(q("suff")))}

    def line(self, nid: str, il: str):
        d = self.lvls.get((self.abs_of.get(nid), il))
        if not d:
            return None
        s = f"@num {nid}.L{il} {d['fmt'] or '?'} \"{d['text'] or ''}\""
        if d["start"] not in (None, "1"):
            s += f" start={d['start']}"
        if d["suff"] not in (None, "tab"):
            s += f" suff={d['suff']}"
        if nid in self.ovr:
            s += " restart@" + ",".join(self.ovr[nid])
        return s


def theme_line(parts) -> str | None:
    """theme1.xml font scheme -> one line. `~minorHAnsi` refs resolve against it."""
    root = parse(parts, "word/theme/theme1.xml")
    if root is None:
        return None
    fs = root.find(f".//{A}fontScheme")
    if fs is None:
        return None
    bits = []
    for which, label in (("minorFont", "minor"), ("majorFont", "major")):
        f = fs.find(A + which)
        if f is None:
            continue
        lat = f.find(A + "latin")
        seg = f"{label}: latin={lat.get('typeface') or '-'}" \
            if lat is not None else f"{label}:"
        ea = f.find(A + "ea")
        if ea is not None and ea.get("typeface"):
            seg += f" ea={ea.get('typeface')}"
        for fo in f.findall(A + "font"):
            if fo.get("script") in ("Hans", "Hant"):
                seg += f" {fo.get('script')}={fo.get('typeface')}"
        bits.append(seg)
    return "@theme " + " | ".join(bits) if bits else None


# ---------------------------------------------------------------- render primitives

_FONT4 = ("latin", "eastAsia", "hAnsi", "cs")


def collapse_font(d: dict) -> dict:
    """Collapse the four font channels at print time (inheritance is
    per-channel; the fact layer must NOT fold): all four equal -> font=X;
    east-asian/cs equal and latin/hAnsi equal -> font=CJK|latin."""
    if not all(k in d for k in _FONT4):
        return d
    vals = {d[k] for k in _FONT4}
    out = {k: v for k, v in d.items() if k not in _FONT4}
    if len(vals) == 1:
        out["font"] = d["latin"]
        return out
    if d["eastAsia"] == d["cs"] and d["latin"] == d["hAnsi"]:
        out["font"] = f'{d["eastAsia"]}|{d["latin"]}'
        return out
    return d


_IND_KEYS = ("hanging", "first", "indL", "indR")


def collapse_ind(d: dict) -> dict:
    """Indent dual-channel print folding: chars wins when present -- that
    is Word's own resolution rule, and dual-writing both channels is
    normal Word output (the pt value is a shadow, deliberately not
    printed). hanging beats first (mutually exclusive)."""
    if not any(k + s in d for k in _IND_KEYS for s in ("•ch", "•pt")):
        return d
    out = {k: v for k, v in d.items() if "•" not in k}
    for k in _IND_KEYS:
        ch, pt = d.get(k + "•ch"), d.get(k + "•pt")
        if ch is not None or pt is not None:
            out[k] = ch if ch is not None else pt   # chars is the truth when double-written
    if "hanging" in out and out.get("hanging") not in ("0ch", "0pt") \
            and "first" in out:
        out.pop("first")         # firstLine is void while hanging is present
    return out


def _off(v) -> bool:
    return v is False or v is None or v == "none"


def _vq(v) -> str:
    """Quote values containing separators (;/|/space). Word emits fallback
    lists like w:ascii="Calibri;Arial Rounded MT Bold"; unquoted
    space-bearing font names shatter vdiff's signature tokenizer."""
    s = str(v)
    return f'"{s}"' if (";" in s or "|" in s or " " in s) \
        and not s.startswith('"') else s


def fmt_delta(d: dict, base: dict, show_off: bool = False) -> str:
    """Fact dict -> delta string. Booleans as +k/-k, others k=v.

    **The minus sign is only valid for boolean keys**: value keys are copied
    from the base then overridden -- a run cannot "clear" one, so `k not in d`
    only means no run / no dominant; printing -latin would be a lie (measured
    6423 all wrong). show_off=True also prints explicit False -- @style lines
    need it: <w:b w:val="0"/> in a style blocks inheritance."""
    d = collapse_ind(collapse_font(d))
    base = collapse_ind(collapse_font(base))
    out = []
    for k in sorted(d):
        v = d[k]
        both_off = _off(v) and _off(base.get(k))
        if base.get(k) == v or (both_off and not (show_off and v is False)):
            continue
        if v is True:
            out.append(f"+{k}")
        elif _off(v):
            out.append(f"-{k}")
        else:
            out.append(f"{k}={_vq(v)}")
    out += [f"-{k}" for k in sorted(base)
            if k not in d and isinstance(base[k], bool) and base[k]]
    return ";".join(out)


# ------------------------------------------------ notation v2: quoting
#: The view's promise is completeness (everything visible, exactly once),
#: not byte-matching: content lines hold REAL TEXT (accepted state, zero
#: escapes) plus (placeholders); everything else lives on └ annotation
#: lines and @cards. Quote conventions on └ lines:
#:   "…"  a fragment OF the content line (copy it as an anchor);
#:   «…»  text NOT in the content line (tracked-deleted / moved-away):
#:        the only copy -- never elided, never anchorable.

_QLIM = 12
_ORD = ("1st", "2nd", "3rd")


def _qwrap(disp: str) -> str:
    """Wrap a display fragment: " -> ' -> head/tail cascade (#8)."""
    if '"' not in disp:
        return f'"{disp}"'
    if "'" not in disp:
        return f"'{disp}'"
    return f'"{disp[:3]}"…"{disp[-3:]}"'


def locate(content: str, start: int, frag: str) -> str:
    """Locator quote for content[start:start+len(frag)]: full quote up to
    _QLIM chars, head…tail elision beyond, (2nd) when the fragment
    repeats. `frag`/`content` are RAW; the DISPLAYED quote is escaped to
    match the printed (escaped) content line -- a copied quote must land
    on the line byte-for-byte. Failure only costs annotation readability;
    the content line is never touched."""
    if not frag:
        return ""
    disp = frag if len(frag) <= _QLIM else frag[:5] + "…" + frag[-4:]
    q = _qwrap(wml.encode_text(disp))
    occ = [m.start() for m in re.finditer(re.escape(frag), content)]
    if len(occ) > 1 and start in occ:
        k = occ.index(start)
        q += f" ({_ORD[k] if k < 3 else str(k + 1) + 'th'})"
    return q


def one_line(lines: list) -> list:
    """THE structural belt: the view is a LIST OF LINES, so a line that
    contains a newline is content that has escaped into structure. Every
    payload source -- element text, ATTRIBUTES (bookmark/style/author
    names), file names, anything added later -- is neutralized here, at
    the one boundary where lines become the view.

    Call-site sanitizers (_safe) still handle backslash escaping for
    payloads; this belt deliberately does NOT touch backslashes, so it
    never double-escapes them. It exists because per-site sanitizing is
    a coverage promise nobody can keep: a review fixed six element-text
    sites and left bookmark names and comment authors forging @cmt
    cards (dxv2-3 follow-up audit)."""
    out = []
    for ln in lines:
        if any(c in ln for c in wml.LINE_BREAKERS) or "\t" in ln:
            ln = wml.kill_breaks(ln.replace("\t", "\\t"))
        out.append(ln)
    return out


def _safe(s: str) -> str:
    """Annotation/card payloads are single logical lines: any newline,
    CR or tab in CONTENT (dead text, field instr/cache, object desc,
    formula, inline comment text) is shown as a visible escape so it
    can never break out of its └ line or forge a @cmt/@fmt card
    (dxv2-3 review P1.3). Also strips the intern sentinels (\x01/\x02)
    on the off chance a document embeds them."""
    s = s.replace("\x01", "").replace("\x02", "")
    return wml.encode_ann(s)


def sid_out(sid: str | None) -> str:
    """A style id on its way INTO the view. Ids are dictionary keys
    internally (they must match `w:pStyle/@w:val` byte for byte), so they
    are escaped at PRINT time only -- through the same reversible codec
    as every other payload. A styleId holding a real U+2028 and one
    holding the six characters `\u2028` used to print identically, and
    `@style` is exactly where a model goes to learn what to write back
    (dxv2-7 review P1.5). `\\` escapes itself, so the printed form
    decodes back to the id unambiguously."""
    return _safe(sid or "")


def att(el, name: str, default: str = "") -> str:
    """THE gate for ATTRIBUTE-sourced payloads (author, bookmark/move
    name, initials, style display name, part file name...).

    Element text already routes through _safe; attributes did not, and
    the split was invisible: bookmark starts used encode_ann while
    bookmark ENDS used the raw value, `_who` never encoded at all. The
    consequences were both directions of ambiguity -- an author holding
    the six characters `\u2028` rendered identically to one holding a
    real U+2028, and an author holding `"` or `»` punched through the
    payload quotes it was printed inside. one_line() is the structural
    belt that keeps lines from breaking; it is deliberately NOT a codec,
    so it cannot make anything injective. This is the codec (dxv2-6
    review B3)."""
    return _safe(el.get(W + name, default) or default)


def dead(text: str) -> str:
    """«»-quote tracked-deleted text (the only copy: no elision ever;
    conservation counts it). Runs the annotation codec so a deleted
    » cannot close the quote early and a newline cannot forge a card
    (dxv2-5 review P1.5)."""
    return f"«{wml.encode_ann(text)}»"


class DocState:
    """Cross-block state for one story: open field/comment/bookmark
    ranges and the @range cards they close into."""

    def __init__(self):
        self.open_fields: list = []      # [{instr, start_block}]
        self.open_comments: dict = {}    # id -> start_block
        self.open_bookmarks: dict = {}   # id -> (name, start_block)
        self.range_cards: list = []      # (start_block, end_block, payload)
        self.move_dst: dict = {}         # move name -> block label (moveTo)
        self.move_src: dict = {}         # move name -> block label
        self.rev_years: set = set()


def _rev_date(el, ds: DocState | None) -> str:
    d = el.get(W + "date") or ""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", d)
    if not m:
        return ""
    if ds is not None:
        ds.rev_years.add(m.group(1))
        if len(ds.rev_years) > 1:
            return f"{m.group(1)}-{int(m.group(2))}/{int(m.group(3))}"
    return f"{int(m.group(2))}/{int(m.group(3))}"


def _who(el, ds: DocState | None) -> str:
    au = att(el, "author", "?")
    dt = _rev_date(el, ds)
    return f"{au} {dt}".rstrip()


def img_card(el, rels: dict) -> str:
    """Image/object -> annotation payload (size + file name + float mode)."""
    ext = el.find(f".//{WP}extent")
    size = (f" {emu_cm(ext.get('cx'))}x{emu_cm(ext.get('cy'))}"
            if ext is not None else "")
    anchor = el.find(f".//{WP}anchor")
    pos = ""
    if anchor is not None:
        # Floating vs inline images are different layout beasts: floats have wrap/z-order/absolute position.
        wrap = next((c.tag.split("}")[1] for c in anchor
                     if isinstance(c.tag, str)
                     and c.tag.split("}")[1].startswith("wrap")), "")
        pos = " float:" + (wrap or "anchor")
        if anchor.get("behindDoc") == "1":
            pos += "+behind"
    blip = el.find(f".//{A}blip")
    if blip is not None:
        rid = blip.get(R + "embed")
        tgt = rels.get(rid, rid) if rid else None
        return f"@img{size}{pos}" + (f" {tgt}" if tgt else "")
    ole = el.find(f".//{O}OLEObject")
    if not size:
        # OLE/VML sizes live in v:shape style="width:..;height:..", not wp:extent
        # -- object width is life-or-death for two-column layout tasks; @obj without size forces --raw
        V = "{urn:schemas-microsoft-com:vml}"
        shp = el.find(f".//{V}shape")
        if shp is not None and shp.get("style"):
            mw = re.search(r"width:([\d.]+)pt", shp.get("style"))
            mh = re.search(r"height:([\d.]+)pt", shp.get("style"))
            if mw and mh:
                size = f" {mw.group(1)}x{mh.group(1)}pt"
    V2 = "{urn:schemas-microsoft-com:vml}"
    idata = el.find(f".//{V2}imagedata")
    media = ""
    if idata is not None:
        rid = idata.get(R + "id")
        if rid:
            media = f" {rels.get(rid, rid)}"   # VML image keeps its media link
    if ole is not None:
        return f"@obj {ole.get('ProgID', '?')}{size}{pos}{media}"
    if media:
        return f"@img{size}{pos}{media} (vml)"
    return f"@obj{size}{pos}"


class SegWalker:
    """**Container-tree** walk of inline content -> notation v2 channels.

    One paragraph in, three channels out:
    - content: the ACCEPTED-STATE text (w:t reachable in this story,
      minus tracked deletions) plus (placeholders) for every non-text
      thing that occupies a position -- zero escapes, zero syntax;
    - annotations: stand-off └ entries quoting the content line;
    - sub_lines: nested stories (text boxes) rendered as indented blocks.

    p.iter() flattening used to pull runs directly -- text-box text leaked
    into the body, fields were invisible, comment anchors vanished; one
    root cause: flattening erases provenance. Every container class is
    modeled explicitly, with the safety net "descend into any unmodeled
    container that contains text" -- **silently dropping text is the
    worst failure mode**. Unmodeled non-text run children get an (xN)
    placeholder + `unexpanded` annotation: the third state ("content
    exists but the view neither shows nor flags it") stays impossible.
    """

    def __init__(self, styles: Styles, rels: dict, ds: DocState | None = None):
        self.styles, self.rels = styles, rels
        self.ds = ds if ds is not None else DocState()
        self.anchored: set = set()          # comment ids seen as ranges

    # ---------------- per-paragraph state
    class _P:
        __slots__ = ("buf", "live", "revs", "reqs", "ph", "fstack",
                     "blk", "cm_open", "bm_open", "movename", "boxes",
                     "dead")

        def __init__(self, blk):
            self.buf: list = []      # content pieces, in order
            self.live: list = []     # (start, end, eff, rev) rev=None|(kind,who)
            self.revs: list = []     # chronological revision events
            self.reqs: list = []     # deferred annotation requests
            self.ph = Counter()      # per-name placeholder counters
            self.fstack: list = []   # field frames
            self.blk = blk           # body block index (int) or None
            self.cm_open: dict = {}  # comment id -> content pos
            self.bm_open: dict = {}  # bookmark id -> (name, pos)
            self.movename: str | None = None
            self.boxes: list = []    # (ph, payload, [w:p...]) text boxes
            self.dead: tuple | None = None   # (kind, who) while inside
            #                                  a del/moveFrom subtree

    def _pos(self, st) -> int:
        return sum(len(s) for _k, s in st.buf)

    @staticmethod
    def _raw(st) -> str:
        """Raw logical content (placeholders verbatim) -- the coordinate
        space every span offset indexes into."""
        return "".join(s for _k, s in st.buf)

    @staticmethod
    def _encode(st) -> str:
        """Printed content line: escape placeholder-lookalikes on the
        FINAL text, grouping consecutive text pieces so a lookalike
        split across w:t boundaries is still caught; real placeholder
        tokens pass through untouched (injectivity)."""
        out, acc = [], []
        for k, s in st.buf:
            if k == "t":
                acc.append(s)
            else:
                if acc:
                    out.append(wml.encode_text("".join(acc)))
                    acc = []
                out.append(s)
        if acc:
            out.append(wml.encode_text("".join(acc)))
        return "".join(out)

    #: what a placeholder stands for when it must be recorded as TEXT
    #: (dead-text annotation / field cache) instead of taking a slot in
    #: the content line
    _PH_AS_TEXT = {"tab": "\t", "ptab": "\t", "br": "\n", "pgbr": "\n",
                   "colbr": "\n", "nl": "\n", "nbhy": "-", "shy": "",
                   "sym": "\ufffc", "img": "\ufffc", "obj": "\ufffc",
                   "shape": "\ufffc", "eq": "\ufffc", "x": "\ufffc",
                   "f": "\ufffc"}

    def _put(self, st, ph_name, numbered=True):
        """THE one placeholder entry point -- and the one containment
        gate. Nothing reaches the accepted-state content line from a
        DEAD subtree (del/moveFrom) or from inside a FIELD CACHE: both
        are recorded in their own channel instead. Enforcing this per
        branch was a coverage promise that leaked five ways
        (footnote/endnote refs, unknown children, text boxes, formulas,
        breaks inside a field cache -- dxv2-4 review P1.1/P1.2);
        enforcing it here is structural. -> bare name, or None when the
        caller must skip its annotation too."""
        if st.dead is not None:
            self._dead(st, self._PH_AS_TEXT.get(ph_name, "\ufffc"),
                       st.dead[0], st.dead[1])
            return None
        fr = st.fstack[-1] if st.fstack else None
        if fr is not None and fr["mode"] == "inline":
            if fr["phase"] == "cached":
                fr["cached"].append(self._PH_AS_TEXT.get(ph_name, "\ufffc"))
            return None
        return self._alloc(st, ph_name, numbered)

    def _alloc(self, st, ph_name, numbered=True):
        """Allocate a placeholder token straight into the content line,
        BYPASSING the containment gate. Callers that place the token
        somewhere else themselves (a nested field embedding its child's
        (fN) into the parent cache) use this; everything else uses
        _put."""
        if numbered:
            st.ph[ph_name] += 1
            tok = f"({ph_name}{st.ph[ph_name]})"
        else:
            tok = f"({ph_name})"
        st.buf.append(("ph", tok))
        return tok[1:-1]

    def _alloc_name(self, st, ph_name):
        """Just the next name (no content slot) -- for a placeholder
        that lives only inside another payload (nested field)."""
        st.ph[ph_name] += 1
        return f"{ph_name}{st.ph[ph_name]}"

    def _text(self, st, s: str, eff, rev):
        """Live text -> content, stored RAW and tagged 't'. Control
        characters are NOT turned into placeholders: they are literal
        TEXT (the matcher sees them in w:t), so they get backslash
        escapes at encode time -- a real <w:tab/> and a literal U+0009
        must not render alike (dxv2-4 review P1.3). Escaping is deferred
        to the FINAL logical text (see _encode) so text split as
        '甲(ta'+'b)乙' cannot dodge it."""
        if st.dead is not None:              # containment, same gate
            self._dead(st, s, st.dead[0], st.dead[1])
            return
        fr = st.fstack[-1] if st.fstack else None
        if fr is not None and fr["mode"] == "inline":
            if fr["phase"] == "cached":
                fr["cached"].append(s)
            return
        if not s:
            return
        s = s.replace("\x01", "").replace("\x02", "")
        a = self._pos(st)
        st.buf.append(("t", s))
        st.live.append((a, a + len(s), eff, rev))
        if rev is not None:
            k, who = rev
            if st.revs and st.revs[-1][0] == k and st.revs[-1][1] == who \
                    and st.revs[-1][3] == a:
                st.revs[-1][3] = a + len(s)          # extend span
            else:
                st.revs.append([k, who, a, a + len(s)])

    def _dead(self, st, s: str, kind: str, who: str):
        if not s:
            return
        p = self._pos(st)
        if st.revs and st.revs[-1][0] == kind and st.revs[-1][1] == who \
                and st.revs[-1][2] == "@" and st.revs[-1][3] == p:
            st.revs[-1][4] += s
        else:
            st.revs.append([kind, who, "@", p, s])   # dead event

    # ---------------- paragraph entry point
    def para(self, p, r_base: dict, blk=None, ph=None, dead=None):
        """`dead=(kind, who)` pre-seeds the containment gate: everything
        this paragraph contains is dead by INHERITANCE, not by carrying
        its own w:del. A row deleted at trPr level and a cell marked
        cellDel own their content that way -- Word drops the whole row/
        cell on accept even when the runs inside are plain w:t. Seeding
        _P.dead routes it all through the one gate (_text/_put) instead
        of asking the table renderer to re-derive deadness per site."""
        st = self._P(blk)
        if dead is not None:
            st.dead = dead
        r_base_of = r_base
        if ph is not None:
            st.ph = ph
        self._walk(p, r_base, st, rev=None)
        # close inline field frames left open: they are RANGE fields
        # (close in a later block) -- register and flush nothing (their
        # cached text already went to content in range mode)
        for fr in st.fstack:
            if fr["mode"] == "inline" and fr["phase"] == "cached":
                # a field still open at paragraph end is a RANGE field
                # (TOC etc.): its cached text is real body text on the
                # page, so it must NOT be swallowed -- it used to vanish
                # entirely (dxv2-4 review P1.2a). The @range card marks
                # the whole span as cache territory.
                txt = "".join(fr["cached"])
                fr["cached"] = []
                if txt:
                    a = self._pos(st)
                    st.buf.append(("t", txt))
                    st.live.append((a, a + len(txt), r_base_of, None))
        for bid, (nm, pos) in st.bm_open.items():
            # start here, end in a LATER block: register the open range
            # so the matching bookmarkEnd can close it into a @range
            # card -- printing only the start let two different spans
            # render identically (dxv2-5 review P1.2)
            st.reqs.append(("bookmark", nm, pos, pos))
            if isinstance(blk, int):
                self.ds.open_bookmarks[bid] = (nm, blk)
        st.bm_open.clear()
        for cid, pos in list(st.cm_open.items()):
            st.reqs.append(("comment-pt", cid, pos))
            self.ds.open_comments[cid] = blk             # extent via @range
        for fr in st.fstack:
            if fr["mode"] == "inline":
                # begin had no end in this story chunk: degrade to range
                fr["mode"] = "range"
                self.ds.open_fields.append(
                    {"instr": fr["instr"].strip(), "start": blk})
                for piece, item in fr["out"]:
                    st.buf.append(("t", piece))
                    if item:
                        st.live.append(item)
        st.fstack.clear()
        return st

    # ---------------- the walk
    def _walk(self, el, base_r, st, rev):
        for node in el:
            tag = node.tag
            if not isinstance(tag, str):
                continue
            if tag == q("r"):
                self._run(node, base_r, st, rev)
            elif tag == q("hyperlink"):
                rid, anc = node.get(R + "id"), node.get(W + "anchor")
                tgt = self.rels.get(rid, "?") if rid \
                    else (f"#{anc}" if anc else "?")
                a = self._pos(st)
                self._walk(node, base_r, st, rev)
                st.reqs.append(("link", a, self._pos(st), tgt))
            elif tag in (q("ins"), q("moveTo")):
                # an outer del/moveFrom DOMINATES: text inserted inside a
                # deletion is still deleted -- do not let the nested ins
                # flip it back to live (dxv2-3 review P1.1: moveFrom>ins
                # resurrected ghost text into the accepted state)
                if rev is not None and rev[0] in ("del", "move-from"):
                    self._walk(node, base_r, st, rev)
                else:
                    kind = "ins" if tag == q("ins") else "move-to"
                    self._walk(node, base_r, st,
                               rev=(kind, _who(node, self.ds)))
            elif tag in (q("del"), q("moveFrom")):
                kind = "del" if tag == q("del") else "move-from"
                who = (kind, _who(node, self.ds))
                prev = st.dead
                st.dead = prev or who        # outermost wins
                self._walk(node, base_r, st, rev=who)
                st.dead = prev
            elif tag == q("fldSimple"):
                instr = (node.get(W + "instr") or "").strip()
                fr = {"mode": "inline", "phase": "cached", "instr": instr,
                      "cached": [], "out": [], "deadtxt": [],
                      "deadwho": "", "dead": rev is not None
                      and rev[0] in ("del", "move-from"), "who":
                      rev[1] if rev else ""}
                st.fstack.append(fr)
                self._walk(node, base_r, st, rev)
                st.fstack.pop()
                self._close_field(st, fr)
            elif tag in (M + "oMath", M + "oMathPara"):
                oms = [node] if tag == M + "oMath" \
                    else node.findall(M + "oMath")
                jc = None
                if tag == M + "oMathPara":
                    j = node.find(f"{M}oMathParaPr/{M}jc")
                    jc = j.get(M + "val") if j is not None else None
                for om in oms:
                    ph = self._put(st, "eq")
                    if ph:
                        st.reqs.append(("obj", ph, "equation "
                                        + _safe("".join(om.itertext()))
                                        + (f" jc={jc}" if jc else "")))
            elif tag == q("bookmarkStart"):
                nm = node.get(W + "name") or ""
                if nm and not nm.startswith(("_Toc", "_GoBack")):
                    st.bm_open[node.get(W + "id")] = (nm, self._pos(st))
            elif tag == q("bookmarkEnd"):
                bid = node.get(W + "id")
                if bid in st.bm_open:
                    nm, a = st.bm_open.pop(bid)
                    st.reqs.append(("bookmark", nm, a, self._pos(st)))
                elif bid in self.ds.open_bookmarks:
                    nm, sb = self.ds.open_bookmarks.pop(bid)
                    if isinstance(sb, int) and isinstance(st.blk, int):
                        self.ds.range_cards.append(
                            (sb, st.blk,
                             f"bookmark {_safe(nm)}"))
            elif tag == q("commentRangeStart"):
                cid = node.get(W + "id")
                self.anchored.add(cid)
                st.cm_open[cid] = self._pos(st)
            elif tag == q("commentRangeEnd"):
                cid = node.get(W + "id")
                if cid in st.cm_open:
                    st.reqs.append(("comment", cid, st.cm_open.pop(cid),
                                    self._pos(st)))
                elif cid in self.ds.open_comments:
                    sb = self.ds.open_comments.pop(cid)
                    if isinstance(sb, int) and isinstance(st.blk, int):
                        self.ds.range_cards.append((sb, st.blk,
                                                    f"comment{cid}"))
            elif tag in (q("moveFromRangeStart"), q("moveToRangeStart")):
                st.movename = node.get(W + "name") or ""
            elif tag == q("sdt"):
                inner = node.find(q("sdtContent"))
                a = self._pos(st)
                if inner is not None:
                    self._walk(inner, base_r, st, rev)
                st.reqs.append(("sdt", a, self._pos(st)))
            elif tag in (q("smartTag"), q("customXml"), q("dir"), q("bdo"),
                         q("sdtContent")):
                self._walk(node, base_r, st, rev)
            elif wml.content(node) is not None:
                # mc:AlternateContent: render the ONE canonical branch
                # (Fallback, the project-wide wrapper policy) -- rendering
                # Choice AND Fallback both doubled the text into the
                # accepted state (dxv2-3 review P1.1c). The branch NOT
                # rendered is NAMED, never silently dropped: Word renders
                # Choice when it understands the extension, so a model
                # editing here must know another version of this content
                # exists.
                for sub in wml.content(node):
                    self._walk([sub], base_r, st, rev)
                if node.tag == MC + "AlternateContent":
                    for ch in node:
                        if isinstance(ch.tag, str) and \
                                ch.tag == MC + "Choice":
                            ph = self._put(st, "x")
                            req = ch.get("Requires") or "?"
                            st.reqs.append((
                                "obj", ph,
                                f"unexpanded mc:Choice Requires={req} "
                                "(Word renders THIS branch; view shows "
                                "the Fallback) --raw"))
            elif tag in (q("pPr"), q("proofErr"), q("bookmarkEnd"),
                         q("commentRangeEnd")):
                pass
            elif node.find(f".//{q('t')}") is not None or \
                    node.find(f".//{M}t") is not None:
                self._walk(node, base_r, st, rev)   # safety net: descend where there is text
            elif tag not in (q("moveFromRangeEnd"), q("moveToRangeEnd"),
                             q("permStart"), q("permEnd"),
                             q("ins"), q("del")):
                pass                                # no text, no position

    # ---------------- runs
    def _run(self, rnode, base_r, st, rev):
        rpr = rnode.find(q("rPr"))
        eff = dict(base_r)
        cs = _val(rpr.find(q("rStyle"))) if rpr is not None else None
        if cs:
            for s in self.styles.chain(cs):
                eff.update(self.styles.r_of.get(s, {}))
        facts = rpr_facts(rpr)
        facts.pop("Δfmt", None)
        eff.update(facts)
        rc = rpr.find(q("rPrChange")) if rpr is not None else None
        run_a = self._pos(st)
        fr = st.fstack[-1] if st.fstack else None
        # inside a del/moveFrom, NOTHING enters the accepted-state content
        # line -- not text, not tabs/breaks/hyphens. They were leaking as
        # (tab)/(br)/(nbhy) placeholders into the body (dxv2-3 P1.1a);
        # their existence is recorded in the «…» dead-text annotation.
        for c in rnode:
            ct = c.tag
            if not isinstance(ct, str):
                continue
            if ct == q("t"):
                s = c.text or ""
                if fr is not None and fr["mode"] == "inline":
                    if fr["phase"] != "cached":
                        pass
                    elif st.dead is not None:
                        # live-looking w:t under a del/moveFrom ancestor:
                        # the frame check used to win and file it as
                        # ACCEPTED cache. Dead wins -- same rule as _text.
                        fr["cached"].append("")
                        fr["deadtxt"].append(s)
                        fr["deadwho"] = fr["deadwho"] or st.dead[1]
                    else:
                        fr["cached"].append(s)
                elif rev is not None and rev[0] in ("del", "move-from"):
                    self._dead(st, s, rev[0], rev[1])
                else:
                    self._text(st, s, eff, rev)
            elif ct == q("delText"):
                who = rev[1] if rev else "?"
                kind = rev[0] if rev else "del"
                if fr is not None and fr["mode"] == "inline":
                    # NOT content -- but not nothing either. This line used
                    # to `append("")`, i.e. throw the characters away: a
                    # deleted field's cached result vanished from every
                    # channel (dxv2-6 review B1). Keep it in the frame's
                    # DEAD cache; _close_field renders it in «».
                    fr["cached"].append("")
                    fr["deadtxt"].append(c.text or "")
                    fr["deadwho"] = fr["deadwho"] or who
                else:
                    self._dead(st, c.text or "", kind, who)
            elif ct in (q("instrText"), q("delInstrText")):
                if fr is not None and fr["phase"] == "instr":
                    fr["instr"] += c.text or ""
                elif fr is None and self.ds.open_fields and \
                        self.ds.open_fields[-1].get("phase") != "cached":
                    self.ds.open_fields[-1]["instr"] += c.text or ""
            elif ct == q("fldChar"):
                ty = c.get(W + "fldCharType")
                if ty == "begin":
                    st.fstack.append({"mode": "inline", "phase": "instr",
                                      "instr": "", "cached": [], "out": [],
                                      "deadtxt": [], "deadwho": "",
                                      "dead": rev is not None and
                                      rev[0] in ("del", "move-from"),
                                      "who": rev[1] if rev else ""})
                    fr = st.fstack[-1]
                elif ty == "separate":
                    if fr is not None:
                        fr["phase"] = "cached"
                    elif self.ds.open_fields:
                        self.ds.open_fields[-1]["phase"] = "cached"
                elif ty == "end":
                    if st.fstack:
                        done = st.fstack.pop()
                        fr = st.fstack[-1] if st.fstack else None
                        self._close_field(st, done)
                    elif self.ds.open_fields:       # closes a cross-block field
                        rec = self.ds.open_fields.pop()
                        if isinstance(rec["start"], int) \
                                and isinstance(st.blk, int):
                            self.ds.range_cards.append(
                                (rec["start"], st.blk,
                                 f"field {_safe(rec['instr'].strip())}"
                                 " cached"))
                        else:
                            st.reqs.append(("obj", "field",
                                            _safe(rec["instr"].strip())
                                            + " spans blocks, cached"))
            elif ct == q("tab"):
                self._put(st, "tab", numbered=False)
            elif ct == q("ptab"):
                ph = self._put(st, "ptab")
                if ph:
                    bits = [c.get(W + k) for k in
                            ("alignment", "relativeTo", "leader")]
                    st.reqs.append(("obj", ph, "positional-tab "
                                    + " ".join(x for x in bits if x)))
            elif ct == q("br"):
                ty = c.get(W + "type")
                nm = {"page": "pgbr", "column": "colbr"}.get(ty, "br")
                self._put(st, nm, numbered=False)
            elif ct == q("cr"):
                self._put(st, "br", numbered=False)
            elif ct == q("noBreakHyphen"):
                self._put(st, "nbhy", numbered=False)
            elif ct == q("softHyphen"):
                self._put(st, "shy", numbered=False)
            elif ct == q("sym"):
                ph = self._put(st, "sym")
                if ph:
                    st.reqs.append(("obj", ph,
                                    f"symbol {c.get(W + 'char', '?')}"
                                    f"@{c.get(W + 'font', '?')}"))
            elif ct in (q("footnoteReference"), q("endnoteReference")):
                kind = "fn" if ct == q("footnoteReference") else "en"
                if st.dead is not None or (
                        st.fstack and st.fstack[-1]["mode"] == "inline"):
                    self._put(st, kind)      # gate records it elsewhere
                else:                        # id (not a counter) is the
                    st.buf.append(           # handle for note bodies
                        ("ph", f"({kind}{c.get(W + 'id')})"))
            elif ct == q("commentReference"):
                cid = c.get(W + "id")
                if cid not in self.anchored:
                    st.reqs.append(("comment-pt", cid, self._pos(st)))
            elif ct in (q("drawing"), q("object"), q("pict")):
                dead_obj = rev is not None and rev[0] in ("del", "move-from")
                tx = c.findall(f".//{q('txbxContent')}")
                card = img_card(c, self.rels)     # "@img ..." / "@obj ..."
                kind = "obj" if card.startswith("@obj") else "img"
                payload = card[len("@" + kind):].strip()
                if tx and not dead_obj and st.dead is None:
                    ph = self._put(st, "shape")
                    if ph:
                        blks = [b for t2 in tx for b in wml.blocks(t2)]
                        st.boxes.append((ph, "textbox "
                                         + (payload or "").strip(), blks))
                elif dead_obj or st.dead is not None:
                    # a DELETED drawing/textbox: its own body text is
                    # still document content and must be conserved, not
                    # collapsed to "del obj" (dxv2-5 review P1.2). Pull
                    # every text node out and put it in the dead channel.
                    # BOTH tags: a deleted textbox's runs carry delText,
                    # not w:t -- iterating only w:t lost every character
                    # of the box (dxv2-6 review B1).
                    boxtext = "".join(
                        t.text or "" for t2 in tx
                        for t in t2.iter(q("t"), q("delText")))
                    who = st.dead or (rev[0], rev[1])
                    if boxtext:
                        self._dead(st, boxtext, who[0], who[1])
                    st.reqs.append(("rev-obj", f"del {kind} {payload}"
                                    .strip() + f" {who[1]}"))
                else:
                    ph = self._put(st, kind)
                    if ph:
                        st.reqs.append(("obj", ph, payload))
            elif ct in (q("rPr"), q("lastRenderedPageBreak"),
                        q("annotationRef"), q("separator"),
                        q("continuationSeparator")):
                pass
            else:                               # the way out: named, never silent
                ph = self._put(st, "x")
                if ph:
                    st.reqs.append(("obj", ph,
                                    f"unexpanded w:{ct.split('}')[1]}"
                                    " --raw"))
        if rc is not None:
            run_b = self._pos(st)
            old = rpr_facts(rc.find(q("rPr")))
            was = fmt_delta(old, facts, show_off=True)
            st.reqs.append(("rPrChange", run_a, run_b,
                            was or "(same keys)", _who(rc, self.ds)))

    def _close_field(self, st, fr):
        """An inline field zone closed: placeholder + one annotation."""
        cached = "".join(fr["cached"])
        deadtxt = "".join(fr["deadtxt"])
        target = st.fstack[-1] if st.fstack else None
        if fr["dead"]:
            # instruction AND cached result are document characters; both
            # go to the DEAD channel, both through the annotation codec so
            # an instruction containing » cannot close the region early.
            body = f"del field {dead(fr['instr'].strip())}"
            if deadtxt:
                body += f" → {dead(deadtxt)}"
            st.reqs.append(("rev-obj", f"{body} {fr['who']}".strip()))
            return
        if deadtxt:
            # a LIVE field whose cache contains tracked deletions: the
            # field survives, those characters do not.
            st.reqs.append(("rev-obj", f"del in field cache "
                                       f"{dead(deadtxt)} {fr['deadwho']}"
                            .strip()))
        if target is not None and target["mode"] == "inline":
            # nested field: its result text flows into the parent's
            # cache, but its OWN instruction is a distinct fact that
            # used to vanish -- the inner PAGEREF/REF told you what the
            # cached number means (dxv2-5 review P1.2). Keep it: emit a
            # placeholder+annotation for the inner field too, and leave
            # its (fN) token inside the parent cache so the nesting is
            # visible.
            inner_instr = fr["instr"].strip()
            # the inner field's own instruction is a distinct fact that
            # must not vanish (dxv2-5 review P1.2): emit it as its own
            # (fN) annotation and drop the (fN) token into whichever
            # parent region is open -- its cache (a nested RESULT) or
            # its instruction (a nested field switch).
            if inner_instr:
                ph = self._alloc_name(st, "f")
                st.reqs.append(("field", ph, _safe(inner_instr) or "?",
                                _safe(cached)))
                token = f"({ph})"
            else:
                token = cached
            if target["phase"] == "cached":
                target["cached"].append(token)
            else:
                target["instr"] += token
            return
        ph = self._put(st, "f")
        if ph is None:
            return
        instr = _safe(fr["instr"].strip()) or "?"
        st.reqs.append(("field", ph, instr, _safe(cached)))


def dominant(items) -> dict:
    """Per-key majority (>=half) over a list of fact dicts."""
    keys = Counter()
    for d in items:
        keys.update(d.keys())
    out = {}
    for k in keys:
        c = Counter(str(d.get(k)) for d in items)
        v, n = c.most_common(1)[0]
        if n * 2 > len(items) and v != "None":
            for d in items:
                if str(d.get(k)) == v:
                    out[k] = d.get(k)
                    break
    return out


# ---------------------------------------------------------------- tables

def _cell_marks(tc) -> dict:
    d: dict = {}
    tcpr = tc.find(q("tcPr"))
    if tcpr is None:
        return d
    kind, val = measure.parse(tcpr.find(q("tcW")))
    if kind == "pct":
        d["w"] = measure.fmt_pct(val)
    elif kind == "dxa" and val:
        d["w"] = twip(val)
    va = _val(tcpr.find(q("vAlign")))
    if va:
        d["va"] = va
    for _k, _n in (("noWrap", "nowrap"), ("hideMark", "hidemark")):
        _e = tcpr.find(q(_k))
        if _e is not None:
            d[_n] = _on(_e)          # value, not presence
    tm = tcpr.find(q("tcMar"))
    if tm is not None:
        d["mar"] = "/".join(
            twip(_val(tm.find(q(k)), "w")) if tm.find(q(k)) is not None else "-"
            for k in ("top", "end", "bottom", "start"))    # T/R/B/L
    s = _shd(tcpr.find(q("shd")))
    if s:
        d["bg"] = s
    bd = tcpr.find(q("tcBorders"))
    if bd is not None:
        sides = []
        for c in bd:
            if c.get(W + "val") in (None, "none", "nil"):
                continue
            sz = c.get(W + "sz")            # eighth-points -> pt: 0.75 vs
            sides.append(c.tag.split("}")[1]  # 1.5 decides三线表 compliance
                         + (f":{eighth_pt(sz)}" if sz else ""))
        d["bd"] = "+".join(sides) if sides else "none"
    td = _val(tcpr.find(q("textDirection")))
    if td:
        d["dir"] = td
    return d


def iter_blocks(container):
    """Logical block children -- delegates to the project-wide walker
    (ONE wrapper policy: sdt/customXml/smartTag/mc:Fallback; a local
    reimplementation drifted and lost a customXml→sdt→p body,
    review #14)."""
    return wml.blocks(container)


def _col_of_cells(tr) -> list:
    """One row's (tc, starting column) list -- gridSpan makes cell index
    != column index, and trPr/gridBefore offsets the whole row (irregular
    tables start late; without it every cell in such a row was attributed
    to the wrong column)."""
    out, ci = [], 0
    gb = _val(tr.find(f"{q('trPr')}/{q('gridBefore')}"))
    if gb:
        ci = int(gb)
    for tc in wml.cells(tr):
        out.append((tc, ci))
        g = _val(tc.find(f"{q('tcPr')}/{q('gridSpan')}"))
        ci += int(g) if g else 1
    return out


def _rowspan(rows, ri, ci) -> int:
    """Rows spanned by a vMerge restart at (row ri, grid col ci)."""
    n = 1
    for tr in rows[ri + 1:]:
        hit = False
        for tc, cj in _col_of_cells(tr):
            if cj == ci:
                vm = tc.find(f"{q('tcPr')}/{q('vMerge')}")
                if vm is not None and _val(vm) != "restart":
                    hit = True
                break
        if not hit:
            break
        n += 1
    return n


def _tr_rev(tr):
    """Row-level tracked insert/delete (trPr/ins|del)."""
    trpr = tr.find(q("trPr"))
    if trpr is None:
        return None, None
    return trpr.find(q("ins")), trpr.find(q("del"))


_DEGRADE_LEN = 160


def table_card(tbl, idx, styles: Styles, walker: SegWalker,
               diff_mode=False) -> list:
    """Table -> header line + one line per row + └ row annotations.

    Baselines come in **three granularities**, each its own: cell props
    (width/borders/shading) are constant along a **column**, row props
    (height/alignment) along **rows**, fonts across the **whole table**.
    Wrong baseline granularity degrades differential encoding to full
    encoding (v1 lesson, kept).
    """
    grid = tbl.find(q("tblGrid"))
    cols = [twip(g.get(W + "w")) for g in grid.findall(q("gridCol"))] \
        if grid is not None else []
    rows = list(wml.rows(tbl))
    pr = tbl.find(q("tblPr"))
    bits = []
    tbl_anns = []
    if pr is not None:
        st = _val(pr.find(q("tblStyle")))
        if st:
            bits.append(f"style={st}")
        jc = _val(pr.find(q("jc")))
        if jc:
            bits.append(f"align={jc}")
        # tblW is the real width mode; tblGrid is advisory
        kind, val = measure.parse(pr.find(q("tblW")))
        if kind == "pct":
            bits.append(f"w={measure.fmt_pct(val)}")
        elif kind == "dxa" and val:
            bits.append(f"w={twip(val)}")
        elif kind in ("auto", "nil"):
            bits.append(f"w={kind}")
        bd = pr.find(q("tblBorders"))
        if bd is not None:
            sides = []
            for c in bd:
                if c.get(W + "val") in (None, "none", "nil"):
                    continue
                sz = c.get(W + "sz")
                sides.append(c.tag.split("}")[1]
                             + (f":{eighth_pt(sz)}" if sz else ""))
            # "borders=none" is TABLE-level only; hand-drawn cell borders
            # live in tcBorders and once made a ruled table read as unruled
            bits.append("borders=" + ("+".join(sides) if sides else "none"))
        lay = _val(pr.find(q("tblLayout")), "type")
        if lay:
            bits.append(f"layout={lay}")
        look = pr.find(q("tblLook"))
        if look is not None:             # conditional-format flags ARE
            flags = [k for k in ("firstRow", "lastRow", "firstColumn",
                                 "lastColumn", "noHBand", "noVBand")
                     if look.get(W + k) == "1"]
            lv = look.get(W + "val")
            if flags:
                bits.append("look=" + "+".join(flags))
            elif lv and lv.strip("0"):   # look=0000 = no flags = say nothing
                bits.append(f"look={lv}")
        kind, val = measure.parse(pr.find(q("tblCellSpacing")))
        if kind == "pct":
            bits.append(f"cellSpacing={measure.fmt_pct(val)}")
        elif kind == "dxa" and val:
            bits.append(f"cellSpacing={twip(val)}")
        elif kind == "invalid":
            bits.append(f"cellSpacing=⟪{val[0]}⟫?")
        if pr.find(q("tblOverlap")) is not None \
                and pr.find(q("tblpPr")) is not None:
            bits.append("overlap=" +      # only floats can overlap
                        (_val(pr.find(q("tblOverlap"))) or "overlap"))
        ti = pr.find(q("tblInd"))
        if ti is not None and ti.get(W + "w") not in (None, "0"):
            bits.append(f"indent={twip(ti.get(W + 'w'))}")
        cm = pr.find(q("tblCellMar"))
        if cm is not None and any(cm.find(q(k)) is not None
                                  for k in ("top", "end", "bottom",
                                            "start")):
            bits.append("mar=" + "/".join(
                twip(_val(cm.find(q(k)), "w")) if cm.find(q(k)) is not None
                else "-" for k in ("top", "end", "bottom", "start")))
        if pr.find(q("tblpPr")) is not None:
            bits.append("+float")
        tpc = pr.find(q("tblPrChange"))
        if tpc is not None:
            tbl_anns.append(f"tblPrChange was --raw {_who(tpc, walker.ds)}")
    if grid is not None and grid.find(q("tblGridChange")) is not None:
        tbl_anns.append("tblGridChange was --raw")
    # ---- column baselines: per-column majorities of cell/para props ----
    ncol = len(cols) or max((len(list(wml.cells(tr))) for tr in rows),
                            default=0)
    col_marks: list = [[] for _ in range(ncol + 1)]
    col_para: list = [[] for _ in range(ncol + 1)]
    rs = []
    for tr in rows:
        for tc, ci in _col_of_cells(tr):
            ci = min(ci, ncol)
            col_marks[ci].append(_cell_marks(tc))
            cell_rs: list = []
            for para in tc.findall(q("p")):
                pp = para.find(q("pPr"))
                sid = _val(pp.find(q("pStyle"))) if pp is not None else None
                pe, base = styles.effective(sid, ppr_facts(pp), {})
                col_para[ci].append(
                    {k: v for k, v in pe.items()
                     if k not in ("style", "Δfmt")})
                for r_ in para.iter(q("r")):
                    e = dict(base)
                    ef = rpr_facts(r_.find(q("rPr")))
                    ef.pop("Δfmt", None)
                    e.update(ef)
                    cell_rs.append(e)
            # ONE vote per cell (its own dominant), not one per run --
            # per-run voting once flipped the whole-table mode on a
            # template-fill task and produced a ghost format-only diff
            if cell_rs:
                rs.append(dominant(cell_rs))
    if diff_mode:
        coldef = [{} for _ in range(ncol + 1)]
        colp = [{} for _ in range(ncol + 1)]
        rdef = {}
    else:
        coldef = [dominant(m) if m else {} for m in col_marks]
        colp = [dominant(m) if m else {} for m in col_para]
        rdef = dominant(rs)
    if rdef:
        bits.append("cellr:" + fmtq(fmt_delta(rdef, {})))
    # ---- row baselines: majorities of row props ----
    def _row_marks(tr):
        d = {}
        trpr = tr.find(q("trPr"))
        if trpr is None:
            return d
        for _k, _n in (("tblHeader", "header-row"),
                       ("cantSplit", "cant-split"),
                       ("hidden", "hidden-row")):
            _e = trpr.find(q(_k))
            if _e is not None:
                d[_n] = _on(_e)      # value, not presence
        rj = _val(trpr.find(q("jc")))
        if rj:
            d["align"] = rj
        h = trpr.find(q("trHeight"))
        if h is not None:
            hr = h.get(W + "hRule")
            d["h"] = twip(h.get(W + "val")) + \
                ("!" if hr == "exact" else "+" if hr == "atLeast" else "")
        return d
    rowdef = {} if diff_mode else dominant([_row_marks(tr) for tr in rows])
    if rowdef:
        bits.append("row:" + fmtq(fmt_delta(rowdef, {})))
    # non-empty column baselines print on the card: cN[cell props]{para props}
    colbits = []
    for ci in range(ncol):
        cb = fmt_delta(coldef[ci], {})
        pb = fmt_delta(colp[ci], {})
        if cb or pb:
            colbits.append(f"c{ci + 1}"
                           + (f"[{fmtq(cb)}]" if cb else "")
                           + ("{" + fmtq(pb) + "}" if pb else ""))
    head = (f"[{idx} table {len(rows)}×{ncol}"
            + (" cols=" + "/".join(cols) if cols else "")
            + ((" " + " ".join(bits)) if bits else "")
            + ((" " + " ".join(colbits)) if colbits else "") + "]")
    out = [head]
    out += [" └ " + a for a in tbl_anns]
    tbl_ph = Counter()
    nested: list = []
    cell_boxes: list = []      # (label, blocks) text boxes inside cells

    def _range_marks(el, sink: list):
        for c in el:
            if not isinstance(c.tag, str):
                continue
            if c.tag == q("bookmarkStart"):
                nm = c.get(W + "name") or ""
                if nm and not nm.startswith(("_Toc", "_GoBack")):
                    sink.append(f"bookmark {_safe(nm)}")
            elif c.tag == q("commentRangeStart"):
                walker.anchored.add(c.get(W + "id"))
                sink.append(f"comment{c.get(W + 'id')}")
    _range_marks(tbl, tbl_anns)
    for ri, tr in enumerate(rows, 1):
        entries: list = []
        _range_marks(tr, entries)
        cells: list = []
        degrade = False
        r_ins, r_del = _tr_rev(tr)
        trpr = tr.find(q("trPr"))
        if trpr is not None and trpr.find(q("trPrChange")) is not None:
            entries.append(f"r{ri} trPrChange was --raw")
        for _g in ("gridBefore", "gridAfter"):
            gv = _val(tr.find(f"{q('trPr')}/{q(_g)}"))
            if gv and int(gv):
                # gridAfter was declared modeled and rendered nowhere --
                # its twin's asymmetry, caught by the generative sweep
                entries.append(f"r{ri} {_g}={gv}")
        cell_recs = []
        for tc, ci in _col_of_cells(tr):
            over = ci >= ncol
            ci = min(ci, ncol)
            ck = ci + 1
            tcpr = tc.find(q("tcPr"))
            centry: list = []
            prefix = ""
            # DEAD BY INHERITANCE: a row deleted at trPr level, or a cell
            # marked cellDel, takes its content with it on accept. Compute
            # it here, once, and hand it to the walker -- the cell text
            # used to be rendered as ACCEPTED state (cellDel) or dropped
            # entirely (trPr/del with plain w:t) (dxv2-6 review B1).
            cell_dead = None
            if r_del is not None:
                cell_dead = ("del", _who(r_del, walker.ds))
            elif tcpr is not None and tcpr.find(q("cellDel")) is not None:
                cell_dead = ("del", _who(tcpr.find(q("cellDel")), walker.ds))
            if tcpr is not None:
                vmg = tcpr.find(q("vMerge"))
                if vmg is not None:
                    if _val(vmg) == "restart":
                        centry.append(
                            f"rowspan={_rowspan(rows, ri - 1, ci)}")
                    else:
                        prefix = "^"
                g = _val(tcpr.find(q("gridSpan")))
                if g and int(g) > 1:
                    centry.append(f"colspan={g}")
                for revtag, kw in (("cellIns", "cellIns"),
                                   ("cellDel", "cellDel"),
                                   ("cellMerge", "cellMerge")):
                    ev = tcpr.find(q(revtag))
                    if ev is not None:
                        centry.append(f"{kw} {_who(ev, walker.ds)}")
                if tcpr.find(q("tcPrChange")) is not None:
                    centry.append("tcPrChange was --raw")
            dl = fmt_delta(_cell_marks(tc), {} if over else coldef[ci])
            if dl:
                centry.append(fmtq(dl))
            # ---- cell text: accepted state, ¶ joins paragraphs ----
            texts: list = []
            _range_marks(tc, centry)
            for child in iter_blocks(tc):
                if child.tag == q("p"):
                    pp = child.find(q("pPr"))
                    sid = _val(pp.find(q("pStyle"))) if pp is not None \
                        else None
                    _, rb = styles.effective(sid, {}, {})
                    pe, _ = styles.effective(sid, ppr_facts(pp), {})
                    pdl = fmt_delta(
                        {k: v for k, v in pe.items()
                         if k not in ("style", "Δfmt")},
                        {} if over else colp[ci])
                    if sid and sid != styles.default_p:
                        centry.append(f"@{sid_out(sid)}")
                    if child.get(W14 + "paraId"):
                        # SCOPE (support matrix): paraId prints for
                        # BLOCK-addressable paragraphs, where #id
                        # complements [N]. A cell paragraph's handle is
                        # its structural path (r1c1), and printing 598
                        # random 8-hex ids cost 17% of one corpus view
                        # with nothing to compress. Not silently
                        # dropped: @doc discloses the count, --raw has
                        # the bytes.
                        PARAID_OFF[0] += 1
                    if pdl:
                        # >1 paragraph in the cell: label which ¶ deviates
                        centry.append(fmtq(pdl) if not texts
                                      else f"¶{len(texts) + 1}:{fmtq(pdl)}")
                    stt = walker.para(child, rb, blk=None, ph=tbl_ph,
                                      dead=cell_dead)
                    c2, _dom2, alines, boxes2 = assemble(
                        stt, styles, rdef if not diff_mode else rb,
                        walker.ds, diff_mode=diff_mode)
                    texts.append(c2)
                    centry += alines
                    for phb, payload, bblks in boxes2:
                        # the box's BODY used to be dropped here: the
                        # cell only showed "(shape1) textbox --raw"
                        # (dxv2-4 review P1.5)
                        centry.append(f"{phb} {payload}".rstrip() + ":")
                        cell_boxes.append((f"{idx}.r{ri}c{ck}.{phb}",
                                           bblks))
                elif child.tag == q("tbl"):
                    texts.append("(tbl)")
                    nested.append((f"{idx}.r{ri}c{ck}", child))
            text = "¶".join(texts)
            if "|" in text or any("¶" in t for t in texts) \
                    or len(text) > _DEGRADE_LEN:
                degrade = True
            cells.append((ck, prefix, text, texts))
            if centry:
                entries.append(f"c{ck} " + " ".join(centry))
        rm = fmt_delta(_row_marks(tr), rowdef)
        if rm:
            entries.insert(0, f"r{ri} {fmtq(rm)}")
        if r_ins is not None:
            # row-level insert subsumes its cells' ins annotations (the
            # text is in the row line; one fact, one annotation)
            who = _who(r_ins, walker.ds)
            entries = [e for e in entries
                       if not re.match(rf"c\d+ ins .*{re.escape(who)}$", e)]
            entries.insert(0, f"r{ri} ins-row {who}")
        if r_del is not None:
            # dead text stays on the CELL del annotations (single copy,
            # conservation counts it there); the row line is bare
            entries.insert(0, f"r{ri} del-row {_who(r_del, walker.ds)}")
            out.append(f"  r{ri}")
            out += ["   └ " + " · ".join(entries)]
            continue
        if degrade:
            out.append(f"  r{ri}:")
            for ck, prefix, _text, texts in cells:
                for t2 in (texts or [""]):
                    ln = f"    c{ck} {prefix}{t2}"
                    # rstrip only a fully-empty slot: trailing spaces in
                    # CONTENT are the document's bytes (xml:space)
                    out.append(ln.rstrip() if not (prefix + t2) else ln)
        else:
            out.append(f"  r{ri} | " + " | ".join(
                (prefix + text) for _ck, prefix, text, _ts in cells))
        if entries:
            out.append("   └ " + " · ".join(entries))
    for lbl, sub in nested:
        out += ["  " + ln for ln in
                table_card(sub, lbl, styles, walker, diff_mode)]
    for lbl, bblks in cell_boxes:
        bw = SegWalker(styles, walker.rels, DocState())
        for k, b2 in enumerate(bblks):
            if b2.tag == q("tbl"):
                out += ["   " + ln for ln in
                        table_card(b2, f"{lbl}.{k}", styles, bw,
                                   diff_mode)]
                continue
            r2 = para_lines(b2, f"{lbl}.{k}", styles, bw, walker.rels,
                            diff_mode)
            if r2 is not None:
                out += ["   " + ln for ln in
                        rec_lines(r2, styles, bw, walker.rels,
                                  diff_mode=diff_mode)]
    return out


# ---------------------------------------------------------------- sections / parts

def sect_card(sp, rels: dict) -> str:
    b = []
    sz = sp.find(q("pgSz"))
    if sz is not None:
        b.append(f"{twip(sz.get(W + 'w'))}x{twip(sz.get(W + 'h'))}"
                 + ("(landscape)" if sz.get(W + "orient") == "landscape" else ""))
    mg = sp.find(q("pgMar"))
    if mg is not None:
        b.append("margin=" + "/".join(
            twip(mg.get(W + k)) for k in ("top", "right", "bottom", "left")))
    cols = sp.find(q("cols"))
    if cols is not None:
        n = cols.get(W + "num") or "1"
        sp_ = cols.get(W + "space")
        b.append(f"cols={n}" + (f":{twip(sp_)}" if sp_ and n != "1" else ""))
    dg = sp.find(q("docGrid"))
    if dg is not None and dg.get(W + "type") not in (None, "default"):
        b.append(f"grid={dg.get(W + 'type')}:{twip(dg.get(W + 'linePitch'))}")
    _tp = sp.find(q("titlePg"))
    if _tp is not None:
        b.append("+titlePg" if _on(_tp) else "-titlePg")
    t = _val(sp.find(q("type")))
    if t:
        b.append(f"type={t}")
    pn = sp.find(q("pgNumType"))
    if pn is not None:
        s = "pgnum=" + (pn.get(W + "fmt") or "decimal")
        if pn.get(W + "start"):
            s += f"@{pn.get(W + 'start')}"
        b.append(s)
    td = _val(sp.find(q("textDirection")))
    if td:
        b.append(f"dir={td}")
    if sp.find(q("pgBorders")) is not None:
        b.append("+pgBorders")
    if sp.find(q("lnNumType")) is not None:
        b.append("+lineNum")
    for ref in (sp.findall(q("headerReference"))
                + sp.findall(q("footerReference"))):
        kind = "hdr" if "header" in ref.tag else "ftr"
        # rId -> part name. Printing only header:default leaves the model guessing which file to edit.
        b.append(f"{kind}:{ref.get(W + 'type')}="
                 f"{rels.get(ref.get(R + 'id'), '?')}")
    return "@sec " + " ".join(b)


def comment_lines(parts, styles=None, part_name=None) -> list:
    """comments.xml + commentsExtended -> @cmt cards. Comment BODIES go
    through the same block renderer as the body text: the old
    all-w:t join lost tabs, paragraph boundaries, formats and whole
    tables, and a literal \n in a comment forged a non-card line
    (Ultra-review repros). Simple one-paragraph comments stay one
    line; anything richer renders as indented [cN.k] blocks."""
    part_name = part_name or "word/comments.xml"
    root = parse(parts, part_name)
    if root is None:
        return []
    ext = parse(parts, "word/commentsExtended.xml")
    done_of, parent_of = {}, {}
    if ext is not None:
        for ce in ext.iter(W15 + "commentEx"):
            pid = ce.get(W15 + "paraId")
            done_of[pid] = ce.get(W15 + "done")
            parent_of[pid] = ce.get(W15 + "paraIdParent")
    comments = root.findall(q("comment"))
    pid2cid = {}
    for c in comments:
        for p in c.findall(q("p")):
            if p.get(W14 + "paraId"):
                pid2cid[p.get(W14 + "paraId")] = c.get(W + "id")
    crels = load_rels(parts, part_name)
    lines = []
    for c in comments:
        cid, au = c.get(W + "id"), att(c, "author", "?")
        dt = (c.get(W + "date") or "")[:10]
        pids = [p.get(W14 + "paraId") for p in c.findall(q("p"))]
        last = pids[-1] if pids else None
        flag = " ✓done" if done_of.get(last) == "1" else ""
        par = parent_of.get(last)
        rep = f" ↳c{pid2cid.get(par, '?')}" if par else ""
        head = f"@cmt c{cid} {au} {dt}{flag}{rep}:"
        if styles is None:
            txt = _safe("".join(t.text or "" for t in c.iter(q("t"))))
            lines.append(f"{head} {txt}")
            continue
        cw = SegWalker(styles, crels, DocState())
        blks = list(wml.blocks(c))
        recs = []                          # (kind, obj, k)
        for k, b in enumerate(blks):
            if b.tag == q("tbl"):
                recs.append(("tbl", b, k))
            else:
                recs.append(("p", para_lines(b, f"c{cid}.{k}", styles,
                                             cw, crels), k))
        # one-line form ONLY for a single plain paragraph (no tables, no
        # annotations, no text boxes). Multi-paragraph comments render as
        # indented [cN.k] blocks -- a bare ¶ join made "a¶b"+"c" and
        # "a"+"b¶c" indistinguishable (dxv2-3 review P1.2c).
        plain = (len(recs) == 1 and recs[0][0] == "p" and recs[0][1]
                 and not recs[0][1]["anns"] and not recs[0][1]["boxes"])
        if plain:
            lines.append(f"{head} {recs[0][1]['content']}")
            continue
        lines.append(head)
        for kind, obj, k in recs:
            if kind == "tbl":
                lines += ["  " + ln for ln in
                          table_card(obj, f"c{cid}.{k}", styles, cw)]
            elif obj is None:
                lines.append(f"  [c{cid}.{k} empty]")
            else:
                lines += ["  " + ln for ln in
                          rec_lines(obj, styles, cw, crels)]
    return lines


def note_lines(parts, part_name: str, tag: str, mark: str,
               styles=None) -> list:
    """`part_name` is the FULL part name (rels-resolved)."""
    """Foot/endnotes in the SAME run syntax as the body -- flattening to
    plain text hid every format: bolding a footnote once produced an
    empty diff. Rels are PART-LOCAL: a hyperlink inside a footnote
    resolves in footnotes.xml.rels; the body walker's rels dict gave
    hdr:default=? question marks (or, worse, someone else's target)."""
    root = parse(parts, part_name)
    if root is None:
        return []
    walker = None
    if styles is not None:
        walker = SegWalker(styles, load_rels(parts, part_name),
                           DocState())
    nrels = load_rels(parts, part_name)
    out = []
    for fn in root.findall(q(tag)):
        if fn.get(W + "type") in ("separator", "continuationSeparator"):
            continue
        fid = fn.get(W + "id")
        if walker is None:
            out.append(f"@{mark} {fid}: " + _safe(
                "".join(t.text or "" for t in fn.iter(q("t")))))
            continue
        nw = SegWalker(styles, nrels, DocState())
        blks = list(wml.blocks(fn))
        recs = []
        for k, b2 in enumerate(blks):
            if b2.tag == q("tbl"):
                recs.append(("tbl", b2, k))
            else:
                recs.append(("p", para_lines(b2, f"{mark}{fid}.{k}",
                                             styles, nw, nrels), k))
        # single plain paragraph -> one line; richer (tables, text boxes,
        # multiple paragraphs, annotations) -> indented blocks, same as
        # comments (a footnote text box / table used to vanish silently)
        plain = (len(recs) == 1 and recs[0][0] == "p" and recs[0][1]
                 and not recs[0][1]["anns"] and not recs[0][1]["boxes"])
        if plain:
            out.append(f"@{mark} {fid}: {recs[0][1]['content']}")
            continue
        out.append(f"@{mark} {fid}:")
        for kind, obj, k in recs:
            if kind == "tbl":
                out += ["  " + ln for ln in
                        table_card(obj, f"{mark}{fid}.{k}", styles, nw)]
            elif obj is None:
                out.append(f"  [{mark}{fid}.{k} empty]")
            else:
                out += ["  " + ln for ln in
                        rec_lines(obj, styles, nw, nrels)]
    return out


# ---------------------------------------------------------------- main flow

def main_document(parts: dict) -> str:
    """The main document part, resolved via _rels/.rels's officeDocument
    relationship -- NOT hardcoded. Some generators legally name it
    something other than word/document.xml, and a hardcoded path made
    read.py die with 'word/document.xml not found' (dxv2-5 review
    P1.3). Falls back to the conventional name."""
    return opc.main_part(parts.get, parts)


def load(src: Path) -> dict:
    """.docx or unpacked dir -> {part name: bytes}.

    **Must collect .rels too**: relationship files end in .rels, not .xml
    -- v1 collected *.xml only, so hdr:default=? and hyperlink targets were
    all question marks: a whole class of resolution ran on an always-empty dict.
    """
    src = Path(src)
    keep = (".xml", ".rels")
    if src.is_dir():
        # name.endswith, NOT Path.suffix: the package root rels file is
        # `_rels/.rels` -- a dotfile whose suffix is '' -- and dropping
        # it made every dir-vs-zip diff report a ghost parts delta and
        # broke main-part resolution on renamed packages (review repro)
        return {str(p.relative_to(src)).replace("\\", "/"): p.read_bytes()
                for p in src.rglob("*") if p.name.endswith(keep)}
    with opc.CappedZip(src) as z:      # capped: zip-bomb defense
        if z.duplicates:
            raise SystemExit(
                "package contains duplicate entries: "
                + ", ".join(z.duplicates[:5])
                + " -- the view would silently pick one; run "
                "validate.py for the full report, then fix the package")
        # keep .xml/.rels PLUS parts a STORY relationship points at, so
        # a header/comments part with an odd extension is not silently
        # dropped (dxv2-5 review P1.3). Only story types: collecting
        # every relationship target dragged 4.6 MB of image binaries
        # into memory for a 0.4 MB document -- the view reads media
        # NAMES from rels, never media bytes.
        RT_ = ("http://schemas.openxmlformats.org/officeDocument/2006/"
               "relationships/")
        _STORY_RT = {RT_ + k for k in
                     ("officeDocument", "header", "footer", "comments",
                      "footnotes", "endnotes", "styles", "numbering",
                      "settings", "theme", "fontTable", "glossaryDocument")}
        referenced = set()
        for n in z.namelist():
            if n.endswith(".rels"):
                try:
                    rr = etree.fromstring(z.read(n))
                except etree.XMLSyntaxError:
                    continue
                base = opc.rels_owner(n)
                for rel in rr.findall(PR + "Relationship"):
                    if rel.get("TargetMode") == "External":
                        continue
                    if (rel.get("Type") or "") not in _STORY_RT:
                        continue
                    try:
                        t = opc.rel_target(target=rel.get("Target"),
                                           base_part=base)
                    except ValueError:
                        continue
                    if t:
                        referenced.add(t)
        return {n: z.read(n) for n in z.namelist()
                if n.endswith(keep) or n in referenced}


def parse(parts: dict, name: str):
    b = parts.get(name)
    return etree.fromstring(b) if b else None


def load_rels(parts: dict, part: str = "word/document.xml") -> dict:
    d, n = part.rsplit("/", 1)
    root = parse(parts, f"{d}/_rels/{n}.rels")
    if root is None:
        return {}
    return {r.get("Id"): r.get("Target")
            for r in root.findall(PR + "Relationship")}


def _empty_line(idxs: list, pids: dict | None = None) -> str:
    """Collapse empty paragraphs but **keep the indices** -- they are the handles; without them these blocks cannot be addressed."""
    pids = pids or {}
    if len(idxs) == 1:
        pid = pids.get(idxs[0])
        # empty paragraphs are addressable too: they carry spacing and
        # are the target of "delete the blank line" tasks, so their
        # stable handle must print (dxv2-4 review P2)
        return f"[{idxs[0]}" + _pid_out(pid) + " empty]"
    head = f"[{idxs[0]}-{idxs[-1]} empty×{len(idxs)}"
    # CONSERVATION: a folded run must still surface every paraId it
    # covers, or the ids of all but a lone empty paragraph vanished
    # (dxv2-5 review P1.1). Listed as N:id pairs so each stays
    # addressable despite the fold.
    have = [(i, pids[i]) for i in idxs if i in pids]
    if have:
        for _i, _p in have:
            PARAID_SEEN.add(_p)          # folded run: printed is printed
        head += " #" + ",".join(f"{i}:{p}" for i, p in have)
    return head + "]"


def _ctx(content: str, pos: int) -> str:
    """Neighbor context for a deleted fragment's position."""
    if not content:
        return ""
    if pos > 0:
        return f' after {_qwrap(content[max(0, pos - 4):pos])}'
    return f' before {_qwrap(content[:4])}'


_ANN_ORDER = ("rev", "comment", "fmt", "link", "field", "obj",
              "bookmark", "sdt")


def assemble(st, styles: Styles, r_base: dict, ds: DocState,
             mark_del=None, mark_ins=None, diff_mode=False):
    """Walked paragraph state -> (content, dom, ann_lines, sub_lines).

    dom is the run-majority format (prints in the bracket); ann_lines are
    └ payloads (category-grouped: same kind shares a line, kinds split).
    """
    content = SegWalker._raw(st)          # raw coords for span slicing
    content_out = SegWalker._encode(st)   # escaped line actually printed
    live = st.live
    dom = {} if diff_mode else dominant([e for _, _, e, _ in live])
    ann: dict = {k: [] for k in _ANN_ORDER}

    # ---- revisions: pair adjacent del+ins by author into replace
    revs = [list(r) for r in st.revs]
    all_dead = bool(revs) and not live and \
        all(r[2] == "@" for r in revs) and \
        not any(k == "t" for k, _ in st.buf)
    # all_ins: every live span carries ins revision of one author
    ins_whos = {r[3][1] for r in live if r[3] and r[3][0] == "ins"}
    all_ins = bool(live) and all(r[3] is not None and r[3][0] == "ins"
                                 for r in live) and len(ins_whos) == 1
    if all_dead and mark_del is not None:
        who = revs[0][1] if revs else _who(mark_del, ds)
        txt = "".join(r[4] for r in revs)
        ann["rev"].append(f"del-paragraph {who} {dead(txt)}")
        revs = []
    elif all_ins and mark_ins is not None:
        ann["rev"].append(f"ins-paragraph {ins_whos.pop()}")
        mark_ins = None
    if mark_del is not None and not all_dead:
        ann["rev"].append(f"del-paragraph-mark {_who(mark_del, ds)}")
    if mark_ins is not None:
        ann["rev"].append(f"ins-paragraph-mark {_who(mark_ins, ds)}")
    i = 0
    while i < len(revs):
        r = revs[i]
        kind, who = r[0], r[1]
        if r[2] == "@":                       # dead text at position r[3]
            pos, txt = r[3], r[4]
            nxt = revs[i + 1] if i + 1 < len(revs) else None
            if kind == "del" and nxt and nxt[0] == "ins" and nxt[1] == who \
                    and nxt[2] == pos:
                frag = content[nxt[2]:nxt[3]]
                ann["rev"].append(f"replace {dead(txt)}→"
                                  f"{locate(content, nxt[2], frag)} {who}")
                i += 2
                continue
            kw = "move-from" if kind == "move-from" else "del"
            extra = ""
            if kind == "move-from" and st.movename:
                dst = ds.move_dst.get(st.movename)
                # the move NAME is a pairing handle: fully rendered as the
                # partner's coordinate when the pair exists. When it does
                # NOT, the handle is the only fact there is, and printing
                # nothing made every unpaired move render alike.
                extra = (f" → [{dst}]" if dst is not None
                         else f" (unpaired: {_safe(st.movename)})")
            ann["rev"].append(f"{kw} {dead(txt)}{_ctx(content, pos)}"
                              f"{extra} {who}".rstrip())
            i += 1
        else:                                 # live ins/move-to span
            a, b = r[2], r[3]
            nxt = revs[i + 1] if i + 1 < len(revs) else None
            if kind == "ins" and nxt and nxt[0] == "del" and \
                    nxt[1] == who and nxt[2] == "@" and nxt[3] == b:
                ann["rev"].append(f"replace {dead(nxt[4])}→"
                                  f"{locate(content, a, content[a:b])} {who}")
                i += 2
                continue
            if all_ins and kind == "ins" and any(
                    x.startswith("ins-paragraph") for x in ann["rev"]):
                i += 1
                continue
            kw = "move-to" if kind == "move-to" else "ins"
            extra = ""
            if kind == "move-to" and st.movename:
                sc = ds.move_src.get(st.movename)
                extra = (f" ← [{sc}]" if sc is not None
                         else f" (unpaired: {_safe(st.movename)})")
            ann["rev"].append(
                f"{kw} {locate(content, a, content[a:b])}{extra} {who}")
            i += 1

    # ---- deferred requests
    for rq in st.reqs:
        k = rq[0]
        if k == "link":
            _, a, b, tgt = rq
            loc = locate(content, a, content[a:b]) or "(no text)"
            ann["link"].append(f"link {loc} → {_safe(tgt)}")
        elif k == "comment":
            _, cid, a, b = rq
            ann["comment"].append(
                f"comment{cid} {locate(content, a, content[a:b])}".rstrip())
        elif k == "comment-pt":
            ann["comment"].append(f"comment{rq[1]}")
        elif k == "field":
            _, ph, instr, cached = rq
            ann["field"].append(f"{ph} {instr} → " +
                                (f'"{cached}" cached' if cached
                                 else "(no cache)"))
        elif k == "obj":
            ann["obj"].append(f"{rq[1]} {_safe(str(rq[2]))}".rstrip())
        elif k == "rev-obj":
            ann["rev"].append(rq[1])
        elif k == "bookmark":
            _, nm, a, b = rq
            loc = locate(content, a, content[a:b])
            ann["bookmark"].append(f"bookmark {_safe(nm)} {loc}".rstrip())
        elif k == "sdt":
            _, a, b = rq
            if b > a:
                ann["sdt"].append(
                    f"sdt {locate(content, a, content[a:b])}")
            else:
                ann["sdt"].append("sdt (empty)")
        elif k == "rPrChange":
            _, a, b, was, who = rq
            loc = locate(content, a, content[a:b])
            ann["fmt"].append(f"rPrChange {loc} was {was} {who}".rstrip())

    # ---- run formats: adjacent equal-delta spans merge, then quote
    base_for_runs = dom if dom else r_base
    spans, cur = [], None
    for a, b, eff, _rv in live:
        dl = fmt_delta(eff, base_for_runs)
        if not dl:
            cur = None
            continue
        if cur is not None and cur[2] == dl and cur[1] == a:
            cur[1] = b
        else:
            cur = [a, b, dl]
            spans.append(cur)
    whole = len(spans) == 1 and spans[0][0] == 0 and \
        spans[0][1] == len(content) and content != ""
    by_delta = Counter(dl for _a, _b, dl in spans)
    summarized = set()
    for a, b, dl in spans:
        if whole:
            ann["fmt"].append(fmtq(dl))       # whole line: no quote needed
        elif by_delta[dl] >= 4:
            # script-alternation shape (CJK/latin runs flip fonts dozens
            # of times per line): per-span quotes double the text. The
            # FACT stays visible once; the exact chars go to --raw.
            if dl not in summarized:
                summarized.add(dl)
                ann["fmt"].append(f"{fmtq(dl)} ×{by_delta[dl]} scattered")
        else:
            ann["fmt"].append(
                f"{fmtq(dl)} {locate(content, a, content[a:b])}")

    lines = []
    for cat in _ANN_ORDER:
        if ann[cat]:
            lines.append(" · ".join(ann[cat]))
    return content_out, dom, lines, st.boxes


#: fmt-delta strings seen this render (candidates for @fmt interning)
DELTAS: Counter = Counter()

#: paraIds NOT printed this render (cell/note/box paragraphs -- see the
#: scope note in table_card). Disclosed on @doc so "not printed" is
#: never confusable with "not there".
#:
#: PARAID_SEEN is the LEDGER OF WHAT WAS ACTUALLY PRINTED, recorded at
#: the two sites that print a `#id`. The count used to be
#: `pid not in "\n".join(out)` -- a substring search over the whole
#: view -- so a document whose body happened to contain the text
#: "12345678" made a cell paragraph with paraId 12345678 count as
#: printed, and the disclosure silently dropped to zero (dxv2-7 review
#: P1.2). A ledger cannot be fooled by content that merely looks like
#: an id; a search always can.
PARAID_OFF = [0]
PARAID_SEEN: set = set()


def _pid_out(pid: str | None) -> str:
    """Format a printed paraId AND record it. The single place a `#id`
    enters the view -- print and ledger cannot drift apart."""
    if not pid:
        return ""
    PARAID_SEEN.add(pid)
    return f" #{pid}"

#: Structural markers around every FORMAT-DELTA payload. @fmt interning
#: substitutes ONLY inside these regions -- it is a positional operation
#: on structured fields produced by fmt_delta, never a text scan over
#: rendered lines (dxv2-3 review rule 3). Content can never carry them:
#: _text strips them from body text and _safe from every payload, so the
#: renderer is their only source.
FA, FB = "\x01", "\x02"


def fmtq(s: str) -> str:
    """Mark a format-delta string as interning territory (and count it
    as an alias candidate). Empty in -> empty out, so call sites can
    stay unconditional."""
    if not s:
        return ""
    DELTAS[s] += 1
    return FA + s + FB


def strip_marks(lines: list) -> list:
    return [ln.replace(FA, "").replace(FB, "") for ln in lines]


def para_lines(el, label, styles: Styles, walker: SegWalker, rels: dict,
               diff_mode=False):
    """One w:p -> structured record (see render). None = plain empty
    paragraph (foldable into [N-M empty×K])."""
    ppr = el.find(q("pPr"))
    sid = _val(ppr.find(q("pStyle"))) if ppr is not None else None
    p_base, r_base = styles.effective(sid, {}, {})
    pe, _ = styles.effective(sid, ppr_facts(ppr), {})
    blk = label if isinstance(label, int) else None
    st = walker.para(el, r_base, blk=blk)
    mark_rpr_el = ppr.find(q("rPr")) if ppr is not None else None
    mark_del = mark_ins = None
    if mark_rpr_el is not None:
        mark_del = mark_rpr_el.find(q("del"))
        mark_ins = mark_rpr_el.find(q("ins"))
    content, dom, ann_lines, boxes = assemble(
        st, styles, r_base, walker.ds, mark_del, mark_ins, diff_mode)
    pf = {k: v for k, v in pe.items() if k not in ("style", "Δfmt")}
    pb = {k: v for k, v in p_base.items() if k != "style"}
    fd = fmt_delta(pf, pb)
    if fd:
        m = re.search(r"frame=[^;\]]+", fd)
        if m:
            DELTAS[m.group(0)] += 1      # sub-bundle candidate (v1 lesson:
            #                              frame values repeat verbatim)
        fd = fmtq(fd)
    ppc = ppr.find(q("pPrChange")) if ppr is not None else None
    if ppc is not None:
        old_p = ppr_facts(ppc.find(q("pPr")))
        was = fmt_delta(old_p, ppr_facts(ppr), show_off=True)
        ann_lines.insert(0, f"pPrChange was {was or '(same keys)'} "
                            f"{_who(ppc, walker.ds)}")
    mark_rpr = rpr_facts(mark_rpr_el)
    mark_rpr.pop("Δfmt", None)
    isec = ppr.find(q("sectPr")) if ppr is not None else None
    dd = fmtq(fmt_delta(dom, r_base))
    if not content.strip() and not ann_lines and not fd and not dd \
            and not mark_rpr and isec is None and not boxes:
        return None
    if mark_rpr and not content.strip():
        md = fmt_delta(mark_rpr, r_base)
        if md:
            ann_lines.append(f"mark {md}")    # spacer paragraph: its
            #                                   height IS the mark format
    pid = el.get(W14 + "paraId")
    rec = {
        "label": label, "pid": pid,
        "style": sid, "star": sid == styles.default_p,
        "fd": fd, "dd": dd,
        "content": content, "anns": ann_lines,
        "boxes": boxes, "sec": isec, "el": el,
    }
    return rec


def rec_lines(rec, styles: Styles, walker: SegWalker, rels: dict,
              fold: set = frozenset(), diff_mode=False) -> list:
    """Record -> final view lines (bracket + content, └ lines, boxes)."""
    head = f"[{rec['label']}"
    head += _pid_out(rec["pid"])         # stable relocation hint (N is
    #                                      the snapshot coordinate)
    if rec["style"]:
        head += " @*" if rec["star"] else f" @{sid_out(rec['style'])}"
    if rec["fd"] and ("fd", rec["label"]) not in fold:
        head += " " + rec["fd"]
    if rec["dd"] and ("dd", rec["label"]) not in fold:
        head += " " + rec["dd"]
    if rec["sec"] is not None:
        head += " §"
    out = [head + "]" + (" " + rec["content"] if rec["content"] else "")]
    out += [" └ " + a for a in rec["anns"]]
    for ph, payload, blks in rec["boxes"]:
        out.append(f" └ {ph} {payload}".rstrip() + ":")
        box_walker = SegWalker(styles, rels, DocState())
        for k, b2 in enumerate(blks):
            lbl = f"{rec['label']}.{ph}.{k}"
            if b2.tag == q("tbl"):
                out += ["   " + ln for ln in
                        table_card(b2, lbl, styles, box_walker,
                                   diff_mode)]
                continue
            sub = para_lines(b2, lbl, styles, box_walker, rels,
                             diff_mode)
            if sub is None:
                continue
            out += ["   " + ln for ln in
                    rec_lines(sub, styles, box_walker, rels,
                              diff_mode=diff_mode)]
    return out


#: tblStylePr conditional-format regions, canonical print order
_TSP_ORDER = ("wholeTable", "firstRow", "lastRow", "firstCol", "lastCol",
              "band1Vert", "band2Vert", "band1Horz", "band2Horz",
              "neCell", "nwCell", "seCell", "swCell")


#: locator quotes ("…" not preceded by =) and dead-text «…» are content
#: fragments -- protected. VALUE quotes (font="Times New Roman") are part
#: of format bundles and must stay substitutable, or every bundle with a
#: quoted font name silently escaped interning (measured on case-16:
#: the document's TOP bundle never interned, +60k chars).
#: Locator quotes are always SPACE-preceded (` "frag"`); value quotes sit
#: directly after `=`. A (?<!=) guard alone mispaired: the closing value
#: quote (not =-preceded) opened a bogus protected span that swallowed
#: the rest of the bundle (measured: both top bundles escaped interning).
_QSPAN = re.compile(r'(?<= )"[^"]*"|«[^»]*»|(?<= )\x27[^\x27]*\x27')


def _sub_outside_quotes(s: str, pat, repl: str) -> str:
    """Substitute only outside protected quoted spans -- quotes on └
    lines carry content-line fragments and must stay verbatim."""
    out, last = [], 0
    for m in _QSPAN.finditer(s):
        out.append(pat.sub(repl, s[last:m.start()]))
        out.append(m.group(0))
        last = m.end()
    out.append(pat.sub(repl, s[last:]))
    return "".join(out)


def _fmt_name(bundle: str, taken: set) -> str:
    """Self-explaining alias: the bundle's most recognizable value."""
    cand = None
    m = re.search(r"font=~?([\w一-鿿]+)", bundle)
    if m:
        cand = m.group(1)[:6]
    if not cand:
        m = re.search(r"color=#(\w{3,6})", bundle)
        if m:
            cand = m.group(1)[:4]
    if not cand:
        m = re.search(r"size=([\d.]+)pt", bundle)
        if m:
            cand = m.group(1) + "pt"
    base = cand or "a"
    name, k = base, 1
    while name in taken:
        k += 1
        name = f"{base}{k}"
    taken.add(name)
    return name


def intern_bundles(lines: list) -> list:
    """@fmt interning, notation v2: repeated format-bundle strings become
    &name references defined once under the header. Measured motivation:
    one wild document repeated the SAME 47-char bundle 5351 times -- 42%
    of a 1MB view. Candidates come from DELTAS (only strings fmt_delta
    actually produced -- no false grabs); substitution touches bracket
    heads, └ payloads and card lines, NEVER content text or quotes."""
    def worth(s, n):
        return n >= 3 and (n - 1) * (len(s) - 2) - len(s) - 8 > 60
    winners = [(s, n) for s, n in DELTAS.items() if worth(s, n)]
    if not winners:
        return lines
    winners.sort(key=lambda kv: -(kv[1] * len(kv[0])))
    taken: set = set()
    defs, subs = [], []
    for s, _n in winners[:80]:
        nm = _fmt_name(s, taken)
        defs.append(f"@fmt &{nm}={s}")
        subs.append((re.compile(r"(?<![\w=#•~&.:+-])" + re.escape(s)
                                + r"(?![\w=#•~.:+-])"), f"&{nm}"))
    # Substitution is POSITIONAL: only the \x01…\x02 regions the
    # renderer wrapped around fmt_delta output are touched. Rendered
    # text is never scanned, so a field instruction / object description
    # / dead text / comment body that happens to spell a bundle string
    # can no longer be rewritten into an alias (dxv2-3 review rule 3;
    # the earlier line-prefix whitelist was both leaky -- └ lines carry
    # content -- and lossy: excluding └ lines wholesale cost 41% of the
    # compression on the alternating-font corpus).
    marked = re.compile(FA + "([^" + FA + FB + "]*)" + FB)

    def _one(m):
        seg = m.group(1)
        for pat, rp in subs:
            seg = pat.sub(rp, seg)
        return seg
    out = [marked.sub(_one, ln) for ln in lines]
    # defs insert after the @doc line (position 1). The note rides the
    # LAST def as a standalone tail line -- inside a def line it could
    # be mistaken for part of the bundle string (bundles contain quoted
    # spaces); as its own line it cannot: &-expansion never reads it.
    defs.append("@fmt aliases above are assigned per document and are not "
                "comparable across files (same name ≠ same meaning)")
    return out[:1] + defs + out[1:]


def story_parts_by_type(parts: dict) -> dict:
    """rel type -> actual part name. Thin wrapper: the walker lives in
    opc so no consumer can hand-roll a fourth one (dxv2-7 review P1.1)."""
    return opc.parts_by_type(parts.get, parts)


def story_names(parts: dict) -> set:
    """Every text-bearing story part. Thin wrapper: the resolver lives in
    opc so read/validate/revisions cannot disagree about which parts a
    package has (dxv2-6 review B4)."""
    return opc.story_parts(parts.get, parts)


def _scan_rev_years(root, ds: DocState):
    """Prescan EVERY revision date's year: the compact M/D form is only
    legal when the whole document lives in one year, and the first
    revision rendered must already know that (order-dependent output
    once dropped the year on early revisions -- Ultra review P2)."""
    for el in root.iter(etree.Element):
        d = el.get(W + "date")
        if d:
            m = re.match(r"(\d{4})-", d)
            if m:
                ds.rev_years.add(m.group(1))


def render(src: Path, diff_mode: bool = False) -> str:
    DELTAS.clear()
    PARAID_OFF[0] = 0
    PARAID_SEEN.clear()
    parts = load(src)
    main = main_document(parts)
    doc = parse(parts, main)
    if doc is None:
        raise SystemExit(
            f"main document part not found (looked for {main!r} via "
            "_rels/.rels officeDocument relationship, then the "
            "conventional word/document.xml)")
    mdir = main.rsplit("/", 1)[0] if "/" in main else ""
    named0 = story_parts_by_type(parts)

    def _side(kind: str):
        """styles/numbering: by RELATIONSHIP first, then beside the main
        part, then the conventional path. `A or B` on an lxml Element is
        a bug in waiting -- an empty <w:styles/> is falsy today and will
        be truthy in a future lxml, and it printed a FutureWarning on
        every real document (stderr noise once broke a pipe test)."""
        for cand in (_rel_side.get(kind),
                     f"{mdir}/{kind}.xml" if mdir else f"{kind}.xml",
                     f"word/{kind}.xml"):
            if not cand:
                continue
            r = parse(parts, cand)
            if r is not None:
                return r
        return None

    _rel_side = {k: named0.get(k) for k in ("styles", "numbering")}
    styles_root = _side("styles")
    styles = Styles(styles_root)
    numb = Numbering(_side("numbering"))
    rels = load_rels(parts, main)
    ds = DocState()
    walker = SegWalker(styles, rels, ds)
    body = doc.find(q("body"))
    _scan_rev_years(body, ds)
    kids = list(body)

    # move-range prescan: move-from/move-to cross references need the
    # other end's block index before we reach it
    for i, el in enumerate(kids):
        for mfs in el.iter(q("moveFromRangeStart")):
            nm = mfs.get(W + "name")
            if nm:
                ds.move_src.setdefault(nm, i)
        for mts in el.iter(q("moveToRangeStart")):
            nm = mts.get(W + "name")
            if nm:
                ds.move_dst.setdefault(nm, i)

    out = []
    n_p = sum(1 for e in kids if e.tag == q("p"))
    n_t = sum(1 for e in kids if e.tag == q("tbl"))
    doc_line = (f"@doc view=2.2 blocks={len(kids)} paras={n_p} "
                f"tables={n_t} parts={len(parts)} "
                "legend=read.py --legend")
    out.append(doc_line)
    _DOC_LINE_AT = len(out) - 1

    # ---- indirection: styles / char styles / table styles / numbering.
    # Scanned over EVERY rendered story part, not just the body: a style
    # used only in a header was absent from @style lines, so an edit to
    # that style produced a ZERO view diff (measured escape).
    # style USE counts must cover every story, discovered the same way
    # everything else discovers them. A name-pattern scan undercounted
    # any package whose parts are legally named otherwise (dxv2-7 P1.1).
    scan_roots = [body]
    for n in sorted(story_names(parts) - {main}):
        r_ = parse(parts, n)
        if r_ is not None:
            scan_roots.append(r_)
    used_cnt = Counter()
    for root_ in scan_roots:
        for p in root_.iter(q("p")):
            pp = p.find(q("pPr"))
            sid = _val(pp.find(q("pStyle"))) if pp is not None else None
            used_cnt[sid or styles.default_p] += 1
    norm_sid = None
    if len(used_cnt) == 1:
        only = next(iter(used_cnt))
        if only is not None and used_cnt[only] >= 20:
            direct = sum(1 for root_ in scan_roots
                         for _ps in root_.iter(q("pStyle")))
            if direct >= used_cnt[only]:     # every para says it explicitly
                norm_sid = only
                out.append(f"@norm @{only} (every paragraph; "
                           "block lines omit it)")
    defined = set(styles.p_of) | set(styles.r_of)
    for sid, n in used_cnt.most_common():
        if sid is None:
            continue
        pe, re_ = styles.effective(sid, {}, {})
        nm = styles.name.get(sid, sid)
        star = "*" if sid == styles.default_p else ""
        if sid not in defined:
            # referenced but absent from styles.xml: Word silently falls
            # back to Normal. Listed unmarked, it read as "a style with
            # no properties" and agents built on it (dxv2-3 C5)
            out.append(f"@style {sid_out(sid)}{star} count={n} ⚠UNDEFINED "
                       "(no such style in styles.xml; Word falls back "
                       "to the default -- define it or drop the "
                       "reference)")
            continue
        out.append(f"@style {sid_out(sid)}{star} count={n}"
                   + (f' "{nm}"' if nm != sid else "")
                   + " p:" + fmtq(fmt_delta(pe, {}, show_off=True))
                   + " r:" + fmtq(fmt_delta(re_, {}, show_off=True)))
    cstyles = Counter(_val(rs) for root_ in scan_roots
                      for rs in root_.iter(q("rStyle")) if _val(rs))
    for sid, n in cstyles.most_common():
        nm = styles.name.get(sid, sid)
        out.append(f"@style {sid_out(sid)} char count={n}"
                   + (f' "{nm}"' if nm != sid else "")
                   + " r:" + fmtq(fmt_delta(styles.r_of.get(sid, {}), {},
                                             show_off=True)))
    # Table styles: cells inherit p:/r: from them, and the conditional
    # formats (tblStylePr firstRow/banding/...) render nowhere else --
    # without this line, editing "make header rows bold" in styles.xml
    # was invisible to the view and therefore to the diff gate.
    raw_style = {}
    if styles_root is not None:
        for st_ in styles_root.findall(q("style")):
            raw_style[st_.get(W + "styleId")] = st_
    tbl_used = Counter(_val(ts) for root_ in scan_roots
                       for ts in root_.iter(q("tblStyle")) if _val(ts))
    for sid, n in tbl_used.most_common():
        pe, re_ = styles.effective(sid, {}, {})
        nm = styles.name.get(sid, sid)
        line = (f"@style {sid_out(sid)} table count={n}"
                + (f' "{nm}"' if nm != sid else "")
                + " p:" + fmtq(fmt_delta(pe, {}, show_off=True))
                + " r:" + fmtq(fmt_delta(re_, {}, show_off=True)))
        per: dict = {}
        for s in styles.chain(sid):         # basedOn chain, later wins
            el_ = raw_style.get(s)
            if el_ is None:
                continue
            for tsp in el_.findall(q("tblStylePr")):
                ty = tsp.get(W + "type")
                if ty:
                    per[ty] = tsp
        for ty in _TSP_ORDER:
            tsp = per.get(ty)
            if tsp is None:
                continue
            sub = []
            pf = ppr_facts(tsp.find(q("pPr")))
            rf = rpr_facts(tsp.find(q("rPr")))
            if pf:
                sub.append("p:" + fmt_delta(pf, {}, show_off=True))
            if rf:
                sub.append("r:" + fmt_delta(rf, {}, show_off=True))
            s2 = _shd(tsp.find(f"{q('tcPr')}/{q('shd')}"))
            if s2:
                sub.append(f"bg={s2}")
            if sub:
                line += f" {ty}({' '.join(sub)})"
        out.append(line)
    nums_used = set()
    for root_ in scan_roots:
        for np_ in root_.iter(q("numPr")):
            nid = _val(np_.find(q("numId")))
            il = _val(np_.find(q("ilvl"))) or "0"
            if nid and nid != "0":
                nums_used.add((nid, il))
    for sid in used_cnt:
        lst = styles.p_of.get(sid, {}).get("list")
        if lst and lst != "none":
            nid, il = lst.split(".L")
            nums_used.add((nid, il))
    for nid, il in sorted(nums_used, key=lambda x: (int(x[0]), int(x[1]))):
        ln = numb.line(nid, il)
        if ln:
            out.append(ln)

    # ---- blocks: collect items first (range folding + range cards need
    # the whole list), then stringify ----
    items: list = []                # ("rec", i, rec) | ("lines", i, [...])
    empty_run: list = []
    empty_pids: dict = {}

    def flush():
        if empty_run:
            items.append(("lines", empty_run[0],
                          [_empty_line(list(empty_run), empty_pids)]))
            empty_run.clear()

    sec_of: dict = {}
    for i, el in enumerate(kids):
        if el.tag == q("p"):
            rec = para_lines(el, i, styles, walker, rels, diff_mode)
            if rec is None:
                empty_run.append(i)
                pid_ = el.get(W14 + "paraId")
                if pid_:
                    empty_pids[i] = pid_
                continue
            if norm_sid is not None and rec["style"] == norm_sid \
                    and not rec["star"]:
                rec["style"] = None
            flush()
            items.append(("rec", i, rec))
        elif el.tag == q("tbl"):
            flush()
            items.append(("lines", i,
                          table_card(el, i, styles, walker, diff_mode)))
        elif el.tag == q("sectPr"):
            flush()
            items.append(("lines", i, [("@SEC-BODY", el)]))
        elif wml.content(el) is not None:
            flush()
            wlines: list = []

            def emit_wrapped(wrap_el, prefix):
                if wrap_el.tag == MC + "AlternateContent":
                    for ch in wrap_el:
                        if isinstance(ch.tag, str) and ch.tag == MC + "Choice":
                            # same rule as the inline case: the branch
                            # Word actually renders must be NAMED, not
                            # silently dropped (dxv2-4 review P1.6)
                            wlines.append(
                                f"[{prefix} !mc:Choice Requires="
                                f"{ch.get('Requires') or '?'} "
                                "(Word renders THIS branch; view shows "
                                "the Fallback) --raw]")
                """ONE recursive emitter for every wrapper kind, using
                the project-wide wrapper policy (wml.content): sdt,
                customXml, smartTag, mc:AlternateContent nest freely
                and in ANY order. Physical [N.k...] provenance is
                preserved at each level."""
                wlines.append(f"[{prefix} ⟨{wrap_el.tag.split('}')[-1]}⟩]")
                for k2, sub in enumerate(wml.content(wrap_el)):
                    if sub.tag == q("p"):
                        r2 = para_lines(sub, f"{prefix}.{k2}", styles,
                                        walker, rels, diff_mode)
                        wlines.extend(
                            rec_lines(r2, styles, walker, rels,
                                      diff_mode=diff_mode)
                            if r2 else [f"[{prefix}.{k2} empty]"])
                    elif sub.tag == q("tbl"):
                        wlines.extend(table_card(sub, f"{prefix}.{k2}",
                                                 styles, walker,
                                                 diff_mode))
                    elif wml.content(sub) is not None:
                        emit_wrapped(sub, f"{prefix}.{k2}")
            emit_wrapped(el, str(i))
            items.append(("lines", i, wlines))
        else:
            flush()
            nm = el.get(W + "name")
            items.append(("lines", i,
                          [f"[{i} !{el.tag.split('}')[-1]}"
                           + (f" {nm}" if nm else "")
                           + (f" id={el.get(W + 'id')}"
                              if el.get(W + "id") else "") + "]"]))
    flush()

    # ---- @range folding: >=3 adjacent paragraphs sharing one deviation
    fold: set = set()
    range_cards = list(ds.range_cards)
    if not diff_mode:
        runs: list = []
        for it in items:
            if it[0] != "rec":
                # empty-paragraph folds are format-transparent: a TOC's
                # alternating entry/empty/entry pattern must still fold
                is_empty = (it[0] == "lines" and it[2]
                            and isinstance(it[2][0], str)
                            and it[2][0].endswith("empty]"))
                runs.append("skip" if is_empty else None)
                continue
            rec = it[2]
            key = (rec["style"], rec["star"], rec["fd"], rec["dd"])
            runs.append(key if (rec["fd"] or rec["dd"] or
                                (rec["style"] and not rec["star"]))
                        else None)
        j = 0
        while j < len(runs):
            if runs[j] is None or runs[j] == "skip":
                j += 1
                continue
            k = j
            while k + 1 < len(runs) and runs[k + 1] in (runs[j], "skip"):
                k += 1
            while runs[k] == "skip":         # never end a range on an empty
                k -= 1
            n_match = sum(1 for t in range(j, k + 1)
                          if runs[t] == runs[j])
            if n_match >= 3:
                a, b = items[j][1], items[k][1]
                sid, star, fd, dd = runs[j]
                payload = " ".join(x for x in (
                    ("@*" if star else f"@{sid_out(sid)}") if sid else "",
                    fd, dd) if x)
                range_cards.append((a, b, payload))
                for t in range(j, k + 1):
                    if runs[t] == "skip":
                        continue
                    rec = items[t][2]
                    fold.add(("fd", rec["label"]))
                    fold.add(("dd", rec["label"]))
                    if sid:
                        rec["style"] = None     # folded into the card
            j = k + 1

    # ---- stringify, inserting @range cards before their start block
    range_cards.sort(key=lambda c: (c[0], c[1]))
    ci = 0
    sec_start = 0
    for it in items:
        kind, i = it[0], it[1]
        while ci < len(range_cards) and range_cards[ci][0] <= i:
            a, b, payload = range_cards[ci]
            out.append(f"@range [{a}–{b}] {payload}")
            ci += 1
        if kind == "rec":
            rec = it[2]
            out += rec_lines(rec, styles, walker, rels, fold, diff_mode)
            if rec["sec"] is not None:
                out.append(f"@sec [{sec_start}-{i}]"
                           + sect_card(rec["sec"], rels)[len("@sec"):])
                sec_start = i + 1
        else:
            for ln in it[2]:
                if isinstance(ln, tuple):    # body-level sectPr
                    out.append(f"@sec [{sec_start}-{len(kids) - 1}]"
                               + sect_card(ln[1], rels)[len("@sec"):])
                else:
                    out.append(ln)
    for a, b, payload in range_cards[ci:]:
        out.append(f"@range [{a}–{b}] {payload}")

    # ---- comments / footnotes / endnotes: structured, one per entry ----
    # story parts by RELATIONSHIP, not by hardcoded file name: a renamed
    # comments/footnotes part is still that story, and its whole body was
    # invisible in the view (dxv2-4 review P1.10)
    named = named0
    out += comment_lines(parts, styles,
                         part_name=named.get("comments"))
    out += note_lines(parts, named.get("footnotes", "word/footnotes.xml"),
                      "footnote", "fn", styles)
    out += note_lines(parts, named.get("endnotes", "word/endnotes.xml"),
                      "endnote", "en", styles)

    # ---- headers/footers: rendered with the SAME emitter as the body.
    # part discovery via the DOCUMENT RELS, not name patterns: a
    # header stored under any legal name is still a header (review
    # #14: a renamed header's entire content vanished from the view);
    # the classic name pattern stays as fallback for rels-less trees
    hf_names = set(named.get("header") or ()) | set(named.get("footer")
                                                    or ())
    for n in sorted(hf_names):
        r_ = parse(parts, n)
        if r_ is None:
            continue
        flags = ""
        for kind, pat in (("tbl", q("tbl")), ("img", q("drawing"))):
            k2 = len(r_.findall(f".//{pat}"))
            if k2:
                flags += f" {kind}×{k2}"
        out.append(f"@part {n}{flags}")
        part_rels = load_rels(parts, n)
        pds = DocState()
        _scan_rev_years(r_, pds)
        pwalker = SegWalker(styles, part_rels, pds)
        k3 = 0
        for sub in iter_blocks(r_):
            if sub.tag == q("p"):
                r2 = para_lines(sub, f"{n.split('/')[1]}:{k3}",
                                styles, pwalker, part_rels, diff_mode)
                out += ["  " + ln for ln in
                        (rec_lines(r2, styles, pwalker, part_rels,
                                   diff_mode=diff_mode)
                         if r2 else [f"[{n.split('/')[1]}:{k3} empty]"])]
            elif sub.tag == q("tbl"):
                out += ["  " + ln for ln in
                        table_card(sub, f"{n.split('/')[1]}:{k3}",
                                   styles, pwalker, diff_mode)]
            k3 += 1

    # ---- theme fonts: printed only when the view has ~ refs ----
    if re.search(r'[=|"]~', "\n".join(out)):
        tl = theme_line(parts)
        if tl:
            out.insert(1, tl)

    # ---- @skip: TOTALITY by set-difference, not a match list. Iterate
    # every property-bag child localname across EVERY story part (not a
    # byte regex on document+styles -- that missed a header's w:kern and
    # any non-w: prefix could dodge it, dxv2-5 review P1.5), subtract the
    # localnames the view actually models, and NAME the remainder. A key
    # the view neither renders nor lists (w:mirrorIndents made two files
    # look identical) is impossible by construction: it is either
    # modeled or on @skip.
    # DOMAIN (dxv2-6 review B2): the old scan walked only PROPERTY-BAG
    # CHILDREN and compared LOCALNAMES. Three whole classes of fact lived
    # outside that domain and vanished with nothing said:
    #   - attributes            (w:shd@themeFill: two files, one view)
    #   - non-bag elements      (w:lastRenderedPageBreak, a run child)
    #   - names on a hand-kept "structural" allowlist that nothing
    #     actually renders (w:formProt was declared modeled and was not)
    # So the domain is now EVERY element and EVERY attribute in every
    # story part, and the subtrahend is not a declaration but the
    # RENDERED VIEW itself: a key counts as modeled only if COVERS names
    # it AND the view is sensitive to it. Anything else gets named.
    #: element localnames the view DOES render (as a value or folded
    #: into a summary such as border=/mar=/@num). Containers we print as
    #: a summary stay here so @skip does not cry wolf on every border
    #: side; anything that merely SOUNDS structural does not belong --
    #: w:formProt sat here for four rounds while nothing rendered it.
    #: STRUCTURE, not a format fact: these carry the document's shape and
    #: are rendered as blocks/placeholders/cards, so naming them on @skip
    #: would be noise. Everything not here and not in _STRUCTURAL/COVERS
    #: gets named -- that is the whole point.
    #: containers the view prints as ONE summary; their children (and the
    #: children's attributes) are covered by that summary.
    #: attributes whose NAME alone is a distinct fact (a value change is
    #: covered by the element's own rendering; an unmodeled attribute is
    #: not). `val` and friends above are the payload of a val-element and
    #: are covered wherever the element is.
    modeled_el = {k.split("@")[0] for k in COVERS} | _STRUCTURAL
    modeled_at = {k.split("@")[1] for k in COVERS if "@" in k}
    present: dict = {}
    _side_names = {v for k, v in story_parts_by_type(parts).items()
                   if k in ("styles", "numbering") and isinstance(v, str)}
    for pn in sorted(story_names(parts) | _side_names):
        root = parse(parts, pn)
        if root is None:
            continue
        for el in root.iter():
            if not isinstance(el.tag, str) or not el.tag.startswith("{"):
                continue
            par = el.getparent()
            if par is not None and isinstance(par.tag, str) \
                    and par.tag.split("}")[-1] in _SUMMARIZED:
                # covered BY ITS PARENT's summary (pBdr -> bd=, tcMar ->
                # mar=, framePr -> frame=). Expressed as "my parent is a
                # summary" rather than by listing every side name, so a
                # side nobody thought of is covered too -- and so the old
                # cry-wolf on every border side does not come back when
                # the domain widens (dxv2-5 fixed it by not descending;
                # descending is now required for the ATTRIBUTE check).
                continue
            ns = el.tag[:el.tag.index("}") + 1]
            if ns not in _WML_NS:
                # VML / DrawingML / customXml payload: OUT OF SCOPE by the
                # support matrix and already named as (imgN)/(objN)/
                # `unexpanded` at the position it occupies. Comparing it
                # by localname here also conflated v:shape with w:shape.
                continue
            ln = el.tag.split("}")[-1]
            named_here = False
            if ln in SKIP:
                key = ln if (ln not in ONOFF or _on(el)) else f"{ln}=off"
                present.setdefault(key, SKIP[ln])
                continue      # a SKIP entry names the element AS A WHOLE;
                              # that subsumes its attributes (six
                              # latentStyles@def* lines said one thing)
            if ln not in modeled_el and ln not in _SUPPRESS_SKIP:
                # a NAMED boolean carries its state: `<w:noProof/>` and
                # `<w:noProof w:val="0"/>` are opposite facts, and a bare
                # name on @skip said the same thing for both.
                key = ln if (ln not in ONOFF or _on(el)) else f"{ln}=off"
                present.setdefault(key, "unmodeled")
                named_here = True
            # ATTRIBUTES ARE CHECKED REGARDLESS of what happened to the
            # element name. Skipping them for _SUPPRESS_SKIP elements was
            # a hole big enough to swallow `w:hyperlink@w:tooltip` and a
            # content control's `w:lock` -- two legal documents with
            # different semantics rendered byte-identically (dxv2-7 review
            # P1.4). The only attributes we skip are the ones already
            # implied by an element name we just printed.
            for at in el.keys():
                if at.startswith("{") and at[:at.index("}") + 1] \
                        not in _WML_NS:
                    continue
                an = at.split("}")[-1]
                if an in _RENDER_NOISE or an in modeled_at:
                    continue
                key = f"{ln}@{an}"
                if key in COVERS:
                    continue
                if named_here and an == "val":
                    continue        # the element's own payload
                present.setdefault(key, SKIP.get(key, "unmodeled"))
    if present:
        out.append("@skip present but not rendered (named for totality; "
                   "check --raw if layout puzzles): "
                   + ", ".join(f"{k}" for k in sorted(present)))
    # paraId disclosure by SET DIFFERENCE against the rendered view, not
    # by a counter incremented at the one call site that remembered to
    # (only table cells did; comment/textbox/separator-note paragraphs
    # were neither printed nor counted -- dxv2-6 review B5).
    off = 0
    for pn in sorted(story_names(parts)):
        root = parse(parts, pn)
        if root is None:
            continue
        for el in root.iter():
            pid = el.get(W14 + "paraId")
            if pid and pid not in PARAID_SEEN:
                off += 1
    PARAID_OFF[0] = off
    if PARAID_OFF[0]:
        out[_DOC_LINE_AT] += (f" paraid=blocks(+{PARAID_OFF[0]} inside "
                              "cells/notes: --raw)")
    out = strip_marks(intern_bundles(out)) if not diff_mode \
        else strip_marks(out)
    if not diff_mode:
        # one self-explaining suffix on the FIRST @range card (not every
        # card: the note is constant, residency is per-line): the folded
        # blocks carry no per-line repeat of the facts, and an agent
        # grepping block lines under-counts without knowing that.
        # diff_mode stays untouched -- a constant note that lands on
        # DIFFERENT first cards in the two renders would surface as a
        # ghost diff line.
        for k4, ln in enumerate(out):
            if ln.startswith("@range ["):
                out[k4] += (" (range facts apply to every block in the "
                            "range and are not repeated on block lines; "
                            "same below)")
                break
    return "\n".join(one_line(out))


#: Self-declared covered format facts (for the metric; structural facts are judged by paired counts, not declaration).
COVERS = {
    "rFonts@ascii", "rFonts@hAnsi", "rFonts@eastAsia", "rFonts@cs",
    "rFonts@asciiTheme", "rFonts@hAnsiTheme", "rFonts@eastAsiaTheme",
    "rFonts@cstheme", "rFonts@hint",
    "sz@val", "szCs@val", "u@val", "u@color", "color@val", "color@themeColor",
    "shd@themeFill", "shd@themeFillTint", "shd@themeFillShade",
    "highlight@val", "vertAlign@val", "spacing@val", "rStyle@val", "lang@val",
    "lang@eastAsia", "lang@bidi", "position",
    "b", "i", "strike", "dstrike", "caps", "smallCaps", "outline", "shadow",
    "emboss", "imprint", "vanish", "specVanish", "rtl", "cs", "bCs", "iCs",
    "pStyle@val", "jc@val",
    "spacing@before", "spacing@after", "spacing@beforeLines",
    "spacing@afterLines", "spacing@line", "spacing@lineRule",
    "spacing@beforeAutospacing", "spacing@afterAutospacing",
    "ind@firstLine", "ind@firstLineChars", "ind@hanging", "ind@hangingChars",
    "ind@left", "ind@leftChars", "ind@right", "ind@rightChars",
    "ind@start", "ind@startChars", "ind@end", "ind@endChars",
    "keepNext", "keepLines", "pageBreakBefore", "widowControl", "bidi",
    "outlineLvl@val", "numId@val", "ilvl@val", "shd@val", "shd@fill",
    "shd@color", "pBdr", "tabs", "framePr", "textAlignment@val",
    "framePr@w", "framePr@h", "framePr@vAnchor", "framePr@hAnchor",
    "cols@num", "docGrid@linePitch", "pgMar@top", "pgMar@right",
    "style@default", "num@numId", "abstractNum@abstractNumId",
    "pgMar@bottom", "pgMar@left", "pgSz@w", "pgSz@h",
    "rPrChange", "pPrChange",
    "tblStyle@val", "tblLayout@type", "tblBorders", "gridSpan@val",
    "vMerge@val", "vMerge(bare)", "trHeight@val", "trHeight@hRule", "tblHeader",
    "snapToGrid", "snapToGrid(bare)", "snapToGrid@val",
    "contextualSpacing", "contextualSpacing(bare)", "contextualSpacing@val",
    "fitText", "fitText@val",
    "tblLook", "tblLook@val", "tblLook@firstRow", "tblLook@lastRow",
    "tblLook@firstColumn", "tblLook@lastColumn", "tblLook@noHBand",
    "tblLook@noVBand",
    "tblCellSpacing", "tblCellSpacing@w", "tblCellSpacing@type",
    "tblOverlap", "tblOverlap@val", "color@themeTint", "color@themeShade",
    "tcW@w", "tcW@type", "vAlign", "tcMar", "noWrap", "hideMark",
    "tcBorders", "tl2br", "tr2bl", "tblW", "tblW@w", "tblW@type", "tblCellMar",
    "tblInd@w", "tblInd@type", "tblpPr", "textDirection",
    "pgSz", "pgMar", "cols", "docGrid", "titlePg", "headerReference",
    "footerReference", "pgNumType", "type", "pgBorders", "lnNumType",
}


LEGEND = r"""\
# view notation (full reference; pitfalls are in SKILL.md)

The view is **a compact map of common cases, not a lossless OOXML
representation**. It guarantees: for common documents you can edit straight
from it, and anything not expanded is **named** (@skip / unexpanded /
(objN)); wherever something is named, or anywhere the view is unclear, the
raw XML via --raw is authoritative.

## Overall: content lines are sacred, annotations run on side channels
The text after a block line [N #paraId …] is the document's **accepted**
text (as if all revisions were accepted); #paraId = stable re-location
handle (only Word-family files have it; N is the live coordinate).
One rule: **every paragraph that gets its own bracketed block line prints
its id; paragraphs folded into cards or table rows do not — @doc counts
them one by one via `paraid=blocks(+N …)`** — so "not printed" never means
"not there"; the numbers add up.
Everything else travels on subordinate channels:
 └ annotation line  inline facts about the content line directly above
                    (indented one space)
 @card             global and range declarations (head/inline/tail cards)
 (placeholder)     non-text content: (tab) (ptab1) (br) (pgbr) (colbr)
                   (nbhy) (shy) (f1) (img1) (obj1) (eq1) (sym1) (shape1)
                   (fn3) (en2) (x1)
Two escape families, told apart by shape: **backslash = literal
character** (`\t` tab, `\n` newline, `\\` backslash, ` ` line
separator — these ARE text and may be copied as anchors); **parentheses =
structural element** (`(tab)`=w:tab, `(br)`=w:br, `(img1)`=image — these
are NOT text; they are walls between anchors). Literal text that happens
to look like a placeholder is backslash-escaped too (`\(tab)`). Any
copied fragment works as an anchor verbatim; the tools decode it.
Anchor rule: a fragment copied from a content line with NO placeholders
always matches --anchor/--replace; quotes containing placeholders or «»
dead text are loudly rejected — no silent error in either direction.

## Block lines
[N #paraId @style par-deviation run-majority-deviation §] content
- N = the Nth direct child of w:body; --raw N and lxml body[N] share the
  same coordinate;
- @xx = style reference (when writing back w:pStyle use the id, not the
  display name in quotes); @* = explicitly marked with the default style
  (≠ no pStyle); deviations from baseline = this block's own style chain;
- § = paragraph carries a section break, @sec follows;
  [N empty]/[N-M empty×K] = empty paragraphs (coordinates kept);
- [N ⟨sdt⟩] = block-level content control, child blocks [N.k];
  [N !tag] = bare non-paragraph element.

## └ annotation lines (same kind merged on one line, separated by ·;
   different kinds on separate lines)
"…" = fragment of the content line (≤12 chars quoted in full; longer:
 first5…last4; repeated identical text gets (2nd))
«…» = dead text **not on the content line** (pending deletion/moved out),
 kept complete, NEVER usable as an anchor
 format  bold italic u strike sub sup color= size= font=latin·eastAsia[·cs]
        highlight= …; keys outside the vocabulary pass through under their
        OOXML names (unmodelled ≠ gone);
        mark k=v = paragraph-mark formatting (source of empty-paragraph
        line height); &name = @fmt bundle reference;
        k=v ×N scattered = the same deviation appears at N spots on this
        line (≥4 folded; use --raw for each)
 revis.  replace «old»→"new" author date · del «dead» after "neighbor" ·
        ins "new text"
        del-paragraph whole-paragraph deletion (dead text in «») ·
        ins-paragraph whole-paragraph insertion
        del/ins-paragraph-mark touches only the paragraph mark (accept =
        merge/split)
        move-from «…» → [N] / move-to "…" ← [N] revision moves
        rPrChange/pPrChange … was old-value (all nine *PrChange kinds share
        the shape; only changed keys listed)
        accept/reject via revisions.py; exact revision ids via --raw
 comment comment3 "commented text" (comment body in the trailing @cmt card)
 field   f1 instruction → "cached result" cached — the result is Word's;
        edit the instruction or the referenced content, never the cache
        (refresh overwrites it); form fields share the (fN) family
 object  img1 size [float:…] filename · obj1 ProgID · eq1 formula text ·
        sym1 charcode@font · shape1 textbox:(boxed blocks follow indented)
        · x1 unexpanded w:tag --raw (the exit for unmodelled elements,
        never silent; mc:AlternateContent renders only the Fallback
        branch; Choice branches are named here)
 bookmark bookmark name "text" (targets of REF/PAGEREF/internal links;
        _Toc*/_GoBack hidden)
 link    link "text" → URL (#name = internal anchor)

## Tables
[N table rows×cols cols=w/w/… w= borders= layout= cellr:table-wide run
 baseline cN[cell baseline]{paragraph baseline}] — geometry and shared
 facts declared once
r1 | cell | cell   one row per line; ^ = vertical-merge continuation cell;
                   paragraphs inside a cell join with ¶
 └ c2 colspan=2 · r1 header-row · c3 ins "…"   cell/row facts carry cK/rK
                   prefixes
r5:               degraded row (cells contain |, ¶, or overlong text): one
                   cell per indented line
(tbl)             nested-table placeholder; the whole table follows
                   indented ([N.r5c2 table …])
row-level revisions: ins-row (row in body text) / del-row (row left blank,
 dead text per cell in «»)

## Cards
@doc statistics and part counts · @theme theme font table (~name is a
 reference, not a font name)
@norm @style = every paragraph in the document explicitly uses the same
 style; block lines omit that reference
@style id* count p:… r:…(**effective values**, style chain resolved;
 * = default paragraph style)
@fmt &name=attribute bundle (frequent repeated strings defined once per
 document; on reading &name expand it from this line first; aliases are
 assigned per document and are not comparable across files)
@num 1.L0 rule (auto-numbering renders from this; never paste literal
 "Chapter 1"-style text into the body)
@range [a–b] facts (range declaration applies to every block in the range,
 not repeated on block lines; coordinates self-close without pairing:
 runs of identical formatting / cross-block field cached / cross-block
 comments / bookmarks / whole-section moves; reading top-down you always
 pass the card before the range)
@sec [a-b] page size/margins/columns/hdr:default=header2.xml (names the
 part to edit)
@cmt c5 author date [✓done] [↳cN]: full comment text — **only** single-
paragraph plain text takes one line; multi-paragraph / table / textbox
comments become indented [c5.k] blocks below, same syntax as the body
(bare ¶-joining would make "a¶b"+"c" indistinguishable from "a"+"b¶c",
so it is not used) · @fn/@en follow the same rule
Control characters in content are always shown escaped (`\n` `\t`): no
content can ever forge a card or annotation line — the view's
"one item per line" is a structural guarantee, not a convention
@part header2.xml (headers/footers rendered as whole parts; coordinates
 inside the part [header2.xml:0 …])
@skip keys present in this document but not modelled (disclosed; see
 --raw)

## Progressive disclosure
--structure prints only cards and block heads (handy for terminal
navigation, NOT a substitute for reading the full text); --raw N prints
block N's raw XML (editable and can be written back). The view is a map,
not the territory: OMML internals, floating-anchor coordinates, exact
lang variants and similar summaries defer to --raw.
"""


def main() -> int:
    # Restore the default SIGPIPE disposition: a plain Unix filter dies
    # by the signal when `... | head` closes the pipe. Python otherwise
    # turns the write into BrokenPipeError AND still tries to flush at
    # interpreter shutdown, which intermittently surfaced as exit 120
    # (dxv2-5 review minor). SIG_DFL makes the OS terminate us cleanly.
    try:
        import signal as _sig
        _sig.signal(_sig.SIGPIPE, _sig.SIG_DFL)
    except (ImportError, AttributeError, ValueError):
        pass                                  # Windows / non-main thread
    ap = argparse.ArgumentParser(description="docx -> content+format+provenance text view")
    ap.add_argument("--covers", action="store_true",
                    help="print self-declared covered format facts (metric use)")
    ap.add_argument("--skips", action="store_true",
                    help="print deliberately unmodeled keys named on @skip (metric use)")
    ap.add_argument("--legend", action="store_true",
                    help="print the full view-notation reference")
    ap.add_argument("--structure", action="store_true",
                    help="header lines + block heads only (orientation "
                         "pass for reference docs / oversized views)")
    ap.add_argument("src", nargs="?", help=".docx file or unpacked directory")
    ap.add_argument("--raw", type=int, help="print that block's raw XML")
    ap.add_argument("--diff", metavar="AFTER",
                    help="semantic diff against another docx (answers 'did I change what I intended')")
    ap.add_argument("--brief", action="store_true",
                    help="--diff: summary + signature groups only")
    ap.add_argument("--full", action="store_true",
                    help="no truncation for --diff (the truncation hint says --full; "
                         "read.py itself must accept it)")
    a = ap.parse_args()
    if a.legend:
        print(LEGEND)
        return 0
    if a.covers:
        print("\n".join(sorted(COVERS)))
        return 0
    if a.skips:
        print("\n".join(sorted(SKIP)))
        return 0
    if not a.src:
        ap.error("give src, or use --covers/--skips")
    if a.diff:
        import vdiff
        print(vdiff.vdiff(Path(a.src), Path(a.diff), full=a.full,
                          brief=a.brief))
        return 0
    if a.raw is not None:
        parts = load(Path(a.src))
        body = etree.fromstring(
            parts[main_document(parts)]).find(q("body"))
        from coords import body_block   # the ONE [N] resolver: --raw -1
        el = deepcopy(body_block(body, a.raw))   # must error, not wrap
        etree.cleanup_namespaces(el)     # WPS files inherit ~18 xmlns decls
        # encoding="unicode": the default serializer entity-escapes every
        # CJK char (&#35838;), making the output un-greppable (measured)
        print(etree.tostring(el, pretty_print=True, encoding="unicode"))
        return 0
    # Deliberately NO partial-rendering/paging flag: paging over view.txt
    # belongs to the host's file reader, and a partial view is one more
    # way to read less than everything (a slimmed slice once hid comment
    # bodies -- the whole feature class was the bug). Do not re-add one.
    view = render(Path(a.src))
    if a.structure:
        keep = []
        for ln in view.split("\n"):
            if ln.startswith("@"):
                keep.append(ln[:120])
            elif ln.lstrip().startswith("["):
                j = ln.find("] ")
                keep.append(ln if j < 0 else
                            ln[:j + 1] + " " + ln[j + 2:j + 26]
                            + ("…" if len(ln) > j + 26 else ""))
        view = "\n".join(keep)
    print(view)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        # `read.py doc.docx | head` closes the pipe early -- that is a
        # normal terminal idiom, not an error. Redirect stdout to
        # devnull so the interpreter's shutdown flush cannot raise a
        # second time, then exit with the conventional SIGPIPE status
        # (dxv2-3 C4: an agent piping to head got a traceback).
        import os as _os
        _os.dup2(_os.open(_os.devnull, _os.O_WRONLY), sys.stdout.fileno())
        sys.exit(141)
