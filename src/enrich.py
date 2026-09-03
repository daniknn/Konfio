"""Phase 2 — classification and message generation.

Merchants go to Gemini in batches with the in-scope MCC catalog as a closed
vocabulary, and come back as `LeadClassification`. Nothing the model returns is
trusted on its own: the catalog corrects the FAMILIA, Google's structured
`paymentOptions` overrides the payment signal, and eligibility is decided in
code. The model's real job is the two things code cannot do — read reviews and
write Spanish.
"""

from __future__ import annotations

import csv
import json
import logging
import time
from typing import Any, Protocol

from google import genai
from google.genai import errors, types

from .config import PROCESSED_LEADS_PATH, PROHIBITED_TERMS, Settings
from .mcc import catalog_prompt_block, in_scope_catalog, is_in_scope, lookup
from .models import ClassifiedLead, LeadClassification, PaymentSignal, ProcessedLead, RawPlace
from .places import signal_from_payment_options

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 4.0
MESSAGE_WORD_LIMIT = 50
QUALIFYING_SIGNALS = frozenset(
    {
        PaymentSignal.CONFIRMED_NO_CARD,
        PaymentSignal.INFERRED_NO_CARD,
        PaymentSignal.COMPETITOR_TERMINAL,
    }
)

SYSTEM_INSTRUCTION = f"""\
Eres analista de adquisición en Konfío, fintech mexicana que vende terminales \
punto de venta (TPV) a PyMEs.

Para cada comercio que recibas debes:
1. Asignar el MCC de 4 dígitos que mejor lo describa, tomándolo ÚNICAMENTE del \
catálogo de abajo. No inventes códigos.
2. Determinar payment_signal con la evidencia disponible: el campo estructurado \
de medios de pago manda sobre las reseñas.
3. Citar en signal_evidence el texto exacto que sustenta la señal. Si la \
evidencia viene del campo estructurado, escribe por ejemplo \
"campo de Google: solo efectivo". Cadena vacía si no hay evidencia.
4. Asignar intent_score de 0 a 100: qué tan probable es que este comercio \
contrate una terminal hoy. Un negocio con alto volumen de reseñas que solo \
acepta efectivo va arriba de 80. Uno que ya cobra con tarjeta sin quejas va \
abajo de 30.
5. Escribir outreach_message: WhatsApp en español mexicano, máximo \
{MESSAGE_WORD_LIMIT} palabras, tuteando, sin emojis, que cite la evidencia \
concreta que encontraste. Nada de plantillas genéricas.

PROHIBIDO ABSOLUTAMENTE en outreach_message: las palabras banco, bancaria, \
cuenta de cheques, depósito, tesorería, rendimiento o inversión. Konfío es \
SOFOM E.N.R. y su licencia bancaria sigue pendiente ante la CNBV; mencionar \
esos productos crea exposición regulatoria. Vende aceptación de pagos con \
tarjeta y el historial que ese volumen construye.

Copia place_id tal cual lo recibiste. Devuelve un elemento por comercio.

CATÁLOGO MCC EN ALCANCE (mcc | familia | descripción):
{catalog_prompt_block()}
"""


class Classifier(Protocol):
    def classify(self, places: list[RawPlace]) -> list[ClassifiedLead]: ...


