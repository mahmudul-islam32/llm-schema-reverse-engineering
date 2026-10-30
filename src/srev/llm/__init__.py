"""LLM layer: prompt builder, OpenAI-compatible client, output parsing, proposers."""

from srev.llm.client import ChatClient, Completion, LLMConfig, LLMError
from srev.llm.output import ParseStats, parse_proposals, parse_ranking
from srev.llm.prompt import Prompt, PromptConfig, build_prompt, build_ranking_prompt
from srev.llm.proposers import (
    HeuristicProposer,
    HybridRanker,
    LLMProposer,
    ProposerOutput,
)

__all__ = [
    "ChatClient",
    "Completion",
    "HeuristicProposer",
    "HybridRanker",
    "LLMConfig",
    "LLMError",
    "LLMProposer",
    "ParseStats",
    "Prompt",
    "PromptConfig",
    "ProposerOutput",
    "build_prompt",
    "build_ranking_prompt",
    "parse_proposals",
    "parse_ranking",
]
