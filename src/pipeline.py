"""Pipeline entry point (`tpv-pipeline`).

Runs both phases and reports the funnel. By default it *adds* to whatever is
already in `data/`, re-screening nothing: this is meant to be a standing weekly
job, and the second week should cost what the new merchants cost. Pass `--fresh`
to start the list over.
"""

from __future__ import annotations

import logging
import sys

from .config import MissingCredentialError, Settings
from .enrich import (
    build_classifier,
    read_processed_leads,
    run_enrichment,
    write_processed_leads,
)
from .extract import new_trace_id, run_extraction
from .models import PaymentSignal, ProcessedLead
from .places import PlacesError, signal_from_payment_options


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )


def main(argv: list[str] | None = None) -> int:
    _configure_logging()
    log = logging.getLogger("pipeline")
    fresh = "--fresh" in (argv if argv is not None else sys.argv[1:])

    try:
        settings = Settings.load()
    except MissingCredentialError as exc:
        log.error("%s", exc)
        return 1

    trace_id = new_trace_id()
    log.info("Corrida %s%s", trace_id, " (desde cero)" if fresh else "")

    previous = [] if fresh else read_processed_leads()
    on_disk = sum(1 for row in previous if row["eligible"] == "True")
    if on_disk:
        log.info("%d leads calificados ya en disco; la meta es %d", on_disk, settings.target_leads)

    try:
        places = run_extraction(settings, trace_id, fresh=fresh, already_qualified=on_disk)
    except PlacesError as exc:
        log.error("%s", exc)
        return 1

    log.info("Fase 1 completa: %d comercios nuevos", len(places))

    # Re-checked here rather than trusted from Phase 1, because a resumed run
    # may be reading merchants that an older, looser screen let through.
    sellable = [
        p
        for p in places
        if p.phone
        and signal_from_payment_options(p.payment_options) is PaymentSignal.CONFIRMED_NO_CARD
    ]
    log.info("%d de %d comercios llegan a Gemini", len(sellable), len(places))

    classifier = build_classifier(settings)
    leads = run_enrichment(sellable, classifier, trace_id, settings.gemini_batch_size)
    write_processed_leads(leads, previous)
    _log_funnel(log, len(places), len(sellable), leads, on_disk)
    log.info(
        "Gemini: %d llamadas · %d tokens de entrada · %d de salida",
        classifier.calls,
        classifier.prompt_tokens,
        classifier.output_tokens,
    )
    return 0


def _log_funnel(
    log: logging.Logger,
    fetched: int,
    sellable: int,
    leads: list[ProcessedLead],
    already: int,
) -> None:
    eligible = [lead for lead in leads if lead.classification.eligible]
    log.info(
        "Embudo: %d nuevos → %d vendibles → %d calificados (total en disco: %d)",
        fetched,
        sellable,
        len(eligible),
        already + len(eligible),
    )
    for signal in PaymentSignal:
        count = sum(1 for lead in eligible if lead.classification.payment_signal is signal)
        if count:
            log.info("  %s: %d", signal.value, count)


if __name__ == "__main__":
    sys.exit(main())
