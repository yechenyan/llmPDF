import unittest
from pathlib import Path

from parse_table.models import ExtractionJob


class ExtractionJobTest(unittest.TestCase):
    def test_default_id_is_stable_and_safe(self) -> None:
        job = ExtractionJob(Path("/tmp/My document (final).pdf"), 7)
        self.assertEqual(job.id, "My-document-final-page-0007")

    def test_relative_batch_path_uses_jobs_file_directory(self) -> None:
        job = ExtractionJob.from_dict(
            {"pdf": "pdfs/source.pdf", "page": 3, "target": "one table"},
            Path("/work"),
        )
        self.assertEqual(job.pdf, Path("/work/pdfs/source.pdf"))
        self.assertEqual(job.page, 3)
        self.assertEqual(job.target, "one table")

    def test_batch_job_reads_merge_hint(self) -> None:
        job = ExtractionJob.from_dict(
            {"pdf": "/tmp/source.pdf", "page": 4, "may_merge_with_previous": True},
            Path("/work"),
        )
        self.assertTrue(job.may_merge_with_previous)


if __name__ == "__main__":
    unittest.main()
