from llmpdf.images_task import caption_for_picture, is_repeated_header_image
from llmpdf.models import BBox, DocumentBlock


def picture(identifier: str, page: int, order: int, bbox: BBox) -> DocumentBlock:
    return DocumentBlock(identifier, page, order, "picture", "<!-- image -->", bbox)


def test_repeated_small_header_image_is_not_a_final_asset() -> None:
    assert is_repeated_header_image(picture("logo", 2, 1, BBox(420, 20, 508, 52)))
    assert not is_repeated_header_image(picture("figure", 2, 2, BBox(80, 100, 500, 400)))
    assert not is_repeated_header_image(picture("cover", 1, 2, BBox(216, 368, 378, 421)))


def test_caption_is_assigned_spatially_even_if_docling_order_is_reversed() -> None:
    figure = picture("figure", 8, 61, BBox(105, 71, 482, 343))
    caption = DocumentBlock("caption", 8, 60, "caption", "Abbildung 2", BBox(200, 359, 380, 367))
    assert caption_for_picture(figure, [caption]) == caption
