# Code Workbench — Design Document

**Repo:** `freecad-code-workbench` · **Namespace:** `freecad.code` · **Display name:** Code Workbench
**Status:** Implemented through v0.2 · functional alpha · 2026-08-09

A FreeCAD workbench that makes build123d and CadQuery first-class citizens inside
FreeCAD: scripts execute in an isolated kernel, results land in the FreeCAD document
as real parametric objects, and the editor is whatever you want it to be—your own
(VS Code, Neovim, …) with hot reload, or the shipped focused embedded editor with
kernel-backed completions and diagnostics.

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

- Replacing a full IDE. The embedded editor owns the CAD edit/recompute/inspect loop;
  multi-file navigation, refactoring, source control, and plugin ecosystems remain
  the job of external editors.
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

**The one hard problem.** FreeCAD embeds its own Python linked against its own OCCT
build. build123d and CadQuery sit on
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
│  Code Workbench (freecad.code, FreeCAD's PySide shim)                    │
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
                        (BREP payloads base64 in protocol v0; see §5.2)
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
- Provisions with `uv` when available (fast, reproducible), including explicit
  Homebrew/user-local discovery for GUI launches whose `PATH` is minimal.
  Falls back only to a compatible system Python (3.10+), never FreeCAD's
  embedded interpreter. Installs the `fc-code-kernel` package plus `build123d`
  / `cadquery` at pinned-compatible versions, and reports captured installer
  stderr when provisioning fails.
- Spawns the kernel subprocess (`python -m fc_code_kernel --port 0`), reads the
  bound port from its stdout handshake, maintains the RPC connection.
- Makes one automatic restart-and-retry attempt after a lost kernel connection;
  surfaces kernel stderr in FreeCAD's Report view.
- Exposes `run_script(path, params) -> dict` to the rest of the workbench.

### 5.2 RPC layer (`freecad/code/rpc.py`, `kernel/fc_code_kernel/server.py`)

- Newline-delimited JSON-RPC 2.0 over localhost TCP. Loopback-only bind, and a
  per-session random token required on every request (see §10).
- v0 carries BREP as base64 inside the JSON result. This is simple and correct;
  if profiling shows large-assembly pain, v1 moves payloads to length-prefixed
  binary frames on the same socket (protocol has a version field from day one).
- Methods:
  - `kernel.hello() -> {kernel, protocol, python, ocp, build123d, cadquery}`
  - `kernel.run(path|source, params) -> {objects: [...], stdout, stderr, error}`
  - `kernel.introspect_params(path) -> [{name, type, default, doc}]`
  - `kernel.complete(source, line, column, path)` / `kernel.signatures(...)`

Runaway scripts: there is deliberately no `kernel.cancel` — a busy `exec()`
cannot be interrupted from outside. Instead each `kernel.run` is bounded by
the `RunTimeoutS` preference (default 60 s, 0 = unlimited); on timeout the
client closes the socket and KILLS the kernel process, the last good shape
is preserved, and a fresh kernel starts lazily on the next run. Crash
isolation was designed for exactly this. With editor autosave, a half-typed
`while True:` is an everyday event, not a corner case.
- An `error` is a structured traceback containing file, line/range, source text,
  and message data so the UI can map failures back to the correct document revision.

### 5.3 Script execution + `show()` shim (`kernel/fc_code_kernel/`)

- Executes the script with `exec()` in a fresh module namespace; injects declared
  parameters as module globals before execution.
- Provides `show()` / `show_object(obj, name=, options=)` with `ocp_vscode`-
  compatible signatures. Anything shown is collected; if nothing is shown
  explicitly, top-level build123d/CQ objects are auto-discovered (same heuristic
  cq-editor uses).
- `serialize.py` unwraps whatever it gets — build123d objects (`.wrapped`),
  CadQuery `Workplane` (`.vals()`), raw `TopoDS_Shape` — and emits
  `(brep_bytes, metadata)` per object. Current metadata is name, color, and alpha;
  assembly hierarchy and subshape provenance are Phase 3 work.
- Parameter contract: a script declares literal module-level assignments and lists
  their names in `PARAMS = [...]`. `introspect_params` reads these statically with
  `ast.literal_eval`; it never executes user code during introspection. Supported
  property types are `float`, `int`, `bool`, and `str`.

### 5.4 Document integration (`freecad/code/feature.py`)

The piece that makes this *native*. Each script is a `ScriptObject`
(`App::FeaturePython` proxy) with properties:

- `SourceFile` (`App::PropertyFile`) for the file-backed script and `AutoWatch`
  (`App::PropertyBool`) for per-object hot reload.
- Per-parameter dynamic properties in a `Parameters` group, created from
  `introspect_params` — so they appear in the property panel and participate in
  FreeCAD expressions/spreadsheets.
- `execute()` → `KernelManager.run_script()` → `Part.Shape.importBrepFromString()`
  → assign to `obj.Shape`. Multiple shown objects currently become one compound;
  preserving the source assembly hierarchy as child objects is Phase 3.
- Script defaults remain authoritative until a user overrides a parameter in the
  property panel. Defaults, overrides, additions, and removals are reconciled on
  successful recompute; a failing or half-edited script never destroys saved values.
- `show()` metadata supplies labels, colors, and transparency while preserving user
  renames and applying per-child face colors when the result is a compound.

Because the shape lives on a normal `Part::FeaturePython`-style object, TechDraw,
FEM meshing, CAM, and Assembly all consume it without knowing anything about
build123d.

**Topological naming caveat.** Each recompute produces a fresh shape with no
stable subshape names, so downstream face/edge references (a TechDraw dimension
on a face, a CAM operation on an edge) can break across recomputes. Mitigation
plan: deterministic subshape hashing (geometry-based matching between old and new
shapes) layered on FreeCAD 1.x's toponaming infrastructure. This is Phase 3 work
and is documented as a current limitation.

### 5.5 Hot reload (`freecad/code/watcher.py`)

`QFileSystemWatcher` on each ScriptObject's `SourceFile`, debounced (~200 ms,
configurable) through a `QTimer`, triggering `touch()` + `recompute()` on the
owning document. Editor-agnostic: save in VS Code, Neovim, or anything else and
FreeCAD updates. Watch state is per-object and toggleable from the toolbar.

