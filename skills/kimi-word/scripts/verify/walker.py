"""Logical WML walker (review #13, blocker B).

Readers and validators must traverse the LOGICAL document structure,
not the physical XML: WordprocessingML lets sdt (content controls),
customXml and smartTag legally wrap content at block, row, cell and
run level. Every `findall("tr")` in the codebase was one wrapper away
from silently losing a row -- an sdt-wrapped w:tr is invisible to it,
so the view lost the row, the width solver missed its constraints and
table tools once mis-addressed everything below it.

ONE walker, consumed by read (table cards), validate (width solver,
vMerge geometry, generation lint) and merge (row addressing). The
invariant it buys: wrapping or unwrapping content in a legal
container never changes semantic output.

sdt descends ONLY through sdtContent (sdtPr holds properties, not
content); customXml and smartTag are transparent containers.
"""
from __future__ import annotations

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"

_SDT = W + "sdt"
_SDT_CONTENT = W + "sdtContent"
_TRANSPARENT = (W + "customXml", W + "smartTag")
_ALT = MC + "AlternateContent"
_FALLBACK = MC + "Fallback"


def content(el):
    """If `el` is a transparent wrapper, return the list of its
    LOGICAL children; else None. ONE wrapper policy for the whole
    project (read's block emitter, the width solver, merge):
    - sdt          -> its sdtContent's children (sdtPr is properties);
    - customXml / smartTag -> own children;
    - mc:AlternateContent  -> the Fallback branch's children (this
      code implements base WML; Fallback is the interop content --
      a Choice branch needs the extension it names)."""
    if el.tag == _SDT:
        for c in el:
            if c.tag == _SDT_CONTENT:
                return list(c)
        return []
    if el.tag in _TRANSPARENT:
        return list(el)
    if el.tag == _ALT:
        for c in el:
            if c.tag == _FALLBACK:
                return list(c)
        return []
    return None


def logical(parent, *localnames):
    """Yield descendants of `parent` whose localname is in
    `localnames`, expanding through ANY depth of legal wrappers
    (sdt/sdtContent, customXml, smartTag, mc:AlternateContent's
    Fallback) WITHOUT descending into the matches themselves or into
    unrelated containers (a nested table's rows are not its host's
    rows). `parent` may be an element or a plain list of elements."""
    want = {W + n for n in localnames}
    for c in parent:
        if c.tag in want:
            yield c
        else:
            inner = content(c)
            if inner is not None:
                yield from logical(inner, *localnames)


def blocks(container):
    """Logical block children: paragraphs and tables."""
    return logical(container, "p", "tbl")


def rows(tbl):
    """Logical rows of ONE table (wrapper-transparent, nesting-opaque)."""
    return logical(tbl, "tr")


def cells(tr):
    """Logical cells of ONE row."""
    return logical(tr, "tc")


def first_row(tbl):
    return next(rows(tbl), None)


# ---------------------------------------------------------------- text model
#: THE shared placeholder table (notation v2). One definition consumed by
#: BOTH sides of the sacred equation:
#: - read.py renders these run children as (name)/(nameN) placeholders in
#:   the content line (they are not w:t text);
#: - track.py/comment.py treat them as WALLS: the match stream contains
#:   only w:t text, so an anchor can never cross one -- the split guard
#:   errors loudly, and the error can name the placeholder the model saw.
#: Divergence between the two sides was measured (view printed \t where
#: the matcher had nothing at all); a single table is the fix, not
#: discipline. Keys are localnames of w:r children.
PLACEHOLDER = {
    "tab": "tab", "ptab": "tab",          # (tab)
    "br": "br", "cr": "br",               # (br); page/column br special-cased by the view
    "sym": "sym",                         # (symN)
    "footnoteReference": "fn",            # (fnN) -- N is the XML id
    "endnoteReference": "en",             # (enN)
    "drawing": "img", "pict": "img",      # (imgN) / textbox -> (shapeN)
    "object": "obj",                      # (objN)
    "noBreakHyphen": "nbhy",              # (nbhy) -- a "-" lookalike lied
    "softHyphen": "shy",                  # (shy) -- used to VANISH silently
}

#: Run children that ARE the match stream. Everything else inside w:r is
#: either a placeholder (table above), field machinery (instrText/fldChar
#: -- field zones are refusal territory), or metadata.
TEXT_TAGS = (W + "t",)

XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"


def rendered(t) -> str:
    """Text as Word RENDERS it: without xml:space="preserve", edge
    whitespace is trimmed at load time -- <w:t>Hello </w:t><w:t>world</w:t>
    draws as "Helloworld". Merging raw bytes instead would turn the
    trimmed-away space into a visible one: a silent layout change.

    THE one implementation of that per-element rule (prep's run merge,
    vdiff's char audit, vredline's rendered joins all consume it --
    three hand copies drifted apart once already)."""
    s = t.text or ""
    if t.get(XML_SPACE) != "preserve":
        s = s.strip(" \t\n\r")
    return s

