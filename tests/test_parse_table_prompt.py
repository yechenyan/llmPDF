import unittest

from parse_table.prompt import (
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
        self.assertIn("from parse_table.runtime import run_extractor", prompt)
        self.assertIn("--spatial-check", prompt)
        self.assertIn("-m parse_table.run_all", prompt)
        self.assertIn("Do not separately read the CSV or metadata afterward", prompt)
        self.assertIn("REQUIRES_VISUAL_REVIEW", prompt)
        self.assertIn("Do not generate `metadata.yaml` yourself", prompt)
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
        self.assertIn("pages [25, 26, 27]", parsing)
        self.assertIn("confirmed as one mergeable table", parsing)
        self.assertIn("Write extract.py once", parsing)
        self.assertIn("run the shared batch command below once", parsing)
        self.assertIn("images of pages 25 and 27", parsing)
        self.assertIn("all other pages are available in ../assets", parsing)
        self.assertIn("independent tables that do not belong", parsing)
        self.assertIn("Do not begin by processing only page 25", parsing)
        self.assertIn("Prefer batch extraction when accuracy permits", parsing)
        self.assertIn("assign page text to rows and columns by coordinates", parsing)
        self.assertIn("Use separate `crop()` checks only", parsing)
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
        self.assertNotIn("source_pages", parsing)


if __name__ == "__main__":
    unittest.main()
