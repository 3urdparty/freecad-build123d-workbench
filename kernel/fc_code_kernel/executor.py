"""Script execution and parameter introspection.

Executes a build123d/CadQuery script in a fresh module namespace, injects
declared parameters, collects shown objects (or auto-discovers them), and
returns serialized BREP + metadata. Errors come back as structured
tracebacks so the workbench can map them onto editor lines.
"""

from __future__ import annotations

import ast
import contextlib
import io
import traceback
from typing import Any

from .serialize import serialize_object
from .show import ShowCollector, set_collector

SCRIPT_FILENAME = "<fc-code-script>"
PARAM_TYPES = {float: "float", int: "int", bool: "bool", str: "str"}


# -- parameter introspection (static, no execution) ---------------------------

def introspect_params(path: str) -> list[dict]:
    """Read parameter declarations without executing the script.

    Contract (DESIGN.md §5.3): module-level literal assignments whose names
    are listed in a module-level ``PARAMS = [...]`` list. Returns
    [{name, type, default, doc}].
    """
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)

    literals: dict[str, Any] = {}
    declared: list[str] | None = None
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        try:
            value = ast.literal_eval(node.value)
        except (ValueError, SyntaxError):
            continue
        if target.id == "PARAMS" and isinstance(value, list):
            declared = [str(n) for n in value]
        else:
            literals[target.id] = value

    if declared is None:
        return []
    out = []
    for name in declared:
        if name not in literals:
            continue
        default = literals[name]
        # bool before int: bool is a subclass of int.
        type_name = ("bool" if isinstance(default, bool)
                     else PARAM_TYPES.get(type(default)))
        if type_name is None:
            continue
        out.append({"name": name, "type": type_name, "default": default, "doc": ""})
    return out


# -- execution -----------------------------------------------------------------

def run_script(path: str | None, source: str | None, params: dict) -> dict:
    if source is None:
        if path is None:
            raise ValueError("kernel.run needs 'path' or 'source'")
        with open(path, encoding="utf-8") as f:
            source = f.read()

    collector = ShowCollector()
    set_collector(collector)
    namespace: dict[str, Any] = {
        "__name__": "__cq_main__",
        "__file__": path or SCRIPT_FILENAME,
        "show": collector.show,
        "show_object": collector.show_object,
    }

    stdout, stderr = io.StringIO(), io.StringIO()
    error = None
    try:
        if params:
            # Parameter injection: pre-seed the namespace with the caller's
            # values and compile the source with the corresponding literal
            # default assignments removed, so the script's own defaults don't
            # overwrite the injected values.
            namespace.update(params)
            code = _strip_param_defaults(source, set(params), path)
        else:
            code = compile(source, path or SCRIPT_FILENAME, "exec")
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exec(code, namespace)
    except Exception:
        error = _structured_traceback(path or SCRIPT_FILENAME)
    finally:
        set_collector(None)
        # Stash the (possibly partial) namespace for live completions —
        # even a failed run usually leaves the imports bound, which is
        # most of what completion needs.
        from .completion import stash_namespace

        stash_namespace(path or SCRIPT_FILENAME, namespace)

    objects = []
    if error is None:
        shown = collector.shown or _autodiscover(namespace)
        for i, item in enumerate(shown):
            try:
                objects.append(serialize_object(item, index=i))
            except Exception as exc:
                stderr.write(f"could not serialize object {i}: {exc}\n")

    return {
        "objects": objects,
        "stdout": stdout.getvalue(),
        "stderr": stderr.getvalue(),
        "error": error,
    }


def _strip_param_defaults(source: str, param_names: set, path: str | None) -> Any:
    """Compile source with declared-parameter default assignments removed, so
    injected values are not overwritten by the script's own literals."""
    tree = ast.parse(source, filename=path or SCRIPT_FILENAME)
    tree.body = [
        node for node in tree.body
        if not (isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id in param_names)
    ]
    return compile(tree, path or SCRIPT_FILENAME, "exec")


def _autodiscover(namespace: dict) -> list:
    """Fallback when a script never calls show(): collect top-level CAD objects."""
    from .show import ShownObject

    found = []
    for name, value in namespace.items():
        if name.startswith("_"):
            continue
        if _looks_like_cad_object(value):
            found.append(ShownObject(value, name, {}))
    return found


def _looks_like_cad_object(value: Any) -> bool:
    # build123d objects and CQ shapes expose .wrapped (a TopoDS_Shape);
    # CadQuery Workplane exposes .vals(). Duck-typed on purpose: no hard
    # dependency on either library from this module.
    if hasattr(value, "wrapped") and value.wrapped is not None:
        return type(value.wrapped).__name__.startswith("TopoDS")
    return callable(getattr(value, "vals", None)) and hasattr(value, "objects")


def _structured_traceback(script_filename: str) -> list[dict]:
    """Traceback frames as [{file, line, text}], innermost last, trimmed to
    frames inside the user's script where possible."""
    import sys

    tb = traceback.TracebackException(*sys.exc_info())
    exc_text = "".join(tb.format_exception_only()).strip()
    frames = [
        {"file": fr.filename, "line": fr.lineno or 0, "text": (fr.line or "").strip()}
        for fr in tb.stack
    ]
    script_frames = [f for f in frames if f["file"] == script_filename]
    out = script_frames or frames or [{"file": script_filename, "line": 0, "text": ""}]
    last = out[-1]
    last["text"] = f"{last['text']}  ({exc_text})" if last["text"] else exc_text
    return out
