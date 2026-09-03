"""Configuration and search-plan contracts.

The fail-fast behaviour is load-bearing: the alternative is discovering a
missing key halfway through a metered API run, having already paid for the
calls made before the crash.
"""

from __future__ import annotations

import pytest

from src.config import (
    FAMILIAS_EN_SCOPE,
    GIRO_QUERIES,
    ZONAS,
    MissingCredentialError,
    Settings,
    search_plan,
)

CREDENTIALS = ("GOOGLE_MAPS_API_KEY", "GEMINI_API_KEY")


@pytest.fixture
def clean_env(monkeypatch):
    """Both keys present and every optional knob unset, so defaults are visible."""
    for name in CREDENTIALS:
        monkeypatch.setenv(name, "test-key")
    for name in (
        "GEMINI_MODEL",
        "MAX_PLACE_DETAILS",
        "TARGET_LEADS",
        "REVIEW_BUDGET",
        "GEMINI_BATCH_SIZE",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("missing", CREDENTIALS)
def test_a_missing_credential_names_itself(clean_env, monkeypatch, missing):
    """The error has to say which variable, or it costs a support round-trip."""
    monkeypatch.delenv(missing)
    with pytest.raises(MissingCredentialError, match=missing):
        Settings.load()


def test_a_blank_credential_is_treated_as_missing(clean_env, monkeypatch):
    """An empty line in .env is the common mistake, and it is not a valid key."""
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "   ")
    with pytest.raises(MissingCredentialError, match="GOOGLE_MAPS_API_KEY"):
        Settings.load()


def test_defaults_are_applied_when_only_credentials_are_set(clean_env):
    """A fresh clone with two keys in .env must run without further configuration."""
    settings = Settings.load()
    assert settings.target_leads == 500
    assert settings.review_budget == 1000
    # The ceiling has to leave room for the target to fire: at the measured 4.7
    # screenings per lead, 500 leads needs ~2,350 of them.
    assert settings.max_place_details >= settings.target_leads * 4.7
    assert settings.gemini_batch_size == 10
    # Floating alias on purpose: a pinned version that gets retired 404s a clone.
    assert settings.gemini_model == "gemini-flash-latest"


def test_optional_knobs_are_read_from_the_environment(clean_env, monkeypatch):
    monkeypatch.setenv("TARGET_LEADS", "50")
    monkeypatch.setenv("REVIEW_BUDGET", "0")
    settings = Settings.load()
    assert settings.target_leads == 50
    assert settings.review_budget == 0


def test_search_plan_is_the_full_giro_by_zona_product():
    plan = search_plan()
    assert len(plan) == len(GIRO_QUERIES) * len(ZONAS)
    assert len({task.query for task in plan}) == len(plan)


def test_the_query_targets_the_zona_but_reports_the_plaza():
    """Prominence ranking is the whole reason these two differ."""
    task = next(t for t in search_plan() if t.zona == "Iztapalapa, Ciudad de México")
    assert task.query == f"{task.giro} en Iztapalapa, Ciudad de México"
    assert task.plaza == "Ciudad de México"


def test_hotel_and_airline_familias_stay_out_of_scope():
    """ISO 18245 reserves 3000-3999 for brands, none of which is a Mexican SME."""
    assert not any("otel" in f or "erol" in f for f in FAMILIAS_EN_SCOPE)
