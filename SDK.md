# Python SDK

`pdf_to_markdown.convert()` is the main PDF-to-Markdown entry point. The CLI calls the same SDK.

## Complete input options

```python
ConvertOptions(
    pdf="/absolute/input.pdf",                 # Required
    output_root="/absolute/results",           # Required
    batch_id=None,                              # Automatic UTC batch by default
    pages=None,                                 # All pages; or "1,3,8-12"
    model="gpt-5.6-sol",
    thinking="medium",
    detection_dpi=96,
    contact_sheet_size=8,
    agent_concurrency=5,                        # Global Agent limit, maximum 5
    find_concurrency=5,                         # Concurrent Find batches, maximum 5
    table_concurrency=5,
    image_concurrency=5,
    image_render_dpi=240,
    image_max_patches=10000,                   # Per-image 32x32 patch budget
    table_image_max_patches=30000,             # Table Agent image patch budget
    analyze_images=True,
    image_model="gpt-5.6-terra",
    image_thinking="medium",
    agent_timeout_seconds=1800.0,               # Up to 30 minutes per Pi call
    confidence_threshold=0.35,
    docling_options={},
    document_converter=None,
    document_converter_cache_key=None,
    retain_docling_tables=True,
    pdftoppm="pdftoppm",
    pi_executable=None,
    parse_table_executable=None,                # Deprecated external-runner compatibility
    keep_sessions=False,
    keep_work=False,                            # Minimal retention is the default
    show_progress=False,                        # Progress events on stderr
    force=False,
)
```

`agent_concurrency` and `find_concurrency` must be between 1 and 5. `table_concurrency`, `image_concurrency`, `agent_timeout_seconds`, `detection_dpi`, `image_render_dpi`, `image_max_patches`, `table_image_max_patches`, and `contact_sheet_size` must be positive. `confidence_threshold` must be between 0 and 1. `batch_id` must be a single path component. `pages` accepts a one-based expression such as `"1-25"` or `"1,3,8-12"`, or an iterable of positive integers. Duplicate pages are removed and source order is retained. Invalid, descending, empty, or out-of-range selections fail during configuration.

`image_render_dpi` is the desired maximum resolution. Before rendering each page, the converter calculates `ceil(width / 32) * ceil(height / 32)`. Image analysis uses `image_max_patches` (10,000 by default), while table extraction uses `table_image_max_patches` (30,000 by default). Pages above the applicable budget are rendered at the highest whole-number DPI that fits it; pages already within the budget keep the requested DPI.

Docling processes the smallest continuous physical-page range covering the selection, then all downstream content is filtered to the exact selected pages. Rendering, detection, table extraction, image analysis, Markdown output, review comparison, and table reruns use the same selection. Page markers retain original numbers; selecting pages 8–12 does not renumber them to 1–5. `assets/metadata.json` records the full `source.page_count` and `source.selected_pages`.

Set `show_progress=True` to write concise stage and job progress to standard error. It defaults to `False` for library use; the CLI enables it automatically.

In the built-in execution mode, all Agent work shares one scheduler with at most `agent_concurrency` workers. Ready tasks are selected by type in this order: Find, cross-page table, ordinary table, then image. Each type is FIFO and selection is repeated whenever a worker becomes free; running lower-priority work is not interrupted. `find_concurrency`, `table_concurrency`, and `image_concurrency` provide per-type limits within the global limit. The deprecated external `parse_table_executable` manages its own table subprocesses and does not use this queue.

The scheduler is dynamic: a completed Find batch immediately releases table groups whose left and right physical-page boundaries are known. A group at an unresolved batch boundary stays blocked. Image work is added after detection and uses any remaining slot only when no higher-priority ready task is waiting.

## Installation with uv

```bash
cd /path/to/pdf-to-markdown
uv sync --extra review --group dev --locked
```

The table extraction engine is included in this project; no neighboring `parse-table` project is required. `pyproject.toml` declares compatible package ranges, while `uv.lock` pins the complete dependency set used for development, testing, and deployment.

## Basic usage

```python
from pathlib import Path

from pdf_to_markdown import ConvertOptions, convert

result = convert(
    ConvertOptions(
        pdf=Path("/absolute/input.pdf"),
        output_root=Path("/absolute/results"),
        pages="1-25",
        table_concurrency=5,
    )
)

print(result.output_markdown)
print(result.metadata)
print(result.total_tokens)
print(result.estimated_price_usd)
```

When `batch_id=None`, the SDK creates a UTC timestamp batch. After a failure, retry with the `batch_id` from the exception to reuse completed work. After a successful conversion, reproducible work files are removed by default. Set `keep_work=True` only when raw Agent logs, renderings, and other debugging intermediates are required.

## Retaining original Docling tables

By default, `output.md` contains only the AI-extracted table while the original Docling table is preserved as a final artifact for audit and comparison:

```python
result = convert(
    ConvertOptions(
        pdf="/absolute/input.pdf",
        output_root="/absolute/results",
        retain_docling_tables=True,
    )
)
```

Original table Markdown is written to `assets/docling-tables/docling-table-XXXX.md` but is not inserted into `output.md` again. In `assets/metadata.json`:

- each AI table's `lineage` records the matching Docling table and match method;
- `docling_tables` records the replacement AI table ID, retained file, and mapping status in reverse;
- a merged cross-page AI table can map to multiple paginated Docling tables;
- ambiguous matches use `ambiguous_continuation` instead of forcing a mapping.

With `retain_docling_tables=False`, mapping metadata remains available but `assets/docling-tables/` is omitted. Rerunning the same batch also removes previously retained original-table artifacts. The CLI enables retention by default and exposes `--no-retain-docling-tables` to disable it.

