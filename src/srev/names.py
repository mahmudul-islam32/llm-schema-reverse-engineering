"""Name-similarity helpers shared by the heuristic proposer and candidate ranking."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

KEY_SUFFIXES = ("id", "key", "no", "nr", "num", "number", "cd", "code", "pk", "fk", "ref")
TABLE_PREFIXES = ("tbl", "tab", "lkp", "lu", "dim", "fact", "t", "ref", "map")
_SPLIT = re.compile(r"[_\W]+|(?<=[a-z])(?=[A-Z])")


def tokens(name: str) -> list[str]:
    return [t.lower() for t in _SPLIT.split(name) if t]


def strip_column_prefix(column: str, table: str) -> str:
    """Remove a TPC-H style table prefix such as ``l_`` or ``ps_`` from a column."""
    parts = column.lower().split("_", 1)
    if len(parts) == 2 and len(parts[0]) <= 3:
        initials = "".join(t[0] for t in tokens(table) if t)
        if parts[0] in (table.lower()[: len(parts[0])], initials):
            return parts[1]
    return column.lower()


def table_stem(table: str) -> str:
    toks = tokens(table)
    while len(toks) > 1 and toks[0] in TABLE_PREFIXES:
        toks = toks[1:]
    stem = "_".join(toks)
    return stem[:-1] if stem.endswith("s") and len(stem) > 3 else stem


def core(column: str, table: str) -> str:
    """Column name without table prefix and key suffix: ``o_custkey`` -> ``cust``."""
    name = strip_column_prefix(column, table)
    toks = tokens(name)
    if len(toks) > 1 and toks[-1] in KEY_SUFFIXES:
        toks = toks[:-1]
    joined = "_".join(toks)
    for suffix in KEY_SUFFIXES:
        if joined.endswith(suffix) and len(joined) > len(suffix) + 1:
            return joined[: -len(suffix)]
    return joined


def keyish(column: str) -> bool:
    toks = tokens(column)
    if not toks:
        return False
    last = toks[-1]
    return last in KEY_SUFFIXES or any(last.endswith(s) for s in ("id", "key", "number"))


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def fk_name_score(src_table: str, src_col: str, ref_table: str, ref_col: str) -> float:
    """How plausible ``src_table.src_col -> ref_table.ref_col`` looks from names alone (0..1)."""
    src_core = core(src_col, src_table)
    ref_core = core(ref_col, ref_table)
    stem = table_stem(ref_table)
    candidates = [similarity(src_core, ref_core), similarity(src_core, stem)]
    if src_core and (stem.startswith(src_core) or src_core.startswith(stem[:4])):
        candidates.append(0.9)
    if strip_column_prefix(src_col, src_table) == strip_column_prefix(ref_col, ref_table):
        candidates.append(1.0)
    score = max(candidates)
    if keyish(src_col) and keyish(ref_col):
        score = min(1.0, score + 0.1)
    return round(score, 3)
