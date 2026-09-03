"""Shared revision registry: ONE taxonomy for validator, resolver and
clone/split paths.

Review #10's root-cause finding: three hand-maintained lists (validate's
duplicate scan, revisions' processing order, track/comment's clone
strips) drifted -- numberingChange and tblPrExChange were resolvable
but invisible to the duplicate scan, so a validated document could
still double-process on --id. Every consumer now imports from here;
harness test_v13 asserts the consumers stay in sync.

Source: ISO 29500 wml.xsd (shipped under scripts/schemas/iso29500).
The 16 POINT names are every element carrying its own annotation w:id
(CT_TrackChange family + *Change property-history records); the RANGE
names are paired markers whose Start/End legitimately SHARE one id --
they must stay out of any per-id uniqueness scan.
"""
from __future__ import annotations

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

#: Point revisions: one element = one revision, id unique per document
#: (ECMA-376 §17.13.5, the id attribute).
POINT = (
    "ins", "del", "moveFrom", "moveTo",              # content
    "cellIns", "cellDel", "cellMerge",               # table cells
    "numberingChange",                               # legacy numbering
    "pPrChange", "rPrChange", "sectPrChange",        # property history
    "tblGridChange", "tblPrChange", "tblPrExChange",
    "tcPrChange", "trPrChange",
)

#: Property-HISTORY records: what a revision-free clone must not carry.
#: Content revisions (ins/del/move*) are NOT in here -- a clone helper
#: that silently dropped the w:del around copied runs would resurrect
#: deleted text; callers handle content revisions deliberately.
HISTORY = (
    "numberingChange", "pPrChange", "rPrChange", "sectPrChange",
    "tblGridChange", "tblPrChange", "tblPrExChange", "tcPrChange",
    "trPrChange",
)

#: Context-sensitive history (review #11): a localname alone does not
#: classify -- w:ins is OVERLOADED by parent context per wml.xsd:
#:     numPr/ins        -> numbering-property history  (CT_NumPr)
#:     trPr/ins|del     -> row insertion/deletion      (CT_TrPr)
#:     pPr/rPr/ins|del  -> paragraph-mark revision     (CT_ParaRPr)
#:     elsewhere        -> content wrapper             (CT_RunTrackChange)
#: Only the numPr form is HISTORY (a property-revision record a clone
#: must not carry); the others mark content/structure and are handled
#: deliberately by their consumers. Listed as (parent, element) pairs.
HISTORY_IN_CONTEXT = (("numPr", "ins"),)

#: Paired range markers: Start/End share one id BY DESIGN (that is the
#: pairing mechanism, same as bookmarks) -- exempt from uniqueness,
#: but subject to PAIR INTEGRITY: per story part, the multiset of
#: canonical Start ids must equal the End ids (validate checks this;
#: revisions pairs via canon_id -- '044' Start / '44' End is one pair).
RANGE_BASES = ("moveFromRange", "moveToRange", "customXmlInsRange",
               "customXmlDelRange", "customXmlMoveFromRange",
               "customXmlMoveToRange")
RANGE = tuple(base + half for base in RANGE_BASES
              for half in ("Start", "End"))


def canon_id(v):
    """ST_DecimalNumber canonical form: '44', '044' and '+44' are ONE
    id. The type derives from xsd:integer, whose lexical space allows
    a leading plus -- a hand-rolled digit predicate missed '+1'
    (review #11: the allocator then handed out a colliding '1' and the
    validator, sharing the same blind spot, passed the result). ONE
    strict parser, used by every reader/allocator/filter; non-numeric
    values pass through unchanged so they still group with
    themselves."""
    if v is None:
        return None
    s = str(v).strip()
    try:
        return int(s, 10)
    except ValueError:
        return s


def iter_point_revisions(root):
    """Yield (localname, element) for every POINT revision under root,
    document order."""
    tags = {f"{{{W}}}{t}": t for t in POINT}
    for el in root.iter(*tags.keys()):
        yield tags[el.tag], el


def strip_history(el) -> int:
    """Recursively remove every property-HISTORY record under (and
    including direct children of) el, in place -> count removed.
    For clone paths: a deep copy of pPr/rPr/tblPr must not duplicate
    the source's revision records (each carries a document-unique id;
    review #10: stripping only DIRECT pPrChange children left a
    numPr/numberingChange alive in the copy). Context-sensitive forms
    (HISTORY_IN_CONTEXT, e.g. numPr/ins -- review #11) are matched by
    (parent, element) pair: 'ins' anywhere else is a content wrapper
    this function must NOT touch."""
    doomed = [f"{{{W}}}{t}" for t in HISTORY]
    k = 0
    for d in list(el.iter(*doomed)):
        p = d.getparent()
        if p is not None:
            p.remove(d)
            k += 1
    for parent_t, child_t in HISTORY_IN_CONTEXT:
        for p in list(el.iter(f"{{{W}}}{parent_t}")):   # includes el itself
            for d in p.findall(f"{{{W}}}{child_t}"):
                p.remove(d)
                k += 1
    return k


def pid_canon(v):
    """paraId/durableId equality is HEX-NUMERIC, case-insensitive:
    '0a' and '0A' are the same id to Word. -> int, or None."""
    try:
        return int(v, 16)
    except (TypeError, ValueError):
        return None


def fresh_pid(payload: str, taken=None) -> str:
    """THE fresh w14:paraId / durableId allocator (v3.2: four sibling
    implementations grew across comment/prep/track/repair -- exactly the
    allocator-drift this module exists to prevent; consolidated here).
    8 uppercase hex digits, < 0x7FFFFFFF, deterministic from `payload`,
    never colliding with `taken` (raw strings fine; compared
    HEX-NUMERICALLY -- a lowercase existing id once failed to block an
    uppercase candidate of the same value). Probes by +2 (bit 0 always
    clear, so the step actually moves; +1 & mask once re-landed on the
    same value and hung forever)."""
    import hashlib
    taken_num = {pid_canon(x) for x in (taken or ())}
    taken_num.discard(None)
    v = int(hashlib.md5(payload.encode()).hexdigest()[:8], 16) \
        & 0x7FFFFFFE
    v = v or 2
    guard = 0
    while v in taken_num:
        v = (v + 2) & 0x7FFFFFFE or 2
        guard += 1
        if guard > 1 << 20:
            raise SystemExit(
                "E_ID_SPACE: cannot allocate a free paraId/durableId | "
                "document uses an implausible number of ids near this "
                "hash | try: report this file")
    return f"{v:08X}"
