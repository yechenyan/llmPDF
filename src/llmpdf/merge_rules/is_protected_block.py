from ..models import DocumentBlock


def is_protected_block(block: DocumentBlock) -> bool:
    """Keep structural headings and captions out of region absorption."""
    return block.kind in {"title", "section_header", "caption"}
