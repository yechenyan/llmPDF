import unittest

from parse_table.table_guard import compact_preview, normalized, reason_for_difference


class TableGuardTest(unittest.TestCase):
    def test_normalized_removes_whitespace_only(self) -> None:
        self.assertEqual(normalized("ab-\n cd"), "ab-cd")

    def test_cross_cell_reason(self) -> None:
        self.assertEqual(
            reason_for_difference("left-right", "left"),
            "The CSV may contain text from an adjacent cell",
        )

    def test_preview_is_bounded(self) -> None:
        self.assertLessEqual(len(compact_preview("x" * 300, 20)), 20)


if __name__ == "__main__":
    unittest.main()
