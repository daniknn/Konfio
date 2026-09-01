from src.config import FAMILIAS_EN_SCOPE
from src.mcc import catalog_prompt_block, in_scope_catalog, is_in_scope, load_catalog, lookup


def test_catalog_loads_full_santo_grial():
    assert len(load_catalog()) == 806


def test_every_code_is_four_digits():
    assert all(len(e.mcc) == 4 and e.mcc.isdigit() for e in load_catalog())


def test_scope_excludes_airline_and_hotel_blocks():
    """ISO 18245 reserves 3000-3999 for airline and hotel brands; none are SMEs."""
    assert not any(e.mcc.startswith("3") for e in in_scope_catalog())
    assert not is_in_scope("3501")


def test_scope_keeps_sme_retail_and_food():
    assert is_in_scope("5812")  # restaurantes
    assert {e.familia for e in in_scope_catalog()} <= FAMILIAS_EN_SCOPE


def test_lookup_normalizes_short_codes():
    assert lookup("742") is not None
    assert lookup("0742") == lookup("742")


def test_lookup_returns_none_for_unknown_code():
    assert lookup("9999") is None


def test_prompt_block_has_one_line_per_in_scope_code():
    assert len(catalog_prompt_block().splitlines()) == len(in_scope_catalog())
