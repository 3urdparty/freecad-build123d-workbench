#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10,<3.15"
# dependencies = [
#   "build123d==0.11.1",
#   "playwright==1.54.0",
# ]
# ///
"""Generate the repository's social preview from a real build123d model.

The model source is executed, tessellated, injected into the HTML template,
and rendered to a 1280x640 PNG by a local Chromium installation. The temporary
HTML is self-contained and requires no network access.

Usage:
    uv run --script assets/social-preview/generate_social_preview.py
    uv run --script assets/social-preview/generate_social_preview.py --keep-html
"""

from __future__ import annotations

import argparse
import html
import importlib.util
import json
import keyword
import math
import os
import re
import shutil
import sys
import tempfile
import tokenize
from io import StringIO
from pathlib import Path
from types import ModuleType

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sys.exit(
        "Playwright is required. Run this script through uv:\n"
        "  uv run --script assets/social-preview/generate_social_preview.py"
    )


WIDTH = 1280
HEIGHT = 640
ASSET_DIR = Path(__file__).resolve().parent
REPO_ROOT = ASSET_DIR.parents[1]
MODEL_PATH = ASSET_DIR / "bearing_flange.py"
TEMPLATE_PATH = ASSET_DIR / "social_preview.html"
LOGO_PATH = REPO_ROOT / "freecad/code/resources/icons/code_workbench.svg"
DEFAULT_OUTPUT = ASSET_DIR / "social-preview.png"
CODE_START = "# social-preview-code:start"
CODE_END = "# social-preview-code:end"


