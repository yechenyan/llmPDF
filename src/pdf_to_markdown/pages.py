from __future__ import annotations

from collections.abc import Iterable


PageSelection = str | Iterable[int] | None


def parse_pages(value: PageSelection) -> tuple[int, ...] | None:
    """Parse one-based PDF page numbers without applying an upper bound."""
    if value is None:
        return None
    if not isinstance(value, str):
        pages = list(value)
        if not pages:
            raise ValueError("pages must select at least one page")
        if any(isinstance(page, bool) or not isinstance(page, int) for page in pages):
            raise ValueError("pages must contain only positive integers")
        if any(page < 1 for page in pages):
            raise ValueError("pages are one-based and must be positive")
        return tuple(sorted(set(pages)))

    spec = value.strip()
    if not spec:
        raise ValueError("pages must not be empty")
    pages: set[int] = set()
    for raw_part in spec.split(","):
        part = raw_part.strip()
        if not part:
            raise ValueError(f"invalid pages expression: {value!r}")
        if "-" not in part:
            if not part.isdigit() or int(part) < 1:
                raise ValueError(f"invalid page number: {part!r}")
            pages.add(int(part))
            continue
        bounds = [bound.strip() for bound in part.split("-")]
        if (
            len(bounds) != 2
            or not all(bound.isdigit() for bound in bounds)
            or any(int(bound) < 1 for bound in bounds)
        ):
            raise ValueError(f"invalid page range: {part!r}")
        first, last = (int(bound) for bound in bounds)
        if first > last:
            raise ValueError(f"page range must be ascending: {part!r}")
        pages.update(range(first, last + 1))
    return tuple(sorted(pages))


def validate_pages(value: PageSelection, page_count: int) -> tuple[int, ...] | None:
    pages = parse_pages(value)
    if pages is None:
        return None
    out_of_range = [page for page in pages if page > page_count]
    if out_of_range:
        raise ValueError(
            f"selected page {out_of_range[0]} exceeds PDF page count {page_count}"
        )
    return pages


def effective_pages(
    selected_pages: tuple[int, ...] | None, page_count: int
) -> list[int]:
    return (
        list(selected_pages)
        if selected_pages is not None
        else list(range(1, page_count + 1))
    )
