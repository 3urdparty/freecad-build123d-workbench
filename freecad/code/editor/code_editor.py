"""The editor widget: QPlainTextEdit with line numbers, auto-indent,
current-line + diagnostic highlighting, and a completion popup fed
asynchronously by the kernel (see panel.py for the wiring)."""

from __future__ import annotations

import html
import os

from PySide import QtCore, QtGui, QtWidgets  # FreeCAD's PySide shim

from .diagnostics import EditorDiagnostic, format_diagnostics_text
from .highlighter import PythonHighlighter

INDENT = "    "
DIAGNOSTIC_GUTTER_WIDTH = 12
GUTTER_RIGHT_PADDING = 4
DIAGNOSTIC_MARKER_SIZE = 6


class _LineNumberArea(QtWidgets.QWidget):
    def __init__(self, editor: CodeEditor):
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self):  # noqa: N802
        return QtCore.QSize(self._editor.line_number_width(), 0)

    def paintEvent(self, event):  # noqa: N802
        self._editor.paint_line_numbers(event)

    def mouseMoveEvent(self, event):  # noqa: N802
        self._editor.show_diagnostic_tooltip(
            self._editor.line_at_y(event.pos().y()), event.globalPos()
        )
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):  # noqa: N802
        line = self._editor.line_at_y(event.pos().y())
        if event.button() == QtCore.Qt.LeftButton and self._editor.diagnostics_at_line(line):
            self._editor.show_diagnostic_menu(line, event.globalPos())
            event.accept()
            return
        super().mousePressEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self._editor.hide_diagnostic_tooltip()
        super().leaveEvent(event)