def load_model() -> ModuleType:
    """Execute the model file and return its module."""
    spec = importlib.util.spec_from_file_location("social_preview_model", MODEL_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load model from {MODEL_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def vector_tuple(vector: object) -> tuple[float, float, float]:
    return (float(vector.X), float(vector.Y), float(vector.Z))


def subtract(
    left: tuple[float, float, float], right: tuple[float, float, float]
) -> tuple[float, float, float]:
    return tuple(a - b for a, b in zip(left, right, strict=True))


def cross(
    left: tuple[float, float, float], right: tuple[float, float, float]
) -> tuple[float, float, float]:
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def rotate_x_then_z(
    vector: tuple[float, float, float], x_angle: float, z_angle: float
) -> tuple[float, float, float]:
    """Apply the same object-space orientation used by the original 3a concept."""
    x, y, z = vector
    x_cos, x_sin = math.cos(x_angle), math.sin(x_angle)
    y, z = y * x_cos - z * x_sin, y * x_sin + z * x_cos
    z_cos, z_sin = math.cos(z_angle), math.sin(z_angle)
    return (x * z_cos - y * z_sin, x * z_sin + y * z_cos, z)


def mesh_from_shape(shape: object) -> dict[str, object]:
    """Tessellate a BREP and calculate smooth normals within each CAD face."""
    vertices, triangles = shape.tessellate(0.22, 0.12)
    raw_positions = [vector_tuple(vertex) for vertex in vertices]
    normals = [[0.0, 0.0, 0.0] for _ in vertices]

    for triangle in triangles:
        first, second, third = (raw_positions[index] for index in triangle)
        face_normal = cross(subtract(second, first), subtract(third, first))
        for index in triangle:
            normals[index][0] += face_normal[0]
            normals[index][1] += face_normal[1]
            normals[index][2] += face_normal[2]

    normalised: list[tuple[float, float, float]] = []
    for normal in normals:
        length = math.sqrt(sum(component * component for component in normal))
        if length == 0:
            normalised.append((0.0, 0.0, 1.0))
        else:
            normalised.append(tuple(component / length for component in normal))

    bounds = shape.bounding_box()
    centre = (
        (bounds.min.X + bounds.max.X) / 2,
        (bounds.min.Y + bounds.max.Y) / 2,
        (bounds.min.Z + bounds.max.Z) / 2,
    )
    centred = [subtract(position, centre) for position in raw_positions]
    positions = [rotate_x_then_z(position, -1.18, 0.62) for position in centred]
    normalised = [rotate_x_then_z(normal, -1.18, 0.62) for normal in normalised]

    def flatten(values: list[tuple[float, float, float]]) -> list[float]:
        return [round(component, 5) for value in values for component in value]

    x_values = [position[0] for position in positions]
    y_values = [position[1] for position in positions]
    return {
        "positions": flatten(positions),
        "normals": flatten(normalised),
        "indices": [index for triangle in triangles for index in triangle],
        "bounds": {
            "min_x": round(min(x_values), 5),
            "max_x": round(max(x_values), 5),
            "min_y": round(min(y_values), 5),
            "max_y": round(max(y_values), 5),
        },
    }


def displayed_source() -> str:
    source = MODEL_PATH.read_text(encoding="utf-8")
    try:
        marked = source.split(CODE_START, 1)[1].split(CODE_END, 1)[0]
    except IndexError as error:
        raise RuntimeError(f"Missing preview markers in {MODEL_PATH}") from error
    return marked.strip("\n") + "\n"


def highlight_python(source: str) -> str:
    """Render source as escaped HTML while preserving its exact layout."""
    lines = source.splitlines(keepends=True)
    output: list[str] = []
    previous_row = 1
    previous_column = 0
    expect_call_name = False

    def between(start: tuple[int, int], end: tuple[int, int]) -> str:
        start_row, start_column = start
        end_row, end_column = end
        if start_row == end_row:
            return lines[start_row - 1][start_column:end_column]
        pieces = [lines[start_row - 1][start_column:]]
        pieces.extend(lines[start_row:end_row - 1])
        pieces.append(lines[end_row - 1][:end_column])
        return "".join(pieces)

    ignored = {tokenize.ENCODING, tokenize.ENDMARKER}
    for token in tokenize.generate_tokens(StringIO(source).readline):
        if token.type in ignored:
            continue
        output.append(html.escape(between((previous_row, previous_column), token.start)))

        css_class = ""
        if token.type == tokenize.NAME and keyword.iskeyword(token.string):
            css_class = "kw"
        elif token.type == tokenize.STRING:
            css_class = "str"
        elif token.type == tokenize.NUMBER:
            css_class = "num"
        elif token.type == tokenize.COMMENT:
            css_class = "comment"
        elif token.type == tokenize.NAME and (expect_call_name or token.string == "show_object"):
            css_class = "call"

        escaped = html.escape(token.string)
        output.append(f'<span class="{css_class}">{escaped}</span>' if css_class else escaped)
        expect_call_name = token.type == tokenize.NAME and token.string in {"def", "class"}
        previous_row, previous_column = token.end

    return "".join(output)


def inline_logo() -> str:
    logo = LOGO_PATH.read_text(encoding="utf-8")
    logo = re.sub(r"<\?xml[^>]*>\s*", "", logo)
    return logo.replace('width="64" height="64"', 'width="64" height="64" aria-hidden="true"')


def render_html(model: ModuleType, mesh: dict[str, object]) -> str:
    replacements = {
        "LOGO_SVG": inline_logo(),
        "MESH_JSON": json.dumps(mesh, separators=(",", ":")),
        "CODE_HTML": highlight_python(displayed_source()),
        "BOLT_SPACING": f"{model.bolt_spacing:.2f}",
        "BORE_D": f"{model.bore_d:.2f}",
        "HOUSING_D": f"{model.housing_d:.2f}",
        "THICKNESS": f"{model.thickness:.2f}",
    }
    rendered = TEMPLATE_PATH.read_text(encoding="utf-8")
    for name, value in replacements.items():
        rendered = rendered.replace(f"{{{{{name}}}}}", value)
    unresolved = re.findall(r"{{[A-Z_]+}}", rendered)
    if unresolved:
        raise RuntimeError(f"Unresolved template placeholders: {', '.join(unresolved)}")
    return rendered


def browser_candidates() -> list[Path]:
    configured = os.environ.get("SOCIAL_PREVIEW_BROWSER")
    candidates = [
        Path(configured).expanduser() if configured else None,
        Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
        Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
    ]
    for command in ("chromium", "chromium-browser", "google-chrome", "chrome"):
        resolved = shutil.which(command)
        if resolved:
            candidates.append(Path(resolved))
    return [candidate for candidate in candidates if candidate and candidate.is_file()]


def screenshot(html_path: Path, output: Path, browser_path: Path | None) -> None:
    with sync_playwright() as playwright:
        executable = browser_path
        if executable is None:
            candidates = browser_candidates()
            executable = candidates[0] if candidates else None
        launch_options = {"headless": True}
        if executable:
            launch_options["executable_path"] = str(executable)

        try:
            browser = playwright.chromium.launch(**launch_options)
        except Exception as error:
            raise RuntimeError(
                "Could not launch Chromium. Install Chromium, pass --browser PATH, or set "
                "SOCIAL_PREVIEW_BROWSER."
            ) from error

        try:
            context = browser.new_context(
                viewport={"width": WIDTH, "height": HEIGHT},
                device_scale_factor=1,
            )
            page = context.new_page()
            page.goto(html_path.as_uri(), wait_until="load")
            page.wait_for_function("window.previewReady === true", timeout=30_000)
            output.parent.mkdir(parents=True, exist_ok=True)
            page.locator("#social-preview").screenshot(
                path=str(output),
                animations="disabled",
                scale="css",
            )
        finally:
            browser.close()


def generate(output: Path, *, browser_path: Path | None, keep_html: bool) -> None:
    model = load_model()
    mesh = mesh_from_shape(model.flange.part)
    rendered_html = render_html(model, mesh)

    if keep_html:
        html_path = output.with_suffix(".html")
        html_path.write_text(rendered_html, encoding="utf-8")
        screenshot(html_path, output, browser_path)
        print(f"Wrote {html_path}")
    else:
        with tempfile.TemporaryDirectory(prefix="freecad-social-preview-") as directory:
            html_path = Path(directory) / "social-preview.html"
            html_path.write_text(rendered_html, encoding="utf-8")
            screenshot(html_path, output, browser_path)

    print(f"Wrote {output} ({WIDTH}x{HEIGHT})")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--browser", type=Path, help="path to a Chromium-compatible browser")
    parser.add_argument(
        "--keep-html",
        action="store_true",
        help="write the populated HTML next to the PNG for debugging",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    generate(args.output, browser_path=args.browser, keep_html=args.keep_html)


if __name__ == "__main__":
    main()
