"""Lead generation is a standing job, not a one-shot script.

The second week has to cost what the new merchants cost, so both data files
merge by `place_id` instead of being rewritten. These tests pin that down: a
merchant already on disk survives the next run, and is never re-screened.
"""

from __future__ import annotations

import pytest

from src import enrich, extract
from src.models import LeadClassification, PaymentSignal, ProcessedLead, RawPlace


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Point both writers at a scratch directory instead of the repo's data/."""
    monkeypatch.setattr(extract, "RAW_LEADS_PATH", tmp_path / "raw_leads.json")
    monkeypatch.setattr(enrich, "PROCESSED_LEADS_PATH", tmp_path / "processed_leads.csv")
    return tmp_path


def _place(place_id: str, **overrides) -> RawPlace:
    return RawPlace(**{"place_id": place_id, "name": f"Fonda {place_id}", **overrides})


def _lead(place_id: str, *, score: int = 80) -> ProcessedLead:
    return ProcessedLead(
        place=_place(place_id, phone="55 1234 5678"),
        classification=LeadClassification(
            mcc="5812",
            familia="Restaurantes",
            payment_signal=PaymentSignal.CONFIRMED_NO_CARD,
            signal_evidence="Google reporta solo efectivo",
            intent_score=score,
            eligible=True,
            disqualifier="",
            outreach_message="Hola, vi que solo aceptan efectivo. ¿Te paso costos de terminal?",
            rationale="Fonda sin aceptación de tarjeta.",
        ),
        trace_id="trc_test",
    )


def test_missing_files_read_as_empty(data_dir):
    """A fresh clone has no data/, and that is a valid starting state, not an error."""
    assert extract.read_raw_leads() == []
    assert enrich.read_processed_leads() == []


def test_a_second_run_adds_merchants_instead_of_replacing_them(data_dir):
    extract.write_raw_leads([_place("p1"), _place("p2")])
    first = extract.read_raw_leads()
    assert {p["place_id"] for p in first} == {"p1", "p2"}

    extract.write_raw_leads([_place("p3")], first)
    assert {p["place_id"] for p in extract.read_raw_leads()} == {"p1", "p2", "p3"}


def test_a_re_fetched_merchant_is_updated_not_duplicated(data_dir):
    """Same place_id, newer payload: one row, and it is the newer one."""
    extract.write_raw_leads([_place("p1", phone=None)])
    existing = extract.read_raw_leads()

    extract.write_raw_leads([_place("p1", phone="55 9999 0000")], existing)
    rows = extract.read_raw_leads()
    assert len(rows) == 1
    assert rows[0]["phone"] == "55 9999 0000"


def test_the_csv_merges_and_re_sorts_across_runs(data_dir):
    """A later run's hotter lead has to outrank an earlier run's colder one."""
    enrich.write_processed_leads([_lead("p1", score=60), _lead("p2", score=95)])
    previous = enrich.read_processed_leads()
    assert [row["place_id"] for row in previous] == ["p2", "p1"]

    enrich.write_processed_leads([_lead("p3", score=80)], previous)
    rows = enrich.read_processed_leads()
    assert [row["place_id"] for row in rows] == ["p2", "p3", "p1"]


def test_known_merchants_are_never_re_screened(data_dir, monkeypatch):
    """The whole point of accumulating: a paid screening call happens once, ever."""
    extract.write_raw_leads([_place("ya_conocido")])

    screened: list[str] = []

    class _Client:
        search_calls = screen_calls = review_calls = 0

        def search(self, query):
            return [
                {
                    "id": "ya_conocido",
                    "displayName": {"text": "Fonda"},
                    "businessStatus": "OPERATIONAL",
                },
                {
                    "id": "nuevo",
                    "displayName": {"text": "Tortillería"},
                    "businessStatus": "OPERATIONAL",
                },
            ]

        def screen(self, place_id):
            screened.append(place_id)
            return {
                "id": place_id,
                "displayName": {"text": place_id},
                "nationalPhoneNumber": "55 1234 5678",
                "paymentOptions": {"acceptsCashOnly": True},
                "userRatingCount": 100,
            }

        def close(self):
            pass

    monkeypatch.setattr(extract, "PlacesClient", lambda _key: _Client())
    monkeypatch.setattr(
        extract, "search_plan", lambda: [extract.SearchTask("q", "giro", "plaza", "zona")]
    )

    settings = _settings()
    places = extract.run_extraction(settings, "trc_test", already_qualified=0)

    assert screened == ["nuevo"]
    assert [p.place_id for p in places] == ["nuevo"]
    # ...and the merchant already on disk is still there afterwards.
    assert {p["place_id"] for p in extract.read_raw_leads()} == {"ya_conocido", "nuevo"}


def test_extraction_is_skipped_once_the_target_is_already_on_disk(data_dir, monkeypatch):
    """Hitting the goal must not cost a single call on the next weekly run."""
    monkeypatch.setattr(
        extract, "PlacesClient", lambda _key: pytest.fail("no debe crearse un cliente")
    )
    assert extract.run_extraction(_settings(), "trc_test", already_qualified=500) == []


def _settings():
    from src.config import Settings

    return Settings(
        google_maps_api_key="k",
        gemini_api_key="k",
        gemini_model="m",
        max_place_details=100,
        target_leads=500,
        review_budget=0,
        gemini_batch_size=10,
    )
