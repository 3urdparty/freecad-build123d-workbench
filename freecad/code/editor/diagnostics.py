"""Backend-neutral diagnostics consumed by embedded editor frontends.

The FreeCAD dock translates kernel traceback dictionaries into these values.
Keeping that translation outside the Qt widget lets a future editor frontend
(native Qt, CodeMirror, or Monaco) consume the same execution diagnostics.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

DiagnosticSeverity = Literal["error", "warning", "information", "hint"]


@dataclass(frozen=True)
class SourceRange:
    """A one-based line range with zero-based columns.

    ``end_column=None`` means the diagnostic applies to the whole final line.
    That is the normal shape for runtime errors, whose traceback has a line
    number but no precise expression range.
    """

    start_line: int
    start_column: int = 0
    end_line: int | None = None
    end_column: int | None = None

    @property
    def last_line(self) -> int:
        return self.end_line or self.start_line


@dataclass(frozen=True)
class DiagnosticFrame:
    file: str
    line: int
    code: str = ""


@dataclass(frozen=True)
class EditorDiagnostic:
    file: str
    range: SourceRange | None
    severity: DiagnosticSeverity
    message: str
    traceback: tuple[DiagnosticFrame, ...]
    document_version: int
    source: str = "execution"


def diagnostics_from_traceback(
    frames: list[dict], source_file: str, document_version: int
) -> list[EditorDiagnostic]:
    """Translate the kernel's wire format into editor-facing diagnostics.

    The innermost frame is the primary diagnostic. A traceback that fails in
    a dependency can still be inspected, but only receives an editor range if
    that primary location belongs to the open source file.
    """
    if not frames:
        return []

    primary = frames[-1]
    line = _positive_int(primary.get("line"))
    location = None
    if primary.get("file") == source_file and line is not None:
        start_column = _nonnegative_int(primary.get("column"), default=0)
        end_line = max(line, _positive_int(primary.get("end_line")) or line)
        end_column = _nonnegative_int(primary.get("end_column"))
        location = SourceRange(line, start_column, end_line, end_column)

    message = str(primary.get("message") or primary.get("text") or "Script failed")
    traceback = tuple(
        DiagnosticFrame(
            file=str(frame.get("file") or ""),
            line=_positive_int(frame.get("line")) or 0,
            code=str(frame.get("code") or frame.get("text") or ""),
        )
        for frame in frames
    )
    return [EditorDiagnostic(
        file=str(primary.get("file") or source_file),
        range=location,
        severity="error",
        message=message,
        traceback=traceback,
        document_version=document_version,
    )]


def format_diagnostics_text(diagnostics: list[EditorDiagnostic]) -> str:
    """Plain-text representation suitable for tooltips and the clipboard."""
    sections = []
    for diagnostic in diagnostics:
        lines = [f"{diagnostic.source.title()} error: {diagnostic.message}"]
        for frame in diagnostic.traceback:
            lines.append(f"  {frame.file}:{frame.line}")
            if frame.code:
                lines.append(f"    {frame.code}")
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


def _positive_int(value) -> int | None:
    return value if isinstance(value, int) and value > 0 else None


def _nonnegative_int(value, default: int | None = None) -> int | None:
    return value if isinstance(value, int) and value >= 0 else default
