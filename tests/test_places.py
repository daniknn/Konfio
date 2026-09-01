import httpx
import pytest

from src.config import SearchTask, is_chain
from src.extract import collect_candidates, fetch_details
from src.places import PlacesClient, PlacesError, to_raw_place


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

    tasks = [SearchTask(query="abarrotes en CDMX", giro="abarrotes", plaza="Ciudad de México")]
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
        SearchTask(query="abarrotes en CDMX", giro="abarrotes", plaza="Ciudad de México"),
        SearchTask(query="taquería en CDMX", giro="taquería", plaza="Ciudad de México"),
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
        SearchTask(query="rota", giro="x", plaza="CDMX"),
        SearchTask(query="buena", giro="y", plaza="CDMX"),
    ]
    candidates = collect_candidates(_client(handler), tasks)
    assert [c[0]["id"] for c in candidates] == ["ok"]


def test_fetch_details_respects_quota_guard():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "x", "displayName": {"text": "X"}})

    client = _client(handler)
    task = SearchTask(query="q", giro="g", plaza="CDMX")
    candidates = [({"id": f"p{i}"}, task) for i in range(10)]
    fetch_details(client, candidates, max_details=3, trace_id="trc_test")
    assert client.details_calls == 3


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
