import httpx
import pytest

from src.config import SearchTask, is_chain
from src.extract import collect_candidates, fetch_details
from src.places import REVIEWS_FIELD_MASK, PlacesClient, PlacesError, to_raw_place


def _place(place_id: str, name: str, status: str = "OPERATIONAL") -> dict:
    return {
        "id": place_id,
        "displayName": {"text": name},
        "businessStatus": status,
    }


def _client(handler) -> PlacesClient:
    transport = httpx.MockTransport(handler)
    return PlacesClient("test-key", client=httpx.Client(transport=transport), backoff_base=0)


def test_search_follows_next_page_token():
    pages = [
        {"places": [_place("a", "Taquería A")], "nextPageToken": "tok"},
        {"places": [_place("b", "Taquería B")]},
    ]
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=pages[len(calls) - 1])

    client = _client(handler)
    assert len(client.search("taquería en CDMX")) == 2
    assert client.search_calls == 2


def test_search_stops_at_max_pages():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"places": [_place("a", "A")], "nextPageToken": "tok"})

    client = _client(handler)
    client.search("taquería", max_pages=2)
    assert client.search_calls == 2


def test_retries_on_429_then_succeeds():
    responses = [
        httpx.Response(429, text="rate limited"),
        httpx.Response(200, json={"places": []}),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    assert _client(handler).search("taquería") == []


def test_403_fails_immediately_without_retrying():
    """A bad key never becomes a good key — retrying only wastes wall-clock time."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(403, text="denied")

    with pytest.raises(PlacesError, match="credencial"):
        _client(handler).search("taquería")
    assert len(calls) == 1


def test_gives_up_after_max_attempts():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    with pytest.raises(PlacesError, match="4 intentos"):
        _client(handler).search("taquería")


def test_chain_detection_is_case_insensitive():
    assert is_chain("OXXO Insurgentes")
    assert is_chain("Farmacias Guadalajara Sucursal Centro")
    assert not is_chain("Abarrotes Doña Mari")


def test_collect_candidates_drops_chains_closed_and_duplicates():
    payload = {
        "places": [
            _place("keep", "Abarrotes Doña Mari"),
            _place("chain", "OXXO Centro"),
            _place("closed", "Tiendita Cerrada", status="CLOSED_PERMANENTLY"),
            _place("keep", "Abarrotes Doña Mari"),
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    tasks = [SearchTask(query="abarrotes en CDMX", giro="abarrotes", plaza="Ciudad de México", zona="CDMX")]
    candidates = collect_candidates(_client(handler), tasks)
    assert [c[0]["id"] for c in candidates] == ["keep"]


def test_collect_candidates_interleaves_across_queries():
    """Quota must spread across giros, not drain into whichever query ran first."""
    by_query = {
        "abarrotes en CDMX": {"places": [_place("a1", "A1"), _place("a2", "A2")]},
        "taquería en CDMX": {"places": [_place("t1", "T1"), _place("t2", "T2")]},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        query = json.loads(request.content)["textQuery"]
        return httpx.Response(200, json=by_query[query])

    tasks = [
        SearchTask(query="abarrotes en CDMX", giro="abarrotes", plaza="Ciudad de México", zona="CDMX"),
        SearchTask(query="taquería en CDMX", giro="taquería", plaza="Ciudad de México", zona="CDMX"),
    ]
    candidates = collect_candidates(_client(handler), tasks)
    assert [c[0]["id"] for c in candidates] == ["a1", "t1", "a2", "t2"]


def test_failed_search_does_not_abort_the_run():
    def handler(request: httpx.Request) -> httpx.Response:
        import json

        if json.loads(request.content)["textQuery"] == "rota":
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json={"places": [_place("ok", "Buena")]})

    tasks = [
        SearchTask(query="rota", giro="x", plaza="CDMX", zona="CDMX"),
        SearchTask(query="buena", giro="y", plaza="CDMX", zona="CDMX"),
    ]
    candidates = collect_candidates(_client(handler), tasks)
    assert [c[0]["id"] for c in candidates] == ["ok"]


_TASK = SearchTask(query="q", giro="g", plaza="CDMX", zona="CDMX")


def _two_stage_client(by_id: dict[str, dict]) -> tuple[PlacesClient, list[str]]:
    """Serves the screen and reviews masks separately, recording which was asked for."""
    masks: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        mask = request.headers["X-Goog-FieldMask"]
        masks.append(mask)
        place_id = request.url.path.rsplit("/", 1)[-1]
        if mask == REVIEWS_FIELD_MASK:
            return httpx.Response(200, json={"id": place_id, "reviews": [{"text": {"text": "hola"}}]})
        return httpx.Response(200, json=by_id[place_id])

    return _client(handler), masks


def test_fetch_details_respects_quota_guard():
    client, _ = _two_stage_client({f"p{i}": {"id": f"p{i}", "displayName": {"text": "X"}} for i in range(10)})
    candidates = [({"id": f"p{i}"}, _TASK) for i in range(10)]
    fetch_details(client, candidates, max_details=3, trace_id="trc_test")
    assert client.screen_calls == 3


def _merchant(place_id: str, *, phone=True, options=None, ratings=500) -> dict:
    detail = {
        "id": place_id,
        "displayName": {"text": place_id},
        "paymentOptions": options if options is not None else {},
        "userRatingCount": ratings,
    }
    if phone:
        detail["nationalPhoneNumber"] = "55 1234 5678"
    return detail


def test_reviews_are_bought_only_for_merchants_worth_reading():
    """The reviews call is the priciest SKU, so it is the one we ration."""
    by_id = {
        "efectivo": _merchant("efectivo", options={"acceptsCashOnly": True}),
        "tarjeta": _merchant("tarjeta", options={"acceptsCreditCards": True}),
        "mudo": _merchant("mudo"),
        "sin_tel": _merchant("sin_tel", phone=False, options={"acceptsCashOnly": True}),
    }
    client, _ = _two_stage_client(by_id)
    candidates = [({"id": pid}, _TASK) for pid in by_id]
    places = fetch_details(
        client, candidates, max_details=10, trace_id="trc_test", review_budget=10
    )

    assert client.screen_calls == 4
    # A confirmed card acceptor and an unreachable merchant never earn reviews.
    assert client.review_calls == 2
    assert {p.place_id for p in places if p.reviews} == {"efectivo", "mudo"}


def test_a_silent_field_buys_reviews_even_with_no_budget_left():
    """Without reviews there is no evidence at all, so this purchase is not optional."""
    client, _ = _two_stage_client({"mudo": _merchant("mudo", ratings=0)})
    fetch_details(client, [({"id": "mudo"}, _TASK)], 10, "trc_test", review_budget=0)
    assert client.review_calls == 1


def test_cash_only_merchants_stop_buying_reviews_once_the_budget_runs_out():
    """Google already qualified them; the quote is a nicety, and niceties have a cap."""
    by_id = {f"p{i}": _merchant(f"p{i}", options={"acceptsCashOnly": True}) for i in range(5)}
    client, _ = _two_stage_client(by_id)
    candidates = [({"id": pid}, _TASK) for pid in by_id]
    fetch_details(client, candidates, 10, "trc_test", review_budget=2)
    assert client.screen_calls == 5
    assert client.review_calls == 2


def test_a_quiet_cash_only_merchant_is_not_worth_a_quote():
    """Three reviews will not contain the sentence we are paying to read."""
    by_id = {"tienda": _merchant("tienda", options={"acceptsCashOnly": True}, ratings=3)}
    client, _ = _two_stage_client(by_id)
    fetch_details(client, [({"id": "tienda"}, _TASK)], 10, "trc_test", review_budget=10)
    assert client.review_calls == 0


def test_screening_stops_once_the_target_is_met():
    """The budget guard should fire on having enough leads, not on having spent enough."""
    by_id = {f"p{i}": _merchant(f"p{i}", options={"acceptsCashOnly": True}) for i in range(20)}
    client, _ = _two_stage_client(by_id)
    candidates = [({"id": pid}, _TASK) for pid in by_id]
    fetch_details(client, candidates, 20, "trc_test", target=3, review_budget=20)
    assert client.screen_calls == 3
    assert client.review_calls == 3


def test_a_failed_reviews_call_keeps_the_merchant():
    """Reviews are evidence, not identity — losing them must not lose the lead."""
    client = _client(
        lambda request: httpx.Response(500, text="boom")
        if request.headers["X-Goog-FieldMask"] == REVIEWS_FIELD_MASK
        else httpx.Response(200, json=_merchant("p1", options={"acceptsCashOnly": True}))
    )

    places = fetch_details(client, [({"id": "p1"}, _TASK)], 10, "trc_test", review_budget=10)
    assert client.review_calls == 0  # four attempts, all refused
    assert [p.place_id for p in places] == ["p1"]
    assert places[0].reviews == []


def test_to_raw_place_maps_payment_options_and_original_review_text():
    raw = {
        "id": "abc",
        "displayName": {"text": "Taquería El Güero"},
        "formattedAddress": "Calle 1, CDMX",
        "nationalPhoneNumber": "55 1234 5678",
        "businessStatus": "OPERATIONAL",
        "paymentOptions": {"acceptsCreditCards": False, "acceptsCashOnly": True},
        "reviews": [
            {
                "text": {"text": "Cash only"},
                "originalText": {"text": "Solo aceptan efectivo"},
                "rating": 4,
            }
        ],
    }
    place = to_raw_place(
        raw, query="taquería en CDMX", plaza="CDMX", trace_id="trc_1", fetched_at="2026-08-31"
    )

    assert place.payment_options == {"acceptsCreditCards": False, "acceptsCashOnly": True}
    assert place.reviews[0].text == "Solo aceptan efectivo"
    assert place.phone == "55 1234 5678"
    assert place.trace_id == "trc_1"


def test_reviews_sort_spanish_first():
    """A tourist's English review is unusable as evidence in a Spanish message."""
    raw = {
        "id": "abc",
        "displayName": {"text": "Taquería"},
        "reviews": [
            {"originalText": {"text": "Great tacos", "languageCode": "en"}},
            {"originalText": {"text": "Solo aceptan efectivo", "languageCode": "es"}},
        ],
    }
    place = to_raw_place(
        raw, query="q", plaza="CDMX", trace_id="trc_1", fetched_at="2026-08-31"
    )
    assert place.reviews[0].text == "Solo aceptan efectivo"
    assert place.reviews[0].language == "es"


def test_to_raw_place_tolerates_sparse_payload():
    """Mexican SME listings are frequently missing phone, website and reviews."""
    place = to_raw_place(
        {"id": "abc", "displayName": {"text": "Tiendita"}},
        query="q",
        plaza="CDMX",
        trace_id="trc_1",
        fetched_at="2026-08-31",
    )
    assert place.phone is None
    assert place.reviews == []
    assert place.payment_options == {}
