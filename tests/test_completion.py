"""Kernel completion engine tests — run against real jedi + build123d
where available (CI kernel-tests job installs both)."""

import os
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(REPO, "kernel"))

jedi = pytest.importorskip("jedi", reason="jedi not installed")

from fc_code_kernel import completion  # noqa: E402
from fc_code_kernel.executor import run_script  # noqa: E402


def test_static_completion_without_namespace():
    # No prior run: tier 3 (static jedi) should still answer.
    items = completion.complete("import json\njson.du", 2, 7, path="/nonexistent.py")
    names = [i["name"] for i in items]
    assert "dump" in names and "dumps" in names


def test_dir_fallback_on_attribute_chain():
    ns = {"data": {"a": 1}}
    completion.stash_namespace("/fake.py", ns)
    items = completion._dir_complete("data.ke", 1, 7, ns)
    assert [i["name"] for i in items] == ["keys"]
    assert items[0]["complete"] == "ys"


def test_dir_fallback_never_calls():
    class Boom:
        def __call__(self):
            raise AssertionError("must not be called")

    ns = {"f": Boom()}
    # 'f().' contains a call — the chain regex must reject it.
    assert completion._dir_complete("f().att", 1, 7, ns) == []


@pytest.mark.skipif(
    not pytest.importorskip("importlib.util").find_spec("build123d"),
    reason="build123d not installed",
)
def test_live_namespace_completion_after_run(tmp_path):
    """The headline behavior: star-import build123d code completes because
    the kernel executed it and holds the live namespace."""
    script = tmp_path / "model.py"
    script.write_text(
        "from build123d import *\n"
        "with BuildPart() as bp:\n"
        "    Box(10, 20, 30)\n"
    )
    result = run_script(path=str(script), source=None, params={})
    assert result["error"] is None

    # star-imported top-level name (static jedi fails at this)
    items = completion.complete("fill", 1, 4, path=str(script))
    assert "fillet" in [i["name"] for i in items]

    # the case jedi.Interpreter crashes on -> dir() fallback must answer
    items = completion.complete("bp.part.", 1, 8, path=str(script))
    names = [i["name"] for i in items]
    assert len(names) > 50 and "bounding_box" in names

    # signatures survive too
    sigs = completion.signatures("extrude(", 1, 8, path=str(script))
    assert sigs and "extrude" in sigs[0]


def test_failed_run_still_stashes_imports(tmp_path):
    script = tmp_path / "broken.py"
    script.write_text("import json\nraise ValueError('x')\n")
    result = run_script(path=str(script), source=None, params={})
    assert result["error"] is not None
    items = completion.complete("json.du", 1, 7, path=str(script))
    assert "dumps" in [i["name"] for i in items]
