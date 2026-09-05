from pathlib import Path

import pytest

from pdf_to_markdown.models import PipelineConfig
from pdf_to_markdown.preflight import run_preflight


class FakeTask:
    def __init__(self, name: str, cached: bool = False) -> None:
        self.name = name
        self.cached = cached

    def is_cached(self, config: PipelineConfig) -> bool:
        return self.cached


def config_for(tmp_path: Path) -> PipelineConfig:
    pdf = tmp_path / "input.pdf"
    pdf.write_bytes(b"%PDF-test")
    return PipelineConfig(pdf=pdf, output_dir=tmp_path / "output")


def test_preflight_rejects_missing_required_executable(tmp_path: Path) -> None:
    config = config_for(tmp_path)
    config.pdftoppm = "definitely-missing-pdftoppm"

    with pytest.raises(FileNotFoundError, match="pdftoppm"):
        run_preflight(config, [FakeTask("02-screenshots")])


def test_preflight_skips_tools_for_cached_tasks(tmp_path: Path) -> None:
    config = config_for(tmp_path)
    config.pdftoppm = "definitely-missing-pdftoppm"

    run_preflight(config, [FakeTask("02-screenshots", cached=True)])


def test_preflight_rejects_missing_pdf(tmp_path: Path) -> None:
    config = PipelineConfig(
        pdf=tmp_path / "missing.pdf",
        output_dir=tmp_path / "output",
    )

    with pytest.raises(FileNotFoundError, match="PDF"):
        run_preflight(config, [])
