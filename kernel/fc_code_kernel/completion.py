"""Code intelligence served from inside the kernel venv.

Because this process *executes* the user's script, completions can resolve
against the live namespace — which is what makes build123d's star-import
convention (`from build123d import *`) and fluent chains work where static
analysis fails.

Three tiers, measured against real build123d (see DESIGN.md §6):
1. jedi.Interpreter over the stashed post-execution namespace — best
   coverage, occasional internal jedi crashes on runtime generics.
2. dir()-eval fallback for pure attribute chains — covers exactly the
   jedi crash cases, ~0.4 ms; evaluates dotted names only, never calls.
3. jedi.Script static analysis — for files that have never run.
"""

from __future__ import annotations

import re

# path -> namespace of the last execution (populated by executor.run_script)
_last_namespaces: dict[str, dict] = {}

_ATTR_CHAIN = re.compile(r"([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\.(\w*)$")


def stash_namespace(path: str, namespace: dict) -> None:
    _last_namespaces[path] = namespace


def get_namespace(path: str | None) -> dict | None:
    if path is None:
        return None
    return _last_namespaces.get(path)


def complete(source: str, line: int, column: int, path: str | None = None) -> list[dict]:
    """Completions at (line, column) — 1-based line, 0-based column."""
    ns = get_namespace(path)

    # Tier 1: live-namespace jedi.
    if ns is not None:
        items = _jedi_complete(source, line, column, namespaces=[ns])
        if items:
            return items
        # Tier 2: dir()-eval on the attribute chain before the cursor.
        items = _dir_complete(source, line, column, ns)
        if items:
            return items

    # Tier 3: static jedi (also the pre-first-run path).
    return _jedi_complete(source, line, column, namespaces=None)


def signatures(source: str, line: int, column: int, path: str | None = None) -> list[str]:
    ns = get_namespace(path)
    for namespaces in ([ns] if ns is not None else None), None:
        try:
            import jedi

            if namespaces is not None:
                script = jedi.Interpreter(source, namespaces)
            else:
                script = jedi.Script(source)
            sigs = script.get_signatures(line, column)
            if sigs:
                return [s.to_string() for s in sigs]
        except Exception:
            continue
    return []


def _jedi_complete(source: str, line: int, column: int,
                   namespaces: list | None) -> list[dict]:
    try:
        import jedi

        if namespaces is not None:
            script = jedi.Interpreter(source, namespaces)
        else:
            script = jedi.Script(source)
        comps = script.complete(line, column)
        return [
            {"name": c.name, "type": c.type, "complete": c.complete}
            for c in comps
            if not c.name.startswith("__")
        ][:200]
    except Exception:
        return []


def _dir_complete(source: str, line: int, column: int, ns: dict) -> list[dict]:
    lines = source.splitlines()
    if not (1 <= line <= len(lines)):
        return []
    prefix_text = lines[line - 1][:column]
    m = _ATTR_CHAIN.search(prefix_text)
    if m is None:
        return []
    expr, prefix = m.group(1), m.group(2)
    try:
        obj = eval(expr, ns)  # dotted names only per the regex — no calls
    except Exception:
        return []
    names = sorted(
        n for n in dir(obj)
        if n.startswith(prefix) and not n.startswith("_")
    )
    return [{"name": n, "type": "attribute", "complete": n[len(prefix):]}
            for n in names][:200]
