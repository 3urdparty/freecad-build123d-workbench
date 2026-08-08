"""Executor tests that don't require OCP: params, errors, stdout, show()."""

import os
import sys
import textwrap

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(REPO, "kernel"))

from fc_code_kernel.executor import introspect_params, run_script  # noqa: E402


def test_introspect_params(tmp_path):
    script = tmp_path / "s.py"
    script.write_text(textwrap.dedent("""\
        length = 40.0
        count = 3
        rounded = True
        label = "hi"
        not_declared = 9
        PARAMS = ["length", "count", "rounded", "label", "missing"]
    """))
    params = {p["name"]: p for p in introspect_params(str(script))}
    assert params["length"] == {"name": "length", "type": "float", "default": 40.0, "doc": ""}
    assert params["count"]["type"] == "int"
    assert params["rounded"]["type"] == "bool"  # bool, not int
    assert params["label"]["type"] == "str"
    assert "not_declared" not in params
    assert "missing" not in params


def test_no_params_block(tmp_path):
    script = tmp_path / "s.py"
    script.write_text("x = 1\n")
    assert introspect_params(str(script)) == []


def test_syntax_error_maps_to_script_line(tmp_path):
    # SyntaxError locations live in exception attributes, not stack frames —
    # the naive traceback pointed users at the kernel's own ast.py.
    script = tmp_path / "s.py"
    script.write_text("x = 1\nwith Foo() as bar\n    pass\n")  # missing ':'
    result = run_script(path=str(script), source=None, params={})
    assert result["error"] is not None
    frame = result["error"][-1]
    assert frame["file"] == str(script)
    assert frame["line"] == 2
    assert frame["column"] > 0
    assert frame["message"].startswith("SyntaxError:")
    assert frame["code"] == "with Foo() as bar"
    assert "SyntaxError" in frame["text"]
    assert "ast.py" not in frame["text"]


def test_syntax_error_with_params_also_maps(tmp_path):
    # The params path parses via ast.parse (introspection + strip) — same
    # attribute-based location handling must apply there.
    script = tmp_path / "s.py"
    script.write_text("length = 1.0\nPARAMS = ['length']\ndef broken(:\n")
    result = run_script(path=str(script), source=None, params={"length": 2.0})
    assert result["error"] is not None
    frame = result["error"][-1]
    assert frame["file"] == str(script)
    assert frame["line"] == 3


def test_run_captures_stdout_and_error(tmp_path):
    script = tmp_path / "s.py"
    script.write_text("print('hello')\nraise ValueError('boom')\n")
    result = run_script(path=str(script), source=None, params={})
    assert result["stdout"] == "hello\n"
    assert result["error"] is not None
    last = result["error"][-1]
    assert last["file"] == str(script)
    assert last["line"] == 2
    assert last["message"] == "ValueError: boom"
    assert last["code"] == "raise ValueError('boom')"
    assert "ValueError: boom" in last["text"]
    assert result["objects"] == []


def test_param_injection_overrides_defaults(tmp_path):
    script = tmp_path / "s.py"
    script.write_text(textwrap.dedent("""\
        length = 10
        PARAMS = ["length"]
        print(length * 2)
    """))
    result = run_script(path=str(script), source=None, params={"length": 21})
    assert result["error"] is None
    assert result["stdout"].strip() == "42"


def test_show_collects_objects_but_serialization_needs_ocp(tmp_path):
    # A plain object isn't a CAD object; it should be reported in stderr,
    # not crash the run.
    script = tmp_path / "s.py"
    script.write_text("show(object(), names=['thing'])\n")
    result = run_script(path=str(script), source=None, params={})
    assert result["error"] is None
    assert result["objects"] == []
    assert "could not serialize" in result["stderr"]
