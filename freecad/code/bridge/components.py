"""Synchronize externally-built build123d components with FreeCAD."""

from __future__ import annotations

import base64
import re
from typing import Any

import FreeCAD as App
import Part


PROPERTY_GROUP = "Build123d"
ID_PROPERTY = "Build123dId"
COMPONENT_ID_PROPERTY = "Build123dId"
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
        _safe_name(component_id),
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

    geometry = doc.addObject(
        "Part::Feature",
        f"{_safe_name(component_id)}_Geometry",
    )

    geometry.Label = "Geometry"

    component.addObject(geometry)

    return component

def update_component(
    params: dict[str, Any],
) -> dict[str, Any]:
    component_id = str(params["id"])
    label = str(
        params.get("name")
        or component_id
    )

    doc = _get_document(
        params.get("document")
    )

    shape = _decode_brep(
        params["brep64"]
    )

    component = find_component(
        doc,
        component_id,
    )

    created = False
    migrated = False

    if component is None:
        legacy = find_legacy_component(
            doc,
            component_id,
        )

        if legacy is not None:
            placement = legacy.Placement

            doc.removeObject(
                legacy.Name
            )
            doc.recompute()

            component = create_component(
                doc,
                component_id,
                label,
            )

            component.Placement = placement
            migrated = True

        else:
            component = create_component(
                doc,
                component_id,
                label,
            )

            created = True

    if component.TypeId != "App::Part":
        raise RuntimeError(
            f"expected App::Part for "
            f"{component_id!r}, "
            f"got {component.TypeId}"
        )

    component.Label = label

    geometry = get_geometry(
        component
    )

    # Incoming BREP stays in component-local coordinates.
    shape.Placement = App.Placement()

    geometry.Shape = shape
    geometry.Placement = App.Placement()

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

    doc.recompute()

    return {
        "id": component_id,
        "object_name": component.Name,
        "label": component.Label,
        "created": created,
        "migrated": migrated,
        "datum_count": len(datums),
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
