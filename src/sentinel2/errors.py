"""Public exception types shared by the CLI and scanner."""

from __future__ import annotations


class ScanError(RuntimeError):
    """Raised when a scan cannot be performed."""


class ReportError(RuntimeError):
    """Raised when a report cannot be loaded or rendered."""
