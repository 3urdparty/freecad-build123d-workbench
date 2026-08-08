"""Color parser tests — pure Python, no FreeCAD/Qt/OCP."""

import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from freecad.code.colors import parse_color  # noqa: E402


def test_hex_forms():
    assert parse_color("#ff0000") == (1.0, 0.0, 0.0)
    assert parse_color("#F80") == (1.0, 136 / 255.0, 0.0)
    assert parse_color("#ff880080") == (1.0, 136 / 255.0, 0.0)  # alpha ignored
    assert parse_color("#zzz") is None
    assert parse_color("#ff88") is None


def test_named():
    assert parse_color("red") == (1.0, 0.0, 0.0)
    assert parse_color("  SteelBlue ") == (0.275, 0.51, 0.706)
    assert parse_color("notacolor") is None


def test_triples():
    assert parse_color((1.0, 0.5, 0.0)) == (1.0, 0.5, 0.0)
    assert parse_color([255, 128, 0]) == (1.0, 128 / 255.0, 0.0)
    assert parse_color((1, 0, 1)) == (1.0, 0.0, 1.0)  # ambiguous -> 0-1 wins
    assert parse_color((300, 0, 0)) is None
    assert parse_color((-1, 0, 0)) is None
    assert parse_color((1.0, 0.5)) is None


def test_junk():
    assert parse_color(None) is None
    assert parse_color(42) is None
    assert parse_color({"r": 1}) is None
    assert parse_color(("a", "b", "c")) is None
