#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10,<3.15"
# dependencies = ["build123d==0.11.1", "cadquery==2.8.0", "playwright==1.54.0", "Pillow"]
# ///
"""Generate deterministic README and release media.

Usage: uv run --script assets/media/generate.py COMMAND
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageStat

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from assets.media.render_common import execute_model, mesh_from_shape, screenshot_html

CACHE, DOCS = ROOT / ".media-cache", ROOT / "docs/assets"
SOCIAL = ROOT / "assets/social-preview/social-preview.png"
GALLERY = {
    ROOT / "examples/bracket.py": DOCS / "example-build123d-bracket.png",
    ROOT / "examples/cq_pillow_block.py": DOCS / "example-cadquery-pillow-block.png",
    ROOT / "examples/bearing_flange.py": DOCS / "example-build123d-bearing-flange.png",
}
HERO_GIF = DOCS / "hero-demo.gif"
OPENING_FRAME = CACHE / "hero-opening-frame.png"
HERO_CONTENT_Y_OFFSET = 56
HERO_FRAME_COUNT = 156
ADDONS = {
    "OpenTheme": Path.home() / "Library/Application Support/FreeCAD/Mod/OpenTheme",
    "FreeCAD-Ribbon": Path.home() / "Library/Application Support/FreeCAD/Mod/FreeCAD-Ribbon",
}


def executable(name: str, candidates: list[str], override: str) -> str:
    if os.environ.get(override):
        return os.environ[override]
    for candidate in candidates:
        resolved = shutil.which(candidate) if "/" not in candidate else candidate
        if resolved and Path(resolved).is_file():
            return resolved
    raise RuntimeError(f"Missing {name}. Install it or set {override}.")


def freecad_bin() -> str:
    return executable(
        "FreeCAD",
        ["/Applications/FreeCAD.app/Contents/MacOS/FreeCAD", "FreeCAD", "freecad"],
        "FREECAD_BIN",
    )


def ffmpeg_bin() -> str:
    return executable("FFmpeg", ["ffmpeg", "/opt/homebrew/bin/ffmpeg"], "FFMPEG_BIN")


def social() -> None:
    subprocess.run(
        [sys.executable, str(ROOT / "assets/social-preview/generate_social_preview.py")], check=True
    )


def gallery() -> None:
    template = (ROOT / "assets/media/model_render.html").read_text(encoding="utf-8")
    for source, output in GALLERY.items():
        if source.name == "cq_pillow_block.py":
            _ensure_cadquery()
        model = execute_model(source)
        screenshot_html(
            template.replace(
                "{{MESH_JSON}}", json.dumps(mesh_from_shape(model.shape), separators=(",", ":"))
            ),
            output,
            1200,
            750,
        )
        print(f"Wrote {output}")


def _ensure_cadquery() -> None:
    """Repair the known VTK/non-VTK OCP wheel overwrite deterministically."""
    try:
        import cadquery  # noqa: F401
    except ImportError as exc:
        if "IVtkOCC" not in str(exc):
            raise
        version = importlib.metadata.version("cadquery-ocp")
        subprocess.run(
            [
                "uv",
                "pip",
                "install",
                "--python",
                sys.executable,
                "--reinstall-package",
                "cadquery-ocp",
                f"cadquery-ocp=={version}",
            ],
            check=True,
        )
        # build123d imported the non-VTK OCP modules before the wheel repair.
        # Reload them so CadQuery sees the restored bindings.
        for name in list(sys.modules):
            if name == "OCP" or name.startswith("OCP.") or name.startswith("cadquery"):
                sys.modules.pop(name)


def _capture(scenario: str, output: Path) -> None:
    profile = CACHE / "freecad-profile"
    if profile.exists():
        shutil.rmtree(profile)
    for path in (
        profile,
        CACHE / "kernel",
        CACHE / "scenarios",
        CACHE / "frames",
        CACHE / "results",
    ):
        path.mkdir(parents=True, exist_ok=True)
    bootstrap = profile / "data" / "Mod" / "MediaCapture"
    bootstrap.mkdir(parents=True)
    for name, source in ADDONS.items():
        if not source.is_dir():
            raise RuntimeError(f"Missing required media addon: {source}")
        target = bootstrap.parent / name
        target.symlink_to(source, target_is_directory=True)
    (bootstrap / "Init.py").write_text("", encoding="utf-8")
    (bootstrap / "InitGui.py").write_text(
        """from PySide import QtCore


