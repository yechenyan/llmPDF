from typing import Any

from ..models import BBox, DocumentBlock
from .is_protected_block import is_protected_block


def find_image_absorbed_blocks(
    image: dict[str, Any],
    blocks: list[DocumentBlock],
    claimed_ids: set[str],
) -> list[DocumentBlock]:
    """Find Docling text fragments already represented by a final image."""
    if not image.get("include_in_markdown", True):
        return []
    bbox_value = image.get("analysis_bbox") or image.get("bbox")
    if not bbox_value:
        return []
    image_bbox = BBox.from_dict(bbox_value)
    owner_block_id = str(image.get("block_id") or "")
    return [
        block
        for block in blocks
        if block.id not in claimed_ids
        and block.id != owner_block_id
        and block.kind in {"text", "list_item", "document_index"}
        and block.bbox is not None
        and not is_protected_block(block)
        and image_bbox.contains_center(block.bbox, margin=2.0)
    ]
