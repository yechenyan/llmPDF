from __future__ import annotations

import copy
import os
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .io_utils import read_json, relative_reference, sha256_file, write_json
from .models import PipelineConfig
from .review_tables import (
    combine_docling_rows,
    count_row_differences,
    csv_text,
    markdown_table_rows,
    read_csv_rows,
    rows_to_markdown,
)
from .validate_task import ValidateTask

REVIEW_STATUSES = {"unreviewed", "approved", "ignored"}
REVIEWED_STATUSES = {"approved", "ignored"}
LEGACY_REVIEW_STATUSES = {
    "accepted_ai": "approved",
    "accepted_docling": "approved",
    "edited": "approved",
    "needs_review": "unreviewed",
    "skipped": "ignored",
}


def normalize_review_status(value: Any) -> str:
    status = str(value or "unreviewed")
    return LEGACY_REVIEW_STATUSES.get(status, status)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _safe_path(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"Asset path escapes result directory: {relative}")
    return candidate


def _atomic_text(path: Path, value: str) -> None:
    temporary = path.with_name(f".{path.name}.review-tmp")
    temporary.write_text(value, encoding="utf-8")
    os.replace(temporary, path)


def _atomic_json(path: Path, value: Any) -> None:
    write_json(path, value)


def _link_or_copy(source: Path, destination: Path) -> None:
    """Create a space-saving snapshot, falling back across filesystems."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


@dataclass
class ReviewSource:
    root: Path
    id: str

    @property
    def metadata_path(self) -> Path:
        return self.root / "assets" / "metadata.json"

    @property
    def draft_path(self) -> Path:
        return self.root / "work" / "review" / "draft.json"

    @property
    def source_root(self) -> Path:
        return self.root / "work" / "review" / "source"

    @property
    def source_metadata_path(self) -> Path:
        return self.source_root / "metadata.json"

    @property
    def source_output_path(self) -> Path:
        return self.source_root / "output.md"

    def metadata(self) -> dict[str, Any]:
        return read_json(self.metadata_path)

    def ensure_source_snapshot(self) -> None:
        metadata = (
            read_json(self.source_metadata_path)
            if self.source_metadata_path.is_file()
            else copy.deepcopy(self.metadata())
        )
        for table in metadata.get("tables", []):
            table.pop("review", None)
            source_csv = self.source_root / "tables" / f"{table['id']}.csv"
            if not source_csv.is_file():
                _link_or_copy(_safe_path(self.root, str(table["csv"])), source_csv)
        self.source_root.mkdir(parents=True, exist_ok=True)
        if not self.source_output_path.is_file():
            _link_or_copy(self.root / "output.md", self.source_output_path)
        if not self.source_metadata_path.is_file():
            write_json(self.source_metadata_path, metadata)

    def source_metadata(self) -> dict[str, Any]:
        self.ensure_source_snapshot()
        return read_json(self.source_metadata_path)

    def source_ai_csv(self, table_id: str) -> Path:
        self.ensure_source_snapshot()
        path = self.source_root / "tables" / f"{table_id}.csv"
        if not path.is_file():
            raise FileNotFoundError(
                f"Missing immutable AI source for {table_id}: {path}"
            )
        return path

    def draft(self) -> dict[str, Any]:
        if not self.draft_path.is_file():
            return {"schema_version": 2, "tables": {}, "updated_at": None}
        payload = read_json(self.draft_path)
        if not isinstance(payload, dict):
            return {"schema_version": 2, "tables": {}}
        for decision in payload.get("tables", {}).values():
            if isinstance(decision, dict):
                decision["status"] = normalize_review_status(decision.get("status"))
        payload["schema_version"] = 2
        return payload

    def application_state(self) -> dict[str, Any]:
        draft = self.draft()
        published_metadata = self.metadata().get("review", {})
        last_applied_at = draft.get("last_published_at") or published_metadata.get(
            "last_published_at"
        )
        draft_updated_at = draft.get("updated_at")
        if not last_applied_at:
            state = "never_applied"
        elif draft_updated_at and str(draft_updated_at) > str(last_applied_at):
            state = "needs_reapply"
        else:
            state = "applied"
        return {
            "application_state": state,
            "last_applied_at": last_applied_at,
            "draft_updated_at": draft_updated_at,
        }

    def pdf_path(self) -> Path | None:
        source = self.source_metadata().get("source", {})
        configured = source.get("path")
        if configured:
            candidate = Path(str(configured)).expanduser()
            if not candidate.is_absolute():
                candidate = self.root / candidate
            if candidate.is_file():
                return candidate.resolve()
        filename = source.get("file")
        local = self.root / str(filename) if filename else None
        return local.resolve() if local and local.is_file() else None

    def artifact_path(self, relative: str) -> Path:
        """Resolve a public Markdown asset without exposing other result files."""
        normalized = Path(relative).as_posix().lstrip("/")
        if normalized == "assets" or not normalized.startswith("assets/"):
            raise ValueError(f"Only generated assets may be served: {relative}")
        path = _safe_path(self.root, normalized)
        if not path.is_file():
            raise FileNotFoundError(f"Generated asset is unavailable: {relative}")
        return path

    def comparison(self) -> dict[str, Any]:
        """Return the current generated Markdown and its comparison metadata."""
        metadata = self.metadata()
        source = metadata.get("source", {})
        output = self.root / "output.md"
        if not output.is_file():
            raise FileNotFoundError(f"Generated Markdown is unavailable: {output}")
        return {
            "source_id": self.id,
            "name": source.get("file") or self.root.name,
            "page_count": int(source.get("page_count", 0)),
            "selected_pages": [
                int(page)
                for page in source.get(
                    "selected_pages",
                    range(1, int(source.get("page_count", 0)) + 1),
                )
            ],
            "pdf_url": f"/api/sources/{self.id}/pdf",
            "artifact_base_url": f"/api/sources/{self.id}/artifacts/",
            "markdown": output.read_text(encoding="utf-8"),
        }

    def table(self, table_id: str) -> dict[str, Any]:
        for table in self.source_metadata().get("tables", []):
            if str(table.get("id")) == table_id:
                return table
        raise KeyError(f"Unknown table: {table_id}")

    def docling_fragments(
        self, metadata: dict[str, Any], table: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        docling_by_id = {
            str(item["id"]): item for item in metadata.get("docling_tables", [])
        }
        fragments = []
        regions: dict[str, Any] = {str(table["page"]): table.get("bbox")}
        for docling_id in (table.get("lineage") or {}).get("docling_table_ids", []):
            record = docling_by_id.get(str(docling_id))
            if not record:
                continue
            markdown_path = record.get("markdown")
            markdown = ""
            if markdown_path:
                path = _safe_path(self.root, str(markdown_path))
                if path.is_file():
                    markdown = path.read_text(encoding="utf-8")
            page = int(record["page"])
            regions[str(page)] = record.get("bbox")
            fragments.append(
                {
                    "id": record["id"],
                    "page": page,
                    "status": record.get("status"),
                    "markdown": markdown,
                    "rows": markdown_table_rows(markdown),
                }
            )
        return fragments, regions

    def summary(self) -> dict[str, Any]:
        metadata = self.source_metadata()
        drafts = self.draft().get("tables", {})
        tables = []
        reviewed = 0
        for table in metadata.get("tables", []):
            table_id = str(table["id"])
            decision = drafts.get(table_id) or table.get("review") or {}
            status = normalize_review_status(decision.get("status"))
            fragments, _ = self.docling_fragments(metadata, table)
            difference_count = count_row_differences(
                read_csv_rows(self.source_ai_csv(table_id)),
                combine_docling_rows(fragments),
            )
            reviewed += int(status in REVIEWED_STATUSES)
            tables.append(
                {
                    "id": table_id,
                    "page": int(table["page"]),
                    "source_pages": [
                        int(page) for page in table.get("source_pages", [table["page"]])
                    ],
                    "name": table.get("name") or table_id,
                    "status": status,
                    "difference_count": difference_count,
                    "lineage_action": (table.get("lineage") or {}).get("action"),
                }
            )
        source = metadata.get("source", {})
        return {
            "id": self.id,
            "name": source.get("file") or self.root.name,
            "path_base": "result_dir",
            "result_dir": ".",
            "pdf_available": self.pdf_path() is not None,
            "page_count": int(source.get("page_count", 0)),
            "selected_pages": [
                int(page)
                for page in source.get(
                    "selected_pages",
                    range(1, int(source.get("page_count", 0)) + 1),
                )
            ],
            "table_count": len(tables),
            "reviewed_count": reviewed,
            **self.application_state(),
            "tables": tables,
        }

    def detail(self, table_id: str) -> dict[str, Any]:
        metadata = self.source_metadata()
        table = self.table(table_id)
        ai_csv = self.source_ai_csv(table_id)
        fragments, regions = self.docling_fragments(metadata, table)
        draft = self.draft().get("tables", {}).get(table_id)
        return {
            "source_id": self.id,
            "table": table,
            "ai_rows": read_csv_rows(ai_csv),
            "docling_fragments": fragments,
            "docling_rows": combine_docling_rows(fragments),
            "preview_regions": regions,
            "pdf_url": f"/api/sources/{self.id}/pdf",
            "draft": draft,
        }

    def save_draft(self, table_id: str, decision: dict[str, Any]) -> dict[str, Any]:
        self.table(table_id)
        status = normalize_review_status(decision.get("status"))
        if status not in REVIEW_STATUSES:
            raise ValueError(f"Invalid review status: {status}")
        rows = decision.get("rows")
        if rows is not None and (
            not isinstance(rows, list)
            or any(
                not isinstance(row, list)
                or any(not isinstance(cell, str) for cell in row)
                for row in rows
            )
        ):
            raise ValueError("rows must be a two-dimensional string array")
        stored = {
            "status": status,
            "selected_source": decision.get("selected_source"),
            "note": str(decision.get("note", "")),
            "rows": rows,
            "updated_at": utc_now(),
        }
        payload = self.draft()
        payload.setdefault("tables", {})[table_id] = stored
        payload["schema_version"] = 2
        payload["updated_at"] = stored["updated_at"]
        write_json(self.draft_path, payload)
        return stored

    def publish(self) -> dict[str, Any]:
        source_metadata = self.source_metadata()
        metadata = copy.deepcopy(source_metadata)
        draft = self.draft()
        decisions = draft.get("tables", {})
        published_at = utc_now()
        history_name = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
        history = self.root / "work" / "review" / "history" / history_name
        history.mkdir(parents=True)
        output_path = self.root / "output.md"
        _link_or_copy(output_path, history / "output.md")
        _link_or_copy(self.metadata_path, history / "metadata.json")
        output = self.source_output_path.read_text(encoding="utf-8")
        affected: list[tuple[Path, Path | None]] = []
        published: list[str] = []
        ignored: list[str] = []
        try:
            for table in metadata.get("tables", []):
                table_id = str(table["id"])
                decision = decisions.get(table_id) or {}
                status = normalize_review_status(decision.get("status"))
                csv_path = _safe_path(self.root, str(table["csv"]))
                backup_csv = history / "tables" / csv_path.name
                if csv_path.is_file():
                    _link_or_copy(csv_path, backup_csv)
                    affected.append((csv_path, backup_csv))
                else:
                    affected.append((csv_path, None))
                ai_rows = read_csv_rows(self.source_ai_csv(table_id))
                old_markdown = rows_to_markdown(
                    ai_rows, table.get("name"), int(table.get("header_rows", 1))
                )
                marker = f"<!-- table:{table_id} page:{table['page']} -->"
                old_block = f"{marker}\n\n{old_markdown}"
                if output.count(old_block) != 1:
                    raise ValueError(
                        f"Cannot safely locate the source Markdown block for {table_id}"
                    )
                rows = decision.get("rows") or ai_rows
                if not rows:
                    raise ValueError(f"{table_id} has no table rows")
                new_markdown = rows_to_markdown(
                    rows, table.get("name"), int(table.get("header_rows", 1))
                )
                output = output.replace(old_block, f"{marker}\n\n{new_markdown}", 1)
                _atomic_text(csv_path, csv_text(rows))
                table["review"] = {
                    "status": status,
                    "selected_source": decision.get("selected_source", "ai"),
                    "note": decision.get("note", ""),
                    "published_at": published_at,
                    "csv_sha256": sha256_file(csv_path),
                }
                published.append(table_id)
                if status == "ignored":
                    ignored.append(table_id)
            _atomic_text(output_path, output)
            metadata.setdefault("review", {})["last_published_at"] = published_at
            metadata["review"]["published_table_count"] = len(published)
            metadata["review"]["applied_table_count"] = len(published)
            metadata["review"]["ignored_table_count"] = len(ignored)
            metadata["review"]["ignored_table_ids"] = ignored
            metadata["output"]["sha256"] = sha256_file(output_path)
            _atomic_json(self.metadata_path, metadata)
            source_path = self.pdf_path() or Path(
                str(metadata.get("source", {}).get("path", self.root / "missing.pdf"))
            )
            retain_docling = any(
                bool(item.get("retained"))
                for item in metadata.get("docling_tables", [])
            )
            ValidateTask().run(
                PipelineConfig(
                    pdf=source_path,
                    output_dir=self.root,
                    retain_docling_tables=retain_docling,
                )
            )
        except Exception:
            shutil.copy2(history / "output.md", output_path)
            shutil.copy2(history / "metadata.json", self.metadata_path)
            for csv_path, backup_csv in affected:
                if backup_csv is None:
                    csv_path.unlink(missing_ok=True)
                else:
                    shutil.copy2(backup_csv, csv_path)
            raise
        draft["last_published_at"] = published_at
        write_json(self.draft_path, draft)
        return {
            "status": "published",
            "published_at": published_at,
            "published_tables": published,
            "ignored_tables": ignored,
            "history": relative_reference(history, self.root),
        }
