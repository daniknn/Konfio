"""MCC catalog: Konfío's merchant-category taxonomy.

`data/mcc_catalog.csv` holds 806 ISO 18245 codes mapped to Konfío's FAMILIA
grouping. Handing the in-scope subset to the LLM turns classification into a
closed-vocabulary task that can be validated against the catalog afterwards.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import cache

from .config import FAMILIAS_EN_SCOPE, MCC_CATALOG_PATH


@dataclass(frozen=True)
class MccEntry:
    mcc: str
    familia: str
    descripcion: str


@cache
def load_catalog() -> tuple[MccEntry, ...]:
    with MCC_CATALOG_PATH.open(encoding="utf-8") as f:
        return tuple(
            MccEntry(
                mcc=row["mcc"].strip(),
                familia=row["familia"].strip(),
                descripcion=row["descripcion"].strip(),
            )
            for row in csv.DictReader(f)
        )


@cache
def in_scope_catalog() -> tuple[MccEntry, ...]:
    return tuple(e for e in load_catalog() if e.familia in FAMILIAS_EN_SCOPE)


@cache
def _by_code() -> dict[str, MccEntry]:
    return {e.mcc: e for e in load_catalog()}


def lookup(mcc: str) -> MccEntry | None:
    return _by_code().get(mcc.strip().zfill(4))


def is_in_scope(mcc: str) -> bool:
    entry = lookup(mcc)
    return entry is not None and entry.familia in FAMILIAS_EN_SCOPE


def catalog_prompt_block(max_chars: int = 160) -> str:
    """Compact catalog rendering for the LLM prompt: one line per code."""
    return "\n".join(
        f"{e.mcc} | {e.familia} | {e.descripcion[:max_chars]}" for e in in_scope_catalog()
    )
