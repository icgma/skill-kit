"""Total measurement model for OOXML sizes (review #13, blocker A).

ONE algebraic value model for every measurement consumer -- read,
validate, generation lint. No other file may parse a width/size
lexical itself: three scattered parsers produced, in successive
reviews, a traceback on the legal "150%", silent loss of the legal
"500pt", and an OverflowError on a long-but-legal decimal (int(float(
s)) -> inf). Each was "fixed" at one call site while the next call
site kept its own bug -- totality has to live in one place.

Value kinds (a total function of the lexical space):
    ("dxa",  twips:int)         absolute width, twentieths of a point
    ("pct",  fraction:Decimal)  relative width, 1.5 == 150%
    ("auto", 0) / ("nil", 0)    no measurement
    ("invalid", (raw, reason))  ANYTHING else -- never silently absent
    (None, None)                the element/attribute is absent

Spec grounding (ECMA-376 §17.4.87 CT_TblWidth, §17.18.87/.107
ST_TblWidth / universal measures, §22.9 ST_TwipsMeasure et al):
- w:type absent defaults to dxa;
- the measurement SYNTAX wins over a contradicting type ("150%" is a
  percentage even under type="dxa"; "500pt" is absolute even where a
  bare number was expected);
- type="pct" plain numbers are fiftieths of a percent (5000 = 100%);
- universal measures carry a unit suffix: mm cm in pt pc pi;
- decimal arithmetic ONLY (decimal.Decimal): OOXML sizes are decimal
  lexicals; binary floats overflow (1e400) and mis-round.
"""
from __future__ import annotations

import re
from decimal import (Decimal, InvalidOperation, ROUND_HALF_UP,
                     localcontext)

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

#: twips per unit as EXACT integer fractions (1in = 1440 twips =
#: 25.4mm, so 1cm = 144000/254 = 72000/127 twips). Integer rational
#: arithmetic, not Decimal: Decimal division truncates cm/mm to the
#: context precision, and context-bound quantize RAISES on a legal
#: 40-digit "...pt" (review #14: 16/42 legal lexicals tracebacked).
_UNIT_FRAC = {
    "pt": (20, 1), "pc": (240, 1), "pi": (240, 1), "in": (1440, 1),
    "cm": (72000, 127), "mm": (7200, 127),
}
_UNIV = re.compile(r"^([-+]?[0-9]+(?:\.[0-9]+)?)(mm|cm|in|pt|pc|pi)$")
_PCT = re.compile(r"^([-+]?[0-9]+(?:\.[0-9]+)?)%$")


def _twips_of(numstr: str, unit: str) -> int:
    """Exact half-up conversion, pure integer arithmetic -- input
    length cannot overflow or lose precision."""
    sign = -1 if numstr.startswith("-") else 1
    numstr = numstr.lstrip("+-")
    ip, _, fp = numstr.partition(".")
    n = int((ip + fp) or "0")
    num, den = _UNIT_FRAC[unit]
    d = den * 10 ** len(fp)
    q, r = divmod(n * num, d)
    return sign * (q + (1 if 2 * r >= d else 0))


def parse(el):
    """CT_TblWidth-shaped element (w + type attributes) -> (kind, val).
    Total: every input maps to exactly one kind; nothing raises,
    nothing silently disappears."""
    if el is None:
        return (None, None)
    return value(el.get(W + "w"), el.get(W + "type"))


def value(s, t=None):
    """The raw lexical pair -> (kind, val). See module docstring."""
    if s is not None:
        s = s.strip()
    if t in ("auto", "nil") and not s or t in ("auto", "nil") \
            and s in ("0", ""):
        return (t, 0)
    if not s:
        return ((t, 0) if t in ("auto", "nil")
                else (None, None) if t is None
                else ("invalid", (s, "no measurement value")))
    m = _PCT.match(s)                   # measurement syntax wins
    if m:
        with localcontext() as ctx:     # /100 terminates: EXACT at
            ctx.prec = len(s) + 10      # input-sized precision
            return ("pct", Decimal(m.group(1)) / Decimal(100))
    m = _UNIV.match(s)
    if m:
        return ("dxa", _twips_of(m.group(1), m.group(2)))
    if t == "auto" or t == "nil":
        return (t, 0)
    if t == "pct":
        try:                            # fiftieths of a percent;
            with localcontext() as ctx:  # /5000 = /2^3/5^4 terminates
                ctx.prec = len(s) + 10
                return ("pct", Decimal(s) / Decimal(5000))
        except InvalidOperation:
            return ("invalid", (s, "not a pct lexical"))
    # dxa: explicit, or the spec's default for a missing type
    try:                # plain integers first: arbitrary length, no
        return ("dxa", int(s, 10))   # context-precision ceiling
    except ValueError:
        pass
    # xsd:decimal lexical space only -- Decimal() alone also accepts
    # exponent notation ("1e5"), which the schema does not
    if not re.fullmatch(r"[-+]?[0-9]*\.[0-9]+|[-+]?[0-9]+\.[0-9]*", s):
        return ("invalid", (s, "not a decimal/universal measure"))
    try:
        with localcontext() as ctx:  # a 400-digit legal decimal blew
            ctx.prec = max(len(s) + 5, 28)   # the default 28-digit
            return ("dxa", int(Decimal(s).quantize(   # context
                0, rounding=ROUND_HALF_UP)))
    except InvalidOperation:
        return ("invalid", (s, "not a decimal/universal measure"))


def twips(s):
    """ST_TwipsMeasure / ST_SignedTwipsMeasure ATTRIBUTE value ->
    twips int, or None (absent/invalid -- callers that must surface
    invalids use value() directly). Accepts plain decimals and
    universal measures; percentages are not in these types' lexical
    space and map to None."""
    kind, val = value(s, None)
    return val if kind == "dxa" else None


def dxa(el) -> int:
    """Convenience: positive dxa twips of el, else 0."""
    kind, val = parse(el)
    return val if kind == "dxa" and val and val > 0 else 0


def fmt_pct(frac) -> str:
    """Display form of a pct fraction. NO float anywhere -- a long
    legal pct went float()-side and displayed 'inf%' (review #14)."""
    with localcontext() as ctx:
        ctx.prec = max(len(frac.as_tuple().digits) + 5, 28)
        s = format(frac * 100, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s + "%"


def fmt_pt(twips: int) -> str:
    """twips -> 'Xpt' display, exact integer arithmetic (pt =
    twips/20; int(huge)/20 went through float and overflowed)."""
    sign = "-" if twips < 0 else ""
    whole, frac = divmod(abs(twips) * 5, 100)
    if not frac:
        return f"{sign}{whole}pt"
    return f"{sign}{whole}.{str(frac).zfill(2).rstrip('0')}pt"
