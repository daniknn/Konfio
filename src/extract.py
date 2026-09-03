"""Phase 1 — merchant extraction.

Runs the giro x plaza search plan, discards candidates that can never convert
before spending a Place Details call on them, and writes `data/raw_leads.json`.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from itertools import zip_longest
from typing import Any

from ulid import ULID

from .config import RAW_LEADS_PATH, SearchTask, Settings, is_chain, search_plan
from .models import PaymentSignal, RawPlace
from .places import PlacesClient, PlacesError, signal_from_payment_options, to_raw_place

logger = logging.getLogger(__name__)

# Screen slightly past what is missing to absorb the few prospects the LLM still
# rejects on an out-of-scope MCC or a compliance violation. Now that qualification
# rests on Google's structured field rather than the model's reading, that leak is
# small: 226 of 232 cash-only merchants with a phone survived the last run.
OVERSHOOT = 1.10

# Below this, Google returns two or three reviews and none of them mention how
# the customer paid — not enough for a quote worth the priciest SKU we have.
MIN_REVIEWS_TO_QUOTE = 30


def new_trace_id() -> str:
    return f"trc_{ULID()}"


def _is_viable_candidate(place: dict[str, Any]) -> bool:
    """Via-negativa filter, applied on the cheap Text Search payload."""
    if place.get("businessStatus") != "OPERATIONAL":
        return False
    name = (place.get("displayName") or {}).get("text", "")
    return bool(name) and not is_chain(name)


def _interleave(buckets: list[list[Any]]) -> Iterator[Any]:
    """Round-robin across queries so the quota is not consumed by the first giro."""
    for row in zip_longest(*buckets):
        for item in row:
            if item is not None:
                yield item


def collect_candidates(
    client: PlacesClient, tasks: list[SearchTask]
) -> list[tuple[dict[str, Any], SearchTask]]:
    """Search every task, drop non-viable candidates, dedupe by place_id."""
    buckets: list[list[tuple[dict[str, Any], SearchTask]]] = []

    for task in tasks:
        try:
            found = client.search(task.query)
        except PlacesError:
            # One dead query must not abort a run that has already paid for others.
            logger.exception("Búsqueda fallida, continuando: %s", task.query)
            continue
        viable = [(p, task) for p in found if _is_viable_candidate(p)]
        logger.info("%s → %d candidatos viables de %d", task.query, len(viable), len(found))
        buckets.append(viable)

    seen: set[str] = set()
    candidates: list[tuple[dict[str, Any], SearchTask]] = []
    for place, task in _interleave(buckets):
        place_id = place.get("id")
        if not place_id or place_id in seen:
            continue
        seen.add(place_id)
        candidates.append((place, task))

    return candidates


def _is_prospect(detail: dict[str, Any]) -> bool:
    """Could this merchant buy a terminal from us?

    Both halves are decided on the cheap screen, and both were set by
    measurement rather than intuition:

    - No published phone, no channel. Among cash-only merchants only 46% list
      one, so this is the single largest source of loss in the funnel.
    - The payment field has to say cash-only. Merchants Google already reports
      as card-accepting are not the segment: across 458 of them, just 1% had a
      review complaining about surcharges, minimums or a broken terminal.
      Merchants Google is *silent* about are excluded too, which is the less
      obvious call — 331 of them cost a reviews call each and converted at
      1.5%, because a silent field usually means a quiet listing, not a cash
      register.
    """
    if not detail.get("nationalPhoneNumber"):
        return False
    structured = signal_from_payment_options(detail.get("paymentOptions") or {})
    return structured is PaymentSignal.CONFIRMED_NO_CARD


def fetch_details(
    client: PlacesClient,
    candidates: list[tuple[dict[str, Any], SearchTask]],
    max_details: int,
    trace_id: str,
    target: int | None = None,
    review_budget: int = 0,
) -> list[RawPlace]:
    """Screen every candidate at the Enterprise SKU, buy reviews only where they pay.

    `max_details` is the safety ceiling that keeps a runaway run from spending
    real money. `target` is the one that normally fires: once enough merchants
    have survived the screen there is nothing left to buy, so the run stops on
    the result it wanted rather than on the budget it was allowed.

    Reviews are never what qualifies a merchant — Google's structured field is —
    so they are bought only while `review_budget` lasts, and only for merchants
    with enough of them for a quote to exist. They buy message quality, nothing
    else, which is why they are the first thing cut when the allowance runs out.
    """
    places: list[RawPlace] = []
    prospects = 0
    budget = review_budget

    for place, task in candidates[:max_details]:
        if target is not None and prospects >= target:
            logger.info("Objetivo de %d prospectos alcanzado; se detiene el screening", target)
            break

        try:
            detail = client.screen(place["id"])
        except PlacesError:
            logger.exception("Screening fallido para %s", place["id"])
            continue

        if _is_prospect(detail):
            prospects += 1
            quotable = (detail.get("userRatingCount") or 0) >= MIN_REVIEWS_TO_QUOTE
            if budget > 0 and quotable:
                budget -= 1
                try:
                    detail["reviews"] = client.reviews(place["id"])
                except PlacesError:
                    # Reviews are evidence, not identity: keep the merchant without them.
                    logger.exception("Reseñas fallidas para %s", place["id"])

        places.append(
            to_raw_place(
                detail,
                query=task.query,
                plaza=task.plaza,
                trace_id=trace_id,
                fetched_at=datetime.now(UTC).isoformat(),
            )
        )

    logger.info(
        "%d prospectos de %d comercios · %d de %d reseñas opcionales compradas",
        prospects,
        len(places),
        review_budget - budget,
        review_budget,
    )
    return places


def read_raw_leads() -> list[dict[str, Any]]:
    if not RAW_LEADS_PATH.exists():
        return []
    return json.loads(RAW_LEADS_PATH.read_text(encoding="utf-8"))


def write_raw_leads(places: list[RawPlace], existing: list[dict[str, Any]] | None = None) -> None:
    by_id = {p["place_id"]: p for p in existing or []}
    by_id.update({p.place_id: p.model_dump() for p in places})
    RAW_LEADS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RAW_LEADS_PATH.write_text(
        json.dumps(list(by_id.values()), ensure_ascii=False, indent=2), encoding="utf-8"
    )


def run_extraction(
    settings: Settings,
    trace_id: str,
    *,
    fresh: bool = False,
    already_qualified: int = 0,
) -> list[RawPlace]:
    """Extract the merchants still missing to reach TARGET_LEADS.

    A merchant already on disk is never re-screened. Lead generation is a
    standing job, not a one-shot script: the second week should cost what the
    new merchants cost, not what the whole list costs.
    """
    existing = [] if fresh else read_raw_leads()
    known = {p["place_id"] for p in existing}
    missing = max(settings.target_leads - already_qualified, 0)
    if not missing:
        logger.info("Ya hay %d leads calificados; no hace falta extraer", already_qualified)
        return []

    client = PlacesClient(settings.google_maps_api_key)
    try:
        candidates = [
            c for c in collect_candidates(client, search_plan()) if c[0]["id"] not in known
        ]
        logger.info(
            "%d candidatos nuevos (%d ya conocidos); faltan %d leads; tope de screenings: %d",
            len(candidates),
            len(known),
            missing,
            settings.max_place_details,
        )
        # A few prospects still fall out at the LLM stage on an out-of-scope MCC
        # or a compliance violation, so aim slightly past what is missing.
        places = fetch_details(
            client,
            candidates,
            settings.max_place_details,
            trace_id,
            target=int(missing * OVERSHOOT),
            review_budget=settings.review_budget,
        )
    finally:
        client.close()

    write_raw_leads(places, existing)
    logger.info(
        "Extracción lista: %d comercios · %d búsquedas · %d screenings · %d reseñas → %s",
        len(places),
        client.search_calls,
        client.screen_calls,
        client.review_calls,
        RAW_LEADS_PATH,
    )
    return places
