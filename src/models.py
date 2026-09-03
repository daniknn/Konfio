"""Data contracts for the pipeline.

`LeadClassification` doubles as the JSON schema handed to Gemini, so the model
cannot return a shape the pipeline is unable to parse.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.0"


class PaymentSignal(StrEnum):
    """How strong the evidence is that this merchant needs a card terminal."""

    # Google's structured paymentOptions field says cash only / no cards.
    CONFIRMED_NO_CARD = "confirmed_no_card"
    # No structured field, but reviews describe a cash-only experience.
    INFERRED_NO_CARD = "inferred_no_card"
    # Already takes cards through a competitor — displacement play.
    COMPETITOR_TERMINAL = "competitor_terminal"
    # Takes cards, no visible pain.
    ACCEPTS_CARD = "accepts_card"
    UNKNOWN = "unknown"


class Review(BaseModel):
    text: str
    rating: int | None = None
    publish_time: str | None = None
    language: str | None = None


class RawPlace(BaseModel):
    """A merchant as returned by Places API, before any LLM involvement."""

    place_id: str
    name: str
    primary_type: str | None = None
    types: list[str] = Field(default_factory=list)
    address: str | None = None
    plaza: str | None = None
    phone: str | None = None
    website: str | None = None
    rating: float | None = None
    user_rating_count: int | None = None
    price_level: str | None = None
    business_status: str | None = None
    payment_options: dict[str, bool] = Field(default_factory=dict)
    reviews: list[Review] = Field(default_factory=list)
    query: str | None = None
    fetched_at: str | None = None
    trace_id: str | None = None
    schema_version: str = SCHEMA_VERSION


class LeadClassification(BaseModel):
    """Structured output contract for the Gemini enrichment call."""

    mcc: str = Field(description="Código MCC de 4 dígitos tomado del catálogo proporcionado.")
    familia: str = Field(description="FAMILIA correspondiente al MCC en el catálogo.")
    payment_signal: PaymentSignal = Field(
        description="Nivel de evidencia sobre si el comercio necesita terminal."
    )
    signal_evidence: str = Field(
        description=(
            "Cita textual de la reseña o del campo de medios de pago que sustenta "
            "payment_signal. Cadena vacía si no hay evidencia."
        )
    )
    intent_score: int = Field(
        ge=0, le=100, description="Probabilidad de que este comercio adopte una terminal hoy."
    )
    eligible: bool = Field(description="Si califica como lead de TPV para Konfío.")
    disqualifier: str = Field(description="Motivo del descarte, o cadena vacía si es elegible.")
    outreach_message: str = Field(
        description=(
            "Mensaje de WhatsApp en español mexicano, máximo 50 palabras, tuteando, "
            "que referencie la evidencia concreta encontrada. Sin emojis."
        )
    )
    rationale: str = Field(description="Una frase explicando la clasificación.")


class ClassifiedLead(LeadClassification):
    """A classification carrying the merchant it belongs to.

    Batched calls send several merchants at once, so the model must echo the
    place_id back — matching replies to merchants by array position silently
    corrupts the whole batch the first time the model drops an item.
    """

    place_id: str = Field(description="El place_id exacto del comercio, copiado tal cual.")


class ProcessedLead(BaseModel):
    """A raw place joined with its classification — one row of processed_leads.csv."""

    place: RawPlace
    classification: LeadClassification
    trace_id: str
    schema_version: str = SCHEMA_VERSION
