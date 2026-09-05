from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import PipelineConfig


def report_progress(config: PipelineConfig, message: str) -> None:
    if config.show_progress:
        print(f"[pdf-to-markdown] {message}", file=sys.stderr, flush=True)
