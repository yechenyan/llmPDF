from llmpdf.merge_rules import (
    find_image_absorbed_blocks,
    find_table_absorbed_blocks,
    is_protected_block,
)
from llmpdf.models import BBox, DocumentBlock


def block(identifier: str, kind: str, bbox: BBox) -> DocumentBlock:
    return DocumentBlock(identifier, 1, 1, kind, identifier, bbox)


def test_protected_block_types() -> None:
    assert is_protected_block(block("heading", "section_header", BBox(0, 0, 1, 1)))
    assert not is_protected_block(block("text", "text", BBox(0, 0, 1, 1)))


def test_table_absorbs_unprotected_blocks_inside_its_bbox() -> None:
    inside = block("inside", "text", BBox(10, 10, 20, 20))
    heading = block("heading", "section_header", BBox(10, 10, 20, 20))
    outside = block("outside", "text", BBox(110, 110, 120, 120))

    assert find_table_absorbed_blocks(
        BBox(0, 0, 100, 100), [inside, heading, outside], set()
    ) == [inside]


def test_image_absorbs_text_but_not_its_owner_block() -> None:
    owner = block("picture", "picture", BBox(0, 0, 100, 100))
    label = block("label", "text", BBox(10, 10, 20, 20))
    image = {
        "block_id": "picture",
        "include_in_markdown": True,
        "analysis_bbox": {"x0": 0, "top": 0, "x1": 100, "bottom": 100},
    }

    assert find_image_absorbed_blocks(image, [owner, label], set()) == [label]
