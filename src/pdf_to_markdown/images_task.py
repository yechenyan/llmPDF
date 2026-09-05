from __future__ import annotations

from collections import defaultdict
from typing import Any

from .io_utils import copy_file, read_json, relativize, sha256_file, write_json
from .models import DocumentBlock, PipelineConfig, TaskResult
from .task import PipelineTask


def is_repeated_header_image(block: DocumentBlock) -> bool:
    """Ignore the small corporate mark repeated in the top-right page header."""
    return bool(
        block.bbox
        and block.page > 1
        and block.bbox.top < 70
        and block.bbox.height < 60
        and block.bbox.width < 160
    )


def caption_for_picture(
    picture: DocumentBlock, captions: list[DocumentBlock]
) -> DocumentBlock | None:
    if picture.bbox is None:
        return None
    candidates = []
    for caption in captions:
        if caption.bbox is None or caption.bbox.top < picture.bbox.bottom - 2:
            continue
        vertical_gap = caption.bbox.top - picture.bbox.bottom
        if vertical_gap > 55:
            continue
        horizontal_overlap = max(
            0.0,
            min(picture.bbox.x1, caption.bbox.x1)
            - max(picture.bbox.x0, caption.bbox.x0),
        )
        if horizontal_overlap <= 0:
            continue
        candidates.append((vertical_gap, caption))
    return min(candidates, key=lambda item: item[0])[1] if candidates else None


class CollectImagesTask(PipelineTask):
    name = "07-collect-images"
    dependencies = ("01-docling",)

    def signature_payload(self, config: PipelineConfig) -> dict:
        value = super().signature_payload(config)
        value["image_logic_version"] = 2
        blocks = config.work_dir / "docling" / "blocks.json"
        if blocks.is_file():
            value["blocks_sha256"] = sha256_file(blocks)
        value["docling_pictures"] = {
            path.name: sha256_file(path)
            for path in sorted((config.work_dir / "docling" / "pictures").glob("*.png"))
        }
        return value

    def run(self, config: PipelineConfig) -> TaskResult:
        payload = read_json(config.work_dir / "docling" / "blocks.json")
        blocks = [DocumentBlock.from_dict(value) for value in payload["blocks"]]
        pictures_by_page: dict[int, list[DocumentBlock]] = defaultdict(list)
        captions_by_page: dict[int, list[DocumentBlock]] = defaultdict(list)
        ignored: list[str] = []
        for block in blocks:
            if block.kind == "caption":
                captions_by_page[block.page].append(block)
            elif block.kind == "picture" and block.bbox is not None:
                if is_repeated_header_image(block):
                    ignored.append(block.id)
                else:
                    pictures_by_page[block.page].append(block)

        work_root = config.work_dir / "images"
        assets_root = config.assets_dir / "images"
        assets_root.mkdir(parents=True, exist_ok=True)
        records: list[dict[str, Any]] = []
        image_index = 0
        for page, pictures in sorted(pictures_by_page.items()):
            for picture in sorted(pictures, key=lambda item: item.order):
                image_index += 1
                image_id = f"image-{image_index:04d}"
                destination = assets_root / f"{image_id}.png"
                source = config.work_dir / "docling" / "pictures" / f"{picture.id}.png"
                if not source.is_file():
                    raise ValueError(
                        f"Docling did not generate picture image for {picture.id}"
                    )
                copy_file(source, destination)
                caption = caption_for_picture(picture, captions_by_page[page])
                records.append(
                    {
                        "id": image_id,
                        "page": page,
                        "block_id": picture.id,
                        "order": picture.order,
                        "caption_block_id": caption.id if caption else None,
                        "caption": caption.markdown.strip() if caption else None,
                        "bbox": {
                            "coordinate_system": "pdfplumber_top_left",
                            "unit": "pt",
                            **picture.bbox.to_dict(),
                        },
                        "image": relativize(destination, config.output_dir),
                    }
                )
        manifest = work_root / "images.json"
        write_json(
            manifest,
            {"schema_version": 1, "images": records, "ignored_picture_blocks": ignored},
        )
        return TaskResult(
            self.name,
            "completed",
            [
                relativize(manifest, config.output_dir),
                *[str(record["image"]) for record in records],
            ],
            {"image_count": len(records), "ignored_repeated_headers": len(ignored)},
        )
