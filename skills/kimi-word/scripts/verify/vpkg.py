"""Package model + XSD layer: how parts are read, MCE-preprocessed and
schema-routed -- one boundary so no check reinvents package access."""
from __future__ import annotations

import re
import sys
import zipfile
from collections import Counter
from pathlib import Path

from lxml import etree

sys.path.insert(0, str(Path(__file__).parent))
import opc  # noqa: E402  (the one relationship resolver + atomic IO)
from findings import Finding, Findings  # noqa: E402

HERE = Path(__file__).parent
SCH = HERE / "schemas"

MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
W15 = "http://schemas.microsoft.com/office/word/2012/wordml"
W16CID = "http://schemas.microsoft.com/office/word/2016/wordml/cid"
W16CEX = "http://schemas.microsoft.com/office/word/2018/wordml/cex"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

# root element -> schema file (the root namespace is never stripped
# within that part). Package-level parts route the same way: their root
# elements are unambiguous, so [Content_Types].xml, every .rels and the
# docProps parts get XSD too -- an OPC-level break (Default missing its
# Extension, an alien attribute on a Relationship) used to be invisible
# to this gate and only caught by hand-written semantic code, if at all.
_WML = SCH / "iso29500/wml.xsd"
# docProps/core.xml is deliberately NOT routed: the standard
# opc-coreProperties.xsd imports the Dublin Core schemas from
# dublincore.org by URL, unresolvable offline -- and shipping a hand-cut
# stub would validate against something nonstandard. core.xml still gets
# well-formedness, [Content_Types] coverage and rels-target checks.
EP_NS = ("http://schemas.openxmlformats.org/officeDocument/2006/"
         "extended-properties")
CUP_NS = ("http://schemas.openxmlformats.org/officeDocument/2006/"
          "custom-properties")
ROOT_SCHEMA = {
    **{f"{{{W}}}{t}": _WML for t in (
        "document", "hdr", "ftr", "footnotes", "endnotes", "comments",
        "styles", "numbering", "settings", "fonts", "webSettings",
        "glossaryDocument")},
    f"{{{W15}}}commentsEx": SCH / "microsoft/wml-2012.xsd",
    f"{{{W15}}}people": SCH / "microsoft/wml-2012.xsd",
    f"{{{W16CID}}}commentsIds": SCH / "microsoft/wml-cid-2016.xsd",
    f"{{{W16CEX}}}commentsExtensible": SCH / "microsoft/wml-cex-2018.xsd",
    f"{{{A}}}theme": SCH / "iso29500/dml-main.xsd",
    # chart / diagram parts: the schemas shipped all along, but the
    # roots were never routed -- "we validate what we have schemas for"
    # must actually be true
    "{http://schemas.openxmlformats.org/drawingml/2006/chart}chartSpace":
        SCH / "iso29500/dml-chart.xsd",
    **{"{http://schemas.openxmlformats.org/drawingml/2006/diagram}" + t:
       SCH / "iso29500/dml-diagram.xsd"
       for t in ("dataModel", "layoutDef", "styleDef", "colorsDef")},
    f"{{{CT_NS}}}Types": SCH / "opc/opc-contentTypes.xsd",
    f"{{{REL_NS}}}Relationships": SCH / "opc/opc-relationships.xsd",
    f"{{{EP_NS}}}Properties":
        SCH / "iso29500/shared-documentPropertiesExtended.xsd",
    f"{{{CUP_NS}}}Properties":
        SCH / "iso29500/shared-documentPropertiesCustom.xsd",
}
_schema_cache: dict = {}


def _schema(path: Path):
    """None when the schema asset itself cannot be built -- the gate
    must degrade to 'this part not XSD-checked', never die on its own
    luggage (a schema with an unresolvable import once crashed every
    validation run, i.e. the whole gate, not one check)."""
    if path not in _schema_cache:
        try:
            _schema_cache[path] = etree.XMLSchema(etree.parse(str(path)))
        except (etree.XMLSchemaParseError, etree.XMLSyntaxError,
                OSError):
            _schema_cache[path] = None
    return _schema_cache[path]


