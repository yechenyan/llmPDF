import json
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pdfplumber
from PIL import Image

from llmpdf.table.models import ExtractionJob, PreparedJob
from llmpdf.table.orchestration import (
    build_contact_sheets,
    preflight_python,
    redact_prepared_page,
    run_dynamic_group,
)
from llmpdf.table.prepare import write_python_wrapper
from llmpdf.table.runner import (
    PiConfig,
    confirmed_group_attachments,
    content_text,
    group_prepared_jobs,
    parse_log,
    parse_merge_decision,
    parse_merge_groups,
    parse_processed_pages,
    pi_environment,
    prioritize_parse_groups,
    run_confirmed_group,
    run_prepared_chain,
    run_prepared_job,
    run_prepared_jobs,
)


class RunnerTest(unittest.TestCase):
    def test_merge_planner_contact_sheets_group_twelve_pages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pages = list(range(1, 14))
            overviews = []
            for page in pages:
                path = root / f"page-{page}.png"
                Image.new("RGB", (1123, 794), "white").save(path)
                overviews.append(path)

            sheets = build_contact_sheets(overviews, pages, root)

            self.assertEqual([path.name for path in sheets], [
                "merge-plan-contact-001.png",
                "merge-plan-contact-002.png",
            ])
            with Image.open(sheets[0]) as image:
                self.assertEqual(image.width, 1548)

    def test_python_wrapper_preserves_virtual_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            wrapper = Path(directory) / "python"
            write_python_wrapper(wrapper, Path(sys.executable))
            self.assertIn('exec "$(dirname "$0")"/..', wrapper.read_text())
            completed = subprocess.run(
                [str(wrapper), "-c", "import sys; print(sys.prefix)"],
                check=True,
                capture_output=True,
                text=True,
            )
            preflight_python(wrapper)
        self.assertEqual(Path(completed.stdout.strip()), Path(sys.prefix))

    def test_content_text(self) -> None:
        self.assertEqual(
            content_text([{"type": "text", "text": "first"}, {"type": "text", "text": "second"}]),
            "first\nsecond",
        )

    def test_parse_log_separates_cached_input(self) -> None:
        events = [
            {"type": "tool_execution_start"},
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "usage": {"input": 120, "cacheRead": 80, "cacheWrite": 5, "output": 30},
                    "content": [{"type": "text", "text": "done"}],
                },
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "pi.jsonl"
            log.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")
            usage, tools, message, event_types = parse_log(log)
        self.assertEqual(usage["input_tokens"], 200)
        self.assertEqual(usage["noncached_input_tokens"], 120)
        self.assertEqual(usage["cached_input_tokens"], 80)
        self.assertEqual(usage["output_tokens"], 30)
        self.assertEqual(tools, 1)
        self.assertEqual(message, "done")
        self.assertEqual(event_types["message_end"], 1)

    def test_parse_merge_decision(self) -> None:
        self.assertTrue(parse_merge_decision('{"merge_with_previous": true}'))
        self.assertFalse(parse_merge_decision('```json\n{"merge_with_previous": false}\n```'))

    def test_parse_merge_groups_requires_complete_ordered_pages(self) -> None:
        self.assertEqual(
            parse_merge_groups('{"groups": [[25, 26, 27], [28, 29], [30]]}', list(range(25, 31))),
            [[25, 26, 27], [28, 29], [30]],
        )
        self.assertIsNone(parse_merge_groups('{"groups": [[25, 27], [26]]}', [25, 26, 27]))
        self.assertEqual(
            parse_merge_groups(
                '{"groups": [[5, 6, 7, 8], [8, 9, 10, 11]]}',
                list(range(5, 12)),
            ),
            [[5, 6, 7, 8], [8, 9, 10, 11]],
        )
        self.assertIsNone(
            parse_merge_groups('{"groups": [[5, 6, 7], [6, 7, 8]]}', [5, 6, 7, 8])
        )
        self.assertTrue(parse_processed_pages('{"processed_pages": [25, 26]}', [25, 26]))

    def test_grouping_keeps_possible_continuations_in_one_chain(self) -> None:
        def prepared(page: int, may_merge: bool) -> PreparedJob:
            job = ExtractionJob(Path("/tmp/source.pdf"), page, may_merge_with_previous=may_merge)
            root = Path(f"/tmp/page-{page}")
            return PreparedJob(job, root, root / "page.pdf", root / "page.png", root / "prompt.md", root / "agent-output")

        chains = group_prepared_jobs(
            [prepared(1, False), prepared(2, True), prepared(3, False), prepared(4, True)]
        )
        self.assertEqual([[item.job.page for item in chain] for chain in chains], [[1, 2], [3, 4]])

    def test_parse_queue_prioritizes_confirmed_and_longer_merge_groups(self) -> None:
        def prepared(page: int) -> PreparedJob:
            job = ExtractionJob(Path("/tmp/source.pdf"), page)
            root = Path(f"/tmp/page-{page}")
            return PreparedJob(job, root, root / "page.pdf", root / "page.png", root / "prompt.md", root / "agent-output")

        planned = [
            ([prepared(3)], False),
            ([prepared(20), prepared(21)], False),
            ([prepared(30), prepared(31)], True),
            ([prepared(10), prepared(11), prepared(12)], True),
            ([prepared(1)], True),
        ]
        prioritized = prioritize_parse_groups(planned)
        self.assertEqual(
            [([item.job.page for item in group], confirmed) for group, confirmed in prioritized],
            [
                ([10, 11, 12], True),
                ([30, 31], True),
                ([20, 21], False),
                ([1], True),
                ([3], False),
            ],
        )

    def test_parse_queue_enqueues_images_after_tables_without_phase_barrier(self) -> None:
        def prepared(page: int, may_merge: bool) -> PreparedJob:
            job = ExtractionJob(
                Path("/tmp/source.pdf"),
                page,
                job_id=f"page-{page:04d}",
                may_merge_with_previous=may_merge,
            )
            root = Path(f"/tmp/page-{page}")
            return PreparedJob(
                job,
                root,
                root / "page.pdf",
                root / "page.png",
                root / "prompt.md",
                root / "agent-output",
            )

        jobs = [prepared(1, False), prepared(2, True), prepared(3, False)]
        events = []
        image_started = threading.Event()

        def fake_chain(group, _config):
            category = "cross" if len(group) > 1 else "normal"
            events.append(f"{category}-start")
            if category == "cross":
                assert image_started.wait(timeout=2)
            events.append(f"{category}-end")
            return {item.job.id: {"returncode": 0} for item in group}

        def image_job():
            events.append("image-start")
            image_started.set()
            return {"status": "completed"}

        with (
            patch("llmpdf.table.orchestration.run_dynamic_group", fake_chain),
            patch("llmpdf.table.orchestration.preflight_python"),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            results = run_prepared_jobs(
                jobs,
                PiConfig(keep_sessions=True),
                2,
                executor=executor,
                trailing_jobs=[("image-page-0004", image_job)],
            )
            results["trailing_job_futures"]["image-page-0004"].result()
        self.assertLess(events.index("normal-start"), events.index("image-start"))
        self.assertLess(events.index("image-start"), events.index("cross-end"))

    def test_long_chain_is_planned_before_extraction(self) -> None:
        def prepared(page: int) -> PreparedJob:
            job = ExtractionJob(
                Path("/tmp/source.pdf"),
                page,
                job_id=f"page-{page:04d}",
                may_merge_with_previous=page > 1,
            )
            root = Path(f"/tmp/page-{page}")
            return PreparedJob(
                job,
                root,
                root / "page.pdf",
                root / "page.png",
                root / "prompt.md",
                root / "agent-output",
            )

        group = [prepared(page) for page in (1, 2, 3)]
        seen = []

        def fake_chain(items, _config):
            seen.append([item.job.page for item in items])
            return {item.job.id: {"returncode": 0} for item in items}

        with (
            patch(
                "llmpdf.table.orchestration.run_dynamic_group",
                side_effect=fake_chain,
            ) as dynamic,
            patch("llmpdf.table.orchestration.preflight_python"),
        ):
            run_prepared_jobs(group, PiConfig(keep_sessions=True), concurrency=1)

        self.assertEqual(seen, [[1, 2, 3]])
        dynamic.assert_called_once()

    def test_chain_uses_fresh_session_per_continuation_page(self) -> None:
        def prepared(page: int) -> PreparedJob:
            root = Path(f"/tmp/page-{page}")
            return PreparedJob(
                ExtractionJob(
                    Path("/tmp/source.pdf"),
                    page,
                    job_id=f"page-{page:04d}",
                    may_merge_with_previous=page > 1,
                ),
                root,
                root / "page.pdf",
                root / "page.png",
                root / "prompt.md",
                root / "agent-output",
            )

        group = [prepared(page) for page in (1, 2, 3)]
        sessions = []
        working_dirs = []

        def fake_pi(**kwargs):
            sessions.append(kwargs["session"])
            working_dirs.append(kwargs["working_dir"])
            return {"returncode": 0, "last_message": '{"merge_with_previous": true}'}

        with (
            patch(
                "llmpdf.table.orchestration.run_prepared_job",
                return_value={"returncode": 0},
            ) as initial,
            patch("llmpdf.table.orchestration._continuation_assets", return_value=(Path("prompt"), [])),
            patch("llmpdf.table.orchestration.run_pi", side_effect=fake_pi),
        ):
            run_prepared_chain(group, PiConfig(keep_sessions=True))

        initial.assert_called_once_with(group[0].directory, PiConfig(keep_sessions=True))
        self.assertEqual(
            sessions,
            [
                group[1].directory / "continuation-session.jsonl",
                group[2].directory / "continuation-session.jsonl",
            ],
        )
        self.assertEqual(working_dirs, [group[0].agent_output, group[0].agent_output])

    def test_confirmed_group_attaches_first_second_and_last_high_resolution_images(self) -> None:
        page_images = [Path(f"page_{page:04d}_dynamic.png") for page in range(25, 30)]
        self.assertEqual(
            confirmed_group_attachments(page_images),
            [
                Path("page_0025_dynamic.png"),
                Path("page_0026_dynamic.png"),
                Path("page_0029_dynamic.png"),
            ],
        )

    def test_confirmed_group_uses_one_parse_call_and_records_covered_pages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepared = []
            for page in (25, 26, 27):
                job_dir = root / f"page-{page:04d}"
                assets = job_dir / "assets"
                output = job_dir / "agent-output"
                assets.mkdir(parents=True)
                output.mkdir()
                pdf = assets / f"page_{page:04d}.pdf"
                image = assets / f"page_{page:04d}_dynamic.png"
                pdf.write_bytes(f"page {page}".encode())
                Image.new("RGB", (4, 4), "white").save(image)
                (assets / "page_info.json").write_text(
                    json.dumps({"physical_page": page}), encoding="utf-8"
                )
                (job_dir / "job.json").write_text(
                    json.dumps({"id": f"page-{page:04d}", "page": page}), encoding="utf-8"
                )
                job = ExtractionJob(
                    Path("/tmp/source.pdf"),
                    page,
                    target="all tables",
                    job_id=f"page-{page:04d}",
                    may_merge_with_previous=page > 25,
                )
                prepared.append(PreparedJob(job, job_dir, pdf, image, job_dir / "prompt.md", output))

            arguments = root / "arguments.txt"
            executable = root / "fake-pi"
            executable.write_text(
                f'#!/bin/sh\nprintf "%s\\n" "$@" > "{arguments}"\n'
                "printf '%s\\n' '{\"type\":\"message_end\",\"message\":{\"role\":\"assistant\",\"usage\":{},\"content\":[{\"type\":\"text\",\"text\":\"{\\\"processed_pages\\\":[25,26,27]}\"}]}}'\n",
                encoding="utf-8",
            )
            executable.chmod(0o755)

            results = run_confirmed_group(prepared, PiConfig(pi_executable=executable))
            metrics = json.loads(
                (prepared[0].directory / "confirmed_group_pi_metrics.json").read_text(
                    encoding="utf-8"
                )
            )
            invoked = arguments.read_text(encoding="utf-8").splitlines()
            self.assertEqual(results[prepared[0].job.id]["pages"], [25, 26, 27])
            self.assertEqual(metrics["pages"], [25, 26, 27])
            self.assertFalse((prepared[0].directory / "pi.jsonl").exists())
            self.assertTrue(any(value.endswith("/assets/page_0025_dynamic.png") for value in invoked))
            self.assertTrue(any(value.endswith("/assets/page_0026_dynamic.png") for value in invoked))
            self.assertTrue(any(value.endswith("/assets/page_0027_dynamic.png") for value in invoked))

    def test_overlapping_groups_run_in_order_and_clean_the_last_page(self) -> None:
        def prepared(page: int) -> PreparedJob:
            root = Path(f"/tmp/page-{page}")
            return PreparedJob(
                ExtractionJob(Path("/tmp/source.pdf"), page, job_id=f"page-{page:04d}"),
                root,
                root / "page.pdf",
                root / "page.png",
                root / "prompt.md",
                root / "agent-output",
            )

        chain = [prepared(page) for page in range(5, 12)]
        by_page = {item.job.page: item for item in chain}
        planned = [
            [by_page[page] for page in (5, 6, 7, 8)],
            [by_page[page] for page in (8, 9, 10, 11)],
        ]
        events = []

        def extract(items, _config):
            events.append(("extract", [item.job.page for item in items]))
            return {item.job.id: {"returncode": 0} for item in items}

        def redact(item, _bboxes):
            events.append(("redact", item.job.page))

        with (
            patch("llmpdf.table.orchestration.plan_long_chain", return_value=planned),
            patch("llmpdf.table.orchestration.run_confirmed_group", side_effect=extract),
            patch("llmpdf.table.orchestration.run_prepared_chain", side_effect=extract),
            patch("llmpdf.table.orchestration._boundary_bboxes", return_value=[(0, 0, 10, 10)]),
            patch("llmpdf.table.orchestration.redact_prepared_page", side_effect=redact),
        ):
            run_dynamic_group(chain, PiConfig(keep_sessions=True))

        self.assertEqual(
            events,
            [
                ("extract", [5, 6, 7, 8]),
                ("redact", 8),
                ("extract", [8, 9, 10, 11]),
                ("redact", 11),
                ("extract", [11]),
            ],
        )

    def test_redaction_marks_png_and_preserves_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            assets = root / "assets"
            assets.mkdir()
            image_path = assets / "page_0008_dynamic.png"
            pdf_path = assets / "page_0008.pdf"
            Image.new("RGB", (100, 100), "black").save(image_path)
            pdf_path.write_bytes(b"%PDF-original")
            (assets / "page_info.json").write_text(
                json.dumps(
                    {
                        "physical_page": 8,
                        "scale_x": 1,
                        "scale_y": 1,
                        "full_dpi": 72,
                    }
                ),
                encoding="utf-8",
            )
            item = PreparedJob(
                ExtractionJob(Path("/tmp/source.pdf"), 8),
                root,
                pdf_path,
                image_path,
                root / "prompt.md",
                root / "agent-output",
            )

            redact_prepared_page(item, [(0, 0, 100, 40)])

            with Image.open(image_path) as image:
                self.assertNotEqual(image.getpixel((95, 35)), (0, 0, 0))
                self.assertEqual(image.getpixel((50, 80)), (0, 0, 0))
            self.assertEqual(pdf_path.read_bytes(), b"%PDF-original")

    def test_pi_environment_forces_sse_without_changing_source_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "agent"
            source.mkdir()
            (source / "auth.json").write_text('{"token":"test"}\n', encoding="utf-8")
            (source / "settings.json").write_text('{"transport":"auto"}\n', encoding="utf-8")
            with pi_environment(PiConfig(agent_dir=source, transport="sse")) as environment:
                runtime = Path(environment["PI_CODING_AGENT_DIR"])
                self.assertNotEqual(runtime, source)
                self.assertEqual(
                    json.loads((runtime / "settings.json").read_text(encoding="utf-8"))["transport"],
                    "sse",
                )
                self.assertTrue((runtime / "auth.json").is_symlink())
            self.assertFalse(runtime.exists())
            self.assertEqual(
                json.loads((source / "settings.json").read_text(encoding="utf-8"))["transport"],
                "auto",
            )

    def test_runner_uses_explicit_session_and_removes_it_after_job(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "assets").mkdir()
            (root / "agent-output").mkdir()
            (root / "prompt.md").write_text("test", encoding="utf-8")
            (root / "assets" / "page_0001_dynamic.png").write_bytes(b"png")
            (root / "job.json").write_text(
                json.dumps({"id": "page-1", "page": 1}), encoding="utf-8"
            )
            arguments = root / "arguments.txt"
            executable = root / "fake-pi"
            executable.write_text(
                f'#!/bin/sh\nprintf "%s\\n" "$@" > "{arguments}"\n'
                "printf '%s\\n' '{\"type\":\"message_end\",\"message\":{\"role\":\"assistant\",\"usage\":{},\"content\":[{\"type\":\"text\",\"text\":\"done\"}]}}'\n",
                encoding="utf-8",
            )
            executable.chmod(0o755)
            result = run_prepared_job(root, PiConfig(pi_executable=executable))
            invoked = arguments.read_text(encoding="utf-8").splitlines()
            self.assertEqual(result["returncode"], 0)
            self.assertEqual(result["job_dir"], root.name)
            self.assertFalse(Path(result["pi_executable"]).is_absolute())
            self.assertIn("--session", invoked)
            self.assertNotIn("--no-session", invoked)
            self.assertFalse((root / "pi-session.jsonl").exists())

    def test_pi_config_defaults_to_auto_transport(self) -> None:
        self.assertEqual(PiConfig().transport, "auto")
        self.assertEqual(PiConfig().timeout_seconds, 1800.0)
        self.assertFalse(PiConfig().keep_sessions)

    def test_runner_times_out_and_reports_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "assets").mkdir()
            (root / "agent-output").mkdir()
            (root / "prompt.md").write_text("test", encoding="utf-8")
            (root / "assets" / "page_0001_dynamic.png").write_bytes(b"png")
            (root / "job.json").write_text(
                json.dumps({"id": "page-1", "page": 1}), encoding="utf-8"
            )
            executable = root / "slow-pi"
            executable.write_text("#!/bin/sh\nsleep 1\n", encoding="utf-8")
            executable.chmod(0o755)

            result = run_prepared_job(
                root,
                PiConfig(pi_executable=executable, timeout_seconds=0.01),
            )

            self.assertEqual(result["returncode"], 124)
            self.assertTrue(result["timed_out"])
            self.assertEqual(result["timeout_seconds"], 0.01)
            self.assertFalse((root / "pi-session.jsonl").exists())

    def test_runner_can_keep_sessions_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "assets").mkdir()
            (root / "agent-output").mkdir()
            (root / "prompt.md").write_text("test", encoding="utf-8")
            (root / "assets" / "page_0001_dynamic.png").write_bytes(b"png")
            (root / "job.json").write_text(
                json.dumps({"id": "page-1", "page": 1}), encoding="utf-8"
            )
            session = root / "pi-session.jsonl"
            session.write_text("existing session\n", encoding="utf-8")
            executable = root / "fake-pi"
            executable.write_text(
                "#!/bin/sh\nprintf '%s\\n' "
                "'{\"type\":\"message_end\",\"message\":{\"role\":\"assistant\","
                "\"usage\":{},\"content\":[{\"type\":\"text\",\"text\":\"done\"}]}}'\n",
                encoding="utf-8",
            )
            executable.chmod(0o755)

            result = run_prepared_job(
                root,
                PiConfig(pi_executable=executable, keep_sessions=True),
            )

            self.assertEqual(result["returncode"], 0)
            self.assertTrue(session.exists())


if __name__ == "__main__":
    unittest.main()
