"""Shared, source-driven CAD-to-browser rendering primitives."""

from __future__ import annotations

import html
import keyword
import math
import os
import shutil
import tempfile
import tokenize
from io import StringIO
from pathlib import Path
from types import SimpleNamespace


def _displayed(shape: object, **metadata: object) -> SimpleNamespace:
    return SimpleNamespace(shape=shape, metadata=metadata)


def execute_model(path: Path) -> SimpleNamespace:
    """Execute checked-in source with Code Workbench-compatible display hooks."""
    displayed: list[SimpleNamespace] = []

    def show(*objects: object, **options: object) -> None:
        displayed.extend(_displayed(obj, **options) for obj in objects)

    def show_object(obj: object, **options: object) -> None:
        displayed.append(_displayed(obj, **options))

    namespace = {
        "__file__": str(path),
        "__name__": "__media_model__",
        "show": show,
        "show_object": show_object,
    }
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), namespace)
    if not displayed:
        raise RuntimeError(f"Model produced no displayed shape: {path}")
    entry = displayed[-1]
    shape = unwrap_shape(entry.shape)
    if shape is None:
        raise RuntimeError(f"Displayed object has no tessellatable shape: {path}")
    return SimpleNamespace(shape=shape, metadata=entry.metadata, namespace=namespace)


def unwrap_shape(value: object) -> object | None:
    """Return the build123d/CadQuery BREP object exposing tessellate()."""
    for candidate in (value, getattr(value, "part", None), getattr(value, "wrapped", None)):
        if candidate is not None and callable(getattr(candidate, "tessellate", None)):
            return candidate
    val = getattr(value, "val", None)
    if callable(val):
        candidate = val()
        for nested in (candidate, getattr(candidate, "wrapped", None)):
            if nested is not None and callable(getattr(nested, "tessellate", None)):
                return nested
    return None


def _vector(value: object) -> tuple[float, float, float]:
    return tuple(
        float(getattr(value, axis) if hasattr(value, axis) else getattr(value, axis.lower()))
        for axis in "XYZ"
    )


def _subtract(a, b):
    return tuple(x - y for x, y in zip(a, b, strict=True))


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def rotate_x_then_z(vector, x_angle=-1.18, z_angle=0.62):
    x, y, z = vector
    c, s = math.cos(x_angle), math.sin(x_angle)
    y, z = y * c - z * s, y * s + z * c
    c, s = math.cos(z_angle), math.sin(z_angle)
    return (x * c - y * s, x * s + y * c, z)


def mesh_from_shape(shape: object) -> dict[str, object]:
    vertices, triangles = shape.tessellate(0.22, 0.12)
    positions = [_vector(vertex) for vertex in vertices]
    normals = [[0.0, 0.0, 0.0] for _ in vertices]
    for triangle in triangles:
        a, b, c = (positions[index] for index in triangle)
        normal = _cross(_subtract(b, a), _subtract(c, a))
        for index in triangle:
            normals[index] = [normals[index][i] + normal[i] for i in range(3)]
    normalized = []
    for normal in normals:
        length = math.sqrt(sum(component * component for component in normal))
        normalized.append((0.0, 0.0, 1.0) if not length else tuple(x / length for x in normal))
    bounds_method = getattr(shape, "bounding_box", None) or getattr(shape, "BoundingBox", None)
    if not callable(bounds_method):
        raise RuntimeError("Shape does not expose a bounding-box API")
    bounds = bounds_method()
    minimum = getattr(bounds, "min", None)
    maximum = getattr(bounds, "max", None)
    if minimum is None or maximum is None:
        minimum = SimpleNamespace(X=bounds.xmin, Y=bounds.ymin, Z=bounds.zmin)
        maximum = SimpleNamespace(X=bounds.xmax, Y=bounds.ymax, Z=bounds.zmax)
    center = (
        (_vector(minimum)[0] + _vector(maximum)[0]) / 2,
        (_vector(minimum)[1] + _vector(maximum)[1]) / 2,
        (_vector(minimum)[2] + _vector(maximum)[2]) / 2,
    )
    positions = [rotate_x_then_z(_subtract(position, center)) for position in positions]
    normalized = [rotate_x_then_z(normal) for normal in normalized]

    def flatten(values):
        return [round(component, 5) for value in values for component in value]

    xs, ys = zip(*((point[0], point[1]) for point in positions), strict=True)
    return {
        "positions": flatten(positions),
        "normals": flatten(normalized),
        "indices": [i for tri in triangles for i in tri],
        "bounds": {
            "min_x": round(min(xs), 5),
            "max_x": round(max(xs), 5),
            "min_y": round(min(ys), 5),
            "max_y": round(max(ys), 5),
        },
    }


def highlight_python(source: str) -> str:
    lines, output = source.splitlines(keepends=True), []
    row, column, expect_call = 1, 0, False

    def between(start, end):
        if start[0] == end[0]:
            return lines[start[0] - 1][start[1] : end[1]]
        return (
            lines[start[0] - 1][start[1] :]
            + "".join(lines[start[0] : end[0] - 1])
            + lines[end[0] - 1][: end[1]]
        )

    for token in tokenize.generate_tokens(StringIO(source).readline):
        if token.type in {tokenize.ENCODING, tokenize.ENDMARKER}:
            continue
        output.append(html.escape(between((row, column), token.start)))
        css = (
            "kw"
            if token.type == tokenize.NAME and keyword.iskeyword(token.string)
            else "str"
            if token.type == tokenize.STRING
            else "num"
            if token.type == tokenize.NUMBER
            else "comment"
            if token.type == tokenize.COMMENT
            else "call"
            if token.type == tokenize.NAME and (expect_call or token.string == "show_object")
            else ""
        )
        escaped = html.escape(token.string)
        output.append(f'<span class="{css}">{escaped}</span>' if css else escaped)
        expect_call, (row, column) = (
            token.type == tokenize.NAME and token.string in {"def", "class"},
            token.end,
        )
    return "".join(output)


def browser_candidates() -> list[Path]:
    configured = os.environ.get("SOCIAL_PREVIEW_BROWSER")
    candidates = [Path(configured).expanduser()] if configured else []
    candidates += [
        Path(p)
        for p in (
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        )
    ]
    candidates += [
        Path(found)
        for name in ("chromium", "chromium-browser", "google-chrome", "chrome")
        if (found := shutil.which(name))
    ]
    return [candidate for candidate in candidates if candidate.is_file()]


def screenshot_html(
    rendered: str, output: Path, width: int, height: int, browser: Path | None = None
) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright is required; run this command through uv.") from exc
    with tempfile.TemporaryDirectory(prefix="freecad-media-") as directory:
        html_path = Path(directory) / "render.html"
        html_path.write_text(rendered, encoding="utf-8")
        with sync_playwright() as playwright:
            executable = browser or (browser_candidates() or [None])[0]
            options = {"headless": True}
            if executable:
                options["executable_path"] = str(executable)
            try:
                instance = playwright.chromium.launch(**options)
            except Exception as exc:
                raise RuntimeError(
                    "Chromium is unavailable; install it or set SOCIAL_PREVIEW_BROWSER."
                ) from exc
            try:
                context = instance.new_context(
                    viewport={"width": width, "height": height}, device_scale_factor=1
                )
                page = context.new_page()
                page.goto(html_path.as_uri(), wait_until="load")
                page.wait_for_function("window.previewReady === true", timeout=30_000)
                output.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(output), animations="disabled", scale="css")
            finally:
                instance.close()
