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


def _deserves_reviews(detail: dict[str, Any]) -> bool:
    """Is this merchant worth an Atmosphere-priced call?

    Two ways to be worthless: no phone means no channel to reach them, and a
    structured field that already confirms card acceptance means they are not the
    segment this pipeline sells to. Measured on 458 card-accepting merchants,
    only 1% of their reviews mentioned any pain worth displacing a terminal over,
    so paying the top SKU to read the other 99% is not worth it.
    """
    if not detail.get("nationalPhoneNumber"):
        return False
    structured = signal_from_payment_options(detail.get("paymentOptions") or {})
    return structured is not PaymentSignal.COMPETITOR_TERMINAL


def fetch_details(
    client: PlacesClient,
    candidates: list[tuple[dict[str, Any], SearchTask]],
    max_details: int,
    trace_id: str,
) -> list[RawPlace]:
    """Screen every candidate at the Enterprise SKU, buy reviews only for survivors."""
    places: list[RawPlace] = []
    skipped = 0

    for place, task in candidates[:max_details]:
        try:
            detail = client.screen(place["id"])
        except PlacesError:
            logger.exception("Screening fallido para %s", place["id"])
            continue

        if _deserves_reviews(detail):
            try:
                detail["reviews"] = client.reviews(place["id"])
            except PlacesError:
                # Reviews are evidence, not identity: keep the merchant without them.
                logger.exception("Reseñas fallidas para %s", place["id"])
        else:
            skipped += 1

        places.append(
            to_raw_place(
                detail,
                query=task.query,
                plaza=task.plaza,
                trace_id=trace_id,
                fetched_at=datetime.now(UTC).isoformat(),
            )
        )

    logger.info("Reseñas omitidas en %d comercios sin teléfono o con tarjeta confirmada", skipped)
    return places


def write_raw_leads(places: list[RawPlace]) -> None:
    RAW_LEADS_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = [p.model_dump() for p in places]
    RAW_LEADS_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def run_extraction(settings: Settings, trace_id: str) -> list[RawPlace]:
    client = PlacesClient(settings.google_maps_api_key)
    try:
        candidates = collect_candidates(client, search_plan())
        logger.info(
            "%d candidatos únicos; tope de detalles: %d",
            len(candidates),
            settings.max_place_details,
        )
        places = fetch_details(client, candidates, settings.max_place_details, trace_id)
    finally:
        client.close()

    write_raw_leads(places)
    logger.info(
        "Extracción lista: %d comercios · %d búsquedas · %d screenings · %d reseñas → %s",
        len(places),
        client.search_calls,
        client.screen_calls,
        client.review_calls,
        RAW_LEADS_PATH,
    )
    return places
