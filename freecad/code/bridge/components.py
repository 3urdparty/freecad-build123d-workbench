"""Synchronize externally-built build123d components with FreeCAD."""

from __future__ import annotations

import base64
import re
from typing import Any

import FreeCAD as App
import FreeCADGui as Gui
import Part


PROPERTY_GROUP = "Build123d"
ID_PROPERTY = "Build123dId"
COMPONENT_ID_PROPERTY = "Build123dId"
FEATURE_ID_PROPERTY = "CaddevFeatureId"
DATUM_ID_PROPERTY = "CaddevDatumId"


def _safe_name(value: str) -> str:
    """Convert a component ID into a valid FreeCAD internal object name."""
    value = re.sub(r"[^A-Za-z0-9_]", "_", value)

    if not value:
        value = "Component"

    if value[0].isdigit():
        value = f"Component_{value}"

    return value


def _get_document(name: str | None = None):
    """Return the requested document, or the active document."""
    if name:
        doc = App.getDocument(name)

        if doc is None:
            raise ValueError(
                f"FreeCAD document not found: {name}"
            )

        return doc

    doc = App.ActiveDocument

    if doc is None:
        raise RuntimeError("No active FreeCAD document")

    return doc

def find_legacy_component(
    doc,
    component_id: str,
):
    for obj in doc.Objects:
        if obj.TypeId != "Part::Feature":
            continue

        if COMPONENT_ID_PROPERTY not in obj.PropertiesList:
            continue

        if getattr(
            obj,
            COMPONENT_ID_PROPERTY,
        ) == component_id:
            return obj

    return None


def find_component(
    doc,
    component_id: str,
):
    for obj in doc.Objects:
        if obj.TypeId != "App::Part":
            continue

        if COMPONENT_ID_PROPERTY not in obj.PropertiesList:
            continue

        if getattr(
            obj,
            COMPONENT_ID_PROPERTY,
        ) == component_id:
            return obj

    return None

def deserialize_location(
    data: dict | None,
) -> App.Placement:
    if data is None:
        return App.Placement()

    return App.Placement(
        App.Vector(*data["position"]),
        App.Rotation(*data["rotation"]),
    )

def _decode_brep(brep64: str):
    """Decode a base64 textual BREP into a native FreeCAD Part.Shape."""
    try:
        brep_bytes = base64.b64decode(
            brep64,
            validate=True,
        )
    except Exception as exc:
        raise ValueError(
            "Invalid base64 BREP payload"
        ) from exc

    try:
        brep_text = brep_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(
            "BREP payload is not UTF-8 textual BREP"
        ) from exc

    shape = Part.Shape()
    shape.importBrepFromString(brep_text)

    if shape.isNull():
        raise ValueError("BREP produced a null shape")

    return shape

def create_component(
    doc,
    component_id: str,
    label: str,
):
    component = doc.addObject(
        "App::Part",
        _safe_name(
            component_id
        ),
    )

    component.Label = label

    component.addProperty(
        "App::PropertyString",
        COMPONENT_ID_PROPERTY,
        "Caddev",
        "Stable caddev component identifier",
    )

    setattr(
        component,
        COMPONENT_ID_PROPERTY,
        component_id,
    )

    return component


def update_component(
    params: dict[str, Any],
) -> dict[str, Any]:
    component_id = str(
        params["id"]
    )

    label = str(
        params.get("name")
        or component_id
    )

    doc = _get_document(
        params.get("document")
    )

    component = find_component(
        doc,
        component_id,
    )

    created = (
        component is None
    )

    if component is None:
        component = create_component(
            doc,
            component_id,
            label,
        )

    component.Label = label

    #
    # Features
    #

    features = (
        params.get("features")
        or {}
    )

    remove_stale_features(
        doc,
        component,
        set(features),
    )

    for (
        feature_id,
        feature_data,
    ) in features.items():
        update_feature(
            doc,
            component,
            feature_id,
            feature_data,
        )

    #
    # Datums
    #

    datums = (
        params.get("datums")
        or {}
    )

    remove_stale_datums(
        doc,
        component,
        set(datums),
    )

    for datum_id, datum_data in datums.items():
        update_datum(
            doc,
            component,
            datum_id,
            datum_data,
        )

    component.touch()
    doc.recompute()
    resolve_assemblies(doc)

    return {
        "id": component_id,
        "object_name": (
            component.Name
        ),
        "label": (
            component.Label
        ),
        "created": created,
        "feature_count": len(
            features
        ),
        "datum_count": len(
            datums
        ),
    }


