"""Pipeline entry point (`tpv-pipeline`)."""

from __future__ import annotations

import logging
import sys

from .config import MissingCredentialError, Settings
from .enrich import build_classifier, run_enrichment, write_processed_leads
from .extract import new_trace_id, run_extraction
from .models import PaymentSignal, ProcessedLead
from .places import PlacesError


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )


def main() -> int:
    _configure_logging()
    log = logging.getLogger("pipeline")

    try:
        settings = Settings.load()
    except MissingCredentialError as exc:
        log.error("%s", exc)
        return 1

    trace_id = new_trace_id()
    log.info("Corrida %s", trace_id)

    try:
        places = run_extraction(settings, trace_id)
    except PlacesError as exc:
        log.error("%s", exc)
        return 1

    log.info("Fase 1 completa: %d comercios extraídos", len(places))

    # No phone means no channel, and the LLM call is the expensive step.
    reachable = [p for p in places if p.phone]
    log.info("%d de %d comercios tienen teléfono público", len(reachable), len(places))

    classifier = build_classifier(settings)
    leads = run_enrichment(reachable, classifier, trace_id, settings.gemini_batch_size)
    write_processed_leads(leads)
    _log_funnel(log, len(places), len(reachable), leads)
    log.info(
        "Gemini: %d llamadas · %d tokens de entrada · %d de salida",
        classifier.calls,
        classifier.prompt_tokens,
        classifier.output_tokens,
    )
    return 0


def _log_funnel(log: logging.Logger, fetched: int, reachable: int, leads: list[ProcessedLead]) -> None:
    eligible = [lead for lead in leads if lead.classification.eligible]
    log.info("Embudo: %d extraídos → %d con teléfono → %d calificados", fetched, reachable, len(eligible))
    for signal in PaymentSignal:
        count = sum(1 for lead in eligible if lead.classification.payment_signal is signal)
        if count:
            log.info("  %s: %d", signal.value, count)


if __name__ == "__main__":
    sys.exit(main())
