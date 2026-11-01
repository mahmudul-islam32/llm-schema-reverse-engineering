import json

import pytest

from srev.cli import main
from srev.datasets import load_dataset
from srev.db import SQLiteConnector, materialize
from srev.extract import extract
from srev.model import Verdict, VerifiedProposal
from srev.ontology import build_plan, to_ntriples, to_owl, to_r2rml
from srev.truth import gold_from_dataset

rdflib = pytest.importorskip("rdflib")
OWL = rdflib.Namespace("http://www.w3.org/2002/07/owl#")
RR = rdflib.Namespace("http://www.w3.org/ns/r2rml#")
RDF, RDFS = rdflib.RDF, rdflib.RDFS
BASE = rdflib.Namespace("http://example.org/srev/")


@pytest.fixture(scope="module")
def museum():
    ds = load_dataset("museum", seed=3, scale=0.3)
    conn = SQLiteConnector(":memory:")
    materialize(ds, conn)
    ev = extract(conn, "museum", rows_per_table=2)
    ok = Verdict(accepted=True, status="ok", statistic="coverage", value=1.0)
    constraints = [VerifiedProposal(proposal=p, verdict=ok) for p in gold_from_dataset(ds).labels]
    yield ds, conn, ev, constraints
    conn.close()


def _graph(text, fmt="turtle"):
    g = rdflib.Graph()
    g.parse(data=text, format=fmt)
    return g


def test_plan_and_owl(museum):
    _, _, ev, constraints = museum
    plan = build_plan(ev, constraints)
    assert set(plan.classes) == {"artist", "object", "owner", "provenance_event", "exhibition"}
    assert [(b.table, b.left, b.right) for b in plan.bridges] == [("loan", "exhibition", "object")]
    assert [(c.name, c.attributes) for c in plan.candidates] == [("City", ["country"])]
    g = _graph(to_owl(plan))
    assert (BASE.ProvenanceEvent, RDF.type, OWL.Class) in g
    # self-reference: previous event is an object property from ProvenanceEvent to itself
    prev = BASE.provenance_event_previous_event
    assert (prev, RDFS.domain, BASE.ProvenanceEvent) in g
    assert (prev, RDFS.range, BASE.ProvenanceEvent) in g
    assert (BASE.loan, RDFS.domain, BASE.Exhibition) in g and (
        BASE.loan,
        RDFS.range,
        BASE.Object,
    ) in g
    assert (BASE.Artist, OWL.hasKey, None) in g
    # FK columns become object properties, not datatype properties
    assert (BASE.object_artist, RDF.type, OWL.ObjectProperty) in g
    assert (BASE.object_artist_id, None, None) not in g
    # FD with an FK on the left marks a redundant copy of parent data
    comment = str(g.value(BASE.object_artist_name, RDFS.comment))
    assert "redundant copy" in comment and "artist_id" in comment
    assert (BASE.City, RDF.type, OWL.Class) in g


def test_r2rml_and_ntriples(museum):
    ds, conn, ev, constraints = museum
    plan = build_plan(ev, constraints, base="http://example.org/m")
    m = _graph(to_r2rml(plan))
    maps = set(m.subjects(RDF.type, RR.TriplesMap))
    assert len(maps) == 6  # five classes + the loan bridge
    assert len(list(m.objects(None, RR.parentTriplesMap))) == 4  # four FKs between classes
    data = _graph(to_ntriples(plan, conn), "nt")
    objects = list(data.subjects(RDF.type, rdflib.URIRef("http://example.org/m/Object")))
    assert len(objects) == len(ds.table("object").rows)
    loans = list(data.triples((None, rdflib.URIRef("http://example.org/m/loan"), None)))
    assert len(loans) == len(ds.table("loan").rows)
    # every FK object link points at an existing subject of the parent class
    owners = set(data.subjects(RDF.type, rdflib.URIRef("http://example.org/m/Owner")))
    links = list(data.objects(None, rdflib.URIRef("http://example.org/m/provenance_event_owner")))
    assert links and set(links) <= owners


def test_cli_export_owl(museum, tmp_path):
    _, _, ev, constraints = museum
    ev.save(tmp_path / "ev.json")
    (tmp_path / "c.json").write_text(json.dumps([c.model_dump() for c in constraints]))
    ds = load_dataset("museum", seed=3, scale=0.3)
    with SQLiteConnector(tmp_path / "m.sqlite") as conn:
        materialize(ds, conn)
    out = tmp_path / "onto"
    assert (
        main(
            [
                "export",
                "owl",
                "--constraints",
                str(tmp_path / "c.json"),
                "--evidence",
                str(tmp_path / "ev.json"),
                "--data",
                "--sqlite",
                str(tmp_path / "m.sqlite"),
                "-o",
                str(out),
            ]
        )
        == 0
    )
    for name in ("ontology.ttl", "mapping.r2rml.ttl", "data.nt"):
        assert (out / name).stat().st_size > 0
    _graph((out / "ontology.ttl").read_text())
