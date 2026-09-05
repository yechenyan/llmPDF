import math

from parse_table.prepare import bounded_render_plan, image_patch_count


def test_a4_at_240_dpi_stays_at_target_dpi() -> None:
    plan = bounded_render_plan(595.28, 841.89, 240, 10_000)

    assert plan["dpi"] == 240
    assert plan["dpi_reduced"] is False
    assert plan["estimated_patches"] <= 10_000


def test_large_page_uses_highest_dpi_within_patch_budget() -> None:
    plan = bounded_render_plan(4953.3, 3499.8, 240, 10_000)

    assert plan["dpi"] < 240
    assert plan["dpi_reduced"] is True
    assert plan["estimated_patches"] <= 10_000

    next_width = math.ceil(4953.3 / 72 * (plan["dpi"] + 1))
    next_height = math.ceil(3499.8 / 72 * (plan["dpi"] + 1))
    assert image_patch_count(next_width, next_height) > 10_000


def test_patch_count_rounds_each_dimension_up() -> None:
    assert image_patch_count(33, 65) == 6
