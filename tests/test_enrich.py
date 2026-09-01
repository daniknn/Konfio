import pytest

from src.enrich import (
    reconcile,
    run_enrichment,
    signal_from_payment_options,
    violates_compliance,
)
from src.models import ClassifiedLead, LeadClassification, PaymentSignal, RawPlace


def _place(**overrides) -> RawPlace:
    defaults = {
        "place_id": "p1",
        "name": "Taquería El Güero",
        "phone": "55 1234 5678",
        "payment_options": {},
    }
    return RawPlace(**{**defaults, **overrides})


def _classification(**overrides) -> LeadClassification:
    defaults = {
        "mcc": "5812",
        "familia": "LO QUE SEA QUE INVENTE EL MODELO",
        "payment_signal": PaymentSignal.INFERRED_NO_CARD,
        "signal_evidence": "solo aceptan efectivo",
        "intent_score": 80,
        "eligible": True,
        "disqualifier": "",
        "outreach_message": "Hola, vi que solo aceptas efectivo. ¿Te paso costos de terminal?",
        "rationale": "Restaurante sin aceptación de tarjeta.",
    }
    return LeadClassification(**{**defaults, **overrides})


class FakeClassifier:
    def __init__(self, replies, fail_on=()):
        self._replies = replies
        self._fail_on = fail_on
        self.batches = 0

    def classify(self, places):
        self.batches += 1
        if self.batches in self._fail_on:
            raise RuntimeError("cuota agotada")
        return self._replies.pop(0)


def test_structured_cash_only_overrides_the_model():
    """Google's first-party field beats the model's guess, always."""
    place = _place(payment_options={"acceptsCashOnly": True})
    result = reconcile(place, _classification(payment_signal=PaymentSignal.ACCEPTS_CARD))
    assert result.payment_signal is PaymentSignal.CONFIRMED_NO_CARD
    assert result.eligible


def test_card_acceptance_downgrades_a_no_card_claim():
    place = _place(payment_options={"acceptsCreditCards": True})
    result = reconcile(place, _classification(payment_signal=PaymentSignal.CONFIRMED_NO_CARD))
    assert result.payment_signal is PaymentSignal.COMPETITOR_TERMINAL


def test_model_may_still_choose_accepts_card_when_cards_are_taken():
    place = _place(payment_options={"acceptsDebitCards": True})
    result = reconcile(place, _classification(payment_signal=PaymentSignal.ACCEPTS_CARD))
    assert result.payment_signal is PaymentSignal.ACCEPTS_CARD
    assert not result.eligible
    assert "sin señal de compra" in result.disqualifier


def test_silent_payment_field_leaves_the_model_in_charge():
    result = reconcile(_place(), _classification(payment_signal=PaymentSignal.INFERRED_NO_CARD))
    assert result.payment_signal is PaymentSignal.INFERRED_NO_CARD


def test_catalog_corrects_an_invented_familia():
    result = reconcile(_place(), _classification(mcc="5812", familia="Inventada"))
    assert result.familia == "Restaurantes"


def test_unknown_mcc_disqualifies():
    result = reconcile(_place(), _classification(mcc="9999"))
    assert not result.eligible
    assert "fuera del catálogo" in result.disqualifier


def test_out_of_scope_mcc_disqualifies():
    result = reconcile(_place(), _classification(mcc="3501"))
    assert not result.eligible
    assert "fuera de alcance" in result.disqualifier


def test_missing_phone_disqualifies():
    result = reconcile(_place(phone=None), _classification())
    assert not result.eligible
    assert result.disqualifier == "sin teléfono público"


@pytest.mark.parametrize(
    "message",
    [
        "Abre tu cuenta de cheques con nosotros hoy",
        "Konfío es el banco de las PyMEs",
        "Mejora el rendimiento de tu tesorería",
    ],
)
def test_prohibited_terms_disqualify_the_lead(message):
    """Regulatory exposure is a hard stop: the lead is dropped, not the wording patched."""
    result = reconcile(_place(), _classification(outreach_message=message))
    assert not result.eligible
    assert "cumplimiento" in result.disqualifier


def test_compliant_message_passes():
    assert violates_compliance("Con la terminal cobras con tarjeta y construyes historial") is None


def test_long_message_warns_but_does_not_disqualify(caplog):
    """Length is a style defect; compliance is a legal one. Different severities."""
    result = reconcile(_place(), _classification(outreach_message="palabra " * 80))
    assert result.eligible
    assert "excede el límite" in caplog.text


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ({"acceptsCashOnly": True}, PaymentSignal.CONFIRMED_NO_CARD),
        ({"acceptsCreditCards": False, "acceptsDebitCards": False}, PaymentSignal.CONFIRMED_NO_CARD),
        ({"acceptsCreditCards": True}, PaymentSignal.COMPETITOR_TERMINAL),
        ({}, None),
    ],
)
def test_signal_derivation_from_structured_field(options, expected):
    assert signal_from_payment_options(options) == expected


def test_run_enrichment_matches_replies_by_place_id_not_position():
    places = [_place(place_id="a"), _place(place_id="b")]
    replies = [
        [
            ClassifiedLead(place_id="b", **_classification(intent_score=90).model_dump()),
            ClassifiedLead(place_id="a", **_classification(intent_score=40).model_dump()),
        ]
    ]
    leads = run_enrichment(places, FakeClassifier(replies), "trc_1", batch_size=10)
    assert [lead.place.place_id for lead in leads] == ["b", "a"]
    assert leads[0].classification.intent_score == 90


def test_run_enrichment_drops_hallucinated_place_ids():
    places = [_place(place_id="a")]
    replies = [[ClassifiedLead(place_id="no-existe", **_classification().model_dump())]]
    assert run_enrichment(places, FakeClassifier(replies), "trc_1") == []


def test_failed_batch_does_not_discard_the_others():
    places = [_place(place_id=f"p{i}") for i in range(4)]
    replies = [
        [ClassifiedLead(place_id="p2", **_classification().model_dump())],
        [ClassifiedLead(place_id="p3", **_classification().model_dump())],
    ]
    classifier = FakeClassifier(replies, fail_on=(1,))
    leads = run_enrichment(places, classifier, "trc_1", batch_size=2)
    assert [lead.place.place_id for lead in leads] == ["p2"]


def test_leads_are_sorted_by_intent_score():
    places = [_place(place_id="a"), _place(place_id="b"), _place(place_id="c")]
    replies = [
        [
            ClassifiedLead(place_id="a", **_classification(intent_score=10).model_dump()),
            ClassifiedLead(place_id="b", **_classification(intent_score=95).model_dump()),
            ClassifiedLead(place_id="c", **_classification(intent_score=55).model_dump()),
        ]
    ]
    leads = run_enrichment(places, FakeClassifier(replies), "trc_1")
    assert [lead.classification.intent_score for lead in leads] == [95, 55, 10]
