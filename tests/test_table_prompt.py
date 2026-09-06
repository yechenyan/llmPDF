import unittest

from llmpdf.table.prompt import (
    build_confirmed_group_prompt,
    build_continuation_prompt,
    build_extraction_prompt,
    build_merge_plan_prompt,
)


class PromptTest(unittest.TestCase):
    def test_paths_and_clean_runtime_template(self) -> None:
        prompt = build_extraction_prompt(16, "the only table on this page", {"physical_page": 16})
        self.assertIn("Task: faithfully extract the tables on the specified PDF page as CSV.", prompt)
        self.assertIn("../assets/page_0016.pdf", prompt)
        self.assertIn("../tools/python", prompt)
        self.assertIn("Output directory: .", prompt)
        self.assertIn("Inspection commands should output only a structural summary", prompt)
        self.assertIn("Begin with a semantic assessment", prompt)
        self.assertIn("abbreviation list, glossary, table of contents, contact list, map legend, or ordinary key-value list", prompt)
        self.assertIn('Return {"tables": 0} immediately and stop', prompt)
        self.assertIn("do not create or modify any `table_*` directory", prompt)
        self.assertIn("## Special advisory rules", prompt)
        self.assertIn("### Advisory 1: Avoid repeated per-cell extraction", prompt)
        self.assertIn("Avoid calling `crop().extract_text()` inside data-row or data-column loops", prompt)
        self.assertIn("at most 10 calls per logical table", prompt)
        self.assertTrue(prompt.rstrip().endswith("at most 10 calls per logical table."))
        self.assertIn("from llmpdf.table.runtime import run_extractor", prompt)
        self.assertIn("--spatial-check", prompt)
        self.assertIn("-m llmpdf.table.run_all", prompt)
        self.assertIn("Do not separately read the CSV or metadata afterward", prompt)
        self.assertIn("REQUIRES_VISUAL_REVIEW", prompt)
        self.assertIn("Do not generate `metadata.yaml` yourself", prompt)
        self.assertIn(
            "Titles inside the table border must also remain in the CSV as header rows, repeating merged cells as required.",
            prompt,
        )
        self.assertNotIn("run_reference_check", prompt)
        self.assertNotIn("subprocess", prompt)
        self.assertNotIn("/Users/", prompt)
        self.assertNotIn("write_and_run_extract", prompt)

    def test_continuation_prompt_is_short_and_handles_all_tables(self) -> None:
        prompt = build_continuation_prompt()
        self.assertIn('{"merge_with_previous": false}', prompt)
        self.assertIn('{"merge_with_previous": true}', prompt)
        self.assertNotIn("source_pages", prompt)
        self.assertIn("other tables on this page that cannot merge", prompt)
        self.assertIn("Do not treat abbreviation lists, glossaries", prompt)
        self.assertNotIn("current physical page", prompt)
        self.assertNotIn("current output directory", prompt)

    def test_long_chain_prompts_keep_planning_and_parsing_separate(self) -> None:
        planning = build_merge_plan_prompt([25, 26, 27])
        self.assertIn('"groups"', planning)
        self.assertIn("Do not call tools", planning)
        parsing = build_confirmed_group_prompt(
            [25, 26, 27],
            "all real data tables",
            [{"physical_page": page} for page in [25, 26, 27]],
        )
        self.assertIn("pages 25–27 of the PDF as CSV", parsing)
        self.assertIn("Pages [25, 26, 27] contain one continuous table", parsing)
        self.assertIn("run the shared batch command below exactly once", parsing)
        self.assertIn("Images of pages 25, 26, 27 represent the first, middle, and last layouts", parsing)
        self.assertIn("Extract visible independent tables on the first page", parsing)
        self.assertIn("stop at the end of the continuous table", parsing)
        self.assertIn("SOURCE_PAGES = [25, 26, 27]", parsing)
        self.assertIn('"middle": (0.0, 0.0, 0.0, 0.0)', parsing)
        self.assertIn("page_bboxes=PAGE_BBOXES", parsing)
        self.assertEqual(parsing.count("## Special advisory rules"), 1)
        self.assertEqual(parsing.count("### Advisory 1: Avoid repeated per-cell extraction"), 1)
        self.assertIn("Avoid calling `crop().extract_text()` inside data-row or data-column loops", parsing)
        self.assertIn("at most 10 calls per logical table", parsing)
        self.assertTrue(parsing.rstrip().endswith("at most 10 calls per logical table."))
        self.assertIn("../assets/page_0025.pdf", parsing)
        self.assertIn("../assets/page_0026.pdf", parsing)
        self.assertIn("../assets/page_0027.pdf", parsing)
        self.assertNotIn("you just finished", parsing)
        self.assertNotIn("Merge Agent", parsing)
        self.assertNotIn("page group", parsing)
        self.assertNotIn("low-resolution image for every page", parsing)
        self.assertNotIn("forbidden", parsing)
        self.assertIn('"processed_pages": [25, 26, 27]', parsing)
        self.assertIn('follow the fast-exit requirement and return only:\n{"tables": 0}', parsing)
        self.assertIn("source_pages=SOURCE_PAGES", parsing)


if __name__ == "__main__":
    unittest.main()
