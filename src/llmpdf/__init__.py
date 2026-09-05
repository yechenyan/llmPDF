"""Task-oriented llmPDF conversion pipeline."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("llmPDF")
except PackageNotFoundError:  # Running directly from an unpacked source tree.
    __version__ = "0+unknown"

from .sdk import (
    BillingSummary,
    ConfigurationError,
    ConversionError,
    ConversionResult,
    ConvertOptions,
    DocumentConverterProtocol,
    TaskExecutionError,
    TokenUsage,
    UsageSummary,
    convert,
)
from .rerun import rerun_tables

__all__ = [
    "BillingSummary",
    "ConfigurationError",
    "ConversionError",
    "ConversionResult",
    "ConvertOptions",
    "DocumentConverterProtocol",
    "TaskExecutionError",
    "TokenUsage",
    "UsageSummary",
    "convert",
    "rerun_tables",
]
