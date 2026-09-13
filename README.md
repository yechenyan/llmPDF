# llmPDF

Convert PDFs into Markdown that is suitable for reading, search, and downstream processing. The tool preserves document structure, extracts tables, and can analyze images and charts. A local review UI lets you compare the generated Markdown with the source PDF.

Use `llmpdf convert` for normal operation. One command runs the complete conversion.

## Requirements

- Python 3.11–3.14
- [uv](https://docs.astral.sh/uv/)
- Poppler's `pdftoppm`
- Node.js 20.6+ when using the default Pi backend
- A working Codex/Pi login, or an installed and authenticated Claude Code CLI
- A network connection

Install Poppler on macOS with Homebrew:

```bash
brew install poppler
```

On Ubuntu or Debian, install `poppler-utils`:

```bash
sudo apt-get install poppler-utils
```

The first run may download document-analysis models and can take longer than later runs.

## Installation

From the project directory, run:

```bash
uv sync --extra review --locked
```

This installs both the converter and the optional local review UI.

Verify the installation:

```bash
uv run llmpdf --help
uv run llmpdf convert --help
```

## Quick start

```bash
uv run llmpdf convert \
  "/absolute/path/to/input.pdf" \
  --output-dir "/absolute/path/to/results"
```

On success, the command prints JSON to standard output. It includes `output_markdown`, `output_dir`, token usage, and elapsed time.

The default result directory is:

```text
<output-dir>/<UTC batch timestamp>/<PDF filename>/
```

Set a stable batch name when you need a predictable path or resumable retries:

```bash
uv run llmpdf convert \
  "/absolute/path/to/input.pdf" \
  --output-dir "/absolute/path/to/results" \
  --batch-id "edis-2024"
```

After a failure, rerun with the same PDF, `--output-dir`, and `--batch-id`. Completed results that remain valid are reused. Use `--force` only when every stage must run again.

To process selected pages while retaining their original PDF page numbers:

```bash
uv run llmpdf convert \
  "/absolute/path/to/input.pdf" \
  --output-dir "/absolute/path/to/results" \
  --pages "1,3,8-12"
```

Only selected pages are rendered, detected, analyzed, and written to `output.md`. Docling receives the smallest continuous physical-page range covering the selection, then its output is filtered to the exact selected pages. For example, `8,9` processes only pages 8–9 in Docling, while `1,3,8-12` processes pages 1–12 in Docling and retains only the requested pages downstream. Metadata records both the complete PDF page count and `selected_pages`; the review comparison displays only those physical pages.

During conversion, concise progress is written to standard error: pipeline stages, rendered-page counts, detection batches, and completed table/image Agent tasks. The final machine-readable result remains the only content written to standard output.

Docling runs in an isolated child process. If that process fails during PDF parsing, the converter keeps the main pipeline alive and retries that PDF once with one Docling parsing thread and batch sizes of one. This fallback is part of the core converter, so it applies equally to `convert`, `run-all`, SDK-driven conversions, and directory batches. User-supplied Docling options, including OCR settings, are retained during the fallback. A second failure is reported normally and can be resumed by running the same command again.

All Agent work uses one dynamic scheduler with at most five workers. Whenever a worker becomes free, it selects the first ready task type in this order: Find, cross-page table, ordinary table, image. Completed Find batches can release bounded table groups before the remaining Find batches finish. Tasks with unresolved neighboring-page dependencies remain blocked rather than being extracted prematurely.

## Agent backend: Pi and Claude Code

Pi is the default backend. Set `--agent-backend claude-code` to use Claude Code
for every Agent task: Find page detection, ordinary and cross-page table
extraction, and image/chart analysis. The PDF conversion pipeline, shared
concurrency limits, output validation, and failure recovery work with both backends.

### Use Claude Code

First make sure the Claude Code CLI is installed, authenticated, and can make
model calls. If it is available in your terminal, verify its location with
`command -v claude` and its version with `claude --version`.

```bash
uv run llmpdf convert input.pdf \
  --output-dir results \
  --agent-backend claude-code
```

If `claude` is outside PATH, pass `--claude-executable`. This also works with
Claude Code bundled inside the Claude desktop application; point to its CLI
executable, rather than the desktop application itself. Quote paths containing
spaces. Relative executable paths are resolved before the job changes directories.

```bash
uv run llmpdf convert input.pdf \
  --output-dir results \
  --agent-backend claude-code \
  --claude-executable "/path/to/Claude Code/claude"
```

The existing Pi model defaults are ignored on Claude Code, letting Claude choose
its default model. To choose models explicitly, use a Claude alias or full model ID:

```bash
uv run llmpdf convert input.pdf \
  --output-dir results \
  --agent-backend claude-code \
  --model sonnet \
  --image-model sonnet
```

The same backend and executable options are available in `llmpdf-table` and
`run-all`. Forward them to directory conversions after `--`:

```bash
uv run llmpdf convert-dir pdf-root --jobs 2 -- --agent-backend claude-code
```

### Python SDK

```python
from llmpdf import ConvertOptions, convert

result = convert(ConvertOptions(
    pdf="input.pdf",
    output_root="results",
    agent_backend="claude-code",
    # claude_executable="/path/to/claude",  # Optional when claude is in PATH.
))
```

### Regenerate tables

Table regeneration retains the selected backend. Executable locations are
runtime settings and are not stored in the run manifest or other configuration
artifacts. If Claude is outside PATH, supply its location again when regenerating:

```bash
uv run llmpdf rerun-tables results/batch/input \
  --claude-executable "/path/to/Claude Code/claude"
```

### Compatibility details

- Thinking and transport settings are Pi-only and are ignored by Claude Code.
- Claude token and cost accounting is not collected. Existing usage fields remain
  zero, meaning **unreported usage**, not free model calls.
- Images are passed directly as image content. Table jobs can read/write files
  and execute commands. Failed calls and invalid outputs fail conversion normally.
- When work is retained, Claude responses are saved beside the compatible Agent
  logs, with runtime host paths converted to relative references.
- `--keep-sessions` enables Claude's own session persistence. Those sessions are
  managed by Claude Code, separately from llmPDF's retained Pi session files.

## `convert` parameters

Syntax:

```text
llmpdf convert PDF --output-dir OUTPUT_DIR [OPTIONS]
```

### Input and output

| Parameter | Default | Description |
| --- | --- | --- |
| `PDF` | Required | Input PDF path. |
| `--output-dir PATH` | Required | Root directory for all batch results. |
| `--batch-id NAME` | UTC timestamp | Batch directory name. It must be a single path component and remain unchanged for retries. |
| `--pages PAGES` | All pages | One-based PDF pages, such as `1-25` or `1,3,8-12`. Pages are de-duplicated and processed in source order. |
| `--force` | Disabled | Ignore existing results and run the conversion again. |
| `--retain-docling-tables` / `--no-retain-docling-tables` | Enabled | Preserve original Docling table Markdown for review. |

### Table and page detection

| Parameter | Default | Description |
| --- | --- | --- |
| `--model MODEL` | `gpt-5.6-sol` | Model used for table detection and extraction. |
| `--thinking LEVEL` | `medium` | Reasoning effort for table operations, such as `low`, `medium`, or `high`. |
| `--detection-dpi N` | `96` | Page-rendering DPI for detection. Higher values improve detail but cost more time and memory. |
| `--contact-sheet-size N` | `8` | Number of PDF pages per detection contact sheet. |
| `--agent-concurrency N` | `5` | Maximum concurrent Agent tasks across all task types. Must be between `1` and `5`. |
| `--find-concurrency N` | `5` | Maximum concurrent Find Agent tasks within the global limit. |
| `--confidence-threshold N` | `0.35` | Minimum candidate confidence, from `0` to `1`. |
| `--table-concurrency N` | `5` | Table-processing concurrency. Must be greater than `0`. |
| `--table-image-max-patches N` | `30000` | Maximum 32x32-pixel patches per table Agent image. Oversized table pages lower only their own render DPI. |

### Images and charts

| Parameter | Default | Description |
| --- | --- | --- |
| `--analyze-images` / `--no-analyze-images` | Enabled | Analyze meaningful images and charts. |
| `--image-model MODEL` | `gpt-5.6-terra` | Model used for image analysis. |
| `--image-thinking LEVEL` | `medium` | Reasoning effort for image analysis. |
| `--image-render-dpi N` | `240` | Page-rendering DPI for image analysis. |
| `--image-max-patches N` | `10000` | Maximum 32x32-pixel patches per image-analysis model image. Oversized pages automatically use a lower DPI; normal pages keep `--image-render-dpi`. |
| `--image-concurrency N` | `5` | Image-processing concurrency. Must be greater than `0`. |

When an image is a readable chart, the tool attempts to produce structured table data. Visually estimated results are prefixed with “AI visual extraction; values may be inaccurate”. If values cannot be read reliably, the result keeps the image description and does not invent table data.

### Runtime and advanced options

| Parameter | Default | Description |
| --- | --- | --- |
| `--agent-timeout-seconds N` | `1800` | Timeout for each model call, in seconds. |
| `--pdftoppm PATH` | `pdftoppm` | Command name or executable path for `pdftoppm`. |
| `--agent-backend BACKEND` | `pi` | Agent runtime for all task types: `pi` or `claude-code`. |
| `--claude-executable PATH` | `claude` in PATH | Claude Code CLI location; used only with `claude-code`. |
| `--pi-executable PATH` | Auto-detected | Explicit Pi executable path. Normally unnecessary. |
| `--docling-options-file PATH` | None | JSON file containing additional document-analysis options. |
| `--keep-sessions` | Disabled | Keep model session files for troubleshooting. |
| `--keep-work` | Disabled | Keep all intermediate files after success. By default, reproducible work files are removed after metrics and validation are complete. |
| `--llmpdf-table-executable PATH` | None | Deprecated compatibility option; avoid it in new integrations. |

Use the built-in help as the authoritative parameter reference for the installed version:

```bash
uv run llmpdf convert --help
```

## Common commands

### Convert a directory of PDFs

Use `convert-dir` to recursively find PDFs and convert them in place:

```bash
uv run llmpdf convert-dir "/absolute/path/to/pdf-root" --jobs 3
```

Each PDF remains beside its generated `output.md`, `assets/`, and `work/` directories. Because these names are shared, in-place mode requires exactly one PDF in each containing directory. Directories containing multiple PDFs are reported as invalid instead of overwriting one result with another.

The directory command skips a PDF only when all of the following are true: `work/status.json` is completed, validation passed, `output.md` is non-empty, and the source SHA-256 in `assets/metadata.json` still matches the PDF. Interrupted, failed, stale, or incomplete results are resumed using valid stage caches.

All PDF workers start immediately. To process up to three PDFs concurrently, run:

```bash
uv run llmpdf convert-dir "/absolute/path/to/pdf-root" \
  --jobs 3
```

When a running PDF finishes, its slot starts the next queued PDF immediately. Status heartbeats are printed every 30 seconds. Combined output is appended to `<pdf-root>/log.md` and synchronized to disk every 300 lines; use `--log-file PATH` to choose another file.

Preview the decisions without converting anything:

```bash
uv run llmpdf convert-dir "/absolute/path/to/pdf-root" --dry-run
```

Options for each individual conversion can be forwarded after `--`:

```bash
uv run llmpdf convert-dir "/absolute/path/to/pdf-root" \
  --jobs 2 -- --no-analyze-images
```

Use `--no-recursive` to inspect only PDFs directly inside the specified directory. The process exits nonzero if any PDF fails or any directory contains multiple PDFs.

The final directory layout is:

```text
<pdf-root>/
├── log.md
└── company-or-document/
    ├── source.pdf
    ├── output.md
    ├── assets/
    └── work/
```

### Other examples

Process text and tables without analyzing images:

```bash
uv run llmpdf convert input.pdf \
  --output-dir results \
  --batch-id text-and-tables \
  --no-analyze-images
```

Reduce concurrency on a resource-constrained machine:

```bash
uv run llmpdf convert input.pdf \
  --output-dir results \
  --batch-id low-load \
  --agent-concurrency 2 \
  --find-concurrency 2 \
  --table-concurrency 2 \
  --image-concurrency 2
```

For a scanned document, create `docling-options.json` to enable OCR:

```json
{
  "do_ocr": true,
  "images_scale": 2.0,
  "ocr_options": {
    "lang": ["de", "en"]
  }
}
```

Then run:

```bash
uv run llmpdf convert scanned.pdf \
  --output-dir results \
  --batch-id scanned-document \
  --docling-options-file docling-options.json
```

Line breaks inside table cells are converted to spaces. Final Markdown does not use `<br>` for cell-internal line breaks.

## Output files

A successful conversion produces:

```text
<output-dir>/<batch-id>/<pdf-stem>/
├── output.md
├── assets/
│   ├── metadata.json
│   ├── tables/
│   ├── images/
│   ├── chart-tables/
│   └── docling-tables/
└── work/
    ├── status.json
    ├── metrics.json
    ├── run-manifest.json
    ├── run-blocks.json.gz
    ├── table-code/             # Agent-authored table extractors
    ├── diagnostics/
    └── review/                 # Created only after review activity
```

- `output.md`: final Markdown.
- `assets/tables/`: extracted table CSV files.
- `assets/images/`: images retained from the PDF.
- `assets/chart-tables/`: CSV files extracted from charts.
- `assets/metadata.json`: page, source, and artifact relationships.
- `work/status.json`: live and final run state, elapsed time, pipeline and Agent task counts, token usage, and Pi's API-price estimate.
- `work/metrics.json`: elapsed time, token usage, and status for the full run and individual model calls.
- `work/run-manifest.json`: source, model, and candidate-page information required to regenerate tables.
- `work/run-blocks.json.gz`: compressed document structure used when regenerated tables are merged back into Markdown.
- `work/table-code/`: the generated `extract.py` for every table plus a compact page-mapping index.
- `work/diagnostics/`: troubleshooting information.

### Run status

`work/status.json` shows whether a conversion is `running`, `completed`, or `failed`. It is updated during the run and retained after cleanup. It also records elapsed time, successful and failed task counts, token usage, and Pi's estimated API cost.

Example:

```json
{
  "status": "completed",
  "elapsed_seconds": 130.0,
  "pipeline": {
    "successful_tasks": 11,
    "failed_tasks": 0
  },
  "agents": {
    "successful_tasks": 12,
    "failed_tasks": 0
  },
  "usage": {
    "total_tokens": 1700,
    "pi_api_price_estimate_usd": 0.012345
  }
}
```

`pi_api_price_estimate_usd` is an estimate for comparing runs, not the actual charge against a Codex plan. See `work/metrics.json` for detailed timing and token information.

Successful conversions use minimal retention by default. Raw Agent logs, page renders, single-page PDFs, Docling work files, and duplicate table/image intermediates are removed only after validation passes and metrics are written. Failed conversions keep their work files so the same batch can resume. Use `--keep-work` for a successful diagnostic run that must retain all intermediates. `--keep-sessions` also prevents minimal cleanup.

Artifact paths in `output.md` and metadata are POSIX paths relative to the current result directory, so the entire directory can be moved. Keep `output.md`, `assets/`, and `work/` together. The source PDF must remain available at the path recorded in `assets/metadata.json` for review and table regeneration.

Large images created from full-page vision analysis are stored as WebP at their original pixel dimensions when that representation is smaller. This changes encoding, not resolution.

## Regenerate tables

Regenerate table extraction from a successful minimal result:

```bash
uv run llmpdf rerun-tables \
  "/absolute/path/to/results/edis-2024/input"
```

The command verifies the source PDF against the saved SHA-256, reruns all table candidates, rebuilds table assets and `output.md`, validates the result, updates metrics, and restores minimal retention. Image analysis and table detection are not rerun. Table regeneration is rejected after review decisions have been saved, preventing accidental loss of review work.

## Manual review

Start the local review UI:

```bash
uv run --extra review llmpdf review \
  --result "/absolute/path/to/results/edis-2024/input"
```

The UI opens at `http://127.0.0.1:8765/` by default. Its document-comparison view displays the PDF on the left and generated Markdown on the right, with synchronized scrolling, a collapsible sidebar, and PDF zoom controls.
Selecting a PDF and table updates the URL with the PDF's absolute source path and
the table ID. These links can be copied, refreshed, and navigated with the
browser's back and forward buttons while the same review project is running.

Load multiple results by repeating `--result`:

```bash
uv run --extra review llmpdf review \
  --result "/path/to/result-a" \
  --result "/path/to/result-b"
```

You can also load a complete batch or a review-project file:

```bash
uv run --extra review llmpdf review --batch "/path/to/batch"
uv run --extra review llmpdf review --project review-project.json
```

### `review` parameters

| Parameter | Default | Description |
| --- | --- | --- |
| `--result PATH` | None | Load a result directory. May be repeated. |
| `--batch PATH` | None | Load a batch directory. May be repeated. |
| `--project PATH` | None | Load a review-project JSON file. |
| `--host HOST` | `127.0.0.1` | Listening address. |
| `--port N` | `8765` | Listening port. |
| `--no-open` | Disabled | Start the server without opening a browser. |

The review UI operates on local results and does not make model calls.

## AI and automation usage

For non-interactive calls, always provide absolute input and output paths and a stable `--batch-id`:

```bash
uv run llmpdf convert \
  "/data/in/report.pdf" \
  --output-dir "/data/out" \
  --batch-id "report-2026-09-03"
```

Calling programs should follow these conventions:

1. Use the process exit code to determine success or failure.
2. On success, parse JSON from standard output and read the final Markdown path from `output_markdown`.
3. On failure, parse JSON from standard error. If it contains `batch_id` or `recovery`, retry with the original parameters.
4. Do not copy `output.md` without its referenced resources; preserve at least `assets/` with it.
5. Read `work/metrics.json` for cost reporting or slow-call diagnostics.

## Python SDK

```python
from pathlib import Path

from llmpdf import ConvertOptions, convert

result = convert(
    ConvertOptions(
        pdf=Path("/data/in/report.pdf"),
        output_root=Path("/data/out"),
        batch_id="report-2026-09-03",
        pages="1-25",
        analyze_images=True,
        agent_concurrency=5,
        find_concurrency=5,
        table_concurrency=5,
        image_concurrency=5,
    )
)

print(result.output_markdown)
print(result.total_tokens)
print(result.elapsed_seconds)
```

Path attributes on the SDK result are absolute `Path` objects that can be accessed directly. Artifact paths in `result.to_dict()` are relative to the result directory for portability. See [SDK.md](SDK.md) for all fields and exception types.

## Troubleshooting

### `pdftoppm` is not found

Install Poppler and verify the command:

```bash
pdftoppm -v
```

If the executable is outside `PATH`, pass `--pdftoppm /absolute/path/to/pdftoppm`.

### Model authentication fails

For Pi, confirm that the local Codex/Pi login is valid and that network access
works. Use `--pi-executable` for a custom executable location.

For Claude Code, confirm that the selected CLI can make model calls independently.
Use `--claude-executable` if it is outside PATH. Authentication, account balance,
and model access errors from Claude are reported as conversion failures.

### Conversion times out

Complex pages and high-resolution images may need more time. Increase `--agent-timeout-seconds`, or lower `--agent-concurrency` when local resources are limited.

### Resume after a failure

Run the same command with the same `--batch-id`. A new batch name starts a new conversion.

### Regenerate everything

Keep the same path and add `--force`:

```bash
uv run llmpdf convert input.pdf \
  --output-dir results \
  --batch-id existing-batch \
  --force
```

## License

llmPDF is distributed under the [llmPDF Limited Use License 1.0](LICENSE).
Unmodified copies may be used for any purpose, including commercial use, and
may be redistributed with other software when the required attribution is
provided. Modification, adaptation, and derivative works are not permitted.
