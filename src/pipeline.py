"""Pipeline entry point (`tpv-pipeline`)."""

from __future__ import annotations

import logging
import sys

from .config import MissingCredentialError, Settings
from .extract import new_trace_id, run_extraction
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