## Docling JSON configuration

Default values are:

```json
{
  "do_ocr": false,
  "images_scale": 2.0,
  "generate_picture_images": true
}
```

Override only the required fields through `docling_options`:

```python
result = convert(
    ConvertOptions(
        pdf="/absolute/input.pdf",
        output_root="/absolute/results",
        docling_options={
            "do_ocr": True,
            "images_scale": 3.0,
            "ocr_options": {"lang": ["de", "en"]},
        },
    )
)
```

Dictionaries merge recursively, scalar values replace defaults, lists replace complete lists, and JSON `null` becomes Python `None`. Effective options are written to `work/docling/effective-options.json` and included in cache validation. Unknown top-level fields or values rejected by Docling cause conversion to fail.

The CLI accepts the same configuration from a JSON file:

```bash
uv run pdf-to-markdown convert /absolute/input.pdf \
  --output-dir /absolute/results \
  --docling-options-file /absolute/docling.json
```

There is no separate OCR argument. Enable or disable OCR through the Docling JSON configuration.

Disabling OCR only disables text recognition. The default `do_table_structure=true` setting and layout analysis still need local Docling model artifacts. An online first run downloads them automatically. For offline deployment, download them first:

```bash
docling-tools models download layout tableformer --output-dir /absolute/models
```

Then use `docling_options={"artifacts_path": "/absolute/models"}` or set `DOCLING_ARTIFACTS_PATH`.

## Injecting a DocumentConverter

Advanced integrations can reuse an existing Docling converter:

```python
from docling.document_converter import DocumentConverter
from pdf_to_markdown import ConvertOptions, convert

converter = DocumentConverter(...)
result = convert(
    ConvertOptions(
        pdf="/absolute/input.pdf",
        output_root="/absolute/results",
        document_converter=converter,
        document_converter_cache_key="company-docling-v3",
    )
)
```

- `document_converter` and a non-empty `docling_options` cannot be used together.
- Built-in Docling defaults are not applied to an injected instance.
- Without `document_converter_cache_key`, the Docling stage does not read cached results.
- The CLI cannot inject a Python object.

## Result object

`ConversionResult` includes:

- `output_dir`, `output_markdown`, `assets_dir`, `metadata`, `metrics`, and `validation_report`;
- `table_count`, `image_count`, `docling_table_count`, `retained_docling_table_count`, and `elapsed_seconds`;
- `table_lineage`, a bidirectional mapping summary for AI and original Docling tables;
- `completed` or `cached` status for each stage;
- `usage.total`, `usage.detection`, `usage.table_extraction`, and `usage.image_analysis`;
- `usage.image_pages`, with per-page image-analysis calls, duration, image count, and status;
- per-batch detection and per-table-page usage details;
- `billing.pi_api_price_estimate_usd`.

```python
print(result.usage.total.noncached_input_tokens)
print(result.usage.total.cached_input_tokens)
print(result.usage.total.output_tokens)
print(result.usage.total.total_tokens)
print(result.billing.pi_api_price_estimate_usd)
print(result.billing.actual_openai_charge_usd)  # Usually None with Codex login
```

`pi_api_price_estimate_usd` is an estimate from the Pi log's pricing table. It is not an actual Codex subscription or credit charge.

Use `result.to_dict()` for a fully JSON-serializable result. Path attributes on `ConversionResult` remain absolute `Path` objects that can be accessed directly. Paths in `to_dict()` and CLI JSON output are POSIX paths relative to the current PDF result directory. The payload therefore contains `path_base: "output_dir"`, `output_dir: "."`, `output_markdown: "output.md"`, and `metadata: "assets/metadata.json"`.

See `uv run pdf-to-markdown convert --help` for all corresponding CLI options. `--docling-options-file` maps to `docling_options`, `--keep-sessions` to `keep_sessions`, `--keep-work` to `keep_work`, `--agent-timeout-seconds` to `agent_timeout_seconds`, and `--retain-docling-tables` to `retain_docling_tables`. `document_converter` and its cache key are SDK-only options.

## Regenerating tables from a minimal result

```python
from pathlib import Path

from pdf_to_markdown import rerun_tables

summary = rerun_tables(Path("/absolute/path/to/result"))
print(summary["table_count"], summary["total_tokens"])
```

The source PDF must still exist and match the SHA-256 recorded by the original conversion. The function reruns table extraction without rerunning image analysis or table detection, rebuilds Markdown, updates metrics, validates the result, and returns the directory to minimal retention.

## Errors and recovery

```python
from dataclasses import replace

from pdf_to_markdown import ConfigurationError, TaskExecutionError

try:
    result = convert(options)
except ConfigurationError as error:
    print(error.to_dict())
except TaskExecutionError as error:
    print(error.failed_task, error.output_dir)
    if error.batch_id:
        result = convert(replace(options, batch_id=error.batch_id))
```

Unknown stage errors are raised as `TaskExecutionError`, with the original exception available as `error.__cause__`. The converter does not call another agent to diagnose failures. Completed work and failure state are preserved. Session files are preserved only when `keep_sessions=True`.

Before conversion starts, preflight validation checks that the PDF is readable, the output directory is writable, and required components such as Docling, `pdftoppm`, and Pi/Node.js are available. A failed preflight check does not start conversion or an agent.

## Complete example

```python
from pdf_to_markdown import ConvertOptions, convert

result = convert(
    ConvertOptions(
        pdf="/absolute/input/e_dis.pdf",
        output_root="/absolute/results",
    )
)

print(result.to_dict())
```
