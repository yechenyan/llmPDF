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
    layout_pages = list(dict.fromkeys((pages[0], pages[min(1, len(pages) - 1)], pages[-1])))
    layout_page_list = ", ".join(str(value) for value in layout_pages)
    layout_names = "first and last" if len(pages) == 2 else "first, middle, and last"
    bbox_roles = ['       "first": (0.0, 0.0, 0.0, 0.0),']
    if len(pages) > 2:
        bbox_roles.append('       "middle": (0.0, 0.0, 0.0, 0.0),')
    bbox_roles.append('       "last": (0.0, 0.0, 0.0, 0.0),')
    bbox_role_lines = "\n".join(bbox_roles)
    task = (
        f"Task: faithfully extract the tables on pages {pages[0]}–{pages[-1]} of the PDF as CSV."
        if grouped
        else "Task: faithfully extract the tables on the specified PDF page as CSV."
    )
    group_instructions = ""
    if grouped:
        group_instructions = f'''
## Confirmed cross-page table

Pages [{page_list}] contain one continuous table. The first page may be partially white because earlier content was already processed; ignore white regions.

Extract visible independent tables on the first page and the continuous table across this group. Merge the continuous table into one CSV with one header. On the last page, stop at the end of the continuous table and ignore everything below it.

Images of pages {layout_page_list} represent the {layout_names} layouts. For the continuous table, set `SOURCE_PAGES` and `PAGE_BBOXES` using those layouts. All group PDFs, images, and page information are available in `../assets`.
'''
    image_workflow = (
        f"Inspect the attached full-page images of the {layout_names} layouts"
        if grouped
        else "Inspect the attached full-page image"
    )
    scope = "these pages" if grouped else "the page"
    pdf_inputs = "\n".join(
        f"- Single-page PDF: {assets / f'page_{value:04d}.pdf'}" for value in pages
    )
    image_input = (
        f"- Full-page images: pages {layout_page_list} are attached directly to this prompt"
        if grouped
        else "- Full-page image: attached directly to this prompt"
    )
    page_info_payload: object = (
        confirmed_group_page_infos
        if grouped and confirmed_group_page_infos is not None
        else page_info
    )
    bbox_instruction = (
        "2. Set `SOURCE_PAGES` to every physical page occupied by the table and set "
        "`PAGE_BBOXES` for its first, middle, and last layouts. A single-page table "
        "uses only `first`; a two-page table uses `first` and `last`."
        if grouped
        else "2. Set `TABLE_BBOX` to the approximate region containing the title, header, and data area. Use pdfplumber's top-left coordinate system in points. It need not be exact, but it must not include adjacent tables."
    )
    extractor_template = (
        f'''   ```python
   from pathlib import Path

   import pdfplumber

   from llmpdf.table.runtime import run_extractor


   TABLE_NAME: str | None = None
   SOURCE_PAGES = [{page_list}]
   PAGE_BBOXES = {{
{bbox_role_lines}
   }}


   def extract_table(pdf_path: Path, output_dir: Path) -> None:
       # Extract the table here and write output_1.csv.
       pass


   if __name__ == "__main__":
       run_extractor(
           extract_table,
           name=TABLE_NAME,
           source_pages=SOURCE_PAGES,
           page_bboxes=PAGE_BBOXES,
       )
   ```'''
        if grouped
        else '''   ```python
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
   ```'''
    )
    prompt = f'''{task}

Base all decisions only on the PDF, full-page images, page information, and current outputs supplied for this task.
Ignore regions marked `IGNORE` in a page image; they were already processed, including the matching content in the PDF.
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

1. Set `TABLE_NAME` to the table title explicitly shown on the page. Titles inside the table border must also remain in the CSV as header rows, repeating merged cells as required. If no title is shown, use `None`; do not invent a name.
{bbox_instruction}
3. Extract the table in `extract_table()` and write `output_1.csv`.
4. Run the extractor through the shared `run_extractor()` function.

   Use this code template:
{extractor_template}

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
    overlap_example = ""
    if len(pages) >= 3:
        boundary = len(pages) // 2
        left = ", ".join(str(page) for page in pages[: boundary + 1])
        right = ", ".join(str(page) for page in pages[boundary:])
        overlap_example = f'''If page {pages[boundary]} is a transition page:
{{"groups": [[{left}], [{right}]]}}
'''
    return f'''Task: determine only how the main tables on consecutive candidate pages should merge across pages.

Contact sheets of low-resolution full-page images are attached in page-number order. Each thumbnail is labeled with its PDF page number. Make a visual decision only. Do not call tools, extract data, or create or modify files.

1. Compare the title, column structure, width, and row flow of tables on adjacent pages.
2. Return one continuous page group for each cross-page table.
3. Adjacent groups may share one boundary page when that page contains the end of one table and the start of another. No other overlap is allowed.
4. Every candidate page must appear at least once. Keep pages and groups in reading order. Do not merge when uncertain.

Return JSON only. For example, if all pages merge:
{{"groups": [[{example}]]}}
{overlap_example}'''


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