class GeminiClassifier:
    def __init__(
        self,
        api_key: str,
        model: str,
        sleep_between: float = 1.0,
        backoff_base: float = BACKOFF_SECONDS,
    ) -> None:
        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._sleep_between = sleep_between
        self._backoff_base = backoff_base
        self.calls = 0
        # Phase 3 reports a measured cost per lead, not an estimated one.
        self.prompt_tokens = 0
        self.output_tokens = 0

    def _payload(self, places: list[RawPlace]) -> str:
        return json.dumps(
            [
                {
                    "place_id": p.place_id,
                    "nombre": p.name,
                    "tipo": p.primary_type,
                    "tipos": p.types[:6],
                    "direccion": p.address,
                    "plaza": p.plaza,
                    "resenas_totales": p.user_rating_count,
                    "calificacion": p.rating,
                    "medios_de_pago": p.payment_options,
                    "resenas": [r.text[:400] for r in p.reviews],
                }
                for p in places
            ],
            ensure_ascii=False,
        )

    def classify(self, places: list[RawPlace]) -> list[ClassifiedLead]:
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            response_mime_type="application/json",
            response_schema=list[ClassifiedLead],
            temperature=0.3,
            # We pass no tools; leaving AFC on makes the SDK warn on every call.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

        last_error: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                response = self._client.models.generate_content(
                    model=self._model,
                    contents=self._payload(places),
                    config=config,
                )
            except errors.APIError as exc:
                if not _is_retryable(exc):
                    raise
                last_error = exc
                if attempt < MAX_ATTEMPTS - 1:
                    delay = self._backoff_base * (2**attempt)
                    logger.warning("Gemini falló, reintentando en %.0fs: %s", delay, exc)
                    time.sleep(delay)
                continue

            self.calls += 1
            self._record_usage(response)
            time.sleep(self._sleep_between)
            return _parse_response(response)

        raise RuntimeError(f"Gemini falló tras {MAX_ATTEMPTS} intentos: {last_error}")

    def _record_usage(self, response: Any) -> None:
        usage = getattr(response, "usage_metadata", None)
        if usage is None:
            return
        self.prompt_tokens += usage.prompt_token_count or 0
        self.output_tokens += usage.candidates_token_count or 0


def _is_retryable(exc: errors.APIError) -> bool:
    """5xx and rate limits recover on their own; a 400 never will."""
    return isinstance(exc, errors.ServerError) or exc.code == 429


def _finish_reason(response: Any) -> str:
    candidates = getattr(response, "candidates", None) or []
    reason = getattr(candidates[0], "finish_reason", None) if candidates else None
    return str(reason) if reason else "sin finish_reason"


def _parse_response(response: Any) -> list[ClassifiedLead]:
    parsed = getattr(response, "parsed", None)
    if parsed:
        return list(parsed)

    text = getattr(response, "text", None)
    if not text:
        # Structured output comes back empty when the batch is truncated or a
        # safety filter trips. The batch is lost either way; name the cause so
        # the log says more than "NoneType is not str".
        raise RuntimeError(f"Gemini devolvió una respuesta vacía ({_finish_reason(response)})")

    return [ClassifiedLead.model_validate(item) for item in json.loads(text)]


def violates_compliance(message: str) -> str | None:
    lowered = message.lower()
    for term in PROHIBITED_TERMS:
        if term in lowered:
            return term
    return None


def reconcile_signal(place: RawPlace, model_signal: PaymentSignal) -> PaymentSignal:
    structured = signal_from_payment_options(place.payment_options)
    if structured is None:
        return model_signal
    if structured is PaymentSignal.CONFIRMED_NO_CARD:
        return structured
    # Cards are demonstrably accepted, so the model may only judge how much
    # pain there is around them — not whether they are accepted at all.
    if model_signal in (PaymentSignal.COMPETITOR_TERMINAL, PaymentSignal.ACCEPTS_CARD):
        return model_signal
    return structured


def reconcile(place: RawPlace, raw: LeadClassification) -> LeadClassification:
    """Correct the model against sources of truth, then decide eligibility in code."""
    data = raw.model_dump()

    entry = lookup(raw.mcc)
    if entry is not None:
        # The catalog owns the mapping; a model-invented FAMILIA is discarded.
        data["mcc"] = entry.mcc
        data["familia"] = entry.familia

    data["payment_signal"] = reconcile_signal(place, raw.payment_signal)

    offending = violates_compliance(raw.outreach_message)
    words = len(raw.outreach_message.split())
    if words > MESSAGE_WORD_LIMIT:
        logger.warning("Mensaje de %s excede el límite: %d palabras", place.place_id, words)

    disqualifier = ""
    if offending:
        disqualifier = f"mensaje incumple guardarraíl de cumplimiento: '{offending}'"
    elif entry is None:
        disqualifier = f"MCC fuera del catálogo: {raw.mcc}"
    elif not is_in_scope(data["mcc"]):
        disqualifier = f"familia fuera de alcance: {data['familia']}"
    elif not place.phone:
        disqualifier = "sin teléfono público"
    elif data["payment_signal"] not in QUALIFYING_SIGNALS:
        disqualifier = f"sin señal de compra: {data['payment_signal']}"

    data["eligible"] = not disqualifier
    data["disqualifier"] = disqualifier
    return LeadClassification.model_validate(data)


