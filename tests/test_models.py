from llmpdf.models import BBox


def test_bbox_overlap_and_center() -> None:
    first = BBox(0, 0, 10, 10)
    second = BBox(5, 5, 15, 15)
    assert first.intersection_area(second) == 25
    assert first.overlap_over_smaller(second) == 0.25
    assert first.contains_center(BBox(2, 2, 4, 4))


def test_bbox_round_trip() -> None:
    bbox = BBox(1.2, 3.4, 5.6, 7.8)
    assert BBox.from_dict(bbox.to_dict()) == bbox

