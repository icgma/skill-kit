#!/usr/bin/env python3
"""Comments: add / reply / resolve -- with **automatic anchor placement**
(six-file sync + run splitting to bracket the range).

    python scripts/comment.py TARGET "comment text" --anchor "text to mark" [--at "#A1B2C3D4"]
    python scripts/comment.py TARGET "agree" --reply-to 5
    python scripts/comment.py TARGET --resolve 5     (--unresolve 5 to undo)
    python scripts/comment.py TARGET --ops plan.json [--dry-run]   (batch)
    TARGET = prep.py's unpacked/ dir, or a .docx (auto unpack/repack, -o output)

Batch: plan.json is a JSON array of op objects, keys = the CLI flags
(`[{"text":"looks good","anchor":"text to mark","at":"#A1B2C3D4"},
   {"text":"agreed","reply_to":"c3"}, {"resolve":"c7"}]`) -- one process,
one rollback boundary, all-or-nothing: a failing op undoes every
earlier op in the plan too (partial application is worse than none;
the caller fixes op #k and reruns the whole plan). A reply may target
a comment added earlier in the same plan. --dry-run runs every op for
real and rolls back at the end -- same code path, nothing kept.

## Checklist knowledge -- easy to miss a piece when writing from scratch

One comment = comments.xml + commentsExtended.xml (thread/resolved) +
commentsIds.xml (durableId) + commentsExtensible.xml + rels registration +
[Content_Types] registration -- six places in sync; miss one and Word
shows the "needs repair" prompt.
Two value pitfalls: comment ids are unique document-wide; durableId/paraId
**< 0x7FFFFFFF** (a bare random 8-hex value is invalid half the time --
actually got bitten by this).

## Anchors

`--anchor` finds the text within the `--at` paragraph (or the single
paragraph matching document-wide), **matching across runs and splitting
runs as needed**, wraps it with commentRangeStart/End, then inserts a
commentReference. Replies place no new anchor -- the reference goes right
after the parent comment's (Word's threading convention).
Anchor not found / not unique -> error, no guessing. Run prep.py first
(text is only contiguous and searchable after merge_runs).
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from lxml import etree

sys.path.insert(0, str(Path(__file__).parent))
import opc  # noqa: E402  (safe unpack + atomic IO)
import revreg  # noqa: E402  (the one decimal-id parser)
import walker as walker_mod  # noqa: E402  (shared codec)

# Windows consoles/pipes default to the legacy ANSI code page (cp1252/cp936):
# the engine's CJK messages must never crash the gate there.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
W14 = "{http://schemas.microsoft.com/office/word/2010/wordml}"
W15 = "{http://schemas.microsoft.com/office/word/2012/wordml}"
W16CID = "{http://schemas.microsoft.com/office/word/2016/wordml/cid}"
W16CEX = "{http://schemas.microsoft.com/office/word/2018/wordml/cex}"
PR = "{http://schemas.openxmlformats.org/package/2006/relationships}"
CT = "{http://schemas.openxmlformats.org/package/2006/content-types}"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
XSP = "{http://www.w3.org/XML/1998/namespace}space"

_WNS = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/'
        '2006/main" xmlns:w14="http://schemas.microsoft.com/office/word/'
        '2010/wordml" xmlns:mc="http://schemas.openxmlformats.org/'
        'markup-compatibility/2006" mc:Ignorable="w14"')

#: part name -> (empty root, FULL relationship Type URI, content-type).
#: The three modern parts use Microsoft namespaces with their own years --
#: gluing "commentsExtended" onto the Office-2006 namespace produces a
#: relationship Word does not recognize: threads and resolved-state
#: silently stop round-tripping (validate now checks these).
PARTS = {
    "comments.xml": (
        f"<w:comments {_WNS}/>",
        "http://schemas.openxmlformats.org/officeDocument/2006/"
        "relationships/comments",
        "application/vnd.openxmlformats-officedocument.wordprocessingml"
        ".comments+xml"),
    "commentsExtended.xml": (
        f'<w15:commentsEx xmlns:w15="{W15[1:-1]}" {_WNS}/>',
        "http://schemas.microsoft.com/office/2011/relationships/"
        "commentsExtended",
        "application/vnd.openxmlformats-officedocument.wordprocessingml"
        ".commentsExtended+xml"),
    "commentsIds.xml": (
        f'<w16cid:commentsIds xmlns:w16cid="{W16CID[1:-1]}" {_WNS}/>',
        "http://schemas.microsoft.com/office/2016/09/relationships/"
        "commentsIds",
        "application/vnd.openxmlformats-officedocument.wordprocessingml"
        ".commentsIds+xml"),
    "commentsExtensible.xml": (
        f'<w16cex:commentsExtensible xmlns:w16cex="{W16CEX[1:-1]}" {_WNS}/>',
        "http://schemas.microsoft.com/office/2018/08/relationships/"
        "commentsExtensible",
        "application/vnd.openxmlformats-officedocument.wordprocessingml"
        ".commentsExtensible+xml"),
}


def _hex31(payload: str, taken: set | None = None) -> str:
    """Delegates to revreg.fresh_pid -- THE one allocator (v3.2)."""
    return revreg.fresh_pid(payload, taken)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Pack:
    """Uniform read/write for an unpacked/ dir or a .docx."""

    def __init__(self, target: str):
        self.p = Path(target)
        self.tmp = None
        if self.p.is_file():
            self.tmp = Path(tempfile.mkdtemp(prefix="cmt"))
            opc.unpack(self.p, self.tmp)   # hostile-entry defense inside
            self.root = self.tmp
        else:
            self.root = self.p

    def tree(self, name: str, create: str | None = None):
        f = self.root / name
        if not f.exists():
            if create is None:
                raise SystemExit(f"missing {name}")
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(etree.tostring(
                etree.fromstring(create.encode()), xml_declaration=True,
                encoding="UTF-8", standalone=True))
        return etree.parse(str(f))

    def save(self, name: str, tree) -> None:
        opc.atomic_write_tree(tree, self.root / name)

    def finish(self, out: str | None) -> None:
        if self.tmp is None:
            return
        dst = Path(out) if out else self.p
        opc.atomic_zip_dir(self.root, dst)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def discard(self) -> None:
        """Drop a .docx target's temp tree without writing the zip
        (dry-run / failure); no-op for a directory target."""
        if self.tmp is not None:
            shutil.rmtree(self.tmp, ignore_errors=True)


def ensure_parts(pk: Pack) -> None:
    """Four parts + rels + content-type; create whatever is missing
    (idempotent). A minimal docx legally has NO document.xml.rels --
    since this function is about to add relationships, an absent rels
    part is created, not treated as an error. [Content_Types].xml stays
    required: a package without it is not a docx at all."""
    rels = pk.tree("word/_rels/document.xml.rels",
                   create='<Relationships xmlns="http://schemas.'
                          'openxmlformats.org/package/2006/'
                          'relationships"/>')
    ct = pk.tree("[Content_Types].xml")
    have_rel = {r.get("Target") for r in rels.getroot()}
    have_ct = {o.get("PartName") for o in ct.getroot().iter(CT + "Override")}
    nxt = max((int(r.get("Id")[3:]) for r in rels.getroot()
               if (r.get("Id") or "").startswith("rId")
               and r.get("Id")[3:].isdigit()), default=0) + 1
    for name, (seed, reltype, ctype) in PARTS.items():
        pk.tree(f"word/{name}", create=seed)
        if name not in have_rel:
            etree.SubElement(rels.getroot(), PR + "Relationship",
                             {"Id": f"rId{nxt}",
                              "Type": reltype, "Target": name})
            nxt += 1
        if f"/word/{name}" not in have_ct:
            etree.SubElement(ct.getroot(), CT + "Override",
                             {"PartName": f"/word/{name}",
                              "ContentType": ctype})
    pk.save("word/_rels/document.xml.rels", rels)
    pk.save("[Content_Types].xml", ct)


# ---------------------------------------------------------------- anchors

_STORY_BREAKERS = {W + "drawing", W + "pict", W + "object",
                   W + "txbxContent"}


#: run children that pass text through untouched; everything else is
#: matching MACHINERY (tab/br/drawing/fldChar/instrText/sym/...)
_STREAM_OK = {W + "rPr", W + "lastRenderedPageBreak"}
_DEAD_WRAPS = {W + "del", W + "moveFrom"}


def _runs_with_text(p):
    """THE match stream: one definition, mirroring the view's content
    line channel (read.py accepted state). -> [(run, text)]

    - live w:t text -> its chars;
    - anything under w:del / w:moveFrom -> DEAD: contributes nothing
      (accepted state stays contiguous across it) -- moved-away or
      deleted text is never editable body text (an Ultra-review repro
      replaced a moveFrom'd fragment as if it were live);
    - every non-text run child -> one \x00 WALL char: targets can never
      contain \x00, so no span silently crosses or swallows a tab /
      field boundary / object (a cross-tab replace once wrapped the tab
      run into w:del without a trace -- the boundary guard only fired
      when the boundary fell INSIDE a run, not when a whole non-text
      run sat strictly inside the span);
    - commentReference host runs stay skipped: spans group around
      point markers by design (measured workflow need);
    - foreign stories (drawings/text boxes) stay excluded.
    """
    out = []
    for r in p.iter(W + "r"):
        if r.find(W + "commentReference") is not None:
            continue
        anc, foreign, dead = r.getparent(), False, False
        while anc is not None and anc is not p:
            if anc.tag in _STORY_BREAKERS:
                foreign = True
                break
            if anc.tag in _DEAD_WRAPS:
                dead = True
            anc = anc.getparent()
        if foreign:
            continue
        if dead:
            out.append((r, ""))
            continue
        parts = []
        for c in r:
            if not isinstance(c.tag, str) or c.tag in _STREAM_OK:
                continue
            if c.tag == W + "t":
                parts.append(c.text or "")
            elif c.tag in (W + "delText", W + "delInstrText"):
                pass                          # dead text: absent
            else:
                parts.append("\x00")          # wall
        out.append((r, "".join(parts)))
    return out


class Ids:
    """Unique revision ids, starting at max+1 over EVERY w:id attribute
    in ALL given roots (attribute-level scan: a document serialized with
    an x: prefix still counts -- a byte-regex on 'w:id=' would not).
    Multiple roots because ECMA-376 wants annotation ids unique per
    DOCUMENT (§17.13.5, id attr): scanning document.xml alone while a
    header carried id=500 handed out duplicates -- pass every story
    root you have. (Lives here, not track.py: track imports from this
    module and _split_run below needs an allocator too.)
    Values go through revreg.canon_id -- THE one decimal parser: a
    home-grown digit predicate skipped the legal '+1', allocated a
    colliding '1', and the validator (sharing the blind spot) passed
    the result (review #11's allocator->validator conspiracy)."""

    def __init__(self, *roots):
        vals = []
        for root in roots:
            for el in root.iter(etree.Element):
                v = revreg.canon_id(el.get(W + "id"))
                if isinstance(v, int):
                    vals.append(v)
        self.next_id = max(vals, default=0) + 1

    def take(self) -> str:
        v = self.next_id
        self.next_id += 1
        return str(v)


def _split_run(run, at: int, ids: Ids | None = None):
    """Split run in two at character offset `at` (deep-copies formatting).
    -> right half.
    Only splits pure-text runs: deep-copying a run with non-text children
    (br/tab/drawing etc.) would duplicate the break/image into both halves
    and misplace it -- refuse to split such runs; the user picks another
    anchor. Exception: lastRenderedPageBreak is a zero-semantics render
    hint Word sprinkles at EVERY page boundary -- long documents made the
    refusal fire constantly (measured). It stays in the left half and is
    dropped from the copy."""
    extra = {etree.QName(c).localname for c in run
             if c.tag not in (W + "rPr", W + "t",
                              W + "lastRenderedPageBreak")}
    if extra:
        import walker as _wml
        shown = ",".join(f"({_wml.PLACEHOLDER.get(x, x)})"
                         for x in sorted(extra))
        raise SystemExit(
            f"anchor boundary falls inside a run with non-text content "
            f"({','.join(sorted(extra))}); cannot split it. The view "
            f"shows these as {shown} placeholders -- anchors cannot "
            "cross a placeholder; split or move the anchor there.")
    right = deepcopy(run)
    for lb in right.findall(W + "lastRenderedPageBreak"):
        right.remove(lb)
    # An rPrChange in the source is deep-copied into the RIGHT half too;
    # the copy gets a FRESH id (left keeps the original). ECMA-376
    # (§17.13.5, id attr) wants annotation ids unique per document, and
    # "same id + same content" is NOT reliable provenance -- two far-
    # apart independent revisions can collide on both, so no validator
    # exemption can tell them from split pieces (v11 tried; reviewer
    # counterexample killed it). Author/date/old-rPr stay identical:
    # each piece restores itself correctly on reject, they just count
    # as separate revisions -- which, in XML terms, they now are.
    for rpc in right.findall(f"{W}rPr/{W}rPrChange"):
        rpc.set(W + "id",
                (ids or Ids(run.getroottree().getroot())).take())
    text = "".join(x.text or "" for x in run.findall(W + "t"))
    for x in run.findall(W + "t")[1:]:
        run.remove(x)
    for x in right.findall(W + "t")[1:]:
        right.remove(x)
    lt, rt = run.find(W + "t"), right.find(W + "t")
    lt.text, rt.text = text[:at], text[at:]
    for el in (lt, rt):
        if el.text != (el.text or "").strip():
            el.set(XSP, "preserve")
    run.addnext(right)
    return right


def place_anchor(body, anchor: str, cid: str, block=None,
                 ids: Ids | None = None) -> None:
    # Always match within a SINGLE paragraph. Flattening a whole block
    # (a table!) would let "AlphaBeta" match across two adjacent cells --
    # a phantom contiguity that no rendering ever shows.
    from coords import by_id  # the one #id resolver
    anchor = walker_mod.decode_ph(anchor)  # view escape -> literal
    # block is a '#id' scope or None (document-wide unique match)
    scope = [by_id(body, block)] if block is not None else [body]
    paras = [p for host in scope for p in
             ([host] if host.tag == W + "p" else host.iter(W + "p"))
             if anchor in "".join(t for _, t in _runs_with_text(p))]
    if not paras:
        raise SystemExit(f"E_ANCHOR_NOT_FOUND: {anchor!r} matched 0 "
                         "paragraphs | try: copy the anchor verbatim "
                         "from a CURRENT view line; if the file was "
                         "never prep'd, run prep.py (merges fragmented "
                         "runs)")
    if len(paras) > 1:
        raise SystemExit(f"E_ANCHOR_AMBIGUOUS: {anchor!r} matched "
                         f"{len(paras)} paragraphs"
                         + ("" if block is not None else
                            " | try: scope it with at='#id' (the "
                            "view block line's id) or lengthen the "
                            "anchor"))
    p = paras[0]
    runs = _runs_with_text(p)
    flat = "".join(t for _, t in runs)
    i = flat.find(anchor)
    if i < 0:
        raise SystemExit(f"anchor not found in block: {anchor!r}. "
                         f"Block text: {flat[:80]!r}...")
    if flat.find(anchor, i + 1) >= 0:
        raise SystemExit(f"anchor matches multiple spots in block: "
                         f"{anchor!r} -- lengthen it with more context")
    j = i + len(anchor)
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
    start_run.addprevious(p.makeelement(W + "commentRangeStart",
                                        {W + "id": cid}))
    re_ = p.makeelement(W + "commentRangeEnd", {W + "id": cid})
    end_run.addnext(re_)
    ref = p.makeelement(W + "r", {})
    rr = etree.SubElement(ref, W + "rPr")
    etree.SubElement(rr, W + "rStyle").set(W + "val", "CommentReference")
    etree.SubElement(ref, W + "commentReference", {W + "id": cid})
    re_.addnext(ref)


def ensure_comment_style(pk: "Pack") -> None:
    """Ensure the CommentReference character style exists in styles.xml --
    with a dangling reference Word silently falls back and the comment
    mark's font size comes out wrong."""
    f = pk.root / "word/styles.xml"
    if not f.exists():
        return
    st = pk.tree("word/styles.xml")
    root = st.getroot()
    if any(s.get(W + "styleId") == "CommentReference"
           for s in root.iter(W + "style")):
        return
    s = etree.SubElement(root, W + "style",
                         {W + "type": "character",
                          W + "styleId": "CommentReference"})
    etree.SubElement(s, W + "name").set(W + "val", "annotation reference")
    etree.SubElement(s, W + "uiPriority").set(W + "val", "99")
    etree.SubElement(s, W + "semiHidden")
    etree.SubElement(s, W + "unhideWhenUsed")
    rpr = etree.SubElement(s, W + "rPr")
    etree.SubElement(rpr, W + "sz").set(W + "val", "16")
    etree.SubElement(rpr, W + "szCs").set(W + "val", "16")
    pk.save("word/styles.xml", st)


# ---------------------------------------------------------------- ops

#: every file an op may touch: ONE rollback boundary per invocation
#: (single op or batch). A failure anywhere must not leave four
#: half-registered parts behind -- and in batch mode must undo the
#: SUCCEEDED earlier ops too: partial application is worse than none,
#: the caller fixes op #k and reruns the whole plan.
#: derived from PARTS, not hand-kept: adding a comment part to PARTS
#: automatically extends the rollback boundary (a hand-copied list is
#: how a new part silently escapes the transaction)
_TARGETS = ["word/" + n for n in PARTS] + [
    "word/_rels/document.xml.rels", "[Content_Types].xml",
    "word/styles.xml", "word/document.xml"]


def _snapshot(pk: Pack) -> dict:
    return {"files": {n: (pk.root / n).read_bytes()
                      if (pk.root / n).exists() else None
                      for n in _TARGETS},
            # dirs the targets live in (derived, deepest first); ones
            # created then rolled back must not survive as empty litter
            "newdirs": [d for d in sorted(
                {str(Path(n).parent) for n in _TARGETS} - {"."},
                key=len, reverse=True)
                if not (pk.root / d).is_dir()]}


def _restore(pk: Pack, snap: dict) -> None:
    for n, data in snap["files"].items():
        f = pk.root / n
        if data is None:
            if f.exists():
                f.unlink()
        elif not f.exists() or f.read_bytes() != data:
            # byte-compare first: a pure phase-1 failure (anchor not
            # found) touched zero files, and rewriting identical bytes
            # would churn every mtime for nothing (review repro)
            f.write_bytes(data)
    for n in snap["newdirs"]:
        try:
            (pk.root / n).rmdir()          # only if empty; else keep
        except OSError:
            pass


def apply_op(pk: Pack, a) -> str:
    """Apply ONE op (add / reply / resolve / unresolve) to the unpacked
    tree, disk writes included; -> one-line summary. NO transaction in
    here -- the CALLER owns the snapshot/rollback boundary. Each op
    re-parses the parts it needs from disk: that is what lets a reply
    target a comment added earlier in the same plan, and it keeps one
    code path for single and batch (the parse cost is dwarfed by the
    per-process cost batching removes)."""

    def read_comments():
        return pk.tree("word/comments.xml")     # no create: error if absent

    def last_pid(cm_tree, comment_id: str):
        for c in cm_tree.getroot().findall(W + "comment"):
            if revreg.canon_id(c.get(W + "id")) \
                    == revreg.canon_id(str(comment_id)):
                ps = c.findall(W + "p")
                return ps[-1].get(W14 + "paraId") if ps else None
        return None

    # the tool's own output says "comment c3"; refusing "c3" as input
    # and demanding "3" made every user round-trip through a mental
    # strip step (dxv2-9 review). Accept both spellings everywhere.
    for _k in ("reply", "resolve", "unresolve"):
        _v = getattr(a, _k)
        if isinstance(_v, str) and _v[:1] in ("c", "C") \
                and _v[1:].isdigit():
            setattr(a, _k, _v[1:])

    if a.resolve or a.unresolve:
        tid, done = (a.resolve, "1") if a.resolve else (a.unresolve, "0")
        cm = read_comments()
        ex = pk.tree("word/commentsExtended.xml")
        pid = last_pid(cm, tid)
        if pid is None:
            raise SystemExit(f"E_NO_COMMENT: no comment with id={tid} | try: the view's @cmt lines list existing ids")
        hit = [ce for ce in ex.getroot().iter(W15 + "commentEx")
               if ce.get(W15 + "paraId") == pid]
        if not hit:
            raise SystemExit("E_PART_INCONSISTENT: commentsExtended "
                             "has no entry for that comment | package "
                             "was already inconsistent | try: read.py "
                             "<file> --raw to inspect")
        for ce in hit:
            ce.set(W15 + "done", done)
        pk.save("word/commentsExtended.xml", ex)
        return f"comment {tid} → done={done}"

    if not a.text:
        raise SystemExit("E_OP_SHAPE: comment text missing | try: comment.py TARGET 'text' --anchor ... , or --resolve cN")
    if not a.reply and not a.anchor:
        raise SystemExit("E_OP_SHAPE: a new comment needs an anchor "
                         "| try: --anchor 'text to mark' "
                         "(--reply-to cN for replies)")

    # ---- phase 1: in-memory mutations + all failure points ----
    dt = pk.tree("word/document.xml")
    body = dt.getroot().find(W + "body")
    parent_pid = host = None
    if a.reply is not None:
        cm0 = read_comments()               # reply implies parts exist
        parent_pid = last_pid(cm0, a.reply)
        if parent_pid is None:
            raise SystemExit(f"parent comment id={a.reply} does not exist")
        for ref in body.iter(W + "commentReference"):
            if revreg.canon_id(ref.get(W + "id")) \
                    == revreg.canon_id(str(a.reply)):
                host = ref.getparent()
        if host is None:
            raise SystemExit(
                f"parent comment {a.reply} has no reference in the body")

    # cid needs only a READ of comments.xml (0 when absent); anchor
    # placement mutates the in-memory document tree and carries the last
    # failure points (no match / multiple / unsplittable run) -- all still
    # before any disk write.
    cid = "0"
    if (pk.root / "word/comments.xml").exists():
        # revreg.canon_id is THE decimal parser: '+0'/'00' are the same
        # id as '0' (an .isdigit() filter once skipped a legal '+0' and
        # re-allocated a colliding '0' -- Ultra-review repro)
        ids = [v for v in (revreg.canon_id(c.get(W + "id"))
                           for c in read_comments().getroot()
                           .findall(W + "comment"))
               if isinstance(v, int)]
        cid = str(max(ids, default=-1) + 1)
    if a.reply is not None:
        # A DEDICATED run, never a copy of the host: an external Word
        # file may keep text/tabs/field chars in the same run as the
        # parent's commentReference, and deepcopy dragged all of it into
        # the body as duplicate content (measured on a crafted file).
        nr = etree.Element(W + "r")
        nr_rpr = etree.SubElement(nr, W + "rPr")
        etree.SubElement(nr_rpr, W + "rStyle").set(W + "val",
                                                   "CommentReference")
        etree.SubElement(nr, W + "commentReference", {W + "id": cid})
        host.addnext(nr)
    else:
        # Fresh-id allocator over EVERY story part on disk (headers may
        # hold the max w:id): a split that clones an rPrChange must not
        # reuse an id that exists anywhere in the document. Malformed
        # side parts are skipped -- they are validate's finding, and
        # failing here would block commenting on an intact body.
        roots = [dt.getroot()]
        for f in sorted((pk.root / "word").glob("*.xml")):
            if f.name == "document.xml":
                continue
            try:
                roots.append(etree.parse(str(f)).getroot())
            except etree.XMLSyntaxError:
                continue
        place_anchor(body, a.anchor, cid, a.block, Ids(*roots))

    # ---- phase 2: writes (ensure parts, append elements, save).
    # ensure_parts() WRITES files, and the four tree() parses can fail on
    # a pre-corrupted part (malformed commentsExtended.xml was the
    # measured case) -- the caller's rollback boundary covers all of it.
    ensure_parts(pk)
    cm = pk.tree("word/comments.xml")
    ex = pk.tree("word/commentsExtended.xml")
    cids = pk.tree("word/commentsIds.xml")
    cex = pk.tree("word/commentsExtensible.xml")

    taken = {v for tr_ in (cm, cids, cex)
             for el in tr_.getroot().iter()
             for k, v in el.attrib.items()
             if k.endswith("}paraId") or k.endswith("}durableId")}
    pid = _hex31(f"cmtp:{cid}:{a.text[:32]}", taken)
    taken.add(pid)
    dur = _hex31(f"dur:{cid}:{pid}", taken)
    c = etree.SubElement(cm.getroot(), W + "comment",
                         {W + "id": cid, W + "author": a.author,
                          W + "date": _now(), W + "initials": a.initials})
    p = etree.SubElement(c, W + "p", {W14 + "paraId": pid,
                                      W14 + "textId": pid})
    # Word convention: the comment body starts with an
    # annotation-reference run (draws the mark inside the balloon)
    ref_run = etree.SubElement(p, W + "r")
    ref_rpr = etree.SubElement(ref_run, W + "rPr")
    etree.SubElement(ref_rpr, W + "rStyle").set(W + "val",
                                                "CommentReference")
    etree.SubElement(ref_run, W + "annotationRef")
    t = etree.SubElement(etree.SubElement(p, W + "r"), W + "t")
    t.text = a.text
    if a.text != a.text.strip():
        t.set(XSP, "preserve")
    exel = etree.SubElement(ex.getroot(), W15 + "commentEx",
                            {W15 + "paraId": pid, W15 + "done": "0"})
    if parent_pid is not None:
        exel.set(W15 + "paraIdParent", parent_pid)
    etree.SubElement(cids.getroot(), W16CID + "commentId",
                     {W16CID + "paraId": pid, W16CID + "durableId": dur})
    etree.SubElement(cex.getroot(), W16CEX + "commentExtensible",
                     {W16CEX + "durableId": dur,
                      W16CEX + "dateUtc": _now()})
    ensure_comment_style(pk)
    for name, tr in (("comments.xml", cm), ("commentsExtended.xml", ex),
                     ("commentsIds.xml", cids),
                     ("commentsExtensible.xml", cex)):
        pk.save(f"word/{name}", tr)
    pk.save("word/document.xml", dt)
    return (f"comment c{cid} added"
            + (f" (reply to c{a.reply})" if a.reply
               else f", anchored to {a.anchor!r}"))


# ---------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("target", help="unpacked/ dir or .docx")
    ap.add_argument("text", nargs="?", help="comment text")
    ap.add_argument("--anchor",
                    help="text to mark (unique within the --at scope)")
    ap.add_argument("--at", dest="block", metavar="#ID",
                    help="paragraph to scope the anchor to, by stable "
                         "#id (the view block line's #A1B2C3D4)")
    ap.add_argument("--reply-to", dest="reply",
                    help="parent comment id (c3 or 3 -- output prints "
                         "cN, so cN must be valid input)")
    ap.add_argument("--resolve", help="mark as resolved (c3 or 3)")
    ap.add_argument("--unresolve", help="undo resolved (c3 or 3)")
    ap.add_argument("--author", default="Claude")
    ap.add_argument("--initials", default="C")
    ap.add_argument("--ops", metavar="PLAN.json",
                    help="batch mode: JSON array of op objects, keys = "
                         "the CLI flags (text/anchor/block/reply_to/"
                         "resolve/unresolve/author/initials); one "
                         "process, all-or-nothing")
    ap.add_argument("--dry-run", action="store_true",
                    help="run every op and roll the tree back at the "
                         "end -- same code path, nothing kept")
    ap.add_argument("-o", "--out",
                    help=".docx-mode output (default: overwrite in place)")
    a = ap.parse_args()

    if a.block is not None:
        import re as _re
        s = str(a.block).strip()
        if not _re.fullmatch(r"#?[0-9A-Fa-f]{8}", s):
            raise SystemExit(f"E_SELECTOR: --at got {s!r} | must be "
                             "'#A1B2C3D4' (8 hex, copied from the "
                             "view's block line)")
        a.block = "#" + s.lstrip("#")

    if a.ops:
        import plan as _plan

        def _at(v, name):
            s2 = str(v).strip()
            import re as _re
            if not (isinstance(v, str)
                    and _re.fullmatch(r"#?[0-9A-Fa-f]{8}", s2)):
                raise ValueError(f"at got {v!r} | must be '#id' (8 hex, "
                                 "from the view's block line)")
            return "#" + s2.lstrip("#")
        if a.text or a.anchor or a.reply or a.resolve or a.unresolve \
                or a.block is not None:
            ap.error("--ops replaces the single-op flags")
        op_list = _plan.load(
            a.ops,
            fields={"text": _plan.str_norm, "anchor": _plan.str_norm,
                    "block": _at, "reply": _plan.cid_norm,
                    "resolve": _plan.cid_norm,
                    "unresolve": _plan.cid_norm,
                    "author": _plan.str_norm,
                    "initials": _plan.str_norm},
            aliases={"reply_to": "reply", "at": "block"},
            defaults=dict(text=None, anchor=None, block=None,
                          reply=None, resolve=None, unresolve=None,
                          author=a.author, initials=a.initials))
        for k2, o in enumerate(op_list):
            # 动作形状:恰好一个,载荷匹配——这是 comment 的语义,
            # 不是计划语法,所以留在这里(机器生成 JSON 最常见的畸形
            # 是键混搭;CLI 的静默优先级在批量里会无声丢 text)
            acts = [k for k in ("anchor", "reply", "resolve", "unresolve")
                    if getattr(o, k) is not None]
            if len(acts) != 1:
                raise SystemExit(
                    f"E_PLAN: op #{k2} needs exactly ONE action "
                    f"(anchor/reply_to/resolve/unresolve), got "
                    f"{acts if acts else 'none'}")
            if acts[0] in ("resolve", "unresolve") and o.text is not None:
                raise SystemExit(
                    f"E_PLAN: op #{k2} {acts[0]} takes no text (it "
                    "would be dropped) | try: reply_to for a "
                    "text reply")
            if acts[0] in ("anchor", "reply") and not o.text:
                raise SystemExit(f"E_PLAN: op #{k2} "
                                 f"{'anchor' if acts[0] == 'anchor' else 'reply_to'}"
                                 " needs text")
    else:
        op_list = [a]

    pk = Pack(a.target)
    # ONE rollback boundary around the whole plan: op #k failing undoes
    # ops #0..k-1 as well -- the workspace is byte-identical to before
    # the call, the message says which op to fix.
    snap = _snapshot(pk)
    msgs = []
    try:
        for k2, o in enumerate(op_list):
            try:
                msgs.append(apply_op(pk, o))
            except SystemExit as e:
                if a.ops:
                    raise SystemExit(
                        f"op #{k2}: {e.code}\nrolled back: the {k2} "
                        "previously successful op(s) were undone as well "
                        "— zero writes; fix this op and rerun the whole "
                        "plan")
                raise
    except BaseException:
        _restore(pk, snap)
        pk.discard()
        raise
    if a.dry_run:
        _restore(pk, snap)
        pk.discard()
        for m in msgs:
            print(m)
        print(f"dry-run: all {len(op_list)} ops are applicable; "
              "nothing written")
        return 0
    pk.finish(a.out)
    # 成功消息在 finish 之后:落盘失败时 stdout 不能已经宣称成功
    # (review 回归:-o 指向不可写路径,旧序先打印 "comment c0 added")
    for m in msgs:
        print(m)
    if a.ops:
        print(f"{len(op_list)} ops applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
