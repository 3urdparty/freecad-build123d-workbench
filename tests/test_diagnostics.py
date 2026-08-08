"""Pure-Python tests for the editor/backend diagnostic boundary."""

import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from freecad.code.editor.diagnostics import (
    diagnostics_from_traceback,
    format_diagnostics_text,
)


def test_traceback_becomes_versioned_source_diagnostic():
    frames = [
        {"file": "/work/model.py", "line": 4, "code": "make_part()"},
        {
            "file": "/work/model.py",
            "line": 9,
            "column": 6,
            "end_line": 9,
            "end_column": 11,
            "code": "raise ValueError('boom')",
            "message": "ValueError: boom",
            "text": "raise ValueError('boom')  (ValueError: boom)",
        },
    ]

    diagnostics = diagnostics_from_traceback(frames, "/work/model.py", 12)

    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic.message == "ValueError: boom"
    assert diagnostic.document_version == 12
    assert diagnostic.range is not None
    assert diagnostic.range.start_line == 9
    assert diagnostic.range.start_column == 6
    assert diagnostic.range.end_column == 11
    assert [frame.line for frame in diagnostic.traceback] == [4, 9]


def test_dependency_failure_has_details_but_no_open_file_range():
    frames = [{
        "file": "/venv/library.py",
        "line": 100,
        "code": "fail()",
        "message": "RuntimeError: failed in dependency",
    }]

    diagnostic = diagnostics_from_traceback(frames, "/work/model.py", 3)[0]

    assert diagnostic.range is None
    assert diagnostic.file == "/venv/library.py"
    assert diagnostic.message == "RuntimeError: failed in dependency"
    assert diagnostic.traceback[0].file == "/venv/library.py"


def test_legacy_frame_text_remains_supported():
    frames = [{"file": "/work/model.py", "line": 2, "text": "legacy failure"}]

    diagnostic = diagnostics_from_traceback(frames, "/work/model.py", 1)[0]

    assert diagnostic.message == "legacy failure"
    assert diagnostic.traceback[0].code == "legacy failure"


def test_empty_traceback_has_no_diagnostics():
    assert diagnostics_from_traceback([], "/work/model.py", 1) == []


def test_plain_text_formatter_includes_message_and_traceback():
    frames = [{
        "file": "/work/model.py",
        "line": 7,
        "code": "make_part()",
        "message": "ValueError: bad radius",
    }]
    diagnostic = diagnostics_from_traceback(frames, "/work/model.py", 2)

    rendered = format_diagnostics_text(diagnostic)

    assert "Execution error: ValueError: bad radius" in rendered
    assert "/work/model.py:7" in rendered
    assert "make_part()" in rendered
