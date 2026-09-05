from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from .io_utils import read_json, relative_reference
from .models import PipelineConfig, TaskResult
from .pages import PageSelection, validate_pages
from .pipeline import run_all

PathInput = str | os.PathLike[str]


class DocumentConverterProtocol(Protocol):
    def convert(self, source: Path) -> Any: ...


@dataclass
class ConvertOptions:
    pdf: PathInput
    output_root: PathInput
    batch_id: str | None = None
    pages: PageSelection = None
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
    docling_options: dict[str, Any] = field(default_factory=dict)
    document_converter: DocumentConverterProtocol | None = None
    document_converter_cache_key: str | None = None
    retain_docling_tables: bool = True
    analyze_images: bool = True
    image_model: str | None = "gpt-5.6-terra"
    image_thinking: str | None = "medium"
    image_render_dpi: int = 240
    image_max_patches: int = 10_000
    table_image_max_patches: int = 30_000
    pdftoppm: str | Path = "pdftoppm"
    pi_executable: PathInput | None = None
    table_executable: PathInput | None = None
    keep_sessions: bool = False
    keep_work: bool = False
    show_progress: bool = False
    force: bool = False


@dataclass(frozen=True)
class TokenUsage:
    noncached_input_tokens: int = 0
    cached_input_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cache_hit_ratio: float = 0.0
    pi_api_price_estimate_usd: float = 0.0

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> TokenUsage:
        value = value or {}
        input_tokens = int(value.get("input_tokens", 0) or 0)
        cached = int(value.get("cached_input_tokens", 0) or 0)
        return cls(
            noncached_input_tokens=int(value.get("noncached_input_tokens", 0) or 0),
            cached_input_tokens=cached,
            input_tokens=input_tokens,
            output_tokens=int(value.get("output_tokens", 0) or 0),
            total_tokens=int(value.get("total_tokens", 0) or 0),
            cache_hit_ratio=float(
                value.get(
                    "cache_hit_ratio", cached / input_tokens if input_tokens else 0.0
                )
                or 0.0
            ),
            pi_api_price_estimate_usd=float(
                value.get("pi_api_price_estimate_usd", 0.0) or 0.0
            ),
        )


@dataclass(frozen=True)
class UsageSummary:
    total: TokenUsage
    detection: TokenUsage
    table_extraction: TokenUsage
    image_analysis: TokenUsage = field(default_factory=TokenUsage)
    detection_batches: list[dict[str, Any]] = field(default_factory=list)
    table_pages: list[dict[str, Any]] = field(default_factory=list)
    image_pages: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_metrics(cls, metrics: dict[str, Any]) -> UsageSummary:
        tokens = metrics.get("tokens") or {}
        return cls(
            total=TokenUsage.from_dict(tokens.get("total")),
            detection=TokenUsage.from_dict(tokens.get("detection")),
            table_extraction=TokenUsage.from_dict(tokens.get("table_extraction")),
            image_analysis=TokenUsage.from_dict(tokens.get("image_analysis")),
            detection_batches=list(tokens.get("detection_batches") or []),
            table_pages=list(tokens.get("table_pages") or []),
            image_pages=list(tokens.get("image_pages") or []),
        )


@dataclass(frozen=True)
class BillingSummary:
    provider: str
    billing_mode: str
    currency: str
    actual_openai_charge_usd: float | None
    pi_api_price_estimate_usd: float
    note: str

    @classmethod
    def from_metrics(
        cls, metrics: dict[str, Any], usage: UsageSummary
    ) -> BillingSummary:
        billing = metrics.get("billing") or {}
        actual = billing.get("actual_openai_charge_usd")
        return cls(
            provider=str(billing.get("provider", "openai-codex")),
            billing_mode=str(billing.get("billing_mode", "chatgpt_codex_plan")),
            currency="USD",
            actual_openai_charge_usd=float(actual) if actual is not None else None,
            pi_api_price_estimate_usd=float(
                billing.get(
                    "pi_api_price_estimate_usd",
                    usage.total.pi_api_price_estimate_usd,
                )
                or 0.0
            ),
            note=str(
                billing.get(
                    "note",
                    "Estimated from Pi's API price table; not the actual Codex plan charge.",
                )
            ),
        )


