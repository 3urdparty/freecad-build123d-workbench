# Code Workbench — Design Document

**Repo:** `freecad-code-workbench` · **Namespace:** `freecad.code` · **Display name:** Code Workbench
**Status:** Draft v0.1 · 2026-08-07

A FreeCAD workbench that makes build123d and CadQuery first-class citizens inside
FreeCAD: scripts execute in an isolated kernel, results land in the FreeCAD document
as real parametric objects, and the editor is whatever you want it to be — your own
(VS Code, Neovim, …) with hot reload, or an embedded LSP-backed editor later.

---

## 1. Goals

- Run build123d and CadQuery scripts and see the results in FreeCAD's 3D view with
  sub-second feedback on edit.
- Results are **document objects**, not viewer-only geometry: they persist in
  `.FCStd` files, recompute on parameter change, and feed TechDraw, FEM, CAM,
  and Assembly downstream.
- Script parameters surface as FreeCAD properties, editable in the property panel
  and drivable from expressions and spreadsheets.
- First-class **external editor** workflow (file watch + hot reload), API-compatible
  with `ocp_vscode`'s `show()` / `show_object()` so existing scripts port unchanged.
- Zero manual environment setup: the workbench provisions and manages its own
  Python environment for build123d/CadQuery.
- Distributed as a standard addon through the FreeCAD Addon Manager.

## 2. Non-goals (for now)

