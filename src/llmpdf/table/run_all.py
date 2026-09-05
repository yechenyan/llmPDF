from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def extractor_scripts(output_dir: Path) -> list[Path]:
    def table_index(path: Path) -> int:
        try:
            return int(path.parent.name.removeprefix("table_"))
        except ValueError:
            return sys.maxsize

    return sorted(output_dir.glob("table_*/extract.py"), key=table_index)


def run_all(pdf_path: Path, output_dir: Path, *, spatial_check: bool = False) -> int:
    scripts = extractor_scripts(output_dir)
    if not scripts:
        print("No table_*/extract.py files found.", file=sys.stderr)
        return 1

    failures = 0
    for script in scripts:
        table_dir = script.parent
        command = [
            sys.executable,
            str(script),
            "--pdf",
            str(pdf_path),
            "--output-dir",
            str(table_dir),
        ]
        if spatial_check:
            command.append("--spatial-check")
        completed = subprocess.run(command, text=True)
        if completed.returncode:
            failures += 1
            print(f"TABLE_RUN_FAILED: {table_dir.name} returncode={completed.returncode}", file=sys.stderr)

    print(f"TABLE_RUN_SUMMARY: tables={len(scripts)} failures={failures}")
    return int(failures > 0)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--spatial-check", action="store_true")
    args = parser.parse_args()
    raise SystemExit(
        run_all(
            args.pdf.resolve(),
            args.output_dir.resolve(),
            spatial_check=args.spatial_check,
        )
    )


if __name__ == "__main__":
    main()