@dataclass
class ConversionResult:
    status: Literal["completed"]
    source_pdf: Path
    batch_id: str
    output_root: Path
    output_dir: Path
    output_markdown: Path
    assets_dir: Path
    metadata: Path
    metrics: Path
    validation_report: Path
    table_count: int
    image_count: int
    docling_table_count: int
    retained_docling_table_count: int
    table_lineage: dict[str, Any]
    elapsed_seconds: float
    tasks: list[TaskResult]
    usage: UsageSummary
    billing: BillingSummary

    @property
    def total_tokens(self) -> int:
        return self.usage.total.total_tokens

    @property
    def estimated_price_usd(self) -> float:
        return self.billing.pi_api_price_estimate_usd

    def to_dict(self) -> dict[str, Any]:
        value = _json_ready(self)
        value.update(
            {
                "path_base": "output_dir",
                "source_pdf": relative_reference(self.source_pdf, self.output_dir),
                "output_root": relative_reference(self.output_root, self.output_dir),
                "output_dir": ".",
                "output_markdown": relative_reference(
                    self.output_markdown, self.output_dir
                ),
                "assets_dir": relative_reference(self.assets_dir, self.output_dir),
                "metadata": relative_reference(self.metadata, self.output_dir),
                "metrics": relative_reference(self.metrics, self.output_dir),
                "validation_report": relative_reference(
                    self.validation_report, self.output_dir
                ),
            }
        )
        value["total_tokens"] = self.total_tokens
        value["estimated_price_usd"] = self.estimated_price_usd
        return value