class CodeEditor(QtWidgets.QPlainTextEdit):
    run_requested = QtCore.Signal()
    save_requested = QtCore.Signal()
    #: emitted with (source, line 1-based, column 0-based) when the user
    #: types '.' or presses Ctrl+Space — the panel answers via the kernel.
    completions_wanted = QtCore.Signal(str, int, int)
    #: emitted when the user types '(' — the panel answers with signature
    #: strings from kernel.signatures and calls show_signatures().
    signatures_wanted = QtCore.Signal(str, int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        font = QtGui.QFont("Menlo")
        font.setStyleHint(QtGui.QFont.Monospace)
        font.setPointSize(12)
        self.setFont(font)
        self.setTabStopDistance(4 * self.fontMetrics().horizontalAdvance(" ")
                                if hasattr(self, "setTabStopDistance") else 0)
        self.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)

        self._highlighter = PythonHighlighter(self.document())
        self._line_numbers = _LineNumberArea(self)
        self._diagnostics: list[EditorDiagnostic] = []
        self._diagnostic_menu = None
        self._tooltip_line: int | None = None
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self._line_numbers.setMouseTracking(True)

        self.blockCountChanged.connect(self._update_margin)
        self.updateRequest.connect(self._update_line_area)
        self.cursorPositionChanged.connect(self._update_extra_selections)
        self.textChanged.connect(self._discard_stale_diagnostics)
        self._update_margin()

        self._completer = QtWidgets.QCompleter([], self)
        self._completer.setWidget(self)
        self._completer.setCompletionMode(QtWidgets.QCompleter.PopupCompletion)
        self._completer.setCaseSensitivity(QtCore.Qt.CaseInsensitive)
        self._completer.activated.connect(self._insert_completion)

    # -- theme-aware chrome ------------------------------------------------------
    #
    # Never use palette().alternateBase() here: several FreeCAD themes pair a
    # dark base with a near-white alternateBase, which painted the gutter and
    # the current-line highlight as unreadable white bars. Derive everything
    # from the editor's actual background instead.

    def _is_dark(self) -> bool:
        return self.palette().base().color().lightness() < 128

    def _gutter_bg(self) -> QtGui.QColor:
        base = self.palette().base().color()
        return base.lighter(112) if self._is_dark() else base.darker(104)

    def _gutter_fg(self) -> QtGui.QColor:
        color = QtGui.QColor(self.palette().text().color())
        color.setAlpha(130)  # line numbers should whisper, not shout
        return color

    def _current_line_bg(self) -> QtGui.QColor:
        base = self.palette().base().color()
        return base.lighter(135) if self._is_dark() else base.darker(106)

    # -- line numbers ----------------------------------------------------------

    def line_number_width(self) -> int:
        digits = max(2, len(str(self.blockCount())))
        return (
            DIAGNOSTIC_GUTTER_WIDTH
            + self.fontMetrics().horizontalAdvance("9") * digits
            + GUTTER_RIGHT_PADDING
        )

    def _update_margin(self, *_):
        self.setViewportMargins(self.line_number_width(), 0, 0, 0)

    def _update_line_area(self, rect, dy):
        if dy:
            self._line_numbers.scroll(0, dy)
        else:
            self._line_numbers.update(0, rect.y(), self._line_numbers.width(),
                                      rect.height())

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        cr = self.contentsRect()
        self._line_numbers.setGeometry(
            QtCore.QRect(cr.left(), cr.top(), self.line_number_width(), cr.height()))

    def paint_line_numbers(self, event) -> None:
        painter = QtGui.QPainter(self._line_numbers)
        painter.fillRect(event.rect(), self._gutter_bg())
        block = self.firstVisibleBlock()
        top = self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
        current = self.textCursor().blockNumber()
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible():
                num = block.blockNumber() + 1
                severity = self._severity_at_line(num)
                if severity is not None:
                    pen = self._diagnostic_color(severity)
                    painter.setBrush(pen)
                    painter.setPen(QtCore.Qt.NoPen)
                    marker_left = (DIAGNOSTIC_GUTTER_WIDTH - DIAGNOSTIC_MARKER_SIZE) / 2
                    marker_top = (
                        top + (self.fontMetrics().height() - DIAGNOSTIC_MARKER_SIZE) / 2
                    )
                    painter.drawEllipse(QtCore.QRectF(
                        marker_left,
                        marker_top,
                        DIAGNOSTIC_MARKER_SIZE,
                        DIAGNOSTIC_MARKER_SIZE,
                    ))
                elif block.blockNumber() == current:
                    pen = self.palette().text().color()  # full strength
                else:
                    pen = self._gutter_fg()
                painter.setPen(pen)
                painter.drawText(
                    DIAGNOSTIC_GUTTER_WIDTH,
                    int(top),
                    self._line_numbers.width()
                    - DIAGNOSTIC_GUTTER_WIDTH
                    - GUTTER_RIGHT_PADDING,
                    self.fontMetrics().height(),
                    QtCore.Qt.AlignRight,
                    str(num),
                )
            top += self.blockBoundingRect(block).height()
            block = block.next()

    # -- diagnostics + current-line highlight ---------------------------------

    def document_version(self) -> int:
        """Version used to keep execution results tied to their source text."""
        return self.document().revision()

    def set_diagnostics(
        self, diagnostics: list[EditorDiagnostic], focus: bool = False
    ) -> None:
        """Replace editor diagnostics and optionally jump to the first one.

        Results for an older document revision are deliberately ignored: an
        underline must never claim that newly edited text produced an earlier
        runtime error. Autosave passes ``focus=False`` so typing is not
        interrupted when the recompute finishes.
        """
        version = self.document_version()
        self._diagnostics = [
            diagnostic for diagnostic in diagnostics
            if diagnostic.document_version == version
        ]
        self._update_extra_selections()
        self._line_numbers.update()
        located = next((item for item in self._diagnostics if item.range), None)
        if located is not None and focus:
            block = self.document().findBlockByNumber(located.range.start_line - 1)
            if block.isValid():
                cursor = QtGui.QTextCursor(block)
                cursor.setPosition(block.position() + located.range.start_column)
                self.setTextCursor(cursor)

    def _discard_stale_diagnostics(self) -> None:
        version = self.document_version()
        if self._diagnostics and any(
            item.document_version != version for item in self._diagnostics
        ):
            self._diagnostics = []
            self.hide_diagnostic_tooltip()
            self._update_extra_selections()
            self._line_numbers.update()

    def diagnostics_at_line(self, line: int) -> list[EditorDiagnostic]:
        return [
            item for item in self._diagnostics
            if item.range is not None
            and item.range.start_line <= line <= item.range.last_line
        ]

    def _severity_at_line(self, line: int):
        priorities = {"error": 0, "warning": 1, "information": 2, "hint": 3}
        severities = [item.severity for item in self.diagnostics_at_line(line)]
        return min(severities, key=priorities.get) if severities else None

    @staticmethod
    def _diagnostic_color(severity: str) -> QtGui.QColor:
        return QtGui.QColor({
            "error": "#d16969",
            "warning": "#d7a04c",
            "information": "#4aa5f0",
            "hint": "#8a8a8a",
        }.get(severity, "#d16969"))

    def _update_extra_selections(self) -> None:
        selections = []
        # current line — subtle shade of the real background, both themes
        sel = QtWidgets.QTextEdit.ExtraSelection()
        sel.format.setBackground(self._current_line_bg())
        sel.format.setProperty(QtGui.QTextFormat.FullWidthSelection, True)
        sel.cursor = self.textCursor()
        sel.cursor.clearSelection()
        selections.append(sel)
        self._line_numbers.update()  # keep the gutter's current-line number in sync
        # Diagnostics use a wave underline over the available range. Runtime
        # tracebacks only identify a line, in which case the whole line is used.
        for diagnostic in self._diagnostics:
            if diagnostic.range is None:
                continue
            dsel = QtWidgets.QTextEdit.ExtraSelection()
            dsel.cursor = self._diagnostic_cursor(diagnostic)
            color = self._diagnostic_color(diagnostic.severity)
            dsel.format.setUnderlineStyle(QtGui.QTextCharFormat.WaveUnderline)
            dsel.format.setUnderlineColor(color)
            if not dsel.cursor.hasSelection():
                # Empty lines cannot carry an underline, so retain a subtle
                # full-width marker as well as the gutter dot.
                bg = QtGui.QColor(color)
                bg.setAlpha(45)
                dsel.format.setBackground(bg)
                dsel.format.setProperty(QtGui.QTextFormat.FullWidthSelection, True)
            selections.append(dsel)
        self.setExtraSelections(selections)

    def _diagnostic_cursor(self, diagnostic: EditorDiagnostic) -> QtGui.QTextCursor:
        location = diagnostic.range
        assert location is not None
        start = self.document().findBlockByNumber(location.start_line - 1)
        end = self.document().findBlockByNumber(location.last_line - 1)
        if not start.isValid() or not end.isValid():
            return QtGui.QTextCursor(self.document())

        start_column = min(location.start_column, len(start.text()))
        if location.end_column is None:
            end_column = len(end.text())
        else:
            end_column = min(location.end_column, len(end.text()))
        cursor = QtGui.QTextCursor(self.document())
        cursor.setPosition(start.position() + start_column)
        cursor.setPosition(end.position() + end_column, QtGui.QTextCursor.KeepAnchor)
        return cursor

    # -- diagnostic inspection -------------------------------------------------

    def line_at_y(self, y: int) -> int:
        return self.cursorForPosition(QtCore.QPoint(0, y)).blockNumber() + 1

    def show_diagnostic_tooltip(self, line: int, global_pos) -> None:
        diagnostics = self.diagnostics_at_line(line)
        if not diagnostics:
            self.hide_diagnostic_tooltip()
            return
        if self._tooltip_line == line:
            return
        self._tooltip_line = line
        QtWidgets.QToolTip.showText(
            global_pos + QtCore.QPoint(10, 12),
            self.diagnostics_html(diagnostics),
            self,
        )

    def hide_diagnostic_tooltip(self) -> None:
        if self._tooltip_line is not None:
            QtWidgets.QToolTip.hideText()
            self._tooltip_line = None

    def show_diagnostic_menu(self, line: int, global_pos) -> None:
        """Open a selectable, copyable diagnostic inspector from the gutter."""
        diagnostics = self.diagnostics_at_line(line)
        if not diagnostics:
            return
        self.hide_diagnostic_tooltip()
        menu = QtWidgets.QMenu(self)
        menu.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        label = QtWidgets.QLabel(self.diagnostics_html(diagnostics), menu)
        label.setContentsMargins(10, 8, 10, 8)
        label.setMaximumWidth(620)
        label.setWordWrap(True)
        label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        widget_action = QtWidgets.QWidgetAction(menu)
        widget_action.setDefaultWidget(label)
        menu.addAction(widget_action)
        menu.addSeparator()
        copy_action = menu.addAction("Copy diagnostic")
        copy_action.triggered.connect(
            lambda: QtWidgets.QApplication.clipboard().setText(
                self.diagnostics_text(diagnostics)
            )
        )
        self._diagnostic_menu = menu
        menu.popup(global_pos)

    @staticmethod
    def diagnostics_html(diagnostics: list[EditorDiagnostic]) -> str:
        sections = []
        for diagnostic in diagnostics:
            title = html.escape(diagnostic.message).replace("\n", "<br>")
            frames = []
            for frame in diagnostic.traceback:
                location = f"{os.path.basename(frame.file) or frame.file}:{frame.line}"
                frames.append(f"<code>{html.escape(location)}</code>")
                if frame.code:
                    frames.append(
                        f"<br><code>&nbsp;&nbsp;{html.escape(frame.code)}</code>"
                    )
            trace = "<br>".join(frames)
            sections.append(
                f"<b>{html.escape(diagnostic.source.title())} error</b><br>"
                f"{title}" + (f"<hr>{trace}" if trace else "")
            )
        return "<br><br>".join(sections)

    @staticmethod
    def diagnostics_text(diagnostics: list[EditorDiagnostic]) -> str:
        return format_diagnostics_text(diagnostics)

    def mouseMoveEvent(self, event):  # noqa: N802
        self.show_diagnostic_tooltip(
            self.line_at_y(event.pos().y()), self.viewport().mapToGlobal(event.pos())
        )
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self.hide_diagnostic_tooltip()
        super().leaveEvent(event)

    # -- key handling -----------------------------------------------------------

    def keyPressEvent(self, event):  # noqa: N802
        popup = self._completer.popup()
        if popup.isVisible() and event.key() in (
                QtCore.Qt.Key_Enter, QtCore.Qt.Key_Return, QtCore.Qt.Key_Tab):
            event.ignore()
            return

        mod = event.modifiers()
        key = event.key()
        ctrl = mod & QtCore.Qt.ControlModifier

        if ctrl and key in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            self.run_requested.emit()
            return
        if ctrl and key == QtCore.Qt.Key_S:
            self.save_requested.emit()
            return
        if ctrl and key == QtCore.Qt.Key_Space:
            self._request_completions()
            return
        if key == QtCore.Qt.Key_Tab and not popup.isVisible():
            self.textCursor().insertText(INDENT)
            return
        if key in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            self._auto_indent()
            return

        super().keyPressEvent(event)

        if event.text() == ".":
            self._request_completions()
        elif event.text() == "(":
            self._request_signatures()
        elif event.text() == ")" or key == QtCore.Qt.Key_Escape:
            QtWidgets.QToolTip.hideText()
        elif popup.isVisible():
            self._refilter_popup()

    def _auto_indent(self) -> None:
        cursor = self.textCursor()
        line = cursor.block().text()[:cursor.positionInBlock()]
        indent = line[:len(line) - len(line.lstrip())]
        if line.rstrip().endswith(":"):
            indent += INDENT
        cursor.insertText("\n" + indent)

    # -- completions -------------------------------------------------------------

    def _cursor_pos(self) -> tuple[int, int]:
        cursor = self.textCursor()
        return cursor.blockNumber() + 1, cursor.positionInBlock()

    def _request_completions(self) -> None:
        line, col = self._cursor_pos()
        self.completions_wanted.emit(self.toPlainText(), line, col)

    # -- signature calltips --------------------------------------------------

    def _request_signatures(self) -> None:
        line, col = self._cursor_pos()
        self.signatures_wanted.emit(self.toPlainText(), line, col)

    def show_signatures(self, sigs: list) -> None:
        """Called by the panel when the kernel answers (GUI thread). Shown
        as a tooltip anchored under the cursor; hidden on ')' or Escape."""
        if not sigs:
            return
        import html

        shown = "<br>".join(html.escape(s) for s in sigs[:3])
        if len(sigs) > 3:
            shown += f"<br><i>… and {len(sigs) - 3} more</i>"
        rect = self.cursorRect()
        QtWidgets.QToolTip.showText(
            self.viewport().mapToGlobal(rect.bottomLeft() + QtCore.QPoint(0, 6)),
            f"<code>{shown}</code>", self)

    def show_completions(self, items: list) -> None:
        """Called by the panel when the kernel answers (GUI thread)."""
        names = [i["name"] for i in items]
        if not names:
            self._completer.popup().hide()
            return
        self._completer.setModel(QtCore.QStringListModel(names, self._completer))
        self._completer.setCompletionPrefix(self._word_prefix())
        rect = self.cursorRect()
        rect.setWidth(self._completer.popup().sizeHintForColumn(0)
                      + self._completer.popup().verticalScrollBar().sizeHint().width())
        self._completer.complete(rect)

    def _word_prefix(self) -> str:
        cursor = self.textCursor()
        text = cursor.block().text()[:cursor.positionInBlock()]
        i = len(text)
        while i > 0 and (text[i - 1].isalnum() or text[i - 1] == "_"):
            i -= 1
        return text[i:]

    def _refilter_popup(self) -> None:
        prefix = self._word_prefix()
        self._completer.setCompletionPrefix(prefix)
        if self._completer.completionCount() == 0:
            self._completer.popup().hide()

    def _insert_completion(self, name: str) -> None:
        prefix = self._word_prefix()
        self.textCursor().insertText(name[len(prefix):])
