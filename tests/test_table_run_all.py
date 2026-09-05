import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from llmpdf.table.run_all import extractor_scripts, run_all


class RunAllTest(unittest.TestCase):
    def test_discovers_tables_in_numeric_directory_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("table_10", "table_2", "table_1"):
                directory = root / name
                directory.mkdir()
                (directory / "extract.py").touch()
            self.assertEqual(
                [path.parent.name for path in extractor_scripts(root)],
                ["table_1", "table_2", "table_10"],
            )

    def test_runs_every_extractor_with_one_public_entrypoint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pdf = root / "page.pdf"
            pdf.touch()
            for name in ("table_1", "table_2"):
                directory = root / name
                directory.mkdir()
                (directory / "extract.py").touch()
            with patch("llmpdf.table.run_all.subprocess.run") as subprocess_run:
                subprocess_run.return_value.returncode = 0
                returncode = run_all(pdf, root, spatial_check=True)
            self.assertEqual(returncode, 0)
            self.assertEqual(subprocess_run.call_count, 2)
            for call in subprocess_run.call_args_list:
                self.assertIn("--spatial-check", call.args[0])


if __name__ == "__main__":
    unittest.main()