def capture():
    import os
    import runpy
    import traceback

    from PySide import QtWidgets

    try:
        runpy.run_path(os.environ["FC_MEDIA_CAPTURE_SCRIPT"], run_name="__main__")
    except Exception:
        traceback.print_exc()
        os._exit(1)


QtCore.QTimer.singleShot(0, capture)
""",
        encoding="utf-8",
    )
    python_startup = profile / "python_startup.py"
    python_startup.write_text(
        """import importlib.util
import sys
import sysconfig
from pathlib import Path

path = Path(sysconfig.get_path("stdlib")) / "code.py"
spec = importlib.util.spec_from_file_location("code", path)
module = importlib.util.module_from_spec(spec)
sys.modules["code"] = module
spec.loader.exec_module(module)
""",
        encoding="utf-8",
    )
    (profile / "user.cfg").write_text(
        """<?xml version="1.0" encoding="UTF-8" standalone="no" ?>
<FCParameters>
  <FCParamGroup Name="Root">
    <FCParamGroup Name="BaseApp">
      <FCParamGroup Name="Preferences">
        <FCParamGroup Name="Mod">
          <FCParamGroup Name="Start">
            <FCBool Name="FirstStart2024" Value="0"/>
          </FCParamGroup>
        </FCParamGroup>
      </FCParamGroup>
    </FCParamGroup>
  </FCParamGroup>
