"""Evaluation: metrics, bootstrap CIs, hallucination taxonomy."""

from srev.eval.hallucination import CATEGORIES, HallucinationReport, analyse, cohen_kappa
from srev.eval.metrics import (
    Aggregate,
    ScoreCard,
    TypeScore,
    aggregate,
    bootstrap_ci,
    paired_permutation_test,
    score,
)

__all__ = [
    "CATEGORIES",
    "Aggregate",
    "HallucinationReport",
    "ScoreCard",
    "TypeScore",
    "aggregate",
    "analyse",
    "bootstrap_ci",
    "cohen_kappa",
    "paired_permutation_test",
    "score",
]
