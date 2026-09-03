#!/usr/bin/env python3
"""**Semantic diff** of two docx files: compared at the view level, not
the XML level.

    python scripts/vdiff.py before.docx after.docx [--full]

Answers the gate the other two do not: **"did I change what I intended,
and nothing else?"** (validate answers "is it legal", rendering answers
"does it look right").

Three payoffs of diffing at the view level:
- **inherits the view's coverage**: changes to anything the view models
  (content / effective formats / provenance / structure) show up; the
  view's own blind spots (named on @skip, plus OLE internals) are the
  diff's blind spots too -- no stronger claim;
- **inherits the view's compression**: a style-layer edit applies to the
  whole document but diffs as a single @style line, because block lines
  are already differential against the style chain;
- **natural denoising**: rsid churn, entity forms and attribute order do
  not exist in the view.

Two mechanisms (both forced by real measurements):
- **index-stripped alignment**: deleting one paragraph shifts every later
  [N]; a naive diff reports 815 lines, stripping the leading index before
  aligning reports 5.
- **signature grouping**: the same direct-format change applied to 200
  paragraphs reports line by line as 678 lines; grouped by field-level
  change signature the largest group reports as one line
  (`+align=right ×287`).
"""
from __future__ import annotations

import argparse
import difflib
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import read as RD  # noqa: E402
import walker as WK  # noqa: E402  (the one rendered-text rule)

_IDX = re.compile(r"^\[([\d.]+(?:-[\d.]+)?)( empty(?:×\d+)?)?\s*")


def _split(line: str):
    """Line -> (index, index-stripped content). Non-block lines get None.
    The `empty(×N)` marker STAYS in the content: stripping it makes
    [5 empty] and [5-15 empty×11] identical, leaving the diff blind to
    empty-paragraph count changes (the carrier of layout spacing)."""
    m = _IDX.match(line)
    if not m:
        return None, line
    return m.group(1), "[" + (m.group(2) or "").strip() + line[m.end():]


def _head_body(content: str):
    """Block-line content -> (head incl. formats, body text)."""
    m = re.match(r"(\[[^\]]*\]|\[)\s?(.*)", content)
    return (m.group(1), m.group(2)) if m else ("", content)


def _body_of(content: str) -> str:
    """Body text of a line, head stripped (for compact old->new)."""
    return _head_body(content or "")[1] or (content or "")


def _sig(old_head: str, new_head: str) -> str:
    """Two block heads -> field-level change signature (added/removed)."""
    def toks(h):
        # quote-aware split: font="Times New Roman" is one token; a bare
        # space split would shatter it into three bogus tokens
        parts = re.findall(r'[^ ;"]*"[^"]*"[^ ;]*|[^ ;"]+', h.strip("[] "))
        return set(t for t in parts if t
                   and not t.startswith("#"))     # paraId is not a change
    a, b = toks(old_head), toks(new_head)
    add = ";".join(sorted(b - a))
    rm = ";".join(sorted(a - b))
    return (f"+{add}" if add else "") + (f" -{rm}" if rm else "")


def _char_multiset(src: Path):
    """Rendered-char multisets over story parts -- the universal audit
    line for "content must not change" tasks (every tester rebuilt this
    by hand, one of them twice). Live text (w:t) and tracked-deleted text
    (w:delText) are counted SEPARATELY: lumping them reported "chars
    conserved" for a tracked deletion (t -> delText moves nothing in the
    union) and "-N chars" for a plain revision accept -- both misleading.
    Objects count drawing + OLE + legacy VML pict (a v:imagedata image
    deleted from an old file used to vanish without a trace here)."""
    import re as _re
    from collections import Counter
    from lxml import etree as _et
    W_ = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    c: Counter = Counter()
    dc: Counter = Counter()
    n_obj = 0
    parts = RD.load(Path(src))
    stories = RD.story_names(parts) - {"word/comments.xml"}
    for name, data in parts.items():
        if name not in stories:
            continue
        root = _et.fromstring(data)
        for t in root.iter(W_ + "t", W_ + "delText"):
            # WK.rendered: the one xml:space trim rule project-wide
            (c if t.tag == W_ + "t" else dc).update(WK.rendered(t))
        n_obj += sum(1 for _ in root.iter(W_ + "drawing")) \
            + sum(1 for _ in root.iter(W_ + "object")) \
            + sum(1 for _ in root.iter(W_ + "pict"))
    return c, dc, n_obj


def _media_digests(src: Path) -> dict:
    """word/media/* + word/embeddings/* -> sha256. The view is text; a
    same-name same-size image swapped for different pixels moved NOTHING
    the text diff can see -- binary payloads need their own audit line."""
    import hashlib
    import zipfile as _zf
    from pathlib import Path as _P
    src = _P(src)
    out = {}

    def _put(name, data):
        if name.startswith(("word/media/", "word/embeddings/")):
            out[name] = hashlib.sha256(data).hexdigest()
    if src.is_dir():
        for p in src.rglob("*"):
            if p.is_file():
                _put(str(p.relative_to(src)).replace("\\", "/"),
                     p.read_bytes())
    else:
        import opc as _opc                 # capped: media diff was an
        with _opc.CappedZip(src) as z:     # unbounded read path (P1.7)
            for n in z.namelist():
                if not n.endswith("/"):
                    _put(n, z.read(n))
    return out