def delete_component(
    params: dict[str, Any],
) -> dict[str, Any]:
    """Delete a component from the FreeCAD document."""

    component_id = str(params["id"])

    doc = _get_document(
        params.get("document")
    )

    obj = find_component(
        doc,
        component_id,
    )

    if obj is None:
        return {
            "id": component_id,
            "deleted": False,
        }

    doc.removeObject(obj.Name)
    doc.recompute()

    return {
        "id": component_id,
        "deleted": True,
    }


def recompute_document(
    params: dict[str, Any],
) -> dict[str, Any]:
    """Recompute the requested FreeCAD document."""

    doc = _get_document(
        params.get("document")
    )

    doc.recompute()

    return {
        "document": doc.Name,
        "recomputed": True,
    }


def document_info(
    params: dict[str, Any],
) -> dict[str, Any]:
    """Return information about synchronized build123d components."""

    doc = _get_document(
        params.get("document")
    )

    components = []

    for obj in doc.Objects:
        if ID_PROPERTY not in obj.PropertiesList:
            continue

        components.append(
            {
                "id": getattr(obj, ID_PROPERTY),
                "object_name": obj.Name,
                "label": obj.Label,
                "type": obj.TypeId,
            }
        )

    return {
        "name": doc.Name,
        "label": doc.Label,
        "components": components,
    }

def get_geometry(component):
    for obj in component.Group:
        if obj.TypeId == "Part::Feature":
            return obj

    raise RuntimeError(
        f"{component.Label} has no geometry object"
    )

def find_datum(
    component,
    datum_id: str,
):
    for obj in component.Group:
        if DATUM_ID_PROPERTY not in obj.PropertiesList:
            continue

        if getattr(
            obj,
            DATUM_ID_PROPERTY,
        ) == datum_id:
            return obj

    return None

def update_datum(
    doc,
    component,
    datum_id: str,
    data: dict,
):
    datum = find_datum(
        component,
        datum_id,
    )

    if datum is None:
        datum = doc.addObject(
            "PartDesign::CoordinateSystem",
            _safe_name(
                f"{component.Name}_{datum_id}"
            ),
        )

        datum.Label = datum_id

        datum.addProperty(
            "App::PropertyString",
            DATUM_ID_PROPERTY,
            "Caddev",
            "Stable caddev datum identifier",
        )

        setattr(
            datum,
            DATUM_ID_PROPERTY,
            datum_id,
        )

        component.addObject(datum)

    position = data["position"]
    rotation = data["rotation"]

    datum.Placement = App.Placement(
        App.Vector(*position),
        App.Rotation(*rotation),
    )

    datum.touch()

    return datum

def remove_stale_datums(
    doc,
    component,
    incoming: set[str],
) -> None:
    for obj in list(component.Group):
        if DATUM_ID_PROPERTY not in obj.PropertiesList:
            continue

        datum_id = getattr(
            obj,
            DATUM_ID_PROPERTY,
        )

        if datum_id not in incoming:
            doc.removeObject(obj.Name)

def find_feature(
    component,
    feature_id: str,
):
    for obj in component.Group:
        if (
            FEATURE_ID_PROPERTY
            not in obj.PropertiesList
        ):
            continue

        if getattr(
            obj,
            FEATURE_ID_PROPERTY,
        ) == feature_id:
            return obj

    return None

def create_feature(
    doc,
    component,
    feature_id: str,
    label: str,
):
    obj = doc.addObject(
        "Part::Feature",
        _safe_name(
            f"{component.Name}_{feature_id}"
        ),
    )

    obj.Label = label

    obj.addProperty(
        "App::PropertyString",
        FEATURE_ID_PROPERTY,
        "Caddev",
        "Stable caddev feature identifier",
    )

    setattr(
        obj,
        FEATURE_ID_PROPERTY,
        feature_id,
    )

    component.addObject(
        obj
    )

    return obj

def update_feature(
    doc,
    component,
    feature_id: str,
    data: dict,
):
    feature = find_feature(
        component,
        feature_id,
    )

    label = str(
        data.get("name")
        or feature_id
    )

    if feature is None:
        feature = create_feature(
            doc,
            component,
            feature_id,
            label,
        )

    feature.Label = label

    shape = _decode_brep(
        data["brep64"]
    )

    # Geometry is always component-local.
    shape.Placement = App.Placement()

    feature.Shape = shape
    feature.Placement = deserialize_location(
        data.get("location")
    )

    apply_appearance(
        feature,
        data.get("appearance"),
    )

    apply_material(
        feature,
        data.get("material"),
    )

    return feature

