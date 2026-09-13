import json
import subprocess
from pathlib import Path

import pytest

from llmpdf.agent_runtime import run_agent
from llmpdf.table.agent_runner import PiConfig, parse_log


def test_pi_passes_original_command_and_options():
    calls = []
    sentinel = object()
    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return sentinel
    assert run_agent(["pi", "--print", "@prompt"], config=PiConfig(),
                     runner=runner, cwd="original") is sentinel
    assert calls == [(["pi", "--print", "@prompt"], {"cwd": "original"})]


@pytest.mark.parametrize("no_tools", [False, True])
def test_claude_image_input_and_compatible_result(tmp_path: Path, no_tools):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Extract the table")
    image = tmp_path / "page.png"
    image.write_bytes(b"image fixture")
    calls = []
    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout='{"type":"system"}\n'
            + json.dumps({"type": "result", "result": '{"pages":[]}',
                          "session_id": "test-session", "usage": {"input_tokens": 123}}), stderr="")
    command = ["pi", "--model", "gpt-5.6-sol", "--print", f"@{prompt}", f"@{image}"]
    if no_tools:
        command.insert(1, "--no-tools")
    log = tmp_path / "pi.jsonl"
    with log.open("w") as stdout:
        result = run_agent(command, config=PiConfig(agent_backend="claude-code"),
                           runner=runner, cwd=tmp_path, stdout=stdout, text=True)
    assert result.returncode == 0
    actual, kwargs = calls[0]
    assert actual[0] == "claude"
    assert "--model" not in actual
    assert actual[actual.index("--tools") + 1] == ("" if no_tools else "Read,Write,Edit,Bash,Glob,Grep")
    assert "--no-session-persistence" in actual
    content = json.loads(kwargs["input"])["message"]["content"]
    assert content[0]["text"] == "Extract the table"
    assert content[1]["source"]["media_type"] == "image/png"
    usage, _, last_message, _ = parse_log(log)
    assert last_message == '{"pages":[]}'
    assert usage["input_tokens"] == 0
    assert log.with_suffix(".claude.json").exists()


@pytest.mark.parametrize("response", ["not json", '{"type":"result","is_error":true,"result":"Denied"}',
                                       '{"type":"system"}'])
def test_claude_errors_are_failures(tmp_path: Path, response):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Extract")
    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, stdout=response, stderr="")
    with (tmp_path / "pi.jsonl").open("w") as stdout:
        result = run_agent(["pi", "--model", "sonnet", "--print", f"@{prompt}"],
            config=PiConfig(agent_backend="claude-code"), runner=runner,
            cwd=tmp_path, stdout=stdout, text=True)
    assert result.returncode != 0
    assert "Claude Code:" in result.stderr


@pytest.mark.parametrize("relative", [False, True])
def test_claude_executable_subprocess(tmp_path: Path, monkeypatch, relative):
    executable = tmp_path / "claude-fixture"
    executable.write_text("#!/usr/bin/env python3\nimport json, sys\n"
        "request=json.loads(sys.stdin.readline())\n"
        "assert request['message']['content'][0]['text']=='Extract'\n"
        "print(json.dumps({'type':'result','result':'done','is_error':False}))\n")
    executable.chmod(0o755)
    monkeypatch.chdir(tmp_path)
    working_dir = tmp_path / "agent-output"
    working_dir.mkdir()
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Extract")
    log = tmp_path / "pi.jsonl"
    with log.open("w") as stdout:
        result = run_agent(["pi", "--model", "sonnet", "--print", f"@{prompt}"],
            config=PiConfig(agent_backend="claude-code", claude_executable=(
                Path(executable.name) if relative else executable
            )),
            cwd=working_dir, stdout=stdout, stderr=subprocess.PIPE, text=True,
            timeout=10, check=False)
    assert result.returncode == 0
    assert parse_log(log)[2] == "done"


def test_claude_backend_changes_agent_cache_only(tmp_path: Path):
    from llmpdf.detection_task import DetectTablesTask
    from llmpdf.models import PipelineConfig
    from llmpdf.screenshots_task import ScreenshotsTask
    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(b"source fixture")
    pi = PipelineConfig(pdf=pdf, output_dir=tmp_path)
    claude = PipelineConfig(pdf=pdf, output_dir=tmp_path, agent_backend="claude-code")
    assert DetectTablesTask().signature(pi) != DetectTablesTask().signature(claude)
    assert ScreenshotsTask().signature(pi) == ScreenshotsTask().signature(claude)


@pytest.mark.parametrize("entrypoint", ["pipeline", "table"])
def test_cli_resolves_relative_claude_executable(tmp_path: Path, monkeypatch, entrypoint):
    monkeypatch.chdir(tmp_path)
    options = ["--agent-backend", "claude-code", "--claude-executable", "tools/claude"]
    if entrypoint == "pipeline":
        from llmpdf.cli import build_parser, config_from_args

        pdf = tmp_path / "source.pdf"
        pdf.write_bytes(b"%PDF-test")
        args = build_parser().parse_args([
            "run-all", "--pdf", str(pdf), "--output-dir", str(tmp_path / "output"),
            *options,
        ])
        config = config_from_args(args)
    else:
        from llmpdf.table.cli import build_parser, pi_config

        args = build_parser().parse_args(["run", "--job-dir", str(tmp_path), *options])
        config = pi_config(args)
    assert config.claude_executable == tmp_path / "tools" / "claude"


def test_claude_persisted_logs_do_not_contain_runtime_paths(tmp_path: Path):
    output = tmp_path / "result"
    working_dir = output / "work" / "agent-output"
    working_dir.mkdir(parents=True)
    executable = tmp_path / "Claude Desktop" / "claude"
    prompt = working_dir / "prompt.md"
    prompt.write_text("Extract")
    log = working_dir / "pi.jsonl"

    def runner(command, **kwargs):
        events = [
            {"type": "system", "cwd": str(working_dir), "executable": str(executable)},
            {"type": "result", "result": f"Read {working_dir}/table.csv", "is_error": False},
        ]
        return subprocess.CompletedProcess(command, 0,
            stdout="\n".join(json.dumps(event) for event in events),
            stderr=f"Diagnostic at {executable}")

    with log.open("w") as stdout:
        result = run_agent(["pi", "--model", "sonnet", "--print", f"@{prompt}"],
            config=PiConfig(agent_backend="claude-code", claude_executable=executable,
                            artifact_root=output),
            runner=runner, cwd=working_dir, stdout=stdout, text=True)
    assert result.returncode == 0
    assert str(tmp_path) not in result.stderr
    assert str(tmp_path) not in log.read_text()
    assert str(tmp_path) not in log.with_suffix(".claude.json").read_text()
    assert parse_log(log)[2] == "Read ./work/agent-output/table.csv"
