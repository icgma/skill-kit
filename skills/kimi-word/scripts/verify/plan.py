#!/usr/bin/env python3
"""THE batch-plan loader shared by track.py and comment.py (v3.2: the
two mains had grown near-identical ~60-line validation loops whose
error wording was already drifting -- the same disease the shared OPC
resolver cured for part resolution).

A plan is a JSON array of op objects whose keys are the CLI flags
(hyphens or underscores). This module owns the SHAPE layer: file
reading, array/object checks, key aliasing, unknown-key refusal,
per-field normalization, `E_PLAN: op #k ...` error framing. What an op
MEANS (exactly one action, payload pairing) stays in each tool -- those
rules are the tool's semantics, not plan grammar.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace


def sel_norm(v, name: str) -> str:
    """Selector: '#A1B2C3D4' -- the stable paragraph id, nothing else
    (positional indexes died with the v3 break: they drift on
    insert/delete and forced a plan-ordering rule)."""
    s = str(v).strip()
    if re.fullmatch(r"#?[0-9A-Fa-f]{8}", s):
        return "#" + s.lstrip("#")
    raise ValueError(f"{name} got {v!r} | must be '#A1B2C3D4' (8 hex, "
                     "copied from the view's block line)")


def cid_norm(v, name: str) -> str:
    """Comment id: 'c3' / '3' / 3 (the tool prints cN, so cN must be
    valid input)."""
    if isinstance(v, bool) or not isinstance(v, (str, int)) \
            or str(v).strip() == "":
        raise ValueError(f"{name} got {v!r} | must be an id like 'c3'/3")
    return str(v)


def str_norm(v, name: str) -> str:
    if not isinstance(v, str):
        raise ValueError(f"{name} must be a string, got {v!r}")
    return v


def bool_norm(v, name: str) -> bool:
    if not isinstance(v, bool):
        raise ValueError(f"{name} must be true/false, got {v!r}")
    return v


def load(path, fields: dict, aliases: dict, defaults: dict) -> list:
    """plan.json -> [SimpleNamespace]. `fields` maps canonical key ->
    normalizer(value, key); `aliases` maps accepted spelling ->
    canonical; `defaults` seeds every op (author etc.). JSON null =
    "not given": the key keeps its default (a null once overrode
    author to None and blew up at write time with a bare TypeError)."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SystemExit(f"E_PLAN: cannot read plan | {e}")
    if not isinstance(raw, list) or not raw:
        raise SystemExit("E_PLAN: plan must be a non-empty JSON array")
    out = []
    for k, item in enumerate(raw):
        if not isinstance(item, dict):
            raise SystemExit(f"E_PLAN: op #{k} is not an object")
        o = SimpleNamespace(**defaults)
        for key, v in item.items():
            key = aliases.get(key.replace("-", "_"),
                              key.replace("-", "_"))
            if key not in fields:
                raise SystemExit(f"E_PLAN: op #{k} unknown key {key!r}")
            if v is None:
                continue
            try:
                setattr(o, key, fields[key](v, key))
            except ValueError as e:
                raise SystemExit(f"E_PLAN: op #{k} {e}")
        out.append(o)
    return out
