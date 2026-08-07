"""fc-code-kernel — execution kernel for the FreeCAD Code Workbench.

Runs in its own virtual environment with OCP/build123d/cadquery. Must never
import FreeCAD. Geometry leaves this process only as serialized BREP.
"""

__version__ = "0.1.0"
