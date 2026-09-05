from __future__ import annotations

import math
import subprocess
import sys
from pathlib import Path

import pdfplumber
from PIL import Image
from pypdf import PdfReader, PdfWriter

from . import WORKFLOW_VERSION
from .io_utils import relative_reference, write_json
from .models import ExtractionJob, PreparedJob
from .prompt import build_extraction_prompt

DEFAULT_TARGET_SMALL_TEXT_PX = 12
DEFAULT_MAX_IMAGE_PATCHES = 30_000
IMAGE_PATCH_SIZE = 32


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[round((len(ordered) - 1) * fraction)]


def image_patch_count(width_px: int, height_px: int) -> int:
    """Return the number of 32px image patches covering an image."""
    if width_px < 1 or height_px < 1:
        raise ValueError("image dimensions must be positive")
    return math.ceil(width_px / IMAGE_PATCH_SIZE) * math.ceil(
        height_px / IMAGE_PATCH_SIZE
    )


def bounded_render_plan(
    width_pt: float,
    height_pt: float,
    target_dpi: int,
    max_image_patches: int = DEFAULT_MAX_IMAGE_PATCHES,
) -> dict:
    """Choose the highest whole-number DPI that fits the image patch budget."""
    if width_pt <= 0 or height_pt <= 0:
        raise ValueError("page dimensions must be positive")
    if target_dpi < 1:
        raise ValueError("target_dpi must be positive")
    if max_image_patches < 1:
        raise ValueError("max_image_patches must be positive")

    def dimensions(dpi: int) -> tuple[int, int]:
        return (
            max(1, math.ceil(width_pt / 72 * dpi)),
            max(1, math.ceil(height_pt / 72 * dpi)),
        )

    low, high = 1, target_dpi
    effective_dpi = 1
    while low <= high:
        candidate = (low + high) // 2
        width_px, height_px = dimensions(candidate)
        if image_patch_count(width_px, height_px) <= max_image_patches:
            effective_dpi = candidate
            low = candidate + 1
        else:
            high = candidate - 1

    width_px, height_px = dimensions(effective_dpi)
    return {
        "target_dpi": target_dpi,
        "dpi": effective_dpi,
        "estimated_width_px": width_px,
        "estimated_height_px": height_px,
        "estimated_patches": image_patch_count(width_px, height_px),
        "max_image_patches": max_image_patches,
        "dpi_reduced": effective_dpi < target_dpi,
    }


def render_page(pdf_path: Path, output_prefix: Path, dpi: int, pdftoppm: str) -> Path:
    subprocess.run(
        [pdftoppm, "-f", "1", "-singlefile", "-png", "-r", str(dpi), str(pdf_path), str(output_prefix)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return output_prefix.with_suffix(".png")


def dynamic_render(
    pdf_path: Path,
    output_prefix: Path,
    target_small_text_px: int,
    pdftoppm: str,
    max_image_patches: int = DEFAULT_MAX_IMAGE_PATCHES,
) -> tuple[Path, dict]:
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[0]
        sizes = [
            float(char["size"])
            for char in page.chars
            if char.get("text", "").strip() and float(char.get("size", 0)) > 0
        ]
        reference_font_pt = percentile(sizes, 0.20) if sizes else 8.0
        target_dpi = max(
            72, min(600, round(72 * target_small_text_px / reference_font_pt))
        )
        render_plan = bounded_render_plan(
            float(page.width),
            float(page.height),
            target_dpi,
            max_image_patches,
        )
    dpi = int(render_plan["dpi"])
    while True:
        image_path = render_page(pdf_path, output_prefix, dpi, pdftoppm)
        with Image.open(image_path) as image:
            width_px, height_px = image.size
        patches = image_patch_count(width_px, height_px)
        if patches <= max_image_patches or dpi == 1:
            break
        dpi -= 1
    render_plan["dpi"] = dpi
    render_plan["dpi_reduced"] = dpi < target_dpi
    return image_path, {
        "target_small_text_px": target_small_text_px,
        "reference_font_pt_p20": round(reference_font_pt, 3),
        **render_plan,
        "width_px": width_px,
        "height_px": height_px,
        "patches": patches,
    }


def build_page_info(
    single_pdf: Path,
    full_image: Path,
    full_dpi: int,
    physical_page: int,
    original_page_count: int,
) -> dict:
    with pdfplumber.open(single_pdf) as pdf, Image.open(full_image) as image:
        page = pdf.pages[0]
        objects = page.objects
        width_px, height_px = image.size
        return {
            "physical_page": physical_page,
            "page_count": original_page_count,
            "display_width_pt": round(page.width, 2),
            "display_height_pt": round(page.height, 2),
            "rotation": page.rotation,
            "chars": len(page.chars),
            "rects": len(objects.get("rect", [])),
            "lines": len(objects.get("line", [])),
            "curves": len(objects.get("curve", [])),
            "images": len(objects.get("image", [])),
            "edges": len(page.edges),
            "full_width_px": width_px,
            "full_height_px": height_px,
            "full_dpi": full_dpi,
            "pdfplumber_origin": "top-left",
            "scale_x": round(width_px / page.width, 10),
            "scale_y": round(height_px / page.height, 10),
            "pdfplumber_version": pdfplumber.__version__,
        }


def prepare_job(
    job: ExtractionJob,
    output_root: Path,
    *,
    target_small_text_px: int = DEFAULT_TARGET_SMALL_TEXT_PX,
    python_executable: Path = Path(sys.executable),
    pdftoppm: str = "pdftoppm",
    artifact_root: Path | None = None,
    max_image_patches: int = DEFAULT_MAX_IMAGE_PATCHES,
) -> PreparedJob:
    resolved_output_root = output_root.resolve()
    artifact_root = (artifact_root or resolved_output_root).resolve()
    job_dir = resolved_output_root / job.id
    assets = job_dir / "assets"
    agent_output = job_dir / "agent-output"
    tools = job_dir / "tools"
    assets.mkdir(parents=True, exist_ok=True)
    agent_output.mkdir(parents=True, exist_ok=True)
    tools.mkdir(parents=True, exist_ok=True)

    python_link = tools / "python"
    if not python_link.exists():
        python_link.symlink_to(python_executable.resolve())
    reader = PdfReader(job.pdf)
    if job.page < 1 or job.page > len(reader.pages):
        raise ValueError(f"Page {job.page} is outside PDF page range 1-{len(reader.pages)}")
    writer = PdfWriter()
    writer.add_page(reader.pages[job.page - 1])
    single_pdf = assets / f"page_{job.page:04d}.pdf"
    with single_pdf.open("wb") as stream:
        writer.write(stream)

    page_image, render_info = dynamic_render(
        single_pdf,
        assets / f"page_{job.page:04d}_dynamic",
        target_small_text_px,
        pdftoppm,
        max_image_patches,
    )
    page_info = build_page_info(single_pdf, page_image, render_info["dpi"], job.page, len(reader.pages))
    page_info["dynamic_render"] = render_info
    write_json(assets / "page_info.json", page_info)

    prompt = job_dir / "prompt.md"
    prompt.write_text(build_extraction_prompt(job.page, job.target, page_info), encoding="utf-8")
    manifest = {
        "workflow_version": WORKFLOW_VERSION,
        "id": job.id,
        "source_pdf": relative_reference(job.pdf, artifact_root),
        "page": job.page,
        "target": job.target,
        "target_small_text_px": target_small_text_px,
        "max_image_patches": max_image_patches,
    }
    write_json(job_dir / "job.json", manifest)
    return PreparedJob(job, job_dir, single_pdf, page_image, prompt, agent_output)
