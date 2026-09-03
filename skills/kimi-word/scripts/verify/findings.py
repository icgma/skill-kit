"""Diagnostic vocabulary shared by checks, subtraction and rendering:
one Finding shape, or baseline monotonicity breaks (review #13/#14)."""
from __future__ import annotations

import re
from pathlib import Path


class Finding:
    """Structured finding (review #13, blocker E): identity + count
    instead of a display string, so baseline comparison is MONOTONIC:
    2 -> 1 of the same problem is never "new"; 1 -> 2 reports exactly
    the increment; a moved instance keeps its identity; different ids
    are different problems. A count embedded in a display string broke
    all four (an orphan marker worsening x1 -> x2 changed the string
    and improving x2 -> x1 ALSO changed it, reading as new).
    severity: error (provable) / warn (estimate) / info (advice)."""
    __slots__ = ("code", "identity", "msg", "severity", "count")

    def __init__(self, code, identity, msg, severity="error", count=1):
        self.code, self.identity = code, tuple(identity)
        self.msg, self.severity, self.count = msg, severity, count

    def __str__(self):
        return self.msg

    __repr__ = __str__

    def __eq__(self, o):
        return isinstance(o, Finding) and \
            (self.code, self.identity, self.msg, self.count) == \
            (o.code, o.identity, o.msg, o.count)

    def __hash__(self):
        return hash((self.code, self.identity, self.msg, self.count))


def _key(msg: str) -> str:
    """Plain-string violation -> position-independent comparison key
    (the auto-wrap identity for findings without a bespoke one). Strip
    only the leading "filename:line" -- a global substitution would
    also erase ':digits ' inside message bodies (e.g. values reported
    by XSD), collapsing distinct violations into one key and hiding
    new ones. body[N] block indices are normalized too: accepting/
    rejecting revisions SHIFTS later blocks, and a pre-existing
    overwide table at body[47] must still match itself at body[49]
    (multiset counting keeps two same-width tables distinct)."""
    msg = re.sub(r"^([^ :]+):\d+ ", r"\1 ", msg, count=1)
    return re.sub(r"body\[\d+\]", "body[*]", msg)


class Findings(list):
    """The ONLY container for diagnostics (review #14 closure gate:
    bad/warn never hold display strings). A plain-string append is
    auto-wrapped into a Finding whose identity is the normalized
    message (_key) -- monotonic for stable-message rules; rules whose
    messages embed variable counts/collections construct Finding
    explicitly with a bespoke identity + count. Display strings are
    produced only at the output layer (str(finding))."""

    def __init__(self, default_severity="error", items=()):
        super().__init__()
        self.sev = default_severity
        for x in items:
            self.append(x)

    def append(self, x):
        if isinstance(x, str):
            x = Finding("TEXT", (_key(x),), x, severity=self.sev)
        super().append(x)

    def extend(self, xs):
        for x in xs:
            self.append(x)


def _dump_lines(stem: str, lines: list):
    """Write full diagnostic lines to a temp file; -> path or None."""
    if not lines:
        return None
    import os
    import tempfile as _tf
    fp = Path(_tf.gettempdir()) / f"{stem}-{os.getpid()}.txt"
    try:
        fp.write_text("\n".join(lines), encoding="utf-8")
    except OSError:
        return None
    return fp
