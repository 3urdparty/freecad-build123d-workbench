"""Python syntax highlighter for the embedded editor (QSyntaxHighlighter)."""

from __future__ import annotations

import keyword

from PySide import QtCore, QtGui  # FreeCAD's PySide shim


def _fmt(color: str, bold: bool = False, italic: bool = False) -> QtGui.QTextCharFormat:
    f = QtGui.QTextCharFormat()
    f.setForeground(QtGui.QColor(color))
    if bold:
        f.setFontWeight(QtGui.QFont.Bold)
    if italic:
        f.setFontItalic(True)
    return f


class PythonHighlighter(QtGui.QSyntaxHighlighter):
    """Regex-based highlighting: keywords, builtins-ish calls, strings,
    comments, numbers, decorators, def/class names. Palette chosen to read
    on both light and dark FreeCAD themes."""

    def __init__(self, document: QtGui.QTextDocument):
        super().__init__(document)
        self._rules: list[tuple[QtCore.QRegularExpression, QtGui.QTextCharFormat]] = []

        kw = "|".join(keyword.kwlist)
        self._add(rf"\b(?:{kw})\b", _fmt("#c586c0", bold=True))
        self._add(r"\b(?:self|cls)\b", _fmt("#9cdcfe", italic=True))
        self._add(r"\b[0-9]+(?:\.[0-9]+)?\b", _fmt("#b5cea8"))
        self._add(r"\bdef\s+(\w+)", _fmt("#dcdcaa"), group=1)
        self._add(r"\bclass\s+(\w+)", _fmt("#4ec9b0", bold=True), group=1)
        self._add(r"@\w+(?:\.\w+)*", _fmt("#dcdcaa", italic=True))
        self._add(r"\b(?:show|show_object)\b", _fmt("#4fc1ff", bold=True))
        # strings after everything else except comments
        self._add(r"'[^'\\]*(?:\\.[^'\\]*)*'", _fmt("#ce9178"))
        self._add(r'"[^"\\]*(?:\\.[^"\\]*)*"', _fmt("#ce9178"))
        self._add(r"#[^\n]*", _fmt("#6a9955", italic=True))

        self._tri_fmt = _fmt("#ce9178")
        self._tri_single = QtCore.QRegularExpression(r"'''")
        self._tri_double = QtCore.QRegularExpression(r'"""')

    def _add(self, pattern: str, fmt: QtGui.QTextCharFormat, group: int = 0) -> None:
        self._rules.append((QtCore.QRegularExpression(pattern), fmt, group))

    def highlightBlock(self, text: str) -> None:  # noqa: N802 (Qt API)
        for regex, fmt, group in self._rules:
            it = regex.globalMatch(text)
            while it.hasNext():
                m = it.next()
                self.setFormat(m.capturedStart(group), m.capturedLength(group), fmt)
        self._highlight_multiline(text)

    def _highlight_multiline(self, text: str) -> None:
        # state 1 = inside ''' ... ''', state 2 = inside """ ... """
        for state, delim in ((1, self._tri_single), (2, self._tri_double)):
            start = 0
            if self.previousBlockState() == state:
                m = delim.match(text)
                if m.hasMatch():
                    end = m.capturedEnd()
                    self.setFormat(0, end, self._tri_fmt)
                    start = end
                else:
                    self.setCurrentBlockState(state)
                    self.setFormat(0, len(text), self._tri_fmt)
                    return
            m = delim.match(text, start)
            while m.hasMatch():
                m2 = delim.match(text, m.capturedEnd())
                if m2.hasMatch():
                    self.setFormat(m.capturedStart(),
                                   m2.capturedEnd() - m.capturedStart(), self._tri_fmt)
                    m = delim.match(text, m2.capturedEnd())
                else:
                    self.setCurrentBlockState(state)
                    self.setFormat(m.capturedStart(),
                                   len(text) - m.capturedStart(), self._tri_fmt)
                    return
