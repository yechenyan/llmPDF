from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class BBox:
    x0: float
    top: float
    x1: float
    bottom: float

    @property
    def width(self) -> float:
        return max(0.0, self.x1 - self.x0)

    @property
    def height(self) -> float:
        return max(0.0, self.bottom - self.top)

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def center(self) -> tuple[float, float]:
        return ((self.x0 + self.x1) / 2, (self.top + self.bottom) / 2)

    def intersection_area(self, other: BBox) -> float:
        width = max(0.0, min(self.x1, other.x1) - max(self.x0, other.x0))
        height = max(0.0, min(self.bottom, other.bottom) - max(self.top, other.top))
        return width * height

    def overlap_over_smaller(self, other: BBox) -> float:
        denominator = min(self.area, other.area)
        return self.intersection_area(other) / denominator if denominator else 0.0

    def contains_center(self, other: BBox, margin: float = 0.0) -> bool:
        x, y = other.center
        return (
            self.x0 - margin <= x <= self.x1 + margin
            and self.top - margin <= y <= self.bottom + margin
        )

    def to_dict(self) -> dict[str, float]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> BBox:
        return cls(*(float(value[key]) for key in ("x0", "top", "x1", "bottom")))


@dataclass
class DocumentBlock:
    id: str
    page: int
    order: int
    kind: str
    markdown: str
    bbox: BBox | None = None
    source_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["bbox"] = self.bbox.to_dict() if self.bbox else None
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> DocumentBlock:
        copied = dict(value)
        copied["bbox"] = BBox.from_dict(copied["bbox"]) if copied.get("bbox") else None
        return cls(**copied)


@dataclass
class PipelineConfig:
    pdf: Path
    output_dir: Path
    selected_pages: tuple[int, ...] | None = None
    model: str = "gpt-5.6-sol"
    thinking: str = "medium"
    detection_dpi: int = 96
    contact_sheet_size: int = 8
    agent_concurrency: int = 5
    find_concurrency: int = 5
    table_concurrency: int = 5
    image_concurrency: int = 5
    agent_timeout_seconds: float = 1800.0
    confidence_threshold: float = 0.35
    pdftoppm: str = "pdftoppm"
    agent_backend: str = "pi"
    claude_executable: Path | None = None
    pi_executable: Path | None = None
    table_executable: Path | None = None
    docling_options: dict[str, Any] = field(default_factory=dict)
    document_converter: Any | None = None
    document_converter_cache_key: str | None = None
    retain_docling_tables: bool = True
    analyze_images: bool = True
    image_model: str | None = "gpt-5.6-terra"
    image_thinking: str | None = "medium"
    image_render_dpi: int = 240
    image_max_patches: int = 10_000
    table_image_max_patches: int = 30_000
    keep_sessions: bool = False
    keep_work: bool = False
    show_progress: bool = False
    force: bool = False
    # Runtime-only scheduler. The pipeline owns its lifecycle and Find,
    # cross-page table, ordinary table, and image work share its five slots.
    agent_executor: Any | None = field(
        default=None, init=False, repr=False, compare=False
    )
    queue_images_with_tables: bool = field(
        default=False, init=False, repr=False, compare=False
    )
    prepared_image_jobs: Any | None = field(
        default=None, init=False, repr=False, compare=False
    )
    image_job_futures: Any | None = field(
        default=None, init=False, repr=False, compare=False
    )
    dynamic_agent_scheduling: bool = field(
        default=False, init=False, repr=False, compare=False
    )
    early_table_jobs: Any | None = field(
        default=None, init=False, repr=False, compare=False
    )
    early_table_futures: Any | None = field(
        default=None, init=False, repr=False, compare=False
    )
    status_tracker: Any | None = field(
        default=None, init=False, repr=False, compare=False
    )

    @property
    def work_dir(self) -> Path:
        return self.output_dir / "work"

    @property
    def assets_dir(self) -> Path:
        return self.output_dir / "assets"


@dataclass
class TaskResult:
    task: str
    status: str
    outputs: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
