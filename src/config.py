"""Environment and search-plan configuration.

Credentials come from the environment only. The pipeline refuses to start when a
required key is missing rather than failing halfway through a paid API run.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
MCC_CATALOG_PATH = DATA_DIR / "mcc_catalog.csv"
RAW_LEADS_PATH = DATA_DIR / "raw_leads.json"
PROCESSED_LEADS_PATH = DATA_DIR / "processed_leads.csv"


class MissingCredentialError(RuntimeError):
    pass


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise MissingCredentialError(
            f"Falta la variable de entorno {name}. "
            f"Copia .env.example a .env y llénala antes de correr el pipeline."
        )
    return value


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw else default


@dataclass(frozen=True)
class Settings:
    google_maps_api_key: str
    gemini_api_key: str
    gemini_model: str
    max_place_details: int
    target_leads: int

    @classmethod
    def load(cls) -> Settings:
        return cls(
            google_maps_api_key=_required("GOOGLE_MAPS_API_KEY"),
            gemini_api_key=_required("GEMINI_API_KEY"),
            gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.0-flash"),
            # Ceiling exists so a runaway run cannot spill past the Places free tier.
            max_place_details=_int_env("MAX_PLACE_DETAILS", 600),
            target_leads=_int_env("TARGET_LEADS", 500),
        )


# FAMILIA values from the MCC catalog that are in scope for an SME card terminal.
# The catalog's hotel and airline blocks are excluded: ISO 18245 assigns those
# hundreds of brand-specific codes, and none of them describe a Mexican SME.
FAMILIAS_EN_SCOPE = frozenset(
    {
        "Restaurantes",
        "Comida Rápida",
        "Ventas al detalle (Retail)",
        "Misceláneos",
        "Supermercados",
        "Refacciones y ferretería",
        "Salones de belleza",
        "Farmacias",
        "Médicos y dentistas",
        "Estacionamientos",
    }
)

# Text Search queries. Each yields up to 20 places per call, so this plan is sized
# to reach TARGET_LEADS with room to spare after disqualification.
PLAZAS = ("Ciudad de México", "Guadalajara", "Monterrey")

GIRO_QUERIES = (
    "abarrotes",
    "barbería",
    "cafetería",
    "carnicería",
    "consultorio dental",
    "estética y salón de belleza",
    "farmacia independiente",
    "ferretería",
    "fonda económica",
    "juguería",
    "lavandería",
    "mercería",
    "papelería",
    "refaccionaria automotriz",
    "taquería",
    "tienda de ropa",
    "veterinaria",
)


# National chains buy card acquiring centrally, so a branch manager cannot say yes.
# Matched as substrings against the lowercased place name.
CHAIN_BRANDS = frozenset(
    {
        "7-eleven",
        "autozone",
        "bodega aurrera",
        "burger king",
        "chedraui",
        "cinépolis",
        "circle k",
        "coppel",
        "domino",
        "elektra",
        "farmacia benavides",
        "farmacias del ahorro",
        "farmacias guadalajara",
        "farmacias similares",
        "home depot",
        "kfc",
        "little caesars",
        "mcdonald",
        "oxxo",
        "sanborns",
        "soriana",
        "starbucks",
        "subway",
        "superama",
        "telcel",
        "walmart",
    }
)


@dataclass(frozen=True)
class SearchTask:
    query: str
    giro: str
    plaza: str


def search_plan() -> list[SearchTask]:
    """Cartesian product of giro x plaza, e.g. 'taquería en Monterrey'."""
    return [
        SearchTask(query=f"{giro} en {plaza}", giro=giro, plaza=plaza)
        for plaza in PLAZAS
        for giro in GIRO_QUERIES
    ]


def is_chain(name: str) -> bool:
    lowered = name.lower()
    return any(brand in lowered for brand in CHAIN_BRANDS)
