# Code Workbench for FreeCAD

Work with [build123d](https://github.com/gumyr/build123d) and
[CadQuery](https://github.com/CadQuery/cadquery) **natively inside FreeCAD**.

Scripts execute in an isolated kernel process (its own Python environment, its
own OCP/OCCT — no binary conflicts with FreeCAD), and the results land in your
FreeCAD document as **real parametric objects**: they save in `.FCStd` files,
recompute when parameters change, and feed TechDraw, FEM, CAM, and Assembly.

Edit in whatever editor you already love — save the file and FreeCAD updates.
The `show()` / `show_object()` API is compatible with
[ocp-vscode](https://github.com/bernhard-42/vscode-ocp-cad-viewer), so existing
scripts port unchanged.

## Status

Early scaffold / pre-alpha. See [docs/DESIGN.md](docs/DESIGN.md) for the full
architecture and roadmap.

## How it works (short version)

```
FreeCAD process                          Kernel process (managed venv)
  freecad.code workbench   ── JSON-RPC ──  fc_code_kernel
  Part.Shape.importBrepFromString  ◄────── BREP bytes + JSON metadata
```

FreeCAD and build123d/CadQuery use binary-incompatible OCCT builds, so geometry
never crosses the boundary as pointers — only as serialized BREP. The workbench
provisions the kernel's virtual environment automatically on first run (using
`uv` when available).

## Install (development)

1. Clone into your FreeCAD `Mod` directory:
   ```
   cd ~/.local/share/FreeCAD/Mod   # or the Mod dir for your platform
   git clone https://github.com/REPLACE_ME/freecad-code-workbench
   ```
2. Restart FreeCAD and select the **Code** workbench.
3. First activation provisions the kernel environment (progress in Report view).

## Quick start

1. **Code → New Script** creates a file-backed script and a `ScriptObject` in the
   active document.
2. Open the file in your editor. Write build123d or CadQuery as usual; call
   `show(part)` (optional — top-level shapes are auto-discovered).
3. Save. The model updates in FreeCAD. Parameters you declare appear in the
   property panel.

## Repository layout

```
freecad/code/        the workbench (runs inside FreeCAD; never imports OCP)
kernel/              fc-code-kernel (runs in the managed venv; never imports FreeCAD)
docs/DESIGN.md       architecture and roadmap
tests/               kernel + RPC tests (no FreeCAD required)
```

## License

LGPL-2.1-or-later, matching FreeCAD addon convention.
