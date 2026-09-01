"""Google Places API (New) client.

Two calls per merchant path: Text Search returns candidate IDs at the Pro SKU
tier, then Place Details pulls the Enterprise + Atmosphere fields we actually
need — `paymentOptions` and `reviews`. Splitting them keeps the cheap call cheap
and lets the caller drop candidates (chains, closed businesses) before spending
a Details call on them.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any

import httpx

from .models import RawPlace, Review

logger = logging.getLogger(__name__)

BASE_URL = "https://places.googleapis.com/v1"

# Pro tier: enough to decide whether a candidate deserves a Details call.
SEARCH_FIELD_MASK = (
    "places.id,places.displayName,places.formattedAddress,"
    "places.primaryType,places.types,places.businessStatus,nextPageToken"
)

# Enterprise + Atmosphere: paymentOptions is the whole point of the pipeline.
DETAILS_FIELD_MASK = (
    "id,displayName,formattedAddress,primaryType,types,businessStatus,"
    "nationalPhoneNumber,websiteUri,rating,userRatingCount,priceLevel,"
    "paymentOptions,reviews"
)

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
MAX_ATTEMPTS = 4
MAX_REVIEWS = 5


class PlacesError(RuntimeError):
    pass


class PlacesClient:
    def __init__(
        self,
        api_key: str,
        client: httpx.Client | None = None,
        backoff_base: float = 1.0,
    ) -> None:
        self._api_key = api_key
        self._client = client or httpx.Client(timeout=30.0)
        self._backoff_base = backoff_base
        self.details_calls = 0
        self.search_calls = 0

    def _headers(self, field_mask: str) -> dict[str, str]:
        return {
            "X-Goog-Api-Key": self._api_key,
            "X-Goog-FieldMask": field_mask,
            "Content-Type": "application/json",
        }

    def _request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        """Single request with exponential backoff on transient failures.

        A 403 means the key is wrong or the API is not enabled — retrying that
        just burns time, so it fails loudly on the first attempt.
        """
        last_error: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                response = self._client.request(method, url, **kwargs)
            except httpx.TransportError as exc:
                last_error = exc
            else:
                if response.status_code == 200:
                    return response.json()
                if response.status_code in (401, 403):
                    raise PlacesError(
                        "Google rechazó la credencial (HTTP "
                        f"{response.status_code}). Revisa que GOOGLE_MAPS_API_KEY sea "
                        "correcta y que 'Places API (New)' esté habilitada en el proyecto."
                    )
                if response.status_code not in RETRYABLE_STATUS:
                    raise PlacesError(
                        f"Places respondió HTTP {response.status_code}: {response.text[:300]}"
                    )
                last_error = PlacesError(f"HTTP {response.status_code}")

            if attempt < MAX_ATTEMPTS - 1:
                delay = self._backoff_base * (2**attempt + random.uniform(0, 0.3))
                logger.warning("Reintentando %s en %.1fs (%s)", url, delay, last_error)
                time.sleep(delay)

        raise PlacesError(f"Places falló tras {MAX_ATTEMPTS} intentos: {last_error}")

    def search(self, query: str, max_pages: int = 2) -> list[dict[str, Any]]:
        """Text Search, following nextPageToken up to max_pages (20 results each)."""
        results: list[dict[str, Any]] = []
        page_token: str | None = None

        for _ in range(max_pages):
            body: dict[str, Any] = {
                "textQuery": query,
                "languageCode": "es",
                "regionCode": "MX",
                "pageSize": 20,
            }
            if page_token:
                body["pageToken"] = page_token

            payload = self._request(
                "POST",
                f"{BASE_URL}/places:searchText",
                headers=self._headers(SEARCH_FIELD_MASK),
                json=body,
            )
            self.search_calls += 1
            results.extend(payload.get("places", []))

            page_token = payload.get("nextPageToken")
            if not page_token:
                break

        return results

    def details(self, place_id: str) -> dict[str, Any]:
        payload = self._request(
            "GET",
            f"{BASE_URL}/places/{place_id}",
            headers=self._headers(DETAILS_FIELD_MASK),
        )
        self.details_calls += 1
        return payload

    def close(self) -> None:
        self._client.close()


def _payment_options(raw: dict[str, Any]) -> dict[str, bool]:
    options = raw.get("paymentOptions") or {}
    return {k: v for k, v in options.items() if isinstance(v, bool)}


def _reviews(raw: dict[str, Any]) -> list[Review]:
    """Prefer originalText: we quote the merchant's customers verbatim, in Spanish."""
    reviews = []
    for item in (raw.get("reviews") or [])[:MAX_REVIEWS]:
        text = (item.get("originalText") or item.get("text") or {}).get("text", "").strip()
        if not text:
            continue
        reviews.append(
            Review(
                text=text,
                rating=item.get("rating"),
                publish_time=item.get("publishTime"),
            )
        )
    return reviews


def to_raw_place(
    raw: dict[str, Any],
    *,
    query: str,
    plaza: str,
    trace_id: str,
    fetched_at: str,
) -> RawPlace:
    return RawPlace(
        place_id=raw["id"],
        name=(raw.get("displayName") or {}).get("text", ""),
        primary_type=raw.get("primaryType"),
        types=raw.get("types", []),
        address=raw.get("formattedAddress"),
        plaza=plaza,
        phone=raw.get("nationalPhoneNumber"),
        website=raw.get("websiteUri"),
        rating=raw.get("rating"),
        user_rating_count=raw.get("userRatingCount"),
        price_level=raw.get("priceLevel"),
        business_status=raw.get("businessStatus"),
        payment_options=_payment_options(raw),
        reviews=_reviews(raw),
        query=query,
        fetched_at=fetched_at,
        trace_id=trace_id,
    )