# ---------- Package abstraction: zip or directory, treated alike ----------

class Pkg:
    def __init__(self, src: Path):
        self.src = src
        if src.is_dir():
            self.names = sorted(
                str(p.relative_to(src)).replace("\\", "/")
                for p in src.rglob("*") if p.is_file())
            self._read = lambda n: (src / n).read_bytes()
        else:
            try:
                z = opc.CappedZip(src)     # capped: zip-bomb defense
            except zipfile.BadZipFile as e:
                raise SystemExit(f"{src} is not a zip/docx: {e}")
            self.names = [n for n in z.namelist() if not n.endswith("/")]
            self._read = z.read

    def read(self, name: str) -> bytes:
        return self._read(name)

    def xml(self, name: str):
        # Memoized: document.xml is used by XSD, semantic and rels checks;
        # re-parsing large parts accounted for over half of measured
        # runtime. Trees are traversed read-only, so sharing is safe
        # (mce_strip deep-copies; the original is never mutated).
        if not hasattr(self, "_cache"):
            self._cache = {}
        if name not in self._cache:
            self._cache[name] = etree.fromstring(self.read(name))
        return self._cache[name]


# ---------- MCE preprocessing ----------

def mce_strip(root):
    """Apply MCE processing and return a validatable tree (deep copy;
    the original tree is untouched).

    Deliberately partial MCE: AlternateContent always takes the Fallback
    (valid baseline content by construction). mc:Ignorable is applied
    with its real LEXICAL scope -- the declaration covers the element
    and its subtree only. The old part-wide union let one paragraph's
    declaration excuse a sibling paragraph's alien elements: over-
    stripping REMOVES content from validation, i.e. false NEGATIVES
    this gate cannot see (measured). MustUnderstand is checked
    separately as a violation; ProcessContent/PreserveElements are not
    implemented -- rare in Word output."""
    import copy
    root = copy.deepcopy(root)
    root_ns = etree.QName(root).namespace
    # 1) AlternateContent -> Fallback first (hoisted Fallback children
    #    then inherit the scope of the AC's position, which is theirs)
    ac_tag, fb_tag = f"{{{MC}}}AlternateContent", f"{{{MC}}}Fallback"
    while True:
        acs = [e for e in root.iter(ac_tag)]
        if not acs:
            break
        for ac in reversed(acs):                    # innermost first
            parent = ac.getparent()
            if parent is None:
                continue
            fb = ac.find(fb_tag)
            i = parent.index(ac)
            if fb is not None:
                for k, c in enumerate(list(fb)):
                    parent.insert(i + k, c)
            parent.remove(ac)

    # 2) scoped strip: walk with the set of namespaces ignorable HERE
    def strip(el, active: frozenset):
        v = el.get(f"{{{MC}}}Ignorable")
        if v:
            here = set(active)
            for pfx in v.split():
                uri = el.nsmap.get(pfx)
                if uri and uri != root_ns:      # never strip the root ns
                    here.add(uri)
            active = frozenset(here)
        for k in list(el.attrib):
            if k.startswith("{"):
                ns = k[1:].split("}", 1)[0]
                if ns in active or ns == MC:
                    del el.attrib[k]
        for c in list(el):
            if not isinstance(c.tag, str):      # comments/PIs
                continue
            if etree.QName(c).namespace in active:
                el.remove(c)                    # whole subtree
            else:
                strip(c, active)
    strip(root, frozenset())
    return root



# ---------- part-tree helpers (shared by every semantic check) ----------

def _ids(root, tag, attr=f"{{{W}}}id"):
    return [e.get(attr) for e in root.iter(f"{{{W}}}{tag}")]


def _guarded(pkg, name, bad):
    """Parse a part or degrade to None -- the XSD pass walks every
    .xml/.rels part and has already recorded the parse failure as a
    violation; reporting it again here would double-count."""
    try:
        return pkg.xml(name)
    except etree.XMLSyntaxError:
        return None


