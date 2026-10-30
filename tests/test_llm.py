import json

import httpx
import pytest

from srev.extract import extract
from srev.llm import (
    ChatClient,
    HeuristicProposer,
    HybridRanker,
    LLMConfig,
    LLMError,
    LLMProposer,
    PromptConfig,
    build_prompt,
    parse_proposals,
    parse_ranking,
)
from srev.llm.client import LLMRequestRejected
from srev.model import Proposal
from srev.qlog import mine_joins
from srev.truth import gold_from_dataset

from .fake_vllm import FakeVLLM

GOOD = {
    "proposals": [
        {
            "type": "FK",
            "lhs": ["takes.student_id"],
            "rhs": ["student.student_id"],
            "confidence": 0.9,
            "rationale": "name",
        },
        {
            "type": "UCC",
            "lhs": ["student.student_id"],
            "rhs": [],
            "confidence": 0.95,
            "rationale": "id",
        },
        {
            "type": "UCC",
            "lhs": ["student.ghost_col"],
            "rhs": [],
            "confidence": 0.3,
            "rationale": "hallucinated",
        },
        {
            "type": "FD",
            "lhs": ["student.zip_code"],
            "rhs": ["other.city"],
            "confidence": 0.5,
            "rationale": "cross-table FD is invalid",
        },
        "not an object",
    ]
}


@pytest.fixture
def evidence(university_db):
    return extract(university_db, "university", rows_per_table=5)


def test_parse_proposals_counts_failures():
    props, stats = parse_proposals(json.dumps(GOOD))
    assert len(props) == 3 and all(p.source == "llm" for p in props)
    assert (stats.responses, stats.items, stats.item_failures) == (1, 5, 2)
    assert stats.item_failure_rate == 0.4
    fenced, stats = parse_proposals("```json\n" + json.dumps(GOOD) + "\n```")
    assert len(fenced) == 3 and stats.fence_stripped == 1
    _, stats = parse_proposals("Sure! Here are the constraints: {")
    assert stats.response_failures == 1 and stats.response_failure_rate == 1.0
    _, stats = parse_proposals('{"answer": []}')
    assert stats.response_failures == 1


def test_parse_ranking():
    scores, stats = parse_ranking(
        '{"ranking": [{"id": 0, "confidence": 0.7}, {"id": 9, "confidence": 1}, {"id": 1}]}', 3
    )
    assert scores == {0: 0.7} and stats.item_failures == 2


def test_prompt_channels_hash_and_budget(evidence, university):
    joins = mine_joins(university.queries, university.schema_map())
    full = build_prompt(evidence, joins, PromptConfig())
    names = build_prompt(evidence, joins, PromptConfig(channels=["names"]))
    assert (
        "sample rows" in full.user and "JOIN PREDICATES" in full.user and "distinct=" in full.user
    )
    assert "sample rows" not in names.user and "distinct=" not in names.user
    assert names.channels == ["names"] and full.channels == ["names", "stats", "samples", "logs"]
    assert build_prompt(evidence, joins, PromptConfig()).sha256 == full.sha256
    para = build_prompt(evidence, joins, PromptConfig(paraphrase=1))
    assert para.sha256 != full.sha256 and para.paraphrase == 1
    small = build_prompt(evidence, joins, PromptConfig(token_budget=1500))
    assert small.trimmed and small.est_tokens < full.est_tokens
    with pytest.raises(ValueError):
        build_prompt(evidence, joins, PromptConfig(channels=["names", "vibes"]))


