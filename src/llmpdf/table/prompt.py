from __future__ import annotations

import json
from pathlib import Path


def build_special_advisory_rules() -> str:
    return '''## Special advisory rules

### Advisory 1: Avoid repeated per-cell extraction

1. Avoid calling `crop().extract_text()` inside data-row or data-column loops.
2. Use `crop().extract_text()` only for already-located difficult cells, with at most 10 calls per logical table.
'''


def build_extraction_prompt(
    page: int,
    target: str,
    page_info: dict,
    *,
    confirmed_group_pages: list[int] | None = None,
    confirmed_group_page_infos: list[dict] | None = None,
    include_special_advisory_rules: bool = True,
) -> str:
    assets = Path("../assets")
    output_dir = Path(".")
    single_pdf = assets / f"page_{page:04d}.pdf"
    python_path = Path("../tools/python")
    pages = confirmed_group_pages or [page]
    grouped = len(pages) > 1
    if grouped and pages[0] != page:
        raise ValueError("The group leader must match page")
    if grouped and confirmed_group_page_infos is not None and len(confirmed_group_page_infos) != len(pages):
        raise ValueError("Each confirmed group page requires page information")
    page_list = ", ".join(str(value) for value in pages)
    task = (
        f"Task: faithfully extract the tables on pages {pages[0]}–{pages[-1]} of the PDF as CSV."
        if grouped
        else "Task: faithfully extract the tables on the specified PDF page as CSV."
    )
    group_instructions = ""
    if grouped:
        group_instructions = f'''
## Confirmed cross-page relationship

The continuous main tables on pages [{page_list}] have been confirmed as one mergeable table.

1. Write extract.py once and read the single-page PDFs for pages [{page_list}] from ../assets in page order.
2. Merge the continuous main table into one CSV, keeping the header only once. Do not create a separate table for each page.
3. Use page {pages[0]} to determine the header and column structure. Check later pages for changes in column boundaries, header position, and row rules; handle any changes separately within the same extract.py.

Prefer batch extraction when accuracy permits. First compare table geometry across pages. If column boundaries and row rules match, reuse one configuration and assign page text to rows and columns by coordinates after reading it in a single pass whenever possible. Use separate `crop()` checks only for pages with layout changes, cross-boundary text, or difficult cells. During validation, output only per-page row counts, first and last records, total column count, and a small number of anomalous samples.

4. High-resolution full-page images of pages {pages[0]} and {pages[-1]} are attached. PDFs, high-resolution images, and page information for all other pages are available in ../assets if needed.
5. If these pages contain independent tables that do not belong to the continuous main table, create separate tables for them.
6. When finished, run the shared batch command below once and check the column count, single header, first and last records on each page, and total row count.

Do not begin by processing only page {pages[0]}, and do not emit continuation pages as separate tables.
'''
    image_workflow = (
        "Inspect the attached full-page images of the first and last pages"
        if grouped
        else "Inspect the attached full-page image"
    )
    scope = "these pages" if grouped else "the page"
    pdf_inputs = "\n".join(
        f"- Single-page PDF: {assets / f'page_{value:04d}.pdf'}" for value in pages
    )
    image_input = (
        f"- Full-page images: pages {pages[0]} and {pages[-1]} are attached directly to this prompt"
        if grouped
        else "- Full-page image: attached directly to this prompt"
    )
    page_info_payload: object = (
        confirmed_group_page_infos
        if grouped and confirmed_group_page_infos is not None
        else page_info
    )
    prompt = f'''{task}

Base all decisions only on the PDF, full-page images, page information, and current outputs supplied for this task.
{group_instructions}

## Available tools

### `pdfplumber`

You may inspect page text and its coordinates, font sizes, rectangles, lines, edges, and candidate table structures.

## Workflow

1. Begin with a semantic assessment. Extract only real data tables with a header-to-record relationship. An abbreviation list, glossary, table of contents, contact list, map legend, or ordinary key-value list is not a table for this task, even when arranged neatly in two or more columns.
   If {scope} contains no real data table, do not call tools and do not create or modify any `table_*` directory. Return {{"tables": 0}} immediately and stop.
2. {image_workflow}, and use the page image to determine the approximate extent and row/column count of each table.
   A table is the data grid with header and record relationships; page titles, explanations, and notes are outside the table.
3. Use pdfplumber for inspection as needed, combining checks into one execution whenever possible. Inspection commands should output only a structural summary, the first three rows, the last row, and any potentially problematic middle rows.
4. Write a separate `table_<number>/extract.py` for every logical table on {scope}. If table content comes from a page image, extract it directly using the model's visual capabilities; do not use OCR.
5. After all `extract.py` files are written, run the shared batch command below exactly once. It runs all tables for this task and reports CSV structure, samples, anomalies, and a spatial-check summary. Do not separately read the CSV or metadata afterward.
6. Validate using the batch report and page image:
   1. `PASSED` means the spatial check found no anomaly, but you must still confirm the overall table structure against the page image.
   2. `REQUIRES_VISUAL_REVIEW` means the spatial check could not validate the table; it does not mean the table passed. Check row/column boundaries and merged cells against the page image.
   3. Modify the relevant `extract.py` and rerun the batch command only when you find a problem.

## Output

Number the logical tables on {scope} in reading order as `table_1`, `table_2`, and so on, with each table written to its corresponding directory.

If one source cell spans multiple CSV rows or columns, repeat the cell's complete content in every CSV cell it covers rather than leaving cells empty.

For each table, the AI only needs to write `table_<number>/extract.py`. Do not generate `metadata.yaml` yourself, and do not implement metadata writing, argument parsing, or spatial reference checks in the extraction code.

Running `extract.py` produces:

1. `output_1.csv`: the final CSV for the table.
2. `metadata.yaml`: generated automatically by the shared runtime.

Each `extract.py` handles exactly one corresponding table and can run independently.

Inside `extract.py`:

1. Set `TABLE_NAME` to the table title explicitly shown on the page. If no title is shown, use `None`; do not invent a name.
2. Set `TABLE_BBOX` to the approximate region containing the title, header, and data area. Use pdfplumber's top-left coordinate system in points. It need not be exact, but it must not include adjacent tables.
3. Extract the table in `extract_table()` and write `output_1.csv`.
4. Run the extractor through the shared `run_extractor()` function.

   Use this code template:
   ```python
   from pathlib import Path

   import pdfplumber

   from llmpdf.table.runtime import run_extractor


   TABLE_NAME: str | None = None
   TABLE_BBOX = (0.0, 0.0, 0.0, 0.0)


   def extract_table(pdf_path: Path, output_dir: Path) -> None:
       # Extract the table here and write output_1.csv.
       pass


   if __name__ == "__main__":
       run_extractor(
           extract_table,
           name=TABLE_NAME,
           bbox=TABLE_BBOX,
       )
   ```

## Task inputs

{pdf_inputs}
{image_input}
- Python with pdfplumber installed: {python_path}
- Output directory: {output_dir}

Target table: {target}

Page information:

Page-information fields:

- `display_width_pt`, `display_height_pt`, and `rotation`: displayed PDF dimensions and rotation of the current page.
- `chars`, `rects`, `lines`, `curves`, `images`, and `edges`: counts of text and graphic objects that describe the page structure.
- `full_width_px`, `full_height_px`, `full_dpi`, `scale_x`, and `scale_y`: dimensions and resolution of the attached full-page image and its coordinate conversion to the PDF.

```json
{json.dumps(page_info_payload, ensure_ascii=False, indent=2)}
```

The page dimensions, rotation, and coordinate conversion between the PDF and attached full-page image have already been calculated. Use them directly; do not estimate them again.

### `pdfplumber` example

```python
import pdfplumber

pdf_path = "{single_pdf}"

with pdfplumber.open(pdf_path) as pdf:
    page = pdf.pages[0]
    print(page.extract_words()[:20])
    print("rects:", len(page.rects))
    print("edges:", len(page.edges))
    print("tables:", len(page.find_tables()))
```

### Batch execution and validation

After writing every `extract.py`, run this command exactly once:

{python_path} -m llmpdf.table.run_all --pdf {single_pdf} --output-dir {output_dir} --spatial-check
'''
    if include_special_advisory_rules:
        prompt += "\n" + build_special_advisory_rules()
    return prompt


