"""The editor widget: QPlainTextEdit with line numbers, auto-indent,
current-line + error-line highlighting, and a completion popup fed
asynchronously by the kernel (see panel.py for the wiring)."""

from __future__ import annotations

from PySide import QtCore, QtGui, QtWidgets  # FreeCAD's PySide shim

from .highlighter import PythonHighlighter

INDENT = "    "


class _LineNumberArea(QtWidgets.QWidget):
    def __init__(self, editor: CodeEditor):
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self):  # noqa: N802
        return QtCore.QSize(self._editor.line_number_width(), 0)

    def paintEvent(self, event):  # noqa: N802
        self._editor.paint_line_numbers(event)


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
        self._error_line: int | None = None

        self.blockCountChanged.connect(self._update_margin)
        self.updateRequest.connect(self._update_line_area)
        self.cursorPositionChanged.connect(self._update_extra_selections)
        self._update_margin()

        self._completer = QtWidgets.QCompleter([], self)
        self._completer.setWidget(self)
        self._completer.setCompletionMode(QtWidgets.QCompleter.PopupCompletion)
        self._completer.setCaseSensitivity(QtCore.Qt.CaseInsensitive)
        self._completer.activated.connect(self._insert_completion)

    # -- line numbers ----------------------------------------------------------

    def line_number_width(self) -> int:
        digits = max(2, len(str(self.blockCount())))
        return 12 + self.fontMetrics().horizontalAdvance("9") * digits

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
        painter.fillRect(event.rect(), self.palette().alternateBase())
        block = self.firstVisibleBlock()
        top = self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible():
                num = block.blockNumber() + 1
                painter.setPen(QtGui.QColor("#d16969") if num == self._error_line
                               else self.palette().text().color())
                painter.drawText(0, int(top), self._line_numbers.width() - 6,
                                 self.fontMetrics().height(),
                                 QtCore.Qt.AlignRight, str(num))
            top += self.blockBoundingRect(block).height()
            block = block.next()

    # -- error + current line highlight ---------------------------------------

    def set_error_line(self, line: int | None, focus: bool = False) -> None:
        """Highlight (and on focus=True, jump to) the failing line. Autosave
        passes focus=False — yanking the cursor mid-typing is hostile."""
        self._error_line = line
        self._update_extra_selections()
        self._line_numbers.update()
        if line is not None and focus:
            block = self.document().findBlockByNumber(line - 1)
            if block.isValid():
                cursor = QtGui.QTextCursor(block)
                self.setTextCursor(cursor)

    def _update_extra_selections(self) -> None:
        selections = []
        # current line
        sel = QtWidgets.QTextEdit.ExtraSelection()
        color = self.palette().alternateBase().color()
        sel.format.setBackground(color)
        sel.format.setProperty(QtGui.QTextFormat.FullWidthSelection, True)
        sel.cursor = self.textCursor()
        sel.cursor.clearSelection()
        selections.append(sel)
        # error line
        if self._error_line is not None:
            block = self.document().findBlockByNumber(self._error_line - 1)
            if block.isValid():
                esel = QtWidgets.QTextEdit.ExtraSelection()
                esel.format.setBackground(QtGui.QColor(209, 105, 105, 60))
                esel.format.setProperty(QtGui.QTextFormat.FullWidthSelection, True)
                esel.cursor = QtGui.QTextCursor(block)
                selections.append(esel)
        self.setExtraSelections(selections)

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
