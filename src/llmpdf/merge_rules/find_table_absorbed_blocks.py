from ..models import BBox, DocumentBlock
from .is_protected_block import is_protected_block


def find_table_absorbed_blocks(
    table_bbox: BBox,
    blocks: list[DocumentBlock],
    claimed_ids: set[str],
) -> list[DocumentBlock]:
    """Find Docling fragments represented by a structured table."""
    return [
        block
        for block in blocks
        if block.id not in claimed_ids
        and block.bbox is not None
        and not is_protected_block(block)
        and table_bbox.contains_center(block.bbox, margin=2.0)
    ]