def remove_stale_features(
    doc,
    component,
    incoming: set[str],
) -> None:
    for obj in list(
        component.Group
    ):
        if (
            FEATURE_ID_PROPERTY
            not in obj.PropertiesList
        ):
            continue

        feature_id = getattr(
            obj,
            FEATURE_ID_PROPERTY,
        )

        if feature_id not in incoming:
            doc.removeObject(
                obj.Name
            )
def ensure_material_properties(
    obj,
) -> None:
    if "MaterialName" not in obj.PropertiesList:
        obj.addProperty(
            "App::PropertyString",
            "MaterialName",
            "Material",
            "Engineering material",
        )

    if "MaterialDensity" not in obj.PropertiesList:
        obj.addProperty(
            "App::PropertyFloat",
            "MaterialDensity",
            "Material",
            "Density in kg/m^3",
        )

    if "YoungsModulus" not in obj.PropertiesList:
        obj.addProperty(
            "App::PropertyFloat",
            "YoungsModulus",
            "Material",
            "Young's modulus in Pa",
        )

    if "PoissonRatio" not in obj.PropertiesList:
        obj.addProperty(
            "App::PropertyFloat",
            "PoissonRatio",
            "Material",
            "Poisson ratio",
        )

    if "MaterialDescription" not in obj.PropertiesList:
        obj.addProperty(
            "App::PropertyString",
            "MaterialDescription",
            "Material",
            "Material description",
        )


def apply_material(
    obj,
    data: dict | None,
) -> None:
    if data is None:
        return

    ensure_material_properties(
        obj
    )

    obj.MaterialName = str(
        data.get("name") or ""
    )

    density = data.get(
        "density"
    )

    if density is not None:
        obj.MaterialDensity = float(
            density
        )

    youngs_modulus = data.get(
        "youngs_modulus"
    )

    if youngs_modulus is not None:
        obj.YoungsModulus = float(
            youngs_modulus
        )

    poisson_ratio = data.get(
        "poisson_ratio"
    )

    if poisson_ratio is not None:
        obj.PoissonRatio = float(
            poisson_ratio
        )

    obj.MaterialDescription = str(
        data.get("description") or ""
    )

def apply_appearance(
    obj,
    data: dict | None,
) -> None:
    if data is None:
        return

    view = obj.ViewObject

    color = data.get("color")

    if color is not None:
        view.ShapeColor = tuple(
            float(value)
            for value in color
        )

    transparency = data.get(
        "transparency"
    )

    if transparency is not None:
        view.Transparency = int(
            transparency
        )

    shininess = data.get(
        "shininess"
    )

    if shininess is not None:
        material = view.ShapeMaterial

        material.Shininess = float(
            shininess
        )

        view.ShapeMaterial = material

def resolve_assemblies(doc) -> None:
    # First force all Assembly joints to re-evaluate
    # their reference/JCS placements.
    for obj in doc.Objects:
        if obj.TypeId != "Assembly::JointGroup":
            continue

        for joint in obj.Group:
            joint.touch()
            joint.recompute()

    # Then solve each assembly.
    for obj in doc.Objects:
        if obj.TypeId != "Assembly::AssemblyObject":
            continue

        obj.touch()
        obj.recompute()

        if hasattr(obj, "solve"):
            obj.solve(False)

    doc.recompute()


def highlight_subelements(
    params: dict[str, Any],
) -> dict[str, Any]:
    doc = App.ActiveDocument

    component_id = str(params["component_id"])
    feature_id = str(params["feature_id"])
    subelements = [
        str(value)
        for value in params.get("subelements", [])
    ]

    component = find_component(
        doc,
        component_id,
    )

    if component is None:
        raise ValueError(
            f"Component not found: {component_id}"
        )

    feature = find_feature(
        component,
        feature_id,
    )

    if feature is None:
        raise ValueError(
            f"Feature not found: {feature_id}"
        )

    Gui.Selection.clearSelection()

    for subelement in subelements:
        Gui.Selection.addSelection(
            feature,
            subelement,
        )

    return {
        "component_id": component_id,
        "feature_id": feature_id,
        "highlighted": len(subelements),
    }
