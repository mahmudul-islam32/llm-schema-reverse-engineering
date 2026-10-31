"""Benchmark datasets: built-in seeded generators plus loading from PostgreSQL."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from srev.datasets import airline, retail, tpch, university
from srev.datasets.base import (
    ColumnDef,
    CuratedFD,
    CuratedFK,
    Dataset,
    ForeignKeyDef,
    TableDef,
)

Generator = Callable[..., Dataset]

REGISTRY: dict[str, tuple[Generator, str]] = {
    "tpch_mini": (tpch.generate, tpch.__doc__ or ""),
    "university": (university.generate, university.__doc__ or ""),
    "retail_legacy": (retail.generate, retail.__doc__ or ""),
    "airline": (airline.generate, airline.__doc__ or ""),
}
ALIASES = {"tpch": "tpch_mini", "retail": "retail_legacy"}


def available() -> list[str]:
    return sorted(REGISTRY)


def load_dataset(name: str, seed: int = 42, **params: Any) -> Dataset:
    name = ALIASES.get(name, name)
    if name not in REGISTRY:
        raise KeyError(f"unknown dataset {name!r}; available: {', '.join(available())}")
    return REGISTRY[name][0](seed=seed, **params)


__all__ = [
    "REGISTRY",
    "ColumnDef",
    "CuratedFD",
    "CuratedFK",
    "Dataset",
    "ForeignKeyDef",
    "TableDef",
    "available",
    "load_dataset",
]