def run_enrichment(
    places: list[RawPlace],
    classifier: Classifier,
    trace_id: str,
    batch_size: int = 10,
) -> list[ProcessedLead]:
    by_id = {p.place_id: p for p in places}
    leads: list[ProcessedLead] = []

    for start in range(0, len(places), batch_size):
        batch = places[start : start + batch_size]
        try:
            classified = classifier.classify(batch)
        except Exception:
            # A dead batch must not discard the batches already paid for.
            logger.exception("Lote fallido en la posición %d, continuando", start)
            continue

        for item in classified:
            place = by_id.get(item.place_id)
            if place is None:
                logger.warning("El modelo devolvió un place_id desconocido: %s", item.place_id)
                continue
            classification = reconcile(place, LeadClassification.model_validate(item.model_dump()))
            leads.append(
                ProcessedLead(place=place, classification=classification, trace_id=trace_id)
            )

        logger.info("Clasificados %d/%d comercios", len(leads), len(places))

    leads.sort(key=lambda lead: lead.classification.intent_score, reverse=True)
    return leads


CSV_COLUMNS = (
    "place_id",
    "nombre",
    "plaza",
    "direccion",
    "telefono",
    "sitio_web",
    "mcc",
    "familia",
    "payment_signal",
    "signal_evidence",
    "intent_score",
    "eligible",
    "disqualifier",
    "outreach_message",
    "rationale",
    "resenas_totales",
    "calificacion",
    "trace_id",
    "schema_version",
)


def _row(lead: ProcessedLead) -> dict[str, Any]:
    place, cls = lead.place, lead.classification
    return {
        "place_id": place.place_id,
        "nombre": place.name,
        "plaza": place.plaza,
        "direccion": place.address,
        "telefono": place.phone,
        "sitio_web": place.website,
        "mcc": cls.mcc,
        "familia": cls.familia,
        "payment_signal": cls.payment_signal.value,
        "signal_evidence": cls.signal_evidence,
        "intent_score": cls.intent_score,
        "eligible": cls.eligible,
        "disqualifier": cls.disqualifier,
        "outreach_message": cls.outreach_message,
        "rationale": cls.rationale,
        "resenas_totales": place.user_rating_count,
        "calificacion": place.rating,
        "trace_id": lead.trace_id,
        "schema_version": lead.schema_version,
    }


def read_processed_leads() -> list[dict[str, Any]]:
    if not PROCESSED_LEADS_PATH.exists():
        return []
    with PROCESSED_LEADS_PATH.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_processed_leads(
    leads: list[ProcessedLead], existing: list[dict[str, Any]] | None = None
) -> None:
    """Merge by place_id and re-sort, so a later run adds leads instead of replacing them."""
    by_id = {row["place_id"]: row for row in existing or []}
    by_id.update({lead.place.place_id: _row(lead) for lead in leads})
    rows = sorted(by_id.values(), key=lambda row: int(row["intent_score"]), reverse=True)

    PROCESSED_LEADS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with PROCESSED_LEADS_PATH.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def build_classifier(settings: Settings) -> GeminiClassifier:
    logger.info(
        "Catálogo en alcance: %d códigos MCC como vocabulario cerrado",
        len(in_scope_catalog()),
    )
    return GeminiClassifier(settings.gemini_api_key, settings.gemini_model)
