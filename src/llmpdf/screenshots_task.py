from __future__ import annotations

import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader

from .io_utils import relativize, write_json
from .models import PipelineConfig, TaskResult
from .pages import effective_pages
from .progress import report_progress
from .task import PipelineTask


def _label_page(image_path: Path, page: int) -> None:
    with Image.open(image_path) as source:
        image = source.convert("RGB")
    banner_height = max(32, image.height // 22)
    canvas = Image.new("RGB", (image.width, image.height + banner_height), "white")
    canvas.paste(image, (0, banner_height))
    draw = ImageDraw.Draw(canvas)
    label = f"PAGE {page:04d}"
    font = ImageFont.load_default(size=max(16, banner_height // 2))
    draw.text((12, banner_height // 2), label, fill="black", anchor="lm", font=font)
    canvas.save(image_path, optimize=True)


def _contact_sheet(paths: list[Path], destination: Path) -> None:
    images: list[Image.Image] = []
    for path in paths:
        with Image.open(path) as source:
            image = source.convert("RGB")
        image.thumbnail((560, 800), Image.Resampling.LANCZOS)
        images.append(image)
    columns = 2
    rows = (len(images) + columns - 1) // columns
    gap = 16
    cell_width = max(image.width for image in images)
    cell_height = max(image.height for image in images)
    sheet = Image.new(
        "RGB",
        (
            columns * cell_width + (columns + 1) * gap,
            rows * cell_height + (rows + 1) * gap,
        ),
        "#d9d9d9",
    )
    for index, image in enumerate(images):
        column, row = index % columns, index // columns
        x = gap + column * (cell_width + gap) + (cell_width - image.width) // 2
        y = gap + row * (cell_height + gap) + (cell_height - image.height) // 2
        sheet.paste(image, (x, y))
    destination.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(destination, optimize=True)


class ScreenshotsTask(PipelineTask):
    name = "02-screenshots"

    def signature_payload(self, config: PipelineConfig) -> dict:
        value = super().signature_payload(config)
        value.update(
            {
                "dpi": config.detection_dpi,
                "contact_sheet_size": config.contact_sheet_size,
                "layout_version": 3,
            }
        )
        return value

    def run(self, config: PipelineConfig) -> TaskResult:
        root = config.work_dir / "detection"
        pages_dir = root / "pages"
        sheets_dir = root / "contact-sheets"
        pages_dir.mkdir(parents=True, exist_ok=True)
        sheets_dir.mkdir(parents=True, exist_ok=True)
        page_count = len(PdfReader(config.pdf).pages)
        selected_pages = effective_pages(config.selected_pages, page_count)
        pages: list[Path] = []
        for completed_pages, page in enumerate(selected_pages, 1):
            destination = pages_dir / f"page_{page:04d}.png"
            prefix = destination.with_suffix("")
            subprocess.run(
                [
                    config.pdftoppm,
                    "-f",
                    str(page),
                    "-l",
                    str(page),
                    "-singlefile",
                    "-png",
                    "-r",
                    str(config.detection_dpi),
                    str(config.pdf.resolve()),
                    str(prefix),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            _label_page(destination, page)
            pages.append(destination)
            report_progress(
                config,
                f"screenshots {completed_pages}/{len(selected_pages)} (page {page})",
            )

        batches = []
        for start in range(0, len(pages), config.contact_sheet_size):
            batch_paths = pages[start : start + config.contact_sheet_size]
            context_paths = ([pages[start - 1]] if start else []) + batch_paths
            batch_pages = selected_pages[start : start + len(batch_paths)]
            first, last = batch_pages[0], batch_pages[-1]
            previous = selected_pages[start - 1] if start else None
            context_pages = ([previous] if previous == first - 1 else []) + batch_pages
            sheet = sheets_dir / f"pages_{first:04d}_{last:04d}.jpg"
            _contact_sheet(context_paths, sheet)
            batches.append(
                {
                    "first_page": first,
                    "last_page": last,
                    "pages": batch_pages,
                    "context_pages": context_pages,
                    "image": relativize(sheet, config.output_dir),
                }
            )
        manifest = root / "screenshots.json"
        write_json(
            manifest,
            {
                "schema_version": 1,
                "dpi": config.detection_dpi,
                "page_count": page_count,
                "selected_pages": selected_pages,
                "batches": batches,
            },
        )
        return TaskResult(
            self.name,
            "completed",
            [
                relativize(manifest, config.output_dir),
                relativize(sheets_dir, config.output_dir),
            ],
            {
                "page_count": page_count,
                "selected_pages": selected_pages,
                "batch_count": len(batches),
            },
        )
