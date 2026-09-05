import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from llmpdf.table.models import ExtractionJob, PreparedJob
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
    run_prepared_job,
    run_prepared_jobs,
)


class RunnerTest(unittest.TestCase):
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
            patch("llmpdf.table.orchestration.run_prepared_chain", fake_chain),
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

    def test_confirmed_group_attaches_first_and_last_high_resolution_images(self) -> None:
        page_images = [Path(f"page_{page:04d}_dynamic.png") for page in range(25, 30)]
        self.assertEqual(
            confirmed_group_attachments(page_images),
            [Path("page_0025_dynamic.png"), Path("page_0029_dynamic.png")],
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
            self.assertTrue(any(value.endswith("/assets/page_0027_dynamic.png") for value in invoked))

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
