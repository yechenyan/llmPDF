from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


DEFAULT_TARGET = "all tables on this page"


def slug(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._")
    return normalized or "pdf"


@dataclass(frozen=True)
class ExtractionJob:
    pdf: Path
    page: int
    target: str = DEFAULT_TARGET
    job_id: str | None = None
    may_merge_with_previous: bool = False

    @property
    def id(self) -> str:
        return self.job_id or f"{slug(self.pdf.stem)}-page-{self.page:04d}"

    @classmethod
    def from_dict(cls, value: dict, base_dir: Path) -> "ExtractionJob":
        pdf = Path(value["pdf"])
        if not pdf.is_absolute():
            pdf = base_dir / pdf
        return cls(
            pdf=pdf.resolve(),
            page=int(value["page"]),
            target=str(value.get("target", DEFAULT_TARGET)),
            job_id=str(value["id"]) if value.get("id") else None,
            may_merge_with_previous=bool(value.get("may_merge_with_previous", False)),
        )


@dataclass(frozen=True)
class PreparedJob:
    job: ExtractionJob
    directory: Path
    single_page_pdf: Path
    page_image: Path
    prompt: Path
    agent_output: Path
