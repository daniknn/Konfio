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
    review_budget: int
    gemini_batch_size: int

    @classmethod
    def load(cls) -> Settings:
        return cls(
            google_maps_api_key=_required("GOOGLE_MAPS_API_KEY"),
            gemini_api_key=_required("GEMINI_API_KEY"),
            # Floating alias on purpose: pinned Gemini versions get retired, and
            # a stale pin makes a fresh clone fail with a 404 instead of running.
            gemini_model=os.getenv("GEMINI_MODEL", "gemini-flash-latest"),
            # Safety net against a runaway loop, not the normal stop condition —
            # the run ends on TARGET_LEADS. Sized so the target can actually fire:
            # at the measured 4.7 screenings per qualified lead, 500 leads needs
            # ~2,350. A lower ceiling silently becomes the binding constraint and
            # the run reports success on a short list.
            max_place_details=_int_env("MAX_PLACE_DETAILS", 2500),
            target_leads=_int_env("TARGET_LEADS", 500),
            # Reviews are the Enterprise + Atmosphere SKU, the priciest in the
            # catalog, and it carries its own free monthly cap of 1,000 calls.
            # They never qualify a merchant — Google's structured paymentOptions
            # field does — so they only buy a sharper quote for the message, and
            # spend from this budget only while it lasts.
            review_budget=_int_env("REVIEW_BUDGET", 1000),
            # Batching amortizes the ~3K-token catalog prefix across merchants.
            gemini_batch_size=_int_env("GEMINI_BATCH_SIZE", 10),
        )


# Konfío is a SOFOM ENR with a pending CNBV banking licence. Outbound copy that
# implies deposit-taking creates regulatory exposure, so generated messages
# containing any of these are rejected rather than edited.
PROHIBITED_TERMS = (
    "banco",
    "bancaria",
    "cuenta de cheques",
    "depósito",
    "deposito",
    "tesorería",
    "tesoreria",
    "rendimiento",
    "inversión",
    "inversion",
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

# Text Search ranks by prominence, so a query aimed at a metro area returns the
# most established businesses in the most banked districts of the country — which
# are exactly the ones that already own a terminal. Measured over 600 merchants:
# the three metro cores yielded 5% cash-only, while the municipalities below
# yielded 40-80%. Same code and same cost per merchant for ~10x the addressable
# rate, so the targeting hypothesis — not the scraper — is the actual product.
#
# `plaza` stays the metro area for reporting; `zona` is what goes into the query.
ZONAS = (
    ("Ciudad de México", "Iztapalapa, Ciudad de México"),
    ("Ciudad de México", "Gustavo A. Madero, Ciudad de México"),
    ("Ciudad de México", "Tláhuac, Ciudad de México"),
    ("Ciudad de México", "Xochimilco, Ciudad de México"),
    ("Ciudad de México", "Iztacalco, Ciudad de México"),
    ("Ciudad de México", "Nezahualcóyotl, Estado de México"),
    ("Ciudad de México", "Ecatepec, Estado de México"),
    ("Ciudad de México", "Chimalhuacán, Estado de México"),
    ("Ciudad de México", "Chalco, Estado de México"),
    ("Ciudad de México", "Valle de Chalco, Estado de México"),
    ("Ciudad de México", "Tultitlán, Estado de México"),
    ("Guadalajara", "Tonalá, Jalisco"),
    ("Guadalajara", "Tlaquepaque, Jalisco"),
    ("Guadalajara", "El Salto, Jalisco"),
    ("Guadalajara", "Tlajomulco de Zúñiga, Jalisco"),
    ("Monterrey", "Guadalupe, Nuevo León"),
    ("Monterrey", "General Escobedo, Nuevo León"),
    ("Monterrey", "Apodaca, Nuevo León"),
    ("Monterrey", "Juárez, Nuevo León"),
    ("Monterrey", "García, Nuevo León"),
)

# Ranked by qualified leads per screening call, measured over 2,500 merchants —
# not by how cash-heavy the trade feels. The two rankings disagree: recaudería is
# 39% cash-only but converts at 9.4%, because those owners rarely publish a phone,
# and reachability is the larger loss in this funnel.
#
#   fonda económica  29.8%      mercería       10.8%      cremería      1.5%
#   tortillería      27.1%      pollería        9.8%      tlapalería    1.4%
#   taquería         18.9%      recaudería      9.4%      dental        1.4%
#   panadería        18.1%      estética        9.1%      ropa          0.7%
#   miscelánea       14.1%      barbería        5.6%      refaccionaria 0.0%
#                               carnicería      4.9%      farmacia      0.0%
#                               papelería       4.4%
#
# The bottom nine cost 1,244 screening calls and returned 13 leads between them.
# Keeping the top five trades FAMILIA breadth for cost, which is the right trade
# while the segment is being proven and the wrong one once it is.
GIRO_QUERIES = (
    "fonda económica",
    "tortillería",
    "taquería",
    "panadería",
    "miscelánea",
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
    zona: str


def search_plan() -> list[SearchTask]:
    """Cartesian product of giro x zona, e.g. 'tortillería en Iztapalapa, Ciudad de México'."""
    return [
        SearchTask(query=f"{giro} en {zona}", giro=giro, plaza=plaza, zona=zona)
        for plaza, zona in ZONAS
        for giro in GIRO_QUERIES
    ]


def is_chain(name: str) -> bool:
    lowered = name.lower()
    return any(brand in lowered for brand in CHAIN_BRANDS)
