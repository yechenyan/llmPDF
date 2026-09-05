from __future__ import annotations

import argparse
from pathlib import Path

from .docling_task import DoclingTask
from .io_utils import read_json, write_json
from .models import PipelineConfig


def main() -> None:
    parser = argparse.ArgumentParser(description="Internal isolated Docling worker")
    parser.add_argument("request", type=Path)
    args = parser.parse_args()
    request = read_json(args.request.resolve())
    selected = request.get("selected_pages")
    config = PipelineConfig(
        pdf=Path(request["pdf"]),
        output_dir=Path(request["output_dir"]),
        selected_pages=tuple(int(page) for page in selected) if selected else None,
        docling_options=dict(request.get("docling_options") or {}),
        show_progress=bool(request.get("show_progress")),
    )
    result = DoclingTask()._run_in_process(config)
    write_json(
        Path(request["result_path"]),
        {
            "task": result.task,
            "status": result.status,
            "outputs": result.outputs,
            "details": result.details,
        },
    )


if __name__ == "__main__":
    main()