</FCParameters>
""",
        encoding="utf-8",
    )
    env = os.environ.copy()
    generator_bins = {Path(sys.executable).resolve().parent}
    if virtual_env := env.get("VIRTUAL_ENV"):
        generator_bins.add(
            (Path(virtual_env) / ("Scripts" if os.name == "nt" else "bin")).resolve()
        )
    for name in (
        "PYTHONHOME",
        "PYTHONPATH",
        "PYTHONSTARTUP",
        "PYTHONINSPECT",
        "PYTHONUSERBASE",
        "PYTHON_BASIC_REPL",
        "VIRTUAL_ENV",
        "CONDA_PREFIX",
        "CONDA_DEFAULT_ENV",
        "CONDA_PROMPT_MODIFIER",
        "UV",
        "UV_RUN_RECURSION_DEPTH",
        "QT_MAC_WANTS_LAYER",
    ):
        env.pop(name, None)
    env["PATH"] = os.pathsep.join(
        entry
        for entry in env.get("PATH", "").split(os.pathsep)
        if entry and Path(entry).resolve() not in generator_bins
    )
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONSTARTUP"] = str(python_startup)
    env.update(
        {
            "FC_MEDIA_REPO_ROOT": str(ROOT),
            "FC_MEDIA_SCENARIO": scenario,
            "FC_MEDIA_OUTPUT": str(output),
            "FC_MEDIA_FRAME_DIR": str(CACHE / "frames"),
            "FC_MEDIA_KERNEL_ENV": str(CACHE / "kernel"),
            "FC_MEDIA_CAPTURE_SCRIPT": str(ROOT / "assets/media/freecad_capture.py"),
            "FREECAD_USER_HOME": str(profile / "home"),
            "FREECAD_USER_DATA": str(profile / "data"),
            "FREECAD_USER_TEMP": str(profile / "temp"),
        }
    )
    command = [
        freecad_bin(),
        "--user-cfg",
        str(profile / "user.cfg"),
        "--system-cfg",
        str(profile / "system.cfg"),
        "-M",
        str(ROOT),
    ]
    subprocess.run(command, check=True, env=env)
    manifest = CACHE / "results" / f"{scenario}.json"
    if not manifest.is_file():
        raise RuntimeError(f"FreeCAD {scenario} capture produced no result manifest: {manifest}")


def hero() -> None:
    _capture("hero", HERO_GIF)
    ffmpeg = ffmpeg_bin()
    missing = [
        index
        for index in range(HERO_FRAME_COUNT)
        if not (CACHE / "frames" / f"frame-{index:03d}.png").is_file()
    ]
    if missing:
        raise RuntimeError(f"Hero capture is missing frames: {missing}")
    for index in range(HERO_FRAME_COUNT):
        path = CACHE / "frames" / f"frame-{index:03d}.png"
        with Image.open(path) as image:
            _hero_focus(image, index).save(path)
    pattern = str(CACHE / "frames/frame-%03d.png")
    palette = CACHE / "palette.png"
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-framerate",
            "10",
            "-i",
            pattern,
            "-vf",
            "scale=1200:750,palettegen=max_colors=96",
            str(palette),
        ],
        check=True,
    )
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-framerate",
            "10",
            "-i",
            pattern,
            "-i",
            str(palette),
            "-lavfi",
            "scale=1200:750[x];[x][1:v]paletteuse=dither=bayer",
            "-loop",
            "0",
            str(HERO_GIF),
        ],
        check=True,
    )
    if HERO_GIF.stat().st_size > 8 * 1024 * 1024:
        raise RuntimeError("Hero GIF exceeds 8 MB")


def opening_frame() -> None:
    _capture("opening-frame", OPENING_FRAME)
    _image(OPENING_FRAME, (1200, 750))
    print(f"Wrote {OPENING_FRAME}")


def _hero_focus(image: Image.Image, index: int) -> Image.Image:
    """Guide attention from the source edit to the geometry it changes."""
    if index < 15 or index > 135:
        return image.convert("RGB")

    strength = min(_ease((index - 14) / 6), _ease((136 - index) / 7))
    if index <= 74:
        phase = "code"
        focuses = [("round", (804, 188, 1198, 199))]
        if index >= 24:
            focuses.extend(
                [
                    ("ellipse", (305, 268, 402, 354)),
                    ("ellipse", (560, 406, 677, 503)),
                ]
            )
    elif index <= 88:
        phase = "transition"
        focuses = [("ellipse", (274, 213, 708, 550))]
    elif index <= 123:
        phase = "parameter"
        focuses = [
            ("round", (2, 457, 183, 478)),
                ("ellipse", (400, 275, 580, 455)),
        ]
    else:
        phase = "result"
        focuses = [("ellipse", (400, 290, 580, 470))]

    focuses = [(shape, _hero_box_below_toolbar(box)) for shape, box in focuses]

    result = image.convert("RGBA")
    shade_alpha = Image.new("L", result.size, round(46 * strength))
    mask_draw = ImageDraw.Draw(shade_alpha)
    for shape, box in focuses:
        if shape == "ellipse":
            mask_draw.ellipse(box, fill=0)
        else:
            mask_draw.rounded_rectangle(box, radius=9, fill=0)
    shade_alpha = shade_alpha.filter(ImageFilter.GaussianBlur(16))
    shade = Image.new("RGBA", result.size, (8, 16, 24, 0))
    shade.putalpha(shade_alpha)
    result = Image.alpha_composite(result, shade)

    cues = Image.new("RGBA", result.size)
    cue_draw = ImageDraw.Draw(cues)
    accent = (44, 201, 184)
    if phase == "code":
        cue_draw.rounded_rectangle(
            _hero_box_below_toolbar((797, 183, 800, 190)),
            radius=2,
            fill=(*accent, round(225 * strength)),
        )
    elif phase == "parameter":
        cue_draw.rounded_rectangle(
            _hero_box_below_toolbar((4, 443, 7, 460)),
            radius=2,
            fill=(*accent, round(225 * strength)),
        )

    if phase == "code" and index >= 24:
        _draw_pulse(
            cue_draw,
            _hero_box_below_toolbar((305, 268, 402, 354)),
            ((index - 24) % 13) / 13,
            strength,
        )
        _draw_pulse(
            cue_draw,
            _hero_box_below_toolbar((560, 406, 677, 503)),
            ((index - 24) % 13) / 13,
            strength,
        )
    elif phase == "transition":
        _draw_pulse(
            cue_draw,
            _hero_box_below_toolbar((274, 213, 708, 550)),
            (index - 62) / 12,
            strength,
        )
    elif phase == "parameter":
        _draw_pulse(
            cue_draw,
            _hero_box_below_toolbar((400, 275, 580, 455)),
            ((index - 89) % 7) / 7,
            strength,
        )
    return Image.alpha_composite(result, cues).convert("RGB")


def _hero_box_below_toolbar(box: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """Align overlays with content displaced by the themed Ribbon toolbar."""
    left, top, right, bottom = box
    return left, top + HERO_CONTENT_Y_OFFSET, right, bottom + HERO_CONTENT_Y_OFFSET


def _ease(value: float) -> float:
    value = max(0.0, min(1.0, value))
    return value * value * (3 - 2 * value)


def _draw_pulse(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], progress: float,
                strength: float) -> None:
    progress = max(0.0, min(1.0, progress))
    spread = round(5 + 14 * progress)
    expanded = (box[0] - spread, box[1] - spread, box[2] + spread, box[3] + spread)
    alpha = round(100 * (1 - progress) * strength)
    draw.ellipse(expanded, outline=(44, 201, 184, alpha), width=2)


def _image(path: Path, size: tuple[int, int]) -> None:
    if not path.is_file():
        raise RuntimeError(f"Missing asset: {path}")
    with Image.open(path) as image:
        if image.size != size or image.format not in {"PNG", "GIF"}:
            raise RuntimeError(f"Invalid image dimensions or format: {path}")
        if max(ImageStat.Stat(image.convert("RGB")).var) == 0:
            raise RuntimeError(f"Blank image: {path}")
        if "A" in image.getbands() and image.getchannel("A").getextrema() == (0, 0):
            raise RuntimeError(f"Fully transparent image: {path}")


def _manifest(scenario: str) -> None:
    path = CACHE / "results" / f"{scenario}.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    if (
        not data.get("success")
        or data.get("freecad_version") != "1.0.2"
        or data.get("object") != "BearingFlange"
    ):
        raise RuntimeError(f"Invalid FreeCAD result manifest: {path}")


def verify(static: bool = False) -> None:
    _image(SOCIAL, (1280, 640))
    if SOCIAL.stat().st_size >= 1024 * 1024:
        raise RuntimeError("Social preview exceeds 1 MB")
    for output in GALLERY.values():
        _image(output, (1200, 750))
    if static:
        return
    _image(HERO_GIF, (1200, 750))
    with Image.open(HERO_GIF) as gif:
        if gif.n_frames != HERO_FRAME_COUNT or gif.info.get("loop") != 0:
            raise RuntimeError(f"Hero GIF must have {HERO_FRAME_COUNT} looping frames")
        duration = (
            sum(gif.seek(i) or gif.info.get("duration", 0) for i in range(gif.n_frames)) / 1000
        )
        if not 15.5 <= duration <= 15.7:
            raise RuntimeError(f"Invalid hero GIF duration: {duration}")
    _manifest("hero")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=["social", "gallery", "hero", "opening-frame", "verify", "verify-static", "all"],
    )
    command = parser.parse_args().command
    actions = {
        "social": social,
        "gallery": gallery,
        "hero": hero,
        "opening-frame": opening_frame,
        "verify": verify,
        "verify-static": lambda: verify(True),
    }
    if command == "all":
        for name in ("social", "gallery", "hero", "verify"):
            actions[name]()
    else:
        actions[command]()


if __name__ == "__main__":
    main()
