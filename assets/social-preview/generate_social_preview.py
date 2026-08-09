#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10,<3.15"
# dependencies = [
#   "build123d==0.11.1",
#   "cadquery==2.8.0",
#   "playwright==1.54.0",
#   "Pillow",
# ]
# ///
"""Generate the social preview from the canonical bearing flange source."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from assets.media.render_common import (
    execute_model,
    highlight_python,
    mesh_from_shape,
    screenshot_html,
)

WIDTH, HEIGHT = 1280, 640
ASSET_DIR = Path(__file__).resolve().parent
MODEL_PATH = ROOT / "examples/bearing_flange.py"
TEMPLATE_PATH = ASSET_DIR / "social_preview.html"
LOGO_PATH = ROOT / "freecad/code/resources/icons/code_workbench.svg"
CODE_START, CODE_END = "# social-preview-code:start", "# social-preview-code:end"


def displayed_source() -> str:
    source = MODEL_PATH.read_text(encoding="utf-8")
    try:
        return source.split(CODE_START, 1)[1].split(CODE_END, 1)[0].strip("\n") + "\n"
    except IndexError as exc:
        raise RuntimeError(f"Missing preview markers in {MODEL_PATH}") from exc


def render_html(model, mesh) -> str:
    logo = re.sub(r"<\?xml[^>]*>\s*", "", LOGO_PATH.read_text(encoding="utf-8"))
    values = {
        "LOGO_SVG": logo.replace(
            'width="64" height="64"', 'width="64" height="64" aria-hidden="true"'
        ),
        "MESH_JSON": json.dumps(mesh, separators=(",", ":")),
        "CODE_HTML": highlight_python(displayed_source()),
    }
    for key in ("bolt_spacing", "bore_d", "housing_d", "thickness"):
        values[key.upper()] = f"{model.namespace[key]:.2f}"
    rendered = TEMPLATE_PATH.read_text(encoding="utf-8")
    for key, value in values.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", value)
    unresolved = re.findall(r"{{[A-Z_]+}}", rendered)
    if unresolved:
        raise RuntimeError(f"Unresolved template placeholders: {', '.join(unresolved)}")
    return rendered


def generate(output: Path, *, browser_path: Path | None = None, keep_html: bool = False) -> None:
    model = execute_model(MODEL_PATH)
    rendered = render_html(model, mesh_from_shape(model.shape))
    if keep_html:
        output.with_suffix(".html").write_text(rendered, encoding="utf-8")
    screenshot_html(rendered, output, WIDTH, HEIGHT, browser_path)
    print(f"Wrote {output} ({WIDTH}x{HEIGHT})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ASSET_DIR / "social-preview.png")
    parser.add_argument("--browser", type=Path)
    parser.add_argument("--keep-html", action="store_true")
    args = parser.parse_args()
    generate(args.output, browser_path=args.browser, keep_html=args.keep_html)


if __name__ == "__main__":
    main()
