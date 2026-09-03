#!/usr/bin/env python3
"""The ONE body-coordinate resolver. The view's [N] means "Nth direct
child of w:body", and every tool must read it the same way:

- 0 <= N < len(body). A negative N is a Python-only notion -- the view
  never prints one, so accepting -1 silently edits the WRONG block
  (one consumer rejected it, another did not: exactly the drift a central
  resolver exists to prevent);
- the trailing body-level sectPr is a real child (it HAS an index) but
  is not an edit anchor: inserting after it puts content behind the
  section properties, which the schema requires to be last.
"""
from __future__ import annotations

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def body_block(body, index: int, op: str = "operation"):
    """-> body[index] after validating the coordinate. SystemExit with
    the tool-facing message otherwise."""
    if not isinstance(index, int) or not (0 <= index < len(body)):
        raise SystemExit(
            f"E_BLOCK_RANGE: block index {index} out of range "
            f"0..{len(body) - 1} | body of this document | "
            "try: target by stable #id from the view's block line")
    return body[index]


W14PARA = ("{http://schemas.microsoft.com/office/word/2010/wordml}"
           "paraId")


def by_id(scope, pid: str, op: str = "operation"):
    """-> the w:p under `scope` whose w14:paraId is `pid` ('#A1B2C3D4'
    or bare hex, case-insensitive). The stable write-side coordinate:
    ids never shift when paragraphs are inserted or deleted (prep
    backfills + dedupes them). SystemExit when absent or ambiguous."""
    want = pid.lstrip("#").upper()
    hits = [p for p in scope.iter(W + "p")
            if (p.get(W14PARA) or "").upper() == want]
    if not hits:
        raise SystemExit(
            f"E_ID_NOT_FOUND: no paragraph with id #{want} | searched "
            "the whole document scope | try: copy the #id from a "
            "CURRENT view (read.py <unpacked>) -- a deleted "
            "paragraph's id dies with it")
    if len(hits) > 1:
        raise SystemExit(
            f"E_ID_AMBIGUOUS: #{want} matches {len(hits)} paragraphs "
            "(duplicated paraId) | try: re-run prep.py -f (it dedupes "
            "ids) or use a text anchor")
    return hits[0]


def insertion_ref(body, index: int):
    """Like body_block, but for "insert after [N]": additionally refuses
    the body-level sectPr -- a paragraph after it fails XSD (sectPr must
    be w:body's last child)."""
    ref = body_block(body, index, "insert")
    if ref.tag == W + "sectPr":
        raise SystemExit(
            f"E_SECTPR_ANCHOR: block [{index}] is the body sectPr "
            "(always last), nothing can go after it | "
            f"try: after={index - 1} (the last real block)")
    return ref
