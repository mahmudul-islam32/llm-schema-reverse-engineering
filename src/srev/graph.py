"""Schema graph analytics on discovered foreign keys (F-22).

The accepted FKs form a directed graph (child table -> parent table). On top
of it this module answers practical analytics-engineering questions:

* **Table roles** for dimensional modelling: ``fact`` (references several
  tables, nothing references it), ``bridge`` (its key is made of FK columns,
  a many-to-many junction), ``dimension`` (referenced by others), ``lookup``
  (a small referenced table without outgoing FKs, e.g. code lists) and
  ``isolated`` (no relationships).
* **Connected components** (subject areas) and **hubs** (most referenced).
* **Join paths**: the shortest FK path between two tables, rendered as SQL.

The rules are transparent heuristics, meant as a starting point for a
modeller, not a replacement for one.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel

from srev.extract import Evidence
from srev.model import Proposal, split_ref

Role = Literal["fact", "bridge", "dimension", "lookup", "isolated"]


@dataclass(frozen=True)
class Edge:
    child: str
    parent: str
    child_cols: tuple[str, ...]
    parent_cols: tuple[str, ...]


class TableInfo(BaseModel):
    name: str
    role: Role
    rows: int
    references: list[str]
    referenced_by: list[str]
    self_reference: bool = False
    reason: str = ""


@dataclass
class SchemaGraph:
    tables: dict[str, int]
    edges: list[Edge]
    keys: dict[str, list[set[str]]] = field(default_factory=dict)
    lookup_max_rows: int = 100

    @classmethod
    def from_constraints(cls, evidence: Evidence, constraints: list[Proposal]) -> SchemaGraph:
        tables = {t.name.lower(): t.row_count for t in evidence.tables}
        edges, keys = [], defaultdict(list)
        for p in constraints:
            if p.type == "FK":
                edges.append(
                    Edge(
                        child=p.table,
                        parent=p.ref_table or "",
                        child_cols=tuple(split_ref(r)[1] for r in p.lhs),
                        parent_cols=tuple(split_ref(r)[1] for r in p.rhs),
                    )
                )
            elif p.type == "UCC":
                keys[p.table].append({split_ref(r)[1] for r in p.lhs})
        return cls(tables=tables, edges=edges, keys=dict(keys))

    # -- structure ---------------------------------------------------------
    def references(self, table: str) -> list[str]:
        return sorted({e.parent for e in self.edges if e.child == table and e.parent != table})

    def referenced_by(self, table: str) -> list[str]:
        return sorted({e.child for e in self.edges if e.parent == table and e.child != table})

    def roles(self) -> list[TableInfo]:
        out = []
        for t, rows in sorted(self.tables.items()):
            refs, by = self.references(t), self.referenced_by(t)
            fk_cols = {c for e in self.edges if e.child == t for c in e.child_cols}
            is_bridge = any(len(k) >= 2 and k <= fk_cols for k in self.keys.get(t, []))
            if is_bridge and len(refs) >= 2:
                role, why = "bridge", "key consists of foreign-key columns (many-to-many)"
            elif len(refs) >= 2 and not by:
                role, why = "fact", f"references {len(refs)} tables, referenced by none"
            elif by and not refs and rows <= self.lookup_max_rows:
                role, why = "lookup", f"small ({rows} rows) and only referenced"
            elif by:
                role, why = "dimension", f"referenced by {len(by)} table(s)"
            elif refs:
                role, why = "fact", "only references other tables"
            else:
                role, why = "isolated", "no discovered relationships"
            out.append(
                TableInfo(
                    name=t, role=role, rows=rows, references=refs, referenced_by=by,
                    self_reference=any(e.child == t == e.parent for e in self.edges), reason=why,
                )
            )  # fmt: skip
        return out

    def components(self) -> list[list[str]]:
        adj = self._undirected()
        seen: set[str] = set()
        comps = []
        for t in sorted(self.tables):
            if t in seen:
                continue
            comp, queue = [], deque([t])
            seen.add(t)
            while queue:
                cur = queue.popleft()
                comp.append(cur)
                for nxt, _ in adj[cur]:
                    if nxt not in seen:
                        seen.add(nxt)
                        queue.append(nxt)
            comps.append(sorted(comp))
        return sorted(comps, key=lambda c: (-len(c), c))

    def hubs(self, top: int = 5) -> list[tuple[str, int]]:
        counts = {t: len(self.referenced_by(t)) for t in self.tables}
        return sorted(((t, n) for t, n in counts.items() if n), key=lambda x: (-x[1], x[0]))[:top]

    # -- join paths --------------------------------------------------------
    def _undirected(self) -> dict[str, list[tuple[str, Edge]]]:
        adj: dict[str, list[tuple[str, Edge]]] = {t: [] for t in self.tables}
        for e in self.edges:
            if e.child == e.parent:
                continue
            adj.setdefault(e.child, []).append((e.parent, e))
            adj.setdefault(e.parent, []).append((e.child, e))
        return adj

    def join_path(self, source: str, target: str) -> list[tuple[str, str, Edge]] | None:
        """Shortest FK path as ``(from_table, to_table, edge)`` steps; None if unreachable."""
        source, target = source.lower(), target.lower()
        if source not in self.tables or target not in self.tables:
            raise KeyError(f"unknown table: {source if source not in self.tables else target}")
        if source == target:
            return []
        adj = self._undirected()
        prev: dict[str, tuple[str, Edge]] = {}
        queue, seen = deque([source]), {source}
        while queue:
            cur = queue.popleft()
            for nxt, edge in sorted(adj[cur], key=lambda x: (x[0], x[1].child_cols)):
                if nxt in seen:
                    continue
                seen.add(nxt)
                prev[nxt] = (cur, edge)
                if nxt == target:
                    steps = []
                    node = target
                    while node != source:
                        before, e = prev[node]
                        steps.append((before, node, e))
                        node = before
                    return list(reversed(steps))
                queue.append(nxt)
        return None

    def join_sql(self, source: str, target: str, columns: str = "*") -> str | None:
        path = self.join_path(source, target)
        if path is None:
            return None
        alias = {source.lower(): "t0"}
        lines = [f"SELECT {columns}", f"FROM {source.lower()} AS t0"]
        for i, (frm, to, e) in enumerate(path, start=1):
            alias[to] = f"t{i}"
            if e.child == frm:  # walking child -> parent
                pairs = zip(e.child_cols, e.parent_cols, strict=True)
                cond = " AND ".join(f"{alias[frm]}.{a} = {alias[to]}.{b}" for a, b in pairs)
            else:  # walking parent -> child
                pairs = zip(e.parent_cols, e.child_cols, strict=True)
                cond = " AND ".join(f"{alias[frm]}.{a} = {alias[to]}.{b}" for a, b in pairs)
            lines.append(f"JOIN {to} AS t{i} ON {cond}")
        return "\n".join(lines)

    def star_schemas(self) -> dict[str, list[str]]:
        """Each fact table with the dimensions/lookups reachable along outgoing FKs."""
        out = {}
        for info in self.roles():
            if info.role not in ("fact", "bridge"):
                continue
            reach, frontier = set(), list(info.references)
            while frontier:
                t = frontier.pop()
                if t in reach or t == info.name:
                    continue
                reach.add(t)
                frontier += self.references(t)
            out[info.name] = sorted(reach)
        return out
