from __future__ import annotations

import pytest

from pdf_to_markdown.pages import effective_pages, parse_pages, validate_pages


def test_parse_pages_supports_ranges_lists_sorting_and_deduplication() -> None:
    assert parse_pages("8-10, 1,3,9") == (1, 3, 8, 9, 10)
    assert parse_pages([8, 1, 8, 3]) == (1, 3, 8)


@pytest.mark.parametrize("value", ["", "0", "3-1", "1,,2", "a", "1-2-3", []])
def test_parse_pages_rejects_invalid_selections(value) -> None:
    with pytest.raises(ValueError):
        parse_pages(value)


def test_validate_pages_rejects_out_of_range_page() -> None:
    with pytest.raises(ValueError, match="exceeds PDF page count 10"):
        validate_pages("1,11", 10)


def test_none_means_every_page() -> None:
    assert parse_pages(None) is None
    assert effective_pages(None, 3) == [1, 2, 3]
