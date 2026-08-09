"""FreeCAD GUI-side capture driver. Invoked only by generate.py."""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import FreeCAD as App  # type: ignore[import-not-found]
import FreeCADGui as Gui  # type: ignore[import-not-found]
from PIL import Image, ImageStat
from pivy import coin  # type: ignore[import-not-found]
from PySide import QtCore, QtGui, QtWidgets  # type: ignore[import-not-found]

ROOT = Path(os.environ["FC_MEDIA_REPO_ROOT"])
SCENARIO = os.environ["FC_MEDIA_SCENARIO"]
OUTPUT = Path(os.environ["FC_MEDIA_OUTPUT"])
FRAMES = Path(os.environ["FC_MEDIA_FRAME_DIR"])
KERNEL = Path(os.environ["FC_MEDIA_KERNEL_ENV"])
EXPECTED_VERSION = "1.0.2"
OPEN_DARK = Path(App.getUserAppDataDir()) / "Mod/OpenTheme/OpenDark/OpenDark.qss"
SOCIAL_PREVIEW_CODE_START = "# social-preview-code:start"
SOCIAL_PREVIEW_CODE_END = "# social-preview-code:end"


def wait_for(predicate, description: str, timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QtWidgets.QApplication.processEvents()
        if predicate():
            return
        QtCore.QThread.msleep(25)
    raise RuntimeError(f"Timed out waiting for {description} after {timeout:.0f}s")


def version() -> str:
    values = App.Version()
    return ".".join(str(value) for value in values[:3])


def apply_visual_theme() -> None:
    if not OPEN_DARK.is_file():
        raise RuntimeError(f"OpenDark stylesheet is unavailable: {OPEN_DARK}")
    main_window_prefs = App.ParamGet("User parameter:BaseApp/Preferences/MainWindow")
    main_window_prefs.SetString("StyleSheet", str(OPEN_DARK))
    QtWidgets.QApplication.instance().setStyleSheet(
        OPEN_DARK.read_text(encoding="utf-8")
        + "\nQDockWidget::title { background-color: #212529; color: #dee2e6; }\n"
    )
    App.ParamGet("User parameter:BaseApp/Preferences/Mod/CodeWorkbench").SetString(
        "EnvDir", str(KERNEL)
    )
    view_prefs = App.ParamGet("User parameter:BaseApp/Preferences/View")
    view_prefs.SetBool("Simple", False)
    view_prefs.SetBool("Gradient", False)
    view_prefs.SetBool("RadialGradient", True)
    view_prefs.SetBool("UseBackgroundColorMid", False)
    view_prefs.SetUnsigned("BackgroundColor", 556083711)
    view_prefs.SetUnsigned("BackgroundColor2", 876232959)
    view_prefs.SetUnsigned("BackgroundColor3", 556083711)
    view_prefs.SetUnsigned("BackgroundColor4", 556083711)


def style_ribbon() -> None:
    for ribbon in Gui.getMainWindow().findChildren(QtWidgets.QDockWidget, "Ribbon"):
        ribbon.setStyleSheet(
            ribbon.styleSheet()
            + "\nRibbonPanelTitle, RibbonToolButton, QToolButton "
            "{ color: #dee2e6; }\n"
        )
        for tabs in ribbon.findChildren(QtWidgets.QTabBar):
            tabs.setStyleSheet(tabs.styleSheet() + "\nQTabBar::tab { color: #dee2e6; }\n")
            for index in range(tabs.count()):
                if tabs.tabText(index) == "Code":
                    tabs.setCurrentIndex(index)
            tabs.clearFocus()
            QtWidgets.QApplication.sendEvent(tabs, QtCore.QEvent(QtCore.QEvent.Leave))


def dismiss_first_start() -> bool:
    """Complete FreeCAD's asynchronous first-start page before document setup."""
    main = Gui.getMainWindow()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        QtWidgets.QApplication.processEvents()
        button = next(
            (
                child
                for child in main.findChildren(QtWidgets.QAbstractButton)
                if child.isVisible() and child.text().strip().lower() == "done"
            ),
            None,
        )
        if button is not None:
            button.click()
            wait_for(lambda button=button: not button.isVisible(), "first-start page dismissal", 10)
            apply_visual_theme()
            Gui.activateWorkbench("CodeWorkbench")
            style_ribbon()
            return True
        QtCore.QThread.msleep(25)
    return False


def configure_window() -> None:
    main = Gui.getMainWindow()
    main.resize(1600, 1000)
    apply_visual_theme()
    Gui.activateWorkbench("CodeWorkbench")
    wait_for(lambda: Gui.activeWorkbench().name() == "CodeWorkbench", "Code Workbench activation")
    wait_for(
        lambda: bool(main.findChildren(QtWidgets.QDockWidget, "Ribbon")),
        "Ribbon UI initialization",
    )
    style_ribbon()
    for dock in main.findChildren(QtWidgets.QDockWidget):
        title = dock.windowTitle().lower()
        if any(name in title for name in ("report", "python console", "selection", "task")):
            dock.hide()
    main.showFullScreen()
    for window in QtWidgets.QApplication.topLevelWidgets():
        if window is not main and window.isVisible():
            window.hide()
    QtWidgets.QApplication.processEvents()
    dismiss_first_start()


def combo_view() -> QtWidgets.QDockWidget | None:
    for dock in Gui.getMainWindow().findChildren(QtWidgets.QDockWidget):
        if "combo view" in dock.windowTitle().lower():
            dock.show()
            dock.setFixedWidth(300)
            return dock
    return None


def select_in_model_tree(obj) -> None:
    """Select through the model tree so Combo View receives its Qt selection signal."""
    for tree in Gui.getMainWindow().findChildren(QtWidgets.QTreeView):
        model = tree.model()
        if model is None:
            continue
        for row in range(model.rowCount()):
            document = model.index(row, 0)
            for child_row in range(model.rowCount(document)):
                item = model.index(child_row, 0, document)
                if model.data(item) == obj.Label:
                    tree.scrollTo(item)
                    point = tree.visualRect(item).center()
                    for event_type, buttons in (
                        (QtCore.QEvent.MouseButtonPress, QtCore.Qt.LeftButton),
                        (QtCore.QEvent.MouseButtonRelease, QtCore.Qt.NoButton),
                    ):
                        QtWidgets.QApplication.sendEvent(
                            tree.viewport(),
                            QtGui.QMouseEvent(
                                event_type,
                                point,
                                QtCore.Qt.LeftButton,
                                buttons,
                                QtCore.Qt.NoModifier,
                            ),
                        )
                    return
    raise RuntimeError("Could not locate the script object in the model tree")


def parameters_visible() -> bool:
    for tree in Gui.getMainWindow().findChildren(QtWidgets.QTreeView):
        model = tree.model()
        if model is None:
            continue
        for row in range(model.rowCount()):
            group = model.index(row, 0)
            if model.data(group) == "Parameters":
                tree.expand(group)
                return model.rowCount(group) >= 4
    return False


def setup_object():
    for document in list(App.listDocuments().values()):
        App.closeDocument(document.Name)
    source_name = "bearing_flange"
    source = ROOT / "examples" / f"{source_name}.py"
    copied = ROOT / ".media-cache/scenarios" / f"{source_name}_{SCENARIO}.py"
    if SCENARIO in {"hero", "opening-frame"}:
        source_text = source.read_text(encoding="utf-8")
        try:
            source_text = (
                source_text.split(SOCIAL_PREVIEW_CODE_START, 1)[1]
                .split(SOCIAL_PREVIEW_CODE_END, 1)[0]
                .strip("\n")
                + "\n"
            )
        except IndexError as exc:
            raise RuntimeError(f"Missing social preview markers in {source}") from exc
        copied.write_text(source_text, encoding="utf-8")
    else:
        shutil.copyfile(source, copied)
    from freecad.code.feature import make_script_object

    document = App.newDocument("Media")
    obj = make_script_object(document, str(copied))
    obj.AutoWatch = False
    document.recompute()
    wait_for(
        lambda: not getattr(obj.Proxy, "last_error", None) and not obj.Shape.isNull(),
        "first successful script recompute",
        180,
    )
    view = Gui.activeDocument().activeView()
    view.setAnimationEnabled(False)
    view.viewAxonometric()
    view.fitAll()
    probe = ROOT / ".media-cache" / "view-probe.png"

    def rendered() -> bool:
        view.redraw()
        view.saveImage(str(probe), 640, 480, "Current")
        with Image.open(probe) as image:
            return max(ImageStat.Stat(image.convert("RGB")).var) > 1

    wait_for(rendered, "non-blank 3D render", 30)
    probe.unlink(missing_ok=True)
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(obj)
    return obj


def window_pixmap():
    main = Gui.getMainWindow()
    QtGui.QCursor.setPos(main.mapToGlobal(QtCore.QPoint(main.width() // 2, 8)))
    main.repaint()
    if Gui.activeDocument() is not None:
        Gui.activeDocument().activeView().redraw()
    QtWidgets.QApplication.processEvents()
    # Keep the mandated Qt widget grab as the primary API. On macOS FreeCAD's
    # OpenGL layer is not included in it, so capture the full-screen Qt window
    # through QScreen instead of mixing two sources frame by frame.
    pixmap = main.grab()
    screen = QtWidgets.QApplication.primaryScreen()
    window = main.windowHandle()
    if window is None:
        return pixmap
    pixmap = screen.grabWindow(window.winId())
    return pixmap


def show_parameters(obj) -> None:
    combo_view()
    # The property editor is FreeCAD's real Combo View widget. Selecting the
    # object causes it to populate; expanding all groups exposes Parameters.
    for tree in Gui.getMainWindow().findChildren(QtWidgets.QTreeView):
        tree.expandAll()
    for tabs in Gui.getMainWindow().findChildren(QtWidgets.QTabWidget):
        for index in range(tabs.count()):
            if tabs.tabText(index).lower() == "data":
                tabs.setCurrentIndex(index)
    Gui.Selection.clearSelection()
    for tree in Gui.getMainWindow().findChildren(QtWidgets.QTreeView):
        tree.clearSelection()
        tree.setCurrentIndex(QtCore.QModelIndex())
    QtWidgets.QApplication.processEvents()
    select_in_model_tree(obj)
    QtWidgets.QApplication.processEvents()
    for tabs in Gui.getMainWindow().findChildren(QtWidgets.QTabWidget):
        for index in range(tabs.count()):
            if tabs.tabText(index).lower() == "data":
                tabs.setCurrentIndex(index)
    wait_for(
        lambda: all(
            name in obj.PropertiesList
            for name in ("bolt_hole_d", "bolt_spacing", "bore_d", "housing_d", "thickness")
        ),
        "parameter properties",
    )
    wait_for(parameters_visible, "expanded Parameters group")


def editor(obj):
    from freecad.code.editor.panel import open_editor

    dock = open_editor(obj)
    dock.setStyleSheet("QDockWidget::title { background: #212529; color: #dee2e6; }")
    dock.setFixedWidth(580)
    wait_for(
        lambda: dock.editor.toPlainText().startswith("from build123d import *"),
        "embedded editor content",
    )
    return dock


def hero(obj) -> None:
    prefs = App.ParamGet("User parameter:BaseApp/Preferences/Mod/CodeWorkbench")
    prefs.SetBool("AutosaveEnabled", True)
    prefs.SetInt("AutosaveMs", 900)
    dock = editor(obj)
    show_parameters(obj)
    if dismiss_first_start():
        dock.show()
        show_parameters(obj)
    FRAMES.mkdir(parents=True, exist_ok=True)
    for path in FRAMES.glob("frame-*.png"):
        path.unlink()

    def frame(index):
        # Let the native OpenGL and dock widgets finish their pending repaint before
        # grabbing the composited window, avoiding transient backing-store text.
        main = Gui.getMainWindow()
        main.repaint()
        Gui.activeDocument().activeView().redraw()
        QtWidgets.QApplication.processEvents()
        QtCore.QThread.msleep(100)
        QtWidgets.QApplication.processEvents()
        dock._status.setText("ok · 12:00:00")
        for transient in main.findChildren(QtWidgets.QDockWidget):
            if any(
                name in transient.windowTitle().lower()
                for name in ("report", "python console", "selection", "task")
            ):
                transient.hide()
        QtWidgets.QApplication.processEvents()
        for window in QtWidgets.QApplication.topLevelWidgets():
            if window.winId() != main.winId() and window.isVisible():
                window.hide()
        raw = FRAMES / f"frame-{index:03d}.raw.png"
        window_pixmap().save(str(raw))
        with Image.open(raw) as image:
            image.convert("RGB").resize((1200, 750), Image.Resampling.LANCZOS).save(
                FRAMES / f"frame-{index:03d}.png"
            )
        raw.unlink()

    def set_ok_status():
        dock._status.setText("ok · 12:00:00")

    for i in range(20):
        frame(i)
    # macOS may display delayed backing-store diagnostics over only the second
    # capture. The opening state is otherwise unchanged, so retain the clean
    # first capture for that frame rather than include transient UI in the GIF.
    shutil.copyfile(FRAMES / "frame-000.png", FRAMES / "frame-001.png")
    cursor = dock.editor.document().find("bolt_hole_d = 9.0")
    if cursor.isNull():
        raise RuntimeError("Could not locate bolt-hole parameter source literal in editor")
    cursor.setPosition(cursor.selectionStart() + len("bolt_hole_d = "))
    cursor.movePosition(QtGui.QTextCursor.Right, QtGui.QTextCursor.KeepAnchor, 3)
    index = 20
    for value in ("8.0", "6.0", "4.0", "2.0"):
        dock.editor.setTextCursor(cursor)
        cursor.insertText(value)
        cursor.setPosition(cursor.position() - len(value))
        cursor.movePosition(QtGui.QTextCursor.Right, QtGui.QTextCursor.KeepAnchor, len(value))
        dock.editor.setTextCursor(cursor)
        dock._autosave_timer.stop()
        for _ in range(4):
            frame(index)
            index += 1
        dock._run()
        wait_for(
            lambda: not getattr(obj.Proxy, "last_error", None),
            f"bolt-hole diameter={value} recompute",
            180,
        )
        set_ok_status()
        for _ in range(9):
            frame(index)
            index += 1

    view = Gui.activeDocument().activeView()
    # Halfway between the front and top views keeps the bore readable without
    # flattening the model and its lighting into an overhead view.
    view.setCameraOrientation(
        coin.SbRotation(0.3826834324, 0.0, 0.0, 0.9238795325).getValue()
    )
    view.fitAll()
    set_ok_status()
    for i in range(index, index + 17):
        frame(i)
    index += 17
    for value in (24.0, 26.0, 28.0, 30.0, 32.0):
        if value != 24.0:
            obj.bore_d = value
            obj.touch()
            obj.Document.recompute()
            wait_for(
                lambda v=value: obj.bore_d == v and not getattr(obj.Proxy, "last_error", None),
                f"bore_d={value} recompute",
                180,
            )
            set_ok_status()
        for _ in range(7):
            frame(index)
            index += 1
    for i in range(index, index + 12):
        frame(i)
    index += 12
    with (
        Image.open(FRAMES / "frame-000.png") as first,
        Image.open(FRAMES / f"frame-{index - 1:03d}.png") as last,
    ):
        for i in range(index, index + 20):
            Image.blend(last, first, (i - index + 1) / 21).save(FRAMES / f"frame-{i:03d}.png")


def opening_frame(obj) -> None:
    dock = editor(obj)
    show_parameters(obj)
    if dismiss_first_start():
        dock.show()
        show_parameters(obj)
    main = Gui.getMainWindow()
    # Match hero's settled opening state without creating a committed fallback asset.
    main.repaint()
    Gui.activeDocument().activeView().redraw()
    QtWidgets.QApplication.processEvents()
    QtCore.QThread.msleep(250)
    QtWidgets.QApplication.processEvents()
    dock._status.setText("ok · 12:00:00")
    pixmap = window_pixmap()
    image = Image.fromqimage(pixmap.toImage()).convert("RGB")
    image.resize((1200, 750), Image.Resampling.LANCZOS).save(OUTPUT)


def main() -> None:
    if version() != EXPECTED_VERSION:
        raise RuntimeError(
            f"FreeCAD {EXPECTED_VERSION} is required for the visual baseline; found {version()}."
        )
    configure_window()
    combo_view()
    obj = setup_object()
    if SCENARIO == "hero":
        hero(obj)
    elif SCENARIO == "opening-frame":
        opening_frame(obj)
    else:
        raise RuntimeError(f"Unknown media scenario: {SCENARIO}")
    data = {
        "scenario": SCENARIO,
        "freecad_version": EXPECTED_VERSION,
        "workbench": "CodeWorkbench",
        "object": "BearingFlange",
        "parameters": {
            name: getattr(obj, name)
            for name in ("bolt_hole_d", "bolt_spacing", "bore_d", "housing_d", "thickness")
        },
        "success": True,
    }
    result = ROOT / ".media-cache/results" / f"{SCENARIO}.json"
    result.parent.mkdir(parents=True, exist_ok=True)
    result.write_text(json.dumps(data, indent=2), encoding="utf-8")
    if SCENARIO == "opening-frame":
        print(f"Wrote {OUTPUT}")
    from freecad.code.kernel_manager import KernelManager

    KernelManager.instance().stop()
    os._exit(0)


if __name__ == "__main__":
    main()
