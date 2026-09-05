import argparse
import asyncio
import hashlib
import json
import time
from pathlib import Path

import pdf_to_markdown.batch as batch_module
from pdf_to_markdown.batch import (
    BatchJob,
    BatchLogger,
    completed_run,
    discover_jobs,
    run_convert_dir,
    run_queue,
)
from pdf_to_markdown.cli import build_parser
from pdf_to_markdown.io_utils import write_json


def test_convert_dir_cli_defaults() -> None:
    args = build_parser().parse_args(["convert-dir", "/tmp/pdfs"])
    assert args.directory == Path("/tmp/pdfs")
    assert args.jobs == 2
    assert args.status_interval == 30.0
    assert args.recursive is True


def test_discover_jobs_is_recursive_and_rejects_shared_output_directory(
    tmp_path: Path,
) -> None:
    first = tmp_path / "one"
    first.mkdir()
    (first / "source.PDF").write_bytes(b"one")
    second = tmp_path / "two"
    second.mkdir()
    (second / "a.pdf").write_bytes(b"a")
    (second / "b.pdf").write_bytes(b"b")

    jobs, warnings = discover_jobs(tmp_path)

    assert [job.pdf.name for job in jobs] == ["source.PDF"]
    assert len(warnings) == 1
    assert "exactly one PDF per directory" in warnings[0]


def test_completed_run_requires_validation_hash_and_nonempty_markdown(
    tmp_path: Path,
) -> None:
    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(b"pdf")
    job = BatchJob(pdf)
    write_json(job.status_path, {"status": "completed"})
    write_json(job.validation_path, {"status": "passed"})
    write_json(
        job.metadata_path,
        {"source": {"sha256": hashlib.sha256(b"pdf").hexdigest()}},
    )
    job.markdown_path.write_text("done\n", encoding="utf-8")

    assert completed_run(job)[0] is True
    pdf.write_bytes(b"changed")
    assert completed_run(job) == (False, "source PDF changed after the completed run")


def test_all_requested_slots_start_without_delay(
    tmp_path: Path, monkeypatch
) -> None:
    async def fake_run_job(*_args, **_kwargs):
        return True

    monkeypatch.setattr(batch_module, "run_job", fake_run_job)
    logger = BatchLogger(tmp_path / "log.md")
    started = time.monotonic()
    try:
        result = asyncio.run(
            run_queue(
                [BatchJob(tmp_path / "source.pdf")],
                workers=3,
                status_interval=30.0,
                converter_args=[],
                logger=logger,
            )
        )
    finally:
        logger.close()

    assert result == (1, 0)
    assert time.monotonic() - started < 2


def test_convert_dir_dry_run_prints_summary(tmp_path: Path, capsys) -> None:
    source = tmp_path / "company"
    source.mkdir()
    (source / "source.pdf").write_bytes(b"pdf")
    args = argparse.Namespace(
        directory=tmp_path,
        jobs=2,
        status_interval=30.0,
        log_file=None,
        limit=None,
        dry_run=True,
        recursive=True,
        converter_args=[],
    )

    assert run_convert_dir(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["discovered"] == 1
    assert payload["pending"] == 1
    assert payload["dry_run"] is True
    assert (tmp_path / "log.md").is_file()