def build_continuation_prompt() -> str:
    return '''Task: process tables on the next page that may merge with existing results.

The single-page PDF, full-page image, and page information for the next page are attached.

1. Compare the headers, column structure, and context of this page's tables with existing tables to decide whether they can merge.
2. If they cannot merge, do not modify or create files. Return {"merge_with_previous": false} and stop.
3. If they can merge, reuse the existing extraction logic, process only this page, and append it to the corresponding table. Remove repeated headers and resolve cross-page line breaks. Process any other tables on this page that cannot merge as new tables.
4. Validate only the new data and merge boundary. Do not reread, print, or validate all prior data unless an anomaly is found.
5. When finished, return {"merge_with_previous": true}.

Do not ignore other new tables on a page merely because it contains a continuation table.
Do not treat abbreviation lists, glossaries, tables of contents, contact lists, map legends, or ordinary key-value lists as other new tables.
'''


def build_merge_plan_prompt(pages: list[int]) -> str:
    example = ", ".join(str(page) for page in pages)
    return f'''Task: determine only how the main tables on consecutive candidate pages should merge across pages.

Low-resolution full-page images are attached in page-number order. Make a visual decision only. Do not call tools, extract data, or create or modify files.

1. Compare the title, column structure, width, and row flow of tables on adjacent pages.
2. Put adjacent pages that can merge into one group; start a new group when they cannot merge.
3. Do not merge when uncertain.
4. Every page number must appear exactly once, and page order must not change.

Return JSON only. For example, if all pages merge:
{{"groups": [[{example}]]}}
'''


def build_confirmed_group_prompt(
    pages: list[int],
    target: str,
    page_infos: list[dict],
) -> str:
    if len(pages) < 2:
        raise ValueError("A confirmed group requires at least two pages")
    return build_extraction_prompt(
        pages[0],
        target,
        page_infos[0],
        confirmed_group_pages=pages,
        confirmed_group_page_infos=page_infos,
        include_special_advisory_rules=False,
    ) + f'''\nIf tables were extracted, return only this when finished:
{{"processed_pages": [{", ".join(str(page) for page in pages)}]}}

If the semantic assessment finds no real data table, follow the fast-exit requirement and return only:
{{"tables": 0}}
\n{build_special_advisory_rules()}'''
