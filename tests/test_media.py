from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "render_common", ROOT / "assets/media/render_common.py"
)
render_common = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(render_common)
GEN_SPEC = importlib.util.spec_from_file_location(
    "media_generate", ROOT / "assets/media/generate.py"
)
media_generate = importlib.util.module_from_spec(GEN_SPEC)
assert GEN_SPEC.loader is not None
GEN_SPEC.loader.exec_module(media_generate)

MODEL = """
class Vector:
    def __init__(self, x, y, z): self.X, self.Y, self.Z = x, y, z
class Bounds:
    min = Vector(0, 0, 0)
    max = Vector(1, 1, 1)
class Shape:
    def tessellate(self, *_):
        return [Vector(0, 0, 0), Vector(1, 0, 0), Vector(0, 1, 0)], [(0, 1, 2)]
    def bounding_box(self): return Bounds()
"""


def test_injected_show_collects_shape(tmp_path: Path) -> None:
    source = tmp_path / "model.py"
    source.write_text(MODEL + "show(Shape())\n", encoding="utf-8")
    assert render_common.execute_model(source).shape is not None


def test_injected_show_object_collects_metadata(tmp_path: Path) -> None:
    source = tmp_path / "model.py"
    source.write_text(
        MODEL + "show_object(Shape(), name='test', color='steelblue')\n",
        encoding="utf-8",
    )
    model = render_common.execute_model(source)
    assert model.metadata == {"name": "test", "color": "steelblue"}


def test_model_without_shape_fails(tmp_path: Path) -> None:
    source = tmp_path / "empty.py"
    source.write_text("value = 1\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="no displayed shape"):
        render_common.execute_model(source)


def test_mesh_contains_positions_normals_and_indices(tmp_path: Path) -> None:
    source = tmp_path / "model.py"
    source.write_text(MODEL + "show(Shape())\n", encoding="utf-8")
    mesh = render_common.mesh_from_shape(render_common.execute_model(source).shape)
    assert mesh["positions"] and mesh["normals"] and mesh["indices"]


def test_image_dimension_validation(tmp_path: Path) -> None:
    image = tmp_path / "image.png"
    rendered = Image.new("RGB", (3, 2), "red")
    rendered.putpixel((0, 0), (0, 0, 255))
    rendered.save(image)
    media_generate._image(image, (3, 2))
    with pytest.raises(RuntimeError, match="dimensions"):
        media_generate._image(image, (2, 3))


def test_hero_focus_dims_only_outside_active_region() -> None:
    image = Image.new("RGB", (1200, 750), "white")
    focused = media_generate._hero_focus(image, 25)
    inside = focused.getpixel((1000, 249))[0]
    outside = focused.getpixel((400, 600))[0]
    assert inside > outside + 10


def test_hero_focus_leaves_loop_transition_untouched() -> None:
    image = Image.new("RGB", (1200, 750), "white")
    assert media_generate._hero_focus(image, 150).tobytes() == image.tobytes()


def test_freecad_result_manifest_validation(tmp_path: Path, monkeypatch) -> None:
    results = tmp_path / "results"
    results.mkdir()
    (results / "hero.json").write_text(
        json.dumps({"success": True, "freecad_version": "1.0.2", "object": "BearingFlange"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(media_generate, "CACHE", tmp_path)
    media_generate._manifest("hero")
