"""Code Workbench — build123d and CadQuery, natively in FreeCAD.

This package runs *inside* FreeCAD's embedded Python. It must never import
OCP, build123d, or cadquery: those live in the managed kernel environment
(see ``kernel/``) and geometry crosses the boundary only as serialized BREP.
"""

__version__ = "0.2.0"

# Name of the kernel distribution installed into the managed venv.
KERNEL_PACKAGE = "fc-code-kernel"