def vdiff(before: Path, after: Path, full: bool = False, brief: bool = False,
          max_group_show: int = 3, max_singles: int = 40) -> str:
    # diff_mode: the ABSOLUTE fact layer. Display-layer compression
    # (@norm/@fmt aliases/@range folds/statistical dominants) has
    # content-derived baselines -- re-encoding on untouched blocks would
    # manufacture ghost diffs (§46 cellr lesson, generalized).
    va = RD.render(Path(before), diff_mode=True).split("\n")
    vb = RD.render(Path(after), diff_mode=True).split("\n")
    ia, ca = zip(*[_split(x) for x in va]) if va else ((), ())
    ib, cb = zip(*[_split(x) for x in vb]) if vb else ((), ())

    out = []
    sm = difflib.SequenceMatcher(None, ca, cb, autojunk=False)
    groups: dict = defaultdict(list)  # signature -> [(old idx, new idx, old head, new head)]
    singles: list = []                # ungroupable (add/delete/text change)

    for tag, a0, a1, b0, b1 in sm.get_opcodes():
        if tag == "equal":
            continue
        if tag == "replace" and (a1 - a0) == (b1 - b0):
            # pair line by line: same body -> pure format change -> group
            for k in range(a1 - a0):
                oc, nc = ca[a0 + k], cb[b0 + k]
                oh, ob_ = _head_body(oc)
                nh, nb_ = _head_body(nc)
                mixed = (ia[a0 + k] is None) != (ib[b0 + k] is None)
                if ob_ == nb_ and oh != nh and not mixed:
                    groups[_sig(oh, nh)].append(
                        (ia[a0 + k], ib[b0 + k], oc, nc))
                elif mixed:
                    # a block line got position-paired with a structural
                    # line -> report honestly as delete + add
                    singles.append(("-", ia[a0 + k], oc, None, None))
                    singles.append(("+", None, None, ib[b0 + k], nc))
                else:
                    singles.append(("~", ia[a0 + k], oc, ib[b0 + k], nc))
            continue
        for k in range(a0, a1):
            singles.append(("-", ia[k], ca[k], None, None))
        for k in range(b0, b1):
            singles.append(("+", None, None, ib[k], cb[k]))

    # cellr is a content-derived aggregate: composition changes (fill a
    # cell, tie the vote) can re-encode untouched rows. The warning goes
    # on the CHANGED TABLE HEAD only -- a per-row "likely not a real
    # change" verdict was worse than the disease: the heuristic was
    # document-global and blessed a REAL bold edit in a different table
    # (measured). Row diffs below a flagged head are reported verbatim;
    # the reader resolves suspicion via --raw, the tool passes no
    # verdict it cannot back.
    _AGG_NOTE = (" [cellr is a content-derived aggregate: rows below "
                 "may re-encode with no underlying change -- verify "
                 "suspicious row diffs via --raw]")

    n_grouped = sum(len(v) for v in groups.values())
    ca_, da, oa = _char_multiset(before)
    cb_, db, ob = _char_multiset(after)
    lost, added = ca_ - cb_, cb_ - ca_
    chars = "conserved" if not lost and not added else \
        f"-{sum(lost.values())} +{sum(added.values())}"
    dl, dg = da - db, db - da
    dnote = "" if not dl and not dg else \
        f", tracked-del chars -{sum(dl.values())} +{sum(dg.values())}"
    objs = "" if oa == ob else f", objects {oa}→{ob}"
    ma, mb = _media_digests(before), _media_digests(after)
    m_chg = sorted(n for n in ma.keys() & mb.keys() if ma[n] != mb[n])
    m_add, m_del = sorted(mb.keys() - ma.keys()), sorted(ma.keys() - mb.keys())
    mnote = ""
    if m_chg or m_add or m_del:
        bits = []
        if m_chg:
            bits.append(f"{len(m_chg)} rewritten ({', '.join(m_chg[:3])})")
        if m_add:
            bits.append(f"+{len(m_add)}")
        if m_del:
            bits.append(f"-{len(m_del)}")
        mnote = "; media " + " ".join(bits)
    header = (f"@vdiff {before} → {after}: "
              f"format-only {n_grouped} in {len(groups)} groups, "
              f"other {len(singles)}; chars {chars}{dnote}{objs}{mnote}")

    def lab(idx, content):
        """Line label: block lines use the index; structural lines
        (@style / table rows / @sec) use their leading words. Lines
        without an index used to print [None→None], leaving the reader
        to guess which line was meant."""
        if idx is not None:
            return f"[{idx}]"
        head = content.strip().split(" ", 2)
        return " ".join(head[:2])[:24]

    def emit(is_full):
        """-> (lines, truncated?). Rendered twice when needed: clipped
        for stdout, full to a sidecar file -- the review duty in
        editing.md is BIDIRECTIONAL and cannot be discharged from a
        truncated listing (measured on 5 tasks that all had to rerun
        with --full)."""
        body: list = []
        trunc = False
        for sig, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            idxs = [f"{o}→{n}" if o != n else str(o) for o, n, _, _ in items]
            span = (idxs[0] if len(idxs) == 1 else
                    f"{idxs[0]}…{idxs[-1]}" if len(idxs) > 6
                    else ",".join(idxs))
            body.append(f"  ×{len(items):<4d} {sig}   @ {span}"
                        + (_AGG_NOTE if "cellr:" in sig else ""))
            w = 10**9 if is_full else 100
            show = items if is_full else items[:max_group_show]
            for o, n, oc, nc in show:
                trunc |= not is_full and (len(oc) > w or len(nc) > w)
                body.append(f"        [{o}] {oc[:w]}")
                body.append(f"        [{n}] {nc[:w]}")
            if not is_full and len(items) > max_group_show:
                body.append(f"        … {len(items) - max_group_show} "
                            "more with same signature")
                trunc = True
        w = 10**9 if is_full else 150
        shown = singles if is_full else singles[:max_singles]
        for kind, oi, oc, ni, nc in shown:
            if not is_full:
                trunc |= len(oc or "") > w or len(nc or "") > w
            if kind == "-":
                body.append(f"  - "
                            f"{'[' + str(oi) + '] ' if oi is not None else ''}"
                            f"{oc[:w]}")
            elif kind == "+":
                body.append(f"  + "
                            f"{'[' + str(ni) + '] ' if ni is not None else ''}"
                            f"{nc[:w]}")
            else:
                agg = ("@tbl" in (oc or "") and "cellr:" in (oc or "")
                       + (nc or "") and oc != nc)
                body.append(f"  ~ {lab(oi, oc)}"
                            + (f"→[{ni}]"
                               if oi is not None and oi != ni else "")
                            + (_AGG_NOTE if agg else ""))
                body.append(f"    old {oc[:w]}")
                body.append(f"    new {nc[:w]}")
        if not is_full and len(singles) > max_singles:
            body.append(f"  … {len(singles) - max_singles} more "
                        "(--full for all)")
            trunc = True
        return body, trunc

    if brief:
        out.append(header)
        for sig, items in sorted(groups.items(),
                                 key=lambda kv: -len(kv[1])):
            out.append(f"  ×{len(items):<4d} {sig}")
        # singles one-liners: --brief used to print the header alone for
        # pure text changes -- zero information (measured dead end).
        # "~" shows old -> NEW: the new value is the edit's payload,
        # showing only the old side answered the wrong question
        for kind, oi, oc, ni, nc in singles[:20]:
            if kind == "~":
                out.append(f"  ~ {lab(oi, oc)} "
                           f"{_body_of(oc)[:40]} → {_body_of(nc)[:40]}")
            else:
                c = (nc if kind == "+" else oc) or ""
                out.append(f"  {kind} {lab(oi if kind != '+' else ni, c)} "
                           f"{c[:60]}")
        if len(singles) > 20:
            out.append(f"  … {len(singles) - 20} more")
        return "\n".join(out)

    body, trunc = emit(full)
    # ALWAYS write the untruncated diff to disk, and say what the file is
    # FOR. The old header ("full diff -> /tmp/...") read as an invitation:
    # agents Read the whole dump right after reading the summary, paying
    # twice (16.6k chars measured, dxv2-9 field report). The dump is a
    # grep target for locating specific entries the summary elided -- and
    # when nothing was truncated, the summary already IS the full diff.
    import os
    import tempfile as _tf
    fp = (Path(_tf.gettempdir()) /
          f"vdiff-full-{os.getpid()}-"
          f"{abs(hash((str(before), str(after)))) % 99991}.txt")
    full_body, _ = emit(True)
    try:
        fp.write_text("\n".join([header] + full_body), encoding="utf-8")
    except OSError:
        fp = None
    if trunc and not full:
        if fp:
            header += (f"; summary clipped, full diff written to {fp} "
                       "(grep to locate; do not read in full)")
    elif fp:
        header += (f"; full diff also written to {fp} (this output is "
                   "already complete; no need to read it)")
    out.append(header)
    out += body
    return "\n".join(out)



# v3.2: no CLI entry -- vdiff is the ENGINE behind `read.py A --diff B`
# and verify's diff stage. Two documented spellings of the same
# operation was the "two names for one thing" smell the naming review
# flagged; the module keeps its name, loses its door.