#: URI -> prefix for XSD diagnostics. libxml2 spells every element as
#: {full-namespace-uri}name, so ONE expected-element list is ~90%
#: boilerplate: a 160-char cut landed inside "Expected is one of (" and
#: the list -- the only actionable part -- was never visible (dxv2-3 C1).
_NS_SHORT = {
    "http://schemas.openxmlformats.org/wordprocessingml/2006/main": "w",
    "http://schemas.openxmlformats.org/officeDocument/2006/"
    "relationships": "r",
    "http://schemas.openxmlformats.org/drawingml/2006/main": "a",
    "http://schemas.openxmlformats.org/drawingml/2006/"
    "wordprocessingDrawing": "wp",
    "http://schemas.microsoft.com/office/word/2010/wordml": "w14",
    "http://schemas.microsoft.com/office/word/2012/wordml": "w15",
    "http://schemas.openxmlformats.org/markup-compatibility/2006": "mc",
    "http://schemas.openxmlformats.org/officeDocument/2006/math": "m",
    "http://schemas.openxmlformats.org/package/2006/relationships": "pr",
    "http://schemas.openxmlformats.org/package/2006/"
    "content-types": "ct",
}
_XSD_LIMIT = 700


def _xsd_msg(msg: str) -> str:
    """XSD diagnostic -> readable: namespaces to prefixes, then a
    generous cap. The expected-element list is what tells an agent
    WHERE the element belongs, so it must survive."""
    for uri, px in _NS_SHORT.items():
        msg = msg.replace("{" + uri + "}", px + ":")
    msg = re.sub(r"\s+", " ", msg).strip()
    if len(msg) > _XSD_LIMIT:
        msg = msg[:_XSD_LIMIT] + f"… (+{len(msg) - _XSD_LIMIT} chars)"
    return msg


def run_checks(pkg: Pkg):
    """-> (violation Findings, warning Findings, count of
    XSD-validated parts). Both containers auto-wrap: no display
    string is ever STORED as a diagnostic (review #14 gate)."""
    # deferred import: vchecks builds on this module (findings <- vpkg
    # <- vchecks); by first call vpkg is fully loaded, so no cycle
    from vchecks import semantic_checks
    bad = Findings("error")
    warn = Findings("warn")
    n_xsd = 0
    # duplicate OPC part names: OPC forbids two entries with the same
    # name; a zip can still carry them and readers silently pick one, so
    # the package is ambiguous -- two different word/document.xml both
    # PASSED and read.py chose one (dxv2-3 review P1.6a)
    _seen_parts: Counter = Counter(pkg.names)
    for nm, k in sorted(_seen_parts.items()):
        if k > 1:
            bad.append(Finding(
                "DUP_PART", ("part", nm),
                f"{nm} appears {k} times in the package (OPC requires "
                "unique part names; readers pick one silently -- the "
                "document is ambiguous)", count=k - 1))
    for name in pkg.names:
        # whole package, not just word/: .rels, [Content_Types].xml and
        # docProps are load-bearing parts too; parts whose root element
        # has no schema mapped (customXml payloads etc.) fall through
        if not name.endswith((".xml", ".rels")):
            continue
        try:
            root = pkg.xml(name)
        except etree.XMLSyntaxError as e:
            bad.append(f"{name} XML parse failed: {e}")
            continue
        for el in root.iter(etree.Element):
            mu = el.get(f"{{{MC}}}MustUnderstand")
            if mu:
                bad.append(f"{name} declares mc:MustUnderstand={mu} -- "
                           "this validator cannot guarantee those "
                           "semantics")
                break
        xsd = ROOT_SCHEMA.get(root.tag)
        if xsd is None:
            continue
        stripped = mce_strip(root)
        sch = _schema(xsd)
        if sch is None:
            warn.append(f"{name}: schema {xsd.name} unavailable, "
                        "not XSD-checked")
            continue
        if not sch.validate(stripped):
            for err in sch.error_log:
                bad.append(f"{name}:{err.line} {_xsd_msg(err.message)}")
        n_xsd += 1
    semantic_checks(pkg, bad, warn)
    return bad, warn, n_xsd