def _transport(content, calls):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        if len(calls) == 1 and content == "retry":
            return httpx.Response(500, text="boom")
        body = content if content != "retry" else json.dumps(GOOD)
        return httpx.Response(
            200,
            json={
                "model": "m",
                "choices": [{"message": {"content": body}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )

    return httpx.MockTransport(handler)


def test_client_payload_cache_and_offline(evidence, tmp_path):
    calls: list = []
    cfg = LLMConfig(model="m", revision="abc", seed=7, cache_dir=str(tmp_path))
    client = ChatClient(cfg, transport=_transport(json.dumps(GOOD), calls))
    prompt = build_prompt(evidence)
    first = client.complete(prompt, {"type": "object"})
    body = calls[0]
    assert body["seed"] == 7 and body["temperature"] == 0.0
    assert body["response_format"]["type"] == "json_schema"
    assert body["messages"][0]["role"] == "system"
    assert not first.cached and first.prompt_tokens == 10
    second = client.complete(prompt, {"type": "object"})
    assert second.cached and len(calls) == 1 and second.content == first.content
    offline = ChatClient(cfg.model_copy(update={"offline": True, "cache_dir": str(tmp_path / "x")}))
    with pytest.raises(LLMError):
        offline.complete(prompt)


def test_client_retries_server_errors(evidence, monkeypatch):
    monkeypatch.setattr("srev.llm.client.time.sleep", lambda s: None)
    calls: list = []
    client = ChatClient(LLMConfig(retries=2), transport=_transport("retry", calls))
    assert client.complete(build_prompt(evidence)).content
    assert len(calls) == 2


def test_client_does_not_retry_bad_requests(evidence):
    calls: list = []

    def handler(request):
        calls.append(1)
        return httpx.Response(400, text="unknown model")

    client = ChatClient(LLMConfig(retries=3), transport=httpx.MockTransport(handler))
    with pytest.raises(LLMRequestRejected):
        client.complete(build_prompt(evidence))
    assert len(calls) == 1


def test_llm_proposer_against_fake_server(evidence, tmp_path):
    with FakeVLLM(GOOD) as server:
        client = ChatClient(LLMConfig(base_url=server.base_url, model="fake/model-7b"))
        out = LLMProposer(client, PromptConfig(channels=["names", "stats"])).propose(evidence, None)
        assert client.server_models() == ["fake/model-7b"]
        client.close()
    assert len(out.proposals) == 3 and out.tokens == 120
    assert out.parse.item_failures == 2
    assert out.prompts[0].sha256 == out.completions[0].prompt_sha256


def test_heuristic_proposer(evidence, university, university_gold):
    joins = mine_joins(university.queries, university.schema_map())
    out = HeuristicProposer().propose(evidence, joins)
    gold = {g.key() for g in university_gold.labels}
    fks = [p for p in out.proposals if p.type == "FK"]
    assert fks and sum(p.key() in gold for p in fks) / len(fks) > 0.8
    assert any(p.type == "FD" and p.key() in gold for p in out.proposals)
    names_only = HeuristicProposer(["names"]).propose(evidence, None)
    assert names_only.proposals and all(p.source == "heuristic" for p in names_only.proposals)


def test_hybrid_ranker(evidence):
    cands = [
        Proposal(
            type="FK",
            lhs=["takes.student_id"],
            rhs=["student.student_id"],
            confidence=0.9,
            source="baseline",
        ),
        Proposal(
            type="FK",
            lhs=["student.tot_cred"],
            rhs=["department.budget"],
            confidence=0.2,
            source="baseline",
        ),
        Proposal(
            type="FD",
            lhs=["student.student_id"],
            rhs=["student.name"],
            confidence=0.0,
            source="baseline",
        ),
    ]
    ranker = HybridRanker(threshold=0.6)
    assert len(ranker.shortlist(cands)) == 2  # key-implied FD dropped
    out = ranker.rank(evidence, None, cands)
    assert [p.lhs for p in out.proposals] == [["takes.student_id"]]
    assert out.proposals[0].source == "hybrid"
    with pytest.raises(ValueError):
        HybridRanker(scorer="llm")
    with FakeVLLM(GOOD) as server:
        client = ChatClient(LLMConfig(base_url=server.base_url))
        out = HybridRanker("llm", client, threshold=0.5).rank(evidence, None, cands)
        client.close()
    assert len(out.proposals) == 1 and out.proposals[0].confidence == 0.9
    assert "CANDIDATES" in out.prompts[0].user


def test_gold_fixture_sanity(university):
    assert gold_from_dataset(university).counts()["FD"] == 4