### 5.6 Workbench UI (`freecad/code/commands.py`, `init_gui.py`)

The Code toolbar and menu expose New Script, Open Script, Edit Script, Re-run,
Reset Parameters to Script, Toggle Watch, Restart Kernel, and Rebuild Kernel
Environment, each with its own icon. The preferences page integrates with FreeCAD's
settings dialog: autosave behavior, venv location, package pins, run timeout,
file-watch debounce, and kernel auto-start.

## 6. Editor strategy — two tiers

**Tier 1 (shipped in v0.1): external editors are the editor.** The watcher + `show()`
compatibility means users keep VS Code/Neovim/PyCharm, with real LSP, their own
keybindings, their own plugins — and FreeCAD becomes the viewer with a full CAD
system behind it. This is most of the value for a fraction of the effort.

**Tier 2 (shipped in v0.2): embedded editor — native Qt + kernel-side jedi.**

The embedded editor is deliberately a focused, single-script CAD editor: it
supports the tight edit → recompute → inspect loop without trying to reproduce
a general-purpose IDE. Multi-file navigation, refactoring, source control,
plugin ecosystems, and deeply configurable editing remain the job of Tier 1.
This is a product boundary as well as a maintenance boundary; features such as
multi-cursor editing, folding, snippets, and rename should trigger a fresh
off-the-shelf-editor evaluation rather than being implemented ad hoc here.