- Reimplementing a code editor from scratch (Phase 2 embeds Monaco; Phase 1 has none).
- Two-way editing (changing FreeCAD geometry does not rewrite the script).
- Supporting FreeCAD < 1.0.
- Sandboxing user scripts (they are the user's own code; see §10).

## 3. Background and prior art

| Project | Lesson |
|---|---|
| [cadquery-freecad-module](https://github.com/jmwright/cadquery-freecad-module) | In-process execution inside FreeCAD's interpreter; drowned in OCCT binary conflicts and version skew. Largely dormant. |
| cq-editor (+ jdegenstein fork) | Good live-reload ergonomics, but standalone — no FreeCAD document, nothing downstream. |
| [ocp-vscode](https://github.com/bernhard-42/vscode-ocp-cad-viewer) | The modern gold standard for script-CAD UX: external editor + websocket viewer + `show()` API. Viewer-only; no CAD system behind the glass. |
| [ocp-freecad-cam](https://github.com/voneiden/ocp-freecad-cam) | Proof that build123d/CQ ↔ FreeCAD interop works, using serialized BREP across the boundary. |

**The one hard problem.** FreeCAD embeds its own Python (3.11 in the 1.1.x builds)
linked against its own OCCT build. build123d and CadQuery sit on
[OCP](https://github.com/CadQuery/OCP) wheels compiled against a *different* OCCT.
A `TopoDS_Shape` cannot be passed by pointer between the two, and importing OCP
into FreeCAD's interpreter puts two OCCT copies in one process — symbol collisions
on Linux, silent corruption, perpetual version-skew maintenance. Every prior
in-process attempt eventually failed on this.

**The decision that drives everything else:** treat the boundary as a
serialization boundary. BREP round-trips losslessly between the stacks
(`export_brep()` on the OCP side → `Part.Shape.importBrepFromString()` on the
FreeCAD side) and tolerates OCCT version differences.

## 4. Architecture overview

```
┌────────────────────────────  FreeCAD process  ───────────────────────────┐
│                                                                          │
│  Code Workbench (freecad.code, PySide6)                                  │
│  ┌──────────────┐  ┌───────────────┐  ┌──────────────────────────────┐   │
│  │ Commands /   │  │ File watcher  │  │ ScriptObject                 │   │
│  │ toolbar / UI │  │ (hot reload)  │  │ (App::FeaturePython proxy)   │   │
│  └──────┬───────┘  └──────┬────────┘  └──────────────┬───────────────┘   │
│         └─────────────────┴──────────────┬───────────┘                   │
│                                 ┌────────▼─────────┐                     │
│                                 │  KernelManager    │  venv provisioning │
│                                 │  (RPC client)     │  spawn / restart   │
│                                 └────────┬─────────┘                     │
└──────────────────────────────────────────┼───────────────────────────────┘
                            JSON-RPC over localhost TCP
                        (BREP payloads base64 in v0; see §6)
┌──────────────────────────────────────────┼───────────────────────────────┐
│  Kernel process (managed venv: own Python, OCP, build123d, cadquery)     │
│  ┌───────────────┐  ┌──────────────────┐  ┌────────────────────────────┐ │
│  │ RPC server    │  │ Script executor  │  │ show() shim (ocp_vscode-   │ │
│  │               │  │ (exec, tracebacks│  │ compatible API)            │ │
│  │               │  │  param injection)│  │                            │ │
│  └───────────────┘  └──────────────────┘  └────────────────────────────┘ │
│                        serialize.py: OCP shapes → BREP bytes + JSON meta │
└───────────────────────────────────────────────────────────────────────────┘
```

Two processes, one contract:

1. **The workbench** (runs inside FreeCAD, imports `FreeCAD`/`Part`/PySide, never OCP).
2. **The kernel** (runs in a managed venv, imports OCP/build123d/cadquery, never FreeCAD).

Neither side ever imports the other side's OCCT binding. The only geometry that
crosses the boundary is BREP bytes plus a JSON metadata sidecar.

## 5. Components

### 5.1 KernelManager (`freecad/code/kernel_manager.py`)

- Locates or creates the managed venv under FreeCAD's user app-data directory
  (`App.getUserAppDataDir()/CodeWorkbench/env`).
- Provisions with `uv` when available (fast, reproducible), falling back to
  `python -m venv` + `pip`. Installs the `fc-code-kernel` package plus
  `build123d` / `cadquery` at pinned-compatible versions.
- Spawns the kernel subprocess (`python -m fc_code_kernel --port 0`), reads the
  bound port from its stdout handshake, maintains the RPC connection.
- Restarts on crash with backoff; surfaces kernel stderr in FreeCAD's Report view.
- Exposes `run_script(path, params) -> ExecResult` to the rest of the workbench.

### 5.2 RPC layer (`freecad/code/rpc.py`, `kernel/fc_code_kernel/server.py`)

- Newline-delimited JSON-RPC 2.0 over localhost TCP. Loopback-only bind, and a
  per-session random token required on every request (see §10).
- v0 carries BREP as base64 inside the JSON result. This is simple and correct;
  if profiling shows large-assembly pain, v1 moves payloads to length-prefixed
  binary frames on the same socket (protocol has a version field from day one).
- Methods:
  - `kernel.hello() -> {version, python, occt, build123d, cadquery}`
  - `kernel.run(path|source, params, request_id) -> {objects: [...], stdout, stderr, error?}`
  - `kernel.introspect_params(path) -> [{name, type, default, doc}]`
  - `kernel.complete(source, line, column, path)` / `kernel.signatures(...)`

Runaway scripts: there is deliberately no `kernel.cancel` — a busy `exec()`
cannot be interrupted from outside. Instead each `kernel.run` is bounded by
the `RunTimeoutS` preference (default 60 s, 0 = unlimited); on timeout the
client closes the socket and KILLS the kernel process, the last good shape
is preserved, and a fresh kernel starts lazily on the next run. Crash
isolation was designed for exactly this. With editor autosave, a half-typed
`while True:` is an everyday event, not a corner case.
- An `error` is a structured traceback: `[{file, line, text}]` so the UI can map
  failures back to editor lines.

### 5.3 Script execution + `show()` shim (`kernel/fc_code_kernel/`)

- Executes the script with `exec()` in a fresh module namespace; injects declared
  parameters as module globals before execution.
- Provides `show()` / `show_object(obj, name=, options=)` with `ocp_vscode`-
  compatible signatures. Anything shown is collected; if nothing is shown
  explicitly, top-level build123d/CQ objects are auto-discovered (same heuristic
  cq-editor uses).
- `serialize.py` unwraps whatever it gets — build123d objects (`.wrapped`),
  CadQuery `Workplane` (`.vals()`), raw `TopoDS_Shape` — and emits
  `(brep_bytes, metadata)` per object. Metadata: name, color, alpha, location,
  and assembly hierarchy path.
- Parameter contract: a script declares parameters as plain module-level
  assignments guarded by the build123d community convention, or an explicit
  `PARAMS = {...}` dict. `introspect_params` reads them without full execution
  where possible (AST scan), falling back to a dry run.

### 5.4 Document integration (`freecad/code/feature.py`)

The piece that makes this *native*. Each script is a `ScriptObject`
(`App::FeaturePython` proxy) with properties:

- `SourceFile` (`App::PropertyFile`) — or `SourceInline` (`App::PropertyString`)
  for scripts embedded in the document.
- Per-parameter dynamic properties in a `Parameters` group, created from
  `introspect_params` — so they appear in the property panel and participate in
  FreeCAD expressions/spreadsheets.
- `execute()` → `KernelManager.run_script()` → `Part.Shape.importBrepFromString()`
  → assign to `obj.Shape`. Multiple shown objects become children under an
  `App::Part` group, preserving the assembly hierarchy from metadata.

Because the shape lives on a normal `Part::FeaturePython`-style object, TechDraw,
FEM meshing, CAM, and Assembly all consume it without knowing anything about
build123d.

**Topological naming caveat.** Each recompute produces a fresh shape with no
stable subshape names, so downstream face/edge references (a TechDraw dimension
on a face, a CAM operation on an edge) can break across recomputes. Mitigation
plan: deterministic subshape hashing (geometry-based matching between old and new
shapes) layered on FreeCAD 1.x's toponaming infrastructure. This is Phase 3 work;
Phase 1 documents the limitation honestly.

### 5.5 Hot reload (`freecad/code/watcher.py`)

`QFileSystemWatcher` on each ScriptObject's `SourceFile`, debounced (~200 ms,
configurable) through a `QTimer`, triggering `touch()` + `recompute()` on the
owning document. Editor-agnostic: save in VS Code, Neovim, or anything else and
FreeCAD updates. Watch state is per-object and toggleable from the toolbar.

### 5.6 Workbench UI (`freecad/code/workbench.py`, `init_gui.py`)

Phase 1 commands: New Script, Open Script (creates a ScriptObject), Re-run,
Toggle Watch, Kernel Status/Restart, Preferences. Preferences page integrates
with FreeCAD's settings dialog: venv location, package pins, debounce interval,
auto-start kernel.

## 6. Editor strategy — two tiers

**Tier 1 (Phase 1): external editors are the editor.** The watcher + `show()`
compatibility means users keep VS Code/Neovim/PyCharm, with real LSP, their own
keybindings, their own plugins — and FreeCAD becomes the viewer with a full CAD
system behind it. This is most of the value for a fraction of the effort.

**Tier 2 (Phase 2): embedded editor — native Qt + kernel-side jedi.**

The original draft proposed Monaco in a `QWebEngineView`. Research (2026-08,
verified empirically against the official FreeCAD 1.0.2 bundle) killed that
and every other off-the-shelf option:

| Option | Verdict |
|---|---|
| Monaco / QWebEngineView | Official bundles ship **no QtWebEngine** (verified: `libQt5WebEngineWidgets` absent) |
| QScintilla | Bindings are PyQt-only; FreeCAD ships PySide — dual Qt bindings in one process is unsafe |
| Spyder editor components (cq-editor's approach) | Hard PyQt5 dependency, same conflict |
| FreeCAD's built-in macro editor | C++ `Gui::PythonEditor`, not exposed for Python embedding; completions would know FreeCAD's Python, not the kernel's |
| **Custom QPlainTextEdit (chosen)** | Pure PySide, works on every build; intelligence via kernel RPC |

Code intelligence design — the architecture's hidden payoff: the kernel has
*executed* the script, so it holds the live namespace. Completions resolve
against real objects instead of static inference, which is what makes
build123d's `from build123d import *` convention and fluent chains work.
Measured three-tier engine (`kernel.complete` / `kernel.signatures` RPC):

1. `jedi.Interpreter` over the stashed post-run namespace — 60–300 ms warm,
   solves star-imports and chains; crashes on a known jedi runtime-generics
   bug for some properties (e.g. `bp.part.`).
2. `dir()`-eval fallback for pure attribute chains (~0.4 ms) — covers exactly
   the jedi crash cases; evaluates dotted names only, never calls.
3. Static `jedi.Script` — pre-first-run files; also provides signatures
   (full typed signatures for build123d verified).

An LSP client (for basedpyright/pylsp) was rejected for v1: no PySide LSP
client exists and writing one is a bigger project than the editor itself.
Monaco remains a possible future enhancement gated on WebEngine detection.

The editor itself (`freecad/code/editor/`): QPlainTextEdit subclass with line
numbers, Python syntax highlighting, auto-indent, completion popup fed
asynchronously (worker thread → Qt signal, stale answers dropped), Ctrl+Enter
run, Ctrl+S save. The dock panel (one per ScriptObject, opened by command or
double-click) saves through the filesystem so the Tier-1 watcher machinery is
reused unchanged, and maps kernel tracebacks to a highlighted error line via
the proxy's `last_error`.

## 7. Packaging and distribution

- Standard addon layout: `package.xml` metadata, `freecad.code` namespace
  package, installable via the Addon Manager (git URL first, addon registry once
  stable).
- The kernel ships as a separate installable (`kernel/`, package name
  `fc-code-kernel`) that the KernelManager installs *into the managed venv* from
  the addon's own checkout (`uv pip install -e <addon>/kernel`), so workbench and
  kernel versions never drift apart.
- Icons/resources under `freecad/code/resources/`; translations via FreeCAD's
  standard mechanism (later).

## 8. Testing and CI

- Kernel tests run against the real OCP stack in CI (no FreeCAD needed):
  serialization round-trips, parameter introspection, error mapping.
- Workbench tests run headless under `FreeCADCmd` in the official FreeCAD
  container image: BREP import, ScriptObject recompute, property round-trips.
- RPC layer tests are pure-Python (both halves importable without either heavy
  dependency).
- GitHub Actions matrix: {kernel-tests × py311/py312} + {freecad-tests × 1.0/1.1}.

## 9. Phased roadmap

| Phase | Scope | Outcome |
|---|---|---|
| **1** | Kernel + venv provisioning, RPC, BREP bridge, ScriptObject, hot reload, minimal toolbar | build123d/CQ models as parametric FreeCAD objects; external-editor workflow end to end |
| **2** | Embedded Monaco + LSP editor, traceback line-mapping UI, parameter panel polish | One-window experience for users who want it |
| **3** | Bidirectional selection (3D pick ↔ code line, via subshape provenance in metadata), subshape-stability hashing, richer assembly metadata | The "magical" tier; toponaming mitigation |

## 10. Security considerations

Scripts are arbitrary user code executed with user privileges — same trust model
as FreeCAD macros, documented as such. The RPC socket binds loopback-only and
requires a per-session random token passed to the kernel at spawn (env var), so
another local user cannot drive the kernel. Scripts embedded in `.FCStd` files
(`SourceInline`) never auto-execute on document open without a per-document
confirmation, mirroring FreeCAD's macro-security posture.

## 11. Open questions — resolved status

1. Pin strategy for `build123d`/`cadquery`/OCP in the managed venv — hard pins
   with an "update environment" button, or ranges? (Leaning: hard pins + button.)
2. **show_object color/alpha — RESOLVED: applied.** Single object → ViewObject
   ShapeColor/Transparency; compound → per-face DiffuseColor per child shape;
   shown name → object Label unless the user has renamed it (user wins).
3. **Embedded-vs-file scripts — RESOLVED: file-backed** (Tier 1 is
   external-editor-first; `SourceInline` remains unimplemented).
4. Whether to expose the kernel to *other* addons (e.g. ocp-freecad-cam could
   reuse the managed venv) via a small public API — still open.
