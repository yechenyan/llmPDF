import csv
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import yaml

from parse_table.runtime import finalize_table


class RuntimeTest(unittest.TestCase):
    def make_job(self, root: Path) -> tuple[Path, Path]:
        assets = root / "assets"
        output_dir = root / "agent-output" / "table_2"
        assets.mkdir(parents=True)
        output_dir.mkdir(parents=True)
        (assets / "page_info.json").write_text(
            json.dumps(
                {
                    "physical_page": 16,
                    "display_width_pt": 600,
                    "display_height_pt": 800,
                }
            ),
            encoding="utf-8",
        )
        with (output_dir / "output_1.csv").open("w", encoding="utf-8", newline="") as stream:
            csv.writer(stream).writerows([["A", "B"], ["1", "2"]])
        pdf_path = assets / "page_0016.pdf"
        pdf_path.touch()
        return pdf_path, output_dir

    def test_writes_metadata_without_running_spatial_check(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pdf_path, output_dir = self.make_job(Path(temporary))
            with patch("parse_table.runtime.inspect_csv") as inspect_csv:
                metadata_path = finalize_table(
                    pdf_path=pdf_path,
                    output_dir=output_dir,
                    name="Visible title",
                    bbox=(10, 20, 590, 700),
                )
            inspect_csv.assert_not_called()
            metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(metadata["name"], "Visible title")
            self.assertEqual(metadata["page"], 16)
            self.assertEqual(metadata["page_table_index"], 2)
            self.assertTrue(metadata["bbox"]["approximate"])

    def test_spatial_check_is_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pdf_path, output_dir = self.make_job(Path(temporary))
            result = Mock(
                status="NO_SPATIAL_ANOMALY",
                message="No anomaly",
                checked_cells=4,
                skipped_cells=0,
                suspects=[],
            )
            result.to_text.return_value = "NO_SPATIAL_ANOMALY"
            with patch("parse_table.runtime.inspect_csv", return_value=result) as inspect_csv:
                with contextlib.redirect_stdout(io.StringIO()):
                    finalize_table(
                        pdf_path=pdf_path,
                        output_dir=output_dir,
                        name=None,
                        bbox=(10, 20, 590, 700),
                        spatial_check=True,
                    )
            inspect_csv.assert_called_once_with(pdf_path, (output_dir / "output_1.csv").resolve())
            result.write_json.assert_called_once_with((output_dir / "output_1.guard.json").resolve())

    def test_spatial_check_writes_compact_validation_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pdf_path, output_dir = self.make_job(Path(temporary))
            result = Mock(
                status="NOT_APPLICABLE",
                message="No unique grid",
                checked_cells=0,
                skipped_cells=0,
                suspects=[],
            )
            with patch("parse_table.runtime.inspect_csv", return_value=result):
                with contextlib.redirect_stdout(io.StringIO()) as stdout:
                    finalize_table(
                        pdf_path=pdf_path,
                        output_dir=output_dir,
                        name=None,
                        bbox=(10, 20, 590, 700),
                        spatial_check=True,
                    )
            report_path = output_dir / "output_1.validation.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["rows"], 2)
            self.assertEqual(report["columns"], 2)
            self.assertEqual(report["header"], ["A", "B"])
            self.assertEqual(report["first_rows"], [["1", "2"]])
            self.assertEqual(report["validation_status"], "REQUIRES_VISUAL_REVIEW")
            self.assertIn("TABLE_REPORT", stdout.getvalue())

    def test_rejects_bbox_outside_page(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pdf_path, output_dir = self.make_job(Path(temporary))
            with self.assertRaises(ValueError):
                finalize_table(
                    pdf_path=pdf_path,
                    output_dir=output_dir,
                    name=None,
                    bbox=(10, 20, 601, 700),
                )


if __name__ == "__main__":
    unittest.main()