The original v0.1 design proposed Monaco in a `QWebEngineView`. Research (2026-08,
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

An LSP client (for basedpyright/pylsp) was rejected for v0.2: no PySide LSP
client exists and writing one is a bigger project than the editor itself.
Monaco remains a possible future enhancement gated on WebEngine detection.

The editor itself (`freecad/code/editor/`): QPlainTextEdit subclass with line
numbers, Python syntax highlighting, auto-indent, completion popup fed
asynchronously (worker thread → Qt signal, stale answers dropped), Ctrl+Enter
run, Ctrl+S save. The dock panel (one per ScriptObject, opened by command or
double-click) saves through the filesystem so the Tier-1 watcher machinery is
reused unchanged.

Execution failures cross the editor boundary as backend-neutral, versioned
diagnostics (`editor/diagnostics.py`): file, source range, severity, message,
traceback, source, and document version. The native widget renders these as
gutter markers and wave underlines; hover shows the cause and traceback, and a
click opens a selectable/copyable inspector. A future CodeMirror or Monaco
frontend should consume this same model rather than teaching the dock about a
second editor API. Results are only attached to the exact document revision
that produced them, preventing stale watcher/autosave errors from marking new
text.

## 7. Packaging and distribution

- Standard modern addon layout: `package.xml` metadata and a `freecad.code`
  namespace package. Direct Git installation is supported today; official discovery
  will use the `FreeCAD/Addons` Index for FreeCAD 1.0+ after public-alpha testing and
  review.
- The kernel ships as a separate installable (`kernel/`, package name
  `fc-code-kernel`) that the KernelManager installs *into the managed venv* from
  the addon's own checkout (`uv pip install -e <addon>/kernel`), so workbench and
  kernel versions never drift apart.
- The managed environment is architecturally necessary because FreeCAD's and OCP's
  OCCT builds cannot safely share a process. Before Addon Index submission, first-run
  downloads should be explicit to the user and documented for reviewers rather than
  appearing as an unexplained activation-time side effect.
- `main` is the stable/release branch; active work happens on short-lived feature
  branches and merges only when release-ready. Every release merge to `main` updates
  all version declarations plus the manifest date, then receives a matching Git tag
  and GitHub release.
- Icons/resources live under `freecad/code/resources/`; translations use FreeCAD's
  standard mechanism when introduced.

## 8. Testing and CI

- `lint`: Ruff over the full repository.
- `core-tests`: dependency-light RPC and executor tests on Python 3.11 and 3.12.
- `kernel-tests`: the full pytest suite against the exact pinned build123d and
  CadQuery packages provisioned for users.
- `freecad-e2e`: headless FreeCAD 1.0 via conda-forge, provisioning the real managed
  environment and exercising script → kernel → BREP → ScriptObject and recompute
  paths. Latest-FreeCAD coverage is a distribution gate before Addon Index review.

## 9. Phased roadmap

| Phase | Scope | Outcome | Status |
|---|---|---|---|
| **1** | Kernel + venv provisioning, RPC, BREP bridge, ScriptObject, hot reload, minimal toolbar | build123d/CQ models as parametric FreeCAD objects; external-editor workflow end to end | ✅ shipped (0.1.0) |
| **2** | Embedded editor (native Qt + kernel jedi — see §6), traceback line-mapping, autosave with last-valid-model, parameter override semantics + visibility, run timeout, names/colors, calltips, preferences page, pinned kernel packages | One-window experience; safe-by-default iteration loop | ✅ shipped (0.2.0) |
| **3** | Assembly hierarchy as child objects (today: compound), bidirectional selection (3D pick ↔ code line, via subshape provenance in metadata), subshape-stability hashing (toponaming mitigation), kernel API for other addons | The "magical" tier | ⬜ not started |

Known engineering debt, tracked outside the phases: the GUI thread still
blocks for the duration of a run (bounded by RunTimeoutS, but a long legit
model is still a stall — the structural fix is an async execute, which cuts
against FreeCAD's synchronous recompute model and needs design); RPC is one
request in flight, so editor completions queue behind a running script;
BREP rides as base64 in JSON (revisit when large assemblies hurt); automated
FreeCAD coverage currently exercises 1.0 on Linux rather than the full supported
OS/version matrix.

## 10. Security considerations

Scripts are arbitrary user code executed with user privileges — same trust model
as FreeCAD macros, documented as such. The RPC socket binds loopback-only and
requires a per-session random token passed to the kernel at spawn (env var), so
another local user cannot drive the kernel. Scripts are file-backed; Code Workbench
does not embed or silently execute source stored inside an `.FCStd` document.

First activation currently provisions a separate Python runtime and downloads pinned
packages from their normal package sources. That behavior must remain disclosed, use
TLS-backed package tooling, and gain an explicit first-run confirmation before broad
distribution through the Addon Index.

## 11. Open questions — resolved status

1. **Pin strategy — RESOLVED: hard pins + button.** `package_pins()` defaults
   to pinned `build123d`/`cadquery` versions (kept in sync with CI); users
   edit the pins in the preferences page and run "Rebuild kernel environment".
2. **show_object color/alpha — RESOLVED: applied.** Single object → ViewObject
   ShapeColor/Transparency; compound → per-face DiffuseColor per child shape;
   shown name → object Label unless the user has renamed it (user wins).
3. **Embedded-vs-file scripts — RESOLVED: file-backed.** `SourceInline` is not part
   of the current object model; this also avoids document-open execution ambiguity.
4. Whether to expose the kernel to *other* addons (e.g. ocp-freecad-cam could
   reuse the managed venv) via a small public API — still open.