class ConversionError(Exception):
    def __init__(
        self,
        message: str,
        *,
        error_type: str,
        failed_task: str | None = None,
        source_pdf: Path | None = None,
        batch_id: str | None = None,
        output_root: Path | None = None,
        output_dir: Path | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_type = error_type
        self.failed_task = failed_task
        self.source_pdf = source_pdf
        self.batch_id = batch_id
        self.output_root = output_root
        self.output_dir = output_dir

    def to_dict(self) -> dict[str, Any]:
        base = (self.output_dir or self.output_root or Path.cwd()).resolve()
        return {
            "status": "failed",
            "error_type": self.error_type,
            "error": self.message,
            "failed_task": self.failed_task,
            "path_base": (
                "output_dir"
                if self.output_dir
                else "output_root"
                if self.output_root
                else "cwd"
            ),
            "source_pdf": (
                relative_reference(self.source_pdf, base) if self.source_pdf else None
            ),
            "batch_id": self.batch_id,
            "output_root": (
                relative_reference(self.output_root, base) if self.output_root else None
            ),
            "output_dir": (
                relative_reference(self.output_dir, base) if self.output_dir else None
            ),
        }


class ConfigurationError(ConversionError):
    pass


class TaskExecutionError(ConversionError):
    pass


def automatic_batch_id() -> str:
    return datetime.now(UTC).strftime("batch-%Y%m%d-%H%M%S-%f")


def _path(value: PathInput) -> Path:
    return Path(value).expanduser().resolve()


def _validate_batch_id(value: str) -> None:
    if Path(value).name != value or value in {".", ".."}:
        raise ValueError("batch_id must be a single directory name")


def _explicit_executable(value: PathInput | None, name: str) -> Path | None:
    if value is None:
        return None
    path = _path(value)
    if not path.is_file() or not os.access(path, os.X_OK):
        raise FileNotFoundError(f"{name} executable is invalid: {path}")
    return path


def build_pipeline_config(options: ConvertOptions) -> tuple[PipelineConfig, str, Path]:
    try:
        pdf = _path(options.pdf)
        output_root = _path(options.output_root)
        if not pdf.is_file() or pdf.suffix.lower() != ".pdf":
            raise FileNotFoundError(f"PDF does not exist or is not a .pdf file: {pdf}")
        if output_root.exists() and not output_root.is_dir():
            raise NotADirectoryError(f"output_root is not a directory: {output_root}")
        batch_id = options.batch_id or automatic_batch_id()
        _validate_batch_id(batch_id)
        if not 1 <= options.agent_concurrency <= 5:
            raise ValueError("agent_concurrency must be between 1 and 5")
        if not 1 <= options.find_concurrency <= 5:
            raise ValueError("find_concurrency must be between 1 and 5")
        if options.table_concurrency < 1:
            raise ValueError("table_concurrency must be positive")
        if options.image_concurrency < 1:
            raise ValueError("image_concurrency must be positive")
        if options.agent_timeout_seconds <= 0:
            raise ValueError("agent_timeout_seconds must be positive")
        if options.detection_dpi < 1 or options.contact_sheet_size < 1:
            raise ValueError("detection_dpi and contact_sheet_size must be positive")
        if options.image_render_dpi < 1:
            raise ValueError("image_render_dpi must be positive")
        if options.image_max_patches < 1:
            raise ValueError("image_max_patches must be positive")
        if options.table_image_max_patches < 1:
            raise ValueError("table_image_max_patches must be positive")
        if not 0 <= options.confidence_threshold <= 1:
            raise ValueError("confidence_threshold must be between 0 and 1")
        if not isinstance(options.docling_options, dict):
            raise TypeError("docling_options must be a dictionary")
        json.dumps(options.docling_options, ensure_ascii=False)
        if options.document_converter is not None and options.docling_options:
            raise ValueError(
                "document_converter and non-empty docling_options cannot be used together"
            )
        if options.document_converter is not None and not callable(
            getattr(options.document_converter, "convert", None)
        ):
            raise TypeError(
                "document_converter must provide a callable convert() method"
            )
        if options.document_converter_cache_key and options.document_converter is None:
            raise ValueError("document_converter_cache_key requires document_converter")
        output_dir = output_root / batch_id / pdf.stem
        selected_pages = None
        if options.pages is not None:
            from pypdf import PdfReader

            selected_pages = validate_pages(options.pages, len(PdfReader(pdf).pages))
        config = PipelineConfig(
            pdf=pdf,
            output_dir=output_dir,
            selected_pages=selected_pages,
            model=options.model,
            thinking=options.thinking,
            detection_dpi=options.detection_dpi,
            contact_sheet_size=options.contact_sheet_size,
            agent_concurrency=options.agent_concurrency,
            find_concurrency=options.find_concurrency,
            table_concurrency=options.table_concurrency,
            image_concurrency=options.image_concurrency,
            agent_timeout_seconds=options.agent_timeout_seconds,
            confidence_threshold=options.confidence_threshold,
            pdftoppm=str(options.pdftoppm),
            pi_executable=_explicit_executable(options.pi_executable, "Pi"),
            table_executable=_explicit_executable(
                options.table_executable, "llmpdf-table"
            ),
            docling_options=dict(options.docling_options),
            document_converter=options.document_converter,
            document_converter_cache_key=options.document_converter_cache_key,
            retain_docling_tables=options.retain_docling_tables,
            analyze_images=options.analyze_images,
            image_model=options.image_model,
            image_thinking=options.image_thinking,
            image_render_dpi=options.image_render_dpi,
            image_max_patches=options.image_max_patches,
            table_image_max_patches=options.table_image_max_patches,
            keep_sessions=options.keep_sessions,
            keep_work=options.keep_work,
            show_progress=options.show_progress,
            force=options.force,
        )
        return config, batch_id, output_root
    except ConversionError:
        raise
    except Exception as error:
        raise ConfigurationError(
            str(error),
            error_type=type(error).__name__,
            source_pdf=Path(options.pdf).expanduser() if options.pdf else None,
            batch_id=options.batch_id,
            output_root=Path(options.output_root).expanduser()
            if options.output_root
            else None,
        ) from error


def _failed_task(config: PipelineConfig) -> str | None:
    state_dir = config.work_dir / "tasks"
    if not state_dir.is_dir():
        return None
    failed = []
    for path in state_dir.glob("*.json"):
        try:
            state = read_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        if state.get("status") == "failed":
            failed.append(state)
    latest = max(
        failed, key=lambda item: str(item.get("completed_at", "")), default=None
    )
    return str(latest.get("task")) if latest else None


def _json_ready(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return _json_ready(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return value


def convert(options: ConvertOptions) -> ConversionResult:
    started = time.perf_counter()
    config, batch_id, output_root = build_pipeline_config(options)
    try:
        tasks = run_all(config)
        output_markdown = config.output_dir / "output.md"
        metadata_path = config.assets_dir / "metadata.json"
        metrics_path = config.work_dir / "metrics.json"
        validation_path = config.work_dir / "diagnostics" / "validation.json"
        required = [output_markdown, metadata_path, metrics_path, validation_path]
        missing = [str(path) for path in required if not path.is_file()]
        if missing:
            raise RuntimeError(
                f"Pipeline completed with missing outputs: {', '.join(missing)}"
            )
        metadata = read_json(metadata_path)
        metrics = read_json(metrics_path)
        validation = read_json(validation_path)
        if validation.get("status") != "passed":
            raise RuntimeError("Pipeline validation did not pass")
        usage = UsageSummary.from_metrics(metrics)
        billing = BillingSummary.from_metrics(metrics, usage)
        return ConversionResult(
            status="completed",
            source_pdf=config.pdf,
            batch_id=batch_id,
            output_root=output_root,
            output_dir=config.output_dir,
            output_markdown=output_markdown,
            assets_dir=config.assets_dir,
            metadata=metadata_path,
            metrics=metrics_path,
            validation_report=validation_path,
            table_count=len(metadata.get("tables") or []),
            image_count=len(metadata.get("images") or []),
            docling_table_count=len(metadata.get("docling_tables") or []),
            retained_docling_table_count=sum(
                1
                for table in metadata.get("docling_tables") or []
                if table.get("retained")
            ),
            table_lineage={
                "tables": [
                    {"id": table["id"], "lineage": table.get("lineage", {})}
                    for table in metadata.get("tables") or []
                ],
                "docling_tables": metadata.get("docling_tables") or [],
            },
            elapsed_seconds=round(time.perf_counter() - started, 3),
            tasks=tasks,
            usage=usage,
            billing=billing,
        )
    except ConversionError:
        raise
    except Exception as error:
        raise TaskExecutionError(
            str(error),
            error_type=type(error).__name__,
            failed_task=_failed_task(config),
            source_pdf=config.pdf,
            batch_id=batch_id,
            output_root=output_root,
            output_dir=config.output_dir,
        ) from error