# ---------------------------------------------------------------- codec
#: Literal text that LOOKS like a placeholder gets one backslash of
#: escape -- the view's ONLY escape, firing ~never (corpus-measured
#: collision ≈ 0) but keeping the view injective: a document saying
#: literal "(tab)" and a document with a real tab must not render
#: identically (Ultra-review repro: they did). Shared codec: read.py
#: encodes content lines, track/comment decode targets -- one
#: definition or the two sides drift.
import re as _re

PH_TOKEN = _re.compile(
    r"\\?\((?:tab|br|pgbr|colbr|ptab\d*|nbhy|shy|tbl|"
    r"f\d+|img\d+|obj\d+|eq\d+|sym\d+|shape\d+|fn\d+|en\d+|x\d+)\)")

#: Every character str.splitlines() treats as a line break. The view is
#: a list of lines, so ALL of them must be escaped out of content --
#: \n/\r/\t alone left U+2028/U+2029/U+0085 able to forge a card line
#: (dxv2-4 review P1.4).
LINE_BREAKERS = "\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029"

#: ONE content codec. Two escape families, disjoint by shape:
#:   backslash  -> a LITERAL character that cannot appear raw
#:                 (\\ \t \n \r \u2028 …); it IS text, anchorable
#:   parentheses-> a STRUCTURAL element (w:tab, w:br, a drawing …);
#:                 it is NOT text and is a wall for the matcher
#: A literal U+0009 inside w:t and a real <w:tab/> used to render
#: identically while behaving differently for every edit (dxv2-4 P1.3).
_ESC_OUT = {"\\": "\\\\", "\t": "\\t", "\n": "\\n", "\r": "\\r"}
for _c in LINE_BREAKERS:
    _ESC_OUT.setdefault(_c, "\\u%04x" % ord(_c))
_ESC_IN = _re.compile(r"\\(u[0-9a-fA-F]{4}|.)")
_ESC_BACK = {"\\": "\\", "t": "\t", "n": "\n", "r": "\r"}


def encode_text(text: str) -> str:
    """Content-line encoding: literal control characters -> backslash
    escapes, then placeholder-lookalikes -> \\(tab). Backslash is
    doubled first, so decoding is unambiguous left to right."""
    out = "".join(_ESC_OUT.get(c, c) for c in text)
    return PH_TOKEN.sub(
        lambda m: m.group(0) if m.group(0).startswith("\\")
        else "\\" + m.group(0), out)


def decode_text(s: str) -> str:
    """Inverse of encode_text for targets copied out of the view."""
    def one(m):
        g = m.group(1)
        if len(g) == 5 and g[0] in "uU":
            return chr(int(g[1:], 16))
        return _ESC_BACK.get(g, g)
    return _ESC_IN.sub(one, s)


def kill_breaks(s: str) -> str:
    """Neutralize every line-breaking character in an annotation/card
    payload (the structural belt's primitive)."""
    return "".join(_ESC_OUT[c] if c in LINE_BREAKERS else c for c in s)


#: characters that DELIMIT annotation payloads -- if they appear raw
#: inside a payload they punch through their own quotes (dead text with
#: » closed «…» early; a field cache with " printed "a"b"). The ONE
#: reversible annotation codec escapes them along with control chars and
#: backslash; decode_text already reverses every \X -> X.
_ANN_DELIMS = "«»\""


def encode_ann(s: str) -> str:
    """Encode a DYNAMIC string bound for an annotation/card payload:
    backslash + control chars + annotation delimiters, all reversible
    via decode_text. Same codec family as content lines -- the review's
    'everything through one reversible codec' (dxv2-5)."""
    out = []
    for c in s:
        if c == "\\":
            out.append("\\\\")
        elif c in LINE_BREAKERS or c == "\t":
            out.append(_ESC_OUT[c])
        elif c in _ANN_DELIMS:
            out.append("\\" + c)
        else:
            out.append(c)
    return "".join(out)


def encode_ph(text: str) -> str:
    """Placeholder-lookalike escaping only (no control-char handling) --
    kept for annotation quotes, which are already control-safe."""
    return PH_TOKEN.sub(
        lambda m: m.group(0) if m.group(0).startswith("\\")
        else "\\" + m.group(0), text)


def decode_ph(target: str) -> str:
    """Back-compat alias: full decode (a target copied from the view may
    carry either escape family)."""
    return decode_text(target)
