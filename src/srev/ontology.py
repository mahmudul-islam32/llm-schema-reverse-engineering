"""Ontology extraction from discovered constraints (F-25).

Relational-to-ontology mapping usually needs declared keys and foreign keys
(the W3C *Direct Mapping* uses exactly those). Undocumented databases have
neither, so this module derives an OWL ontology and a W3C R2RML mapping from
the constraints ``srev discover`` verified on the data:

* every table becomes an ``owl:Class``, except *pure bridge tables* (all
  columns form the key and are FKs into two tables), which become an
  ``owl:ObjectProperty`` between the two classes (many-to-many);
* every non-FK column becomes an ``owl:DatatypeProperty`` (functional, with
  an XSD range from the column type);
* every FK becomes an ``owl:ObjectProperty`` (functional, domain = child
  class, range = parent class; self-references allowed);
* every key (UCC) becomes an ``owl:hasKey`` axiom;
* every non-key FD ``X -> A`` whose left side is not an FK suggests a hidden
  concept (``owner.city -> country`` suggests a *City* class); these are
  emitted as annotated candidate classes for a modeller to confirm. FDs whose
  left side is an FK mark ``A`` as a redundant copy of parent data.

Every axiom carries its evidence (constraint and measured statistic) as an
``rdfs:comment``. The same plan also renders the R2RML mapping and, from a
live database, the instance data as N-Triples.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from srev.db import Connector
from srev.extract import Evidence
from srev.model import Proposal, Verdict, VerifiedProposal, split_ref

XSD = {
    "integer": "xsd:integer",
    "numeric": "xsd:decimal",
    "boolean": "xsd:boolean",
    "text": "xsd:string",
}
_SUFFIX = re.compile(r"_(id|key|no|nr|cd|code|ref|fk)$")


def _pascal(name: str) -> str:
    parts = [p for p in re.split(r"[^A-Za-z0-9]+", name) if p]
    out = "".join(p[:1].upper() + p[1:] for p in parts) or "Thing"
    return out if out[0].isalpha() else f"C{out}"


def _local(name: str) -> str:
    out = re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_") or "x"
    return out if out[0].isalpha() else f"p_{out}"


def _xsd(col_type: str, family: str) -> str:
    t = col_type.lower()
    if family == "temporal":
        return "xsd:dateTime" if "timestamp" in t else ("xsd:time" if t == "time" else "xsd:date")
    if family == "numeric" and ("double" in t or "real" in t or "float" in t):
        return "xsd:double"
    return XSD.get(family, "xsd:string")


def _lit(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'


def _evidence(p: Proposal, v: Verdict | None) -> str:
    stat = f"; {v.statistic}={v.value}" if v and v.value is not None else ""
    return f"discovered by srev: {p.describe()}{stat}"


@dataclass
class DataProp:
    column: str
    prop: str
    range: str
    comment: str = ""


@dataclass
class ObjProp:
    columns: list[str]
    prop: str
    parent: str
    parent_columns: list[str]
    comment: str


@dataclass
class ClassSpec:
    table: str
    name: str
    key: list[str]
    keys: list[tuple[list[str], str]] = field(default_factory=list)
    data: list[DataProp] = field(default_factory=list)
    objects: list[ObjProp] = field(default_factory=list)


@dataclass
class BridgeSpec:
    table: str
    prop: str
    left: str
    left_columns: list[str]
    left_parent_columns: list[str]
    right: str
    right_columns: list[str]
    right_parent_columns: list[str]
    comment: str


@dataclass
class ConceptCandidate:
    name: str
    table: str
    determinant: list[str]
    attributes: list[str]
    comment: str


@dataclass
class OntologyPlan:
    base: str
    database: str
    classes: dict[str, ClassSpec]
    bridges: list[BridgeSpec]
    candidates: list[ConceptCandidate]

    def iri(self, table: str) -> str:
        return f"{self.base}{_local(table.lower())}/"


def build_plan(
    evidence: Evidence,
    constraints: list[VerifiedProposal],
    base: str = "http://example.org/srev/",
) -> OntologyPlan:
    base = base if base.endswith(("/", "#")) else base + "/"
    tables = {t.name.lower(): t for t in evidence.tables}
    cols = {(t, c.name.lower()): c for t, te in tables.items() for c in te.columns}
    real = {(t, c.name.lower()): c.name for t, te in tables.items() for c in te.columns}
    verdict = {v.proposal.key(): v.verdict for v in constraints}
    props = [v.proposal for v in constraints]
    fks = [p for p in props if p.type == "FK"]
    uccs = [p for p in props if p.type == "UCC"]

    def colname(ref: str) -> str:
        t, c = split_ref(ref)
        return real.get((t, c), c)

    # primary key per table: smallest key-like UCC, NULL-free first
    def rank(p: Proposal) -> tuple[int, int, str]:
        nulls = sum(cols[split_ref(r)].null_count for r in p.lhs if split_ref(r) in cols)
        return (len(p.lhs), int(nulls > 0), ",".join(p.lhs))

    pk: dict[str, Proposal] = {}
    for u in sorted(uccs, key=rank):
        pk.setdefault(u.table, u)

    # pure bridge tables: the key covers all columns and consists of FKs into two tables
    bridges: list[BridgeSpec] = []
    bridge_tables: set[str] = set()
    for t, te in tables.items():
        key = pk.get(t)
        table_fks = [f for f in fks if f.table == t]
        if key is None or len(key.lhs) != len(te.columns) or len(table_fks) != 2:
            continue
        fk_cols = {r for f in table_fks for r in f.lhs}
        if set(key.lhs) != fk_cols:
            continue
        a, b = sorted(table_fks, key=lambda f: f.lhs)
        bridges.append(
            BridgeSpec(
                table=te.name,
                prop=_local(f"{t}"),
                left=a.ref_table or "",
                left_columns=[colname(r) for r in a.lhs],
                left_parent_columns=[colname(r) for r in a.rhs],
                right=b.ref_table or "",
                right_columns=[colname(r) for r in b.lhs],
                right_parent_columns=[colname(r) for r in b.rhs],
                comment=f"many-to-many via bridge table {te.name}; "
                + _evidence(key, verdict.get(key.key())),
            )
        )
        bridge_tables.add(t)

    used: set[str] = set()

    def unique(name: str) -> str:
        out, i = name, 2
        while out in used:
            out, i = f"{name}_{i}", i + 1
        used.add(out)
        return out

    classes: dict[str, ClassSpec] = {}
    for t, te in tables.items():
        if t in bridge_tables:
            continue
        spec = ClassSpec(
            table=te.name,
            name=_pascal(t),
            key=[colname(r) for r in pk[t].lhs] if t in pk else [],
        )
        fk_cols = {r for f in fks if f.table == t for r in f.lhs}
        for c in te.columns:
            ref = f"{t}.{c.name.lower()}"
            if ref in fk_cols:
                continue
            spec.data.append(
                DataProp(c.name, _local(f"{t}_{c.name.lower()}"), _xsd(c.type, c.family))
            )
        for f in (f for f in fks if f.table == t):
            names = [_SUFFIX.sub("", split_ref(r)[1]) or split_ref(r)[1] for r in f.lhs]
            spec.objects.append(
                ObjProp(
                    columns=[colname(r) for r in f.lhs],
                    prop=unique(_local(f"{t}_{'_'.join(names)}")),
                    parent=f.ref_table or "",
                    parent_columns=[colname(r) for r in f.rhs],
                    comment=_evidence(f, verdict.get(f.key())),
                )
            )
        for u in (u for u in uccs if u.table == t):
            spec.keys.append(([colname(r) for r in u.lhs], _evidence(u, verdict.get(u.key()))))
        classes[t] = spec

    # FDs: hidden concepts (lhs not an FK) or redundant copies of parent data (lhs is an FK)
    candidates: dict[tuple[str, tuple[str, ...]], ConceptCandidate] = {}
    for p in (p for p in props if p.type == "FD"):
        t = p.table
        if t not in classes or any(set(u.lhs) <= set(p.lhs) for u in uccs if u.table == t):
            continue
        fk_lhs = {r for f in fks if f.table == t for r in f.lhs}
        rhs_col = colname(p.rhs[0])
        if set(p.lhs) <= fk_lhs:
            for d in classes[t].data:
                if d.column == rhs_col:
                    d.comment = (
                        f"redundant copy of parent data, determined by FK column(s) "
                        f"{', '.join(colname(r) for r in p.lhs)}; "
                        + _evidence(p, verdict.get(p.key()))
                    )
            continue
        det = tuple(colname(r) for r in p.lhs)
        cand = candidates.setdefault(
            (t, det),
            ConceptCandidate(
                name=_pascal("_".join(_SUFFIX.sub("", d.lower()) for d in det)),
                table=classes[t].table,
                determinant=list(det),
                attributes=[],
                comment="candidate concept: determinant of a non-key functional dependency",
            ),
        )
        cand.attributes.append(rhs_col)
        cand.comment += f"; {_evidence(p, verdict.get(p.key()))}"
    class_names = {c.name for c in classes.values()}
    for cand in candidates.values():
        if cand.name in class_names:  # e.g. a City candidate next to an existing city table
            cand.name += "Concept"
        class_names.add(cand.name)
    return OntologyPlan(base, evidence.database, classes, bridges, list(candidates.values()))


def to_owl(plan: OntologyPlan) -> str:
    b = plan.base
    out = [
        f"@prefix : <{b}> .",
        "@prefix owl: <http://www.w3.org/2002/07/owl#> .",
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .",
        "@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .",
        "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .",
        "",
        f"<{b}> a owl:Ontology ;",
        f"    rdfs:comment {_lit(f'Extracted by srev from {plan.database}; review before use.')} .",
        "",
    ]
    for spec in plan.classes.values():
        out += [
            f":{spec.name} a owl:Class ;",
            f"    rdfs:label {_lit(spec.table)} ;",
            f"    rdfs:comment {_lit(f'table {spec.table}')} .",
        ]
        for d in spec.data:
            out += [
                f":{d.prop} a owl:DatatypeProperty, owl:FunctionalProperty ;",
                f"    rdfs:label {_lit(f'{spec.table}.{d.column}')} ;",
                f"    rdfs:domain :{spec.name} ;",
                f"    rdfs:range {d.range}"
                + (f" ;\n    rdfs:comment {_lit(d.comment)} ." if d.comment else " ."),
            ]
        for o in spec.objects:
            parent = plan.classes.get(o.parent.lower())
            out += [
                f":{o.prop} a owl:ObjectProperty, owl:FunctionalProperty ;",
                f"    rdfs:label {_lit(f'{spec.table}.' + ', '.join(o.columns))} ;",
                f"    rdfs:domain :{spec.name} ;",
                f"    rdfs:range :{parent.name if parent else _pascal(o.parent)} ;",
                f"    rdfs:comment {_lit(o.comment)} .",
            ]
        for cols, comment in spec.keys:
            props = [
                next((d.prop for d in spec.data if d.column == c), None)
                or next((o.prop for o in spec.objects if c in o.columns), None)
                for c in cols
            ]
            if all(props):
                out += [
                    f"# key {', '.join(cols)}: {comment}",
                    f":{spec.name} owl:hasKey ( {' '.join(':' + str(p) for p in props)} ) .",
                ]
        out.append("")
    for br in plan.bridges:
        left, right = plan.classes.get(br.left.lower()), plan.classes.get(br.right.lower())
        out += [
            f":{br.prop} a owl:ObjectProperty ;",
            f"    rdfs:label {_lit(br.table)} ;",
            f"    rdfs:domain :{left.name if left else _pascal(br.left)} ;",
            f"    rdfs:range :{right.name if right else _pascal(br.right)} ;",
            f"    rdfs:comment {_lit(br.comment)} .",
            "",
        ]
    for c in plan.candidates:
        out += [
            f":{c.name} a owl:Class ;",
            f"    rdfs:label {_lit(c.name)} ;",
            f"    rdfs:comment {_lit(c.comment + '; attributes: ' + ', '.join(c.attributes))} .",
            "",
        ]
    return "\n".join(out).rstrip() + "\n"


def _template(base_iri: str, columns: list[str]) -> str:
    return base_iri + "/".join("{" + c + "}" for c in columns)


def to_r2rml(plan: OntologyPlan) -> str:
    b = plan.base
    out = [
        "@prefix rr: <http://www.w3.org/ns/r2rml#> .",
        f"@prefix : <{b}> .",
        f"@prefix map: <{b}mapping#> .",
        "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .",
        "",
    ]
    for t, spec in plan.classes.items():
        parts = [
            f"map:{spec.name} a rr:TriplesMap",
            f"    rr:logicalTable [ rr:tableName {_lit(spec.table)} ]",
            f"    rr:subjectMap [ rr:template {_lit(_template(plan.iri(t), spec.key))} ; "
            f"rr:class :{spec.name} ]"
            if spec.key
            else f"    rr:subjectMap [ rr:termType rr:BlankNode ; rr:class :{spec.name} ]",
        ]
        parts += [
            f"    rr:predicateObjectMap [ rr:predicate :{d.prop} ; rr:objectMap [ rr:column "
            f"{_lit(d.column)} ; rr:datatype {d.range} ] ]"
            for d in spec.data
        ]
        for o in spec.objects:
            parent = plan.classes.get(o.parent.lower())
            if parent is None:
                continue
            joins = " ; ".join(
                f"rr:joinCondition [ rr:child {_lit(c)} ; rr:parent {_lit(pc)} ]"
                for c, pc in zip(o.columns, o.parent_columns, strict=True)
            )
            parts.append(
                f"    rr:predicateObjectMap [ rr:predicate :{o.prop} ; rr:objectMap [ "
                f"rr:parentTriplesMap map:{parent.name} ; {joins} ] ]"
            )
        out += [" ;\n".join(parts) + " .", ""]
    for br in plan.bridges:
        left, right = plan.classes.get(br.left.lower()), plan.classes.get(br.right.lower())
        if not left or not right or not left.key or not right.key:
            continue
        # the bridge columns hold the parents' key values, so the parents' IRI templates apply
        lt = _template(
            plan.iri(br.left.lower()),
            [
                br.left_columns[br.left_parent_columns.index(k)]
                if k in br.left_parent_columns
                else k
                for k in left.key
            ],
        )
        rt = _template(
            plan.iri(br.right.lower()),
            [
                br.right_columns[br.right_parent_columns.index(k)]
                if k in br.right_parent_columns
                else k
                for k in right.key
            ],
        )
        out += [
            f"map:{_pascal(br.table)} a rr:TriplesMap ;",
            f"    rr:logicalTable [ rr:tableName {_lit(br.table)} ] ;",
            f"    rr:subjectMap [ rr:template {_lit(lt)} ] ;",
            f"    rr:predicateObjectMap [ rr:predicate :{br.prop} ; "
            f"rr:objectMap [ rr:template {_lit(rt)} ] ] .",
            "",
        ]
    return "\n".join(out).rstrip() + "\n"


def _nt_literal(value: Any, datatype: str) -> str:
    if isinstance(value, bool):
        value = "true" if value else "false"
    elif hasattr(value, "isoformat"):
        value = value.isoformat()
    text = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    full = datatype.replace("xsd:", "http://www.w3.org/2001/XMLSchema#")
    return f'"{text}"^^<{full}>'


def to_ntriples(plan: OntologyPlan, conn: Connector, max_rows: int | None = None) -> str:
    """Instance data following the plan (what an R2RML processor would produce)."""
    lines: list[str] = []
    rdf_type = "<http://www.w3.org/1999/02/22-rdf-syntax-ns#type>"
    limit = f" LIMIT {int(max_rows)}" if max_rows else ""

    def subject(table: str, spec: ClassSpec, row: dict[str, Any], prefix_cols: list[str]) -> str:
        values = [row[c] for c in prefix_cols]
        if any(v is None for v in values):
            return ""
        return "<" + plan.iri(table) + "/".join(quote(str(v), safe="") for v in values) + ">"

    for t, spec in plan.classes.items():
        if not spec.key:
            continue
        cols = [c.name for c in conn.list_columns(spec.table)]
        rows = conn.query(
            f"SELECT {', '.join(conn.quote(c) for c in cols)} FROM {conn.qtable(spec.table)}{limit}"
        )
        for raw in rows:
            row = dict(zip(cols, raw, strict=True))
            s = subject(t, spec, row, spec.key)
            if not s:
                continue
            lines.append(f"{s} {rdf_type} <{plan.base}{spec.name}> .")
            for d in spec.data:
                if row.get(d.column) is not None:
                    lines.append(
                        f"{s} <{plan.base}{d.prop}> {_nt_literal(row[d.column], d.range)} ."
                    )
            for o in spec.objects:
                parent = plan.classes.get(o.parent.lower())
                if parent is None or not parent.key:
                    continue
                pvals = {pc: row[c] for c, pc in zip(o.columns, o.parent_columns, strict=True)}
                if any(v is None for v in pvals.values()) or set(pvals) != set(parent.key):
                    continue
                obj = subject(o.parent.lower(), parent, pvals, parent.key)
                lines.append(f"{s} <{plan.base}{o.prop}> {obj} .")
    for br in plan.bridges:
        left, right = plan.classes.get(br.left.lower()), plan.classes.get(br.right.lower())
        if not left or not right:
            continue
        cols = br.left_columns + br.right_columns
        rows = conn.query(
            f"SELECT {', '.join(conn.quote(c) for c in cols)} FROM {conn.qtable(br.table)}{limit}"
        )
        for raw in rows:
            row = dict(zip(cols, raw, strict=True))
            lv = {pc: row[c] for c, pc in zip(br.left_columns, br.left_parent_columns, strict=True)}
            rv = {
                pc: row[c] for c, pc in zip(br.right_columns, br.right_parent_columns, strict=True)
            }
            ls = subject(br.left.lower(), left, lv, left.key) if set(lv) == set(left.key) else ""
            rs = (
                subject(br.right.lower(), right, rv, right.key) if set(rv) == set(right.key) else ""
            )
            if ls and rs:
                lines.append(f"{ls} <{plan.base}{br.prop}> {rs} .")
    return "\n".join(lines) + ("\n" if lines else "")
