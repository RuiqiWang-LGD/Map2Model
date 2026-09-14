"""Construct audited native CAD faces from an explicit geometry recipe.

This module consumes geometry semantics already decided by an Agent or user.  It
does not classify images.  It writes a complete staged artifact set and validates
the saved DXF with ``check_face_hierarchy.inspect`` before publishing it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any

import ezdxf
from shapely import affinity
from shapely.geometry import GeometryCollection, LineString, MultiLineString, Polygon, box
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_face_hierarchy


OUTPUT_NAMES = {
    "dxf": "cad.dxf",
    "faces": "faces.json",
    "manifest": "manifest.json",
    "report": "construction.json",
    "validation": "validation.json",
}
FIT_KEYS = {"simplify", "max_shift", "max_area_change"}
SHAPE_POINT_KEYS = {"points"}
SHAPE_LOCAL_KEYS = {
    "origin", "angle_degrees", "rectangles", "polygons", "subtract_rectangles"
}


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path) -> Any:
    def reject(value: str):
        raise ValueError(f"JSON contains nonfinite value {value}")

    try:
        return json.loads(path.read_text(encoding="utf-8-sig"), parse_constant=reject)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read JSON input {path}: {exc}") from exc


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _number(value: Any, label: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    number = float(value)
    if positive and number <= 0:
        raise ValueError(f"{label} must be positive")
    return number


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
    return value


def _xy(value: Any, label: str) -> tuple[float, float]:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(f"{label} must be [x,y]")
    return (_number(value[0], label), _number(value[1], label))


def _rectangle(value: Any, label: str) -> tuple[float, float, float, float]:
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError(f"{label} must be [xmin,ymin,xmax,ymax]")
    result = tuple(_number(v, label) for v in value)
    if result[0] >= result[2] or result[1] >= result[3]:
        raise ValueError(f"{label} must have positive width and height")
    return result


def _ring(value: Any, label: str) -> list[tuple[float, float]]:
    if not isinstance(value, list) or len(value) < 3:
        raise ValueError(f"{label} must contain at least three points")
    points = [_xy(p, label) for p in value]
    if points[0] == points[-1]:
        points.pop()
    if len(points) < 3:
        raise ValueError(f"{label} must contain at least three distinct ring points")
    return points


def _one_polygon(geometry, label: str) -> Polygon:
    if geometry.is_empty or geometry.geom_type != "Polygon":
        raise ValueError(f"{label} must produce exactly one polygon")
    polygon = orient(geometry, sign=1.0)
    if not polygon.is_valid or polygon.area <= 0:
        raise ValueError(f"{label} must produce a valid positive-area polygon")
    if polygon.interiors:
        raise ValueError(f"{label} must not contain holes; use separate child rings")
    return polygon


def _shape(value: Any, label: str = "shape") -> Polygon:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    keys = set(value)
    if keys == SHAPE_POINT_KEYS:
        return _one_polygon(Polygon(_ring(value["points"], label + ".points")), label)
    if keys != SHAPE_LOCAL_KEYS:
        raise ValueError(f"{label} must use exactly points or the complete local-shape fields")

    origin = _xy(value["origin"], label + ".origin")
    angle = _number(value["angle_degrees"], label + ".angle_degrees")
    for field in ("rectangles", "polygons", "subtract_rectangles"):
        if not isinstance(value[field], list):
            raise ValueError(f"{label}.{field} must be a list")
    positives = [box(*_rectangle(r, label + ".rectangles")) for r in value["rectangles"]]
    positives.extend(
        _one_polygon(Polygon(_ring(r, label + ".polygons")), label + ".polygons")
        for r in value["polygons"]
    )
    if not positives:
        raise ValueError(f"{label} needs at least one rectangle or polygon")
    geometry = unary_union(positives)
    negatives = [box(*_rectangle(r, label + ".subtract_rectangles"))
                 for r in value["subtract_rectangles"]]
    if negatives:
        geometry = geometry.difference(unary_union(negatives))
    geometry = affinity.rotate(geometry, angle, origin=(0, 0), use_radians=False)
    geometry = affinity.translate(geometry, origin[0], origin[1])
    return _one_polygon(geometry, label)


def _fit_config(value: Any, label: str) -> dict[str, float]:
    if not isinstance(value, dict) or set(value) != FIT_KEYS:
        raise ValueError(f"{label} must contain exactly simplify, max_shift and max_area_change")
    result = {key: _number(value[key], f"{label}.{key}", positive=True) for key in FIT_KEYS}
    if result["max_area_change"] > 1:
        raise ValueError(f"{label}.max_area_change must be <= 1")
    return result


def _fit_polygon(source: Polygon, fit: dict[str, float], label: str,
                 frame_boundary=None) -> tuple[Polygon, dict[str, Any] | None, dict[str, float]]:
    candidate = source.simplify(fit["simplify"], preserve_topology=True)
    try:
        candidate = _one_polygon(candidate, label)
    except ValueError:
        return source, {"code": "fit_invalid", "id": label}, {}
    shift = source.boundary.hausdorff_distance(candidate.boundary)
    area_fraction = source.symmetric_difference(candidate).area / source.area
    metrics = {
        "source_vertices": len(source.exterior.coords) - 1,
        "result_vertices": len(candidate.exterior.coords) - 1,
        "hausdorff": shift,
        "symmetric_difference_fraction": area_fraction,
    }
    if frame_boundary is not None:
        before = source.boundary.intersection(frame_boundary)
        after = candidate.boundary.intersection(frame_boundary)
        contact_change = before.symmetric_difference(after).length
        metrics["frame_contact_change"] = contact_change
        if contact_change > 1e-9:
            return source, {"code": "fit_changed_frame_contact", "id": label,
                            "change": contact_change}, metrics
    if shift > fit["max_shift"] or area_fraction > fit["max_area_change"]:
        return source, {"code": "fit_bounds_exceeded", "id": label,
                        "hausdorff": shift, "area_fraction": area_fraction}, metrics
    return candidate, None, metrics


def _points(poly: Polygon) -> list[tuple[float, float]]:
    return [(float(x), float(y)) for x, y in list(poly.exterior.coords)[:-1]]


def _validate_common(recipe: Any, expected: set[str]) -> None:
    if not isinstance(recipe, dict) or set(recipe) != expected:
        raise ValueError("Missing or unknown recipe fields")
    if recipe["schema_version"] != 1:
        raise ValueError("schema_version must be 1")


def _validate_outer(recipe: Any) -> dict[str, Any]:
    _validate_common(recipe, {"schema_version", "module", "units", "frame", "tolerances",
                              "expected_road_components", "parents"})
    if recipe["module"] != "outer":
        raise ValueError("outer recipe module must be outer")
    if type(recipe["units"]) is not int or not 0 <= recipe["units"] <= 24:
        raise ValueError("units must be a DXF INSUNITS integer")
    frame = list(_rectangle(recipe["frame"], "frame"))
    tolerances = recipe["tolerances"]
    if not isinstance(tolerances, dict) or set(tolerances) != {
        "coordinate", "area", "min_edge", "min_clearance"
    }:
        raise ValueError("tolerances must match the face checker")
    tolerances = {key: _number(value, "tolerances." + key, positive=True)
                  for key, value in tolerances.items()}
    if tolerances["coordinate"] >= min(tolerances["min_edge"], tolerances["min_clearance"]):
        raise ValueError("coordinate tolerance must be smaller than edge and clearance")
    expected = recipe["expected_road_components"]
    if type(expected) is not int or expected < 1:
        raise ValueError("expected_road_components must be a positive integer")
    if not isinstance(recipe["parents"], list) or not recipe["parents"]:
        raise ValueError("parents must be a nonempty list")
    rows = []
    seen = set()
    for index, row in enumerate(recipe["parents"]):
        if not isinstance(row, dict) or set(row) not in (
                {"id", "clipped", "source", "shape"},
                {"id", "clipped", "source", "shape", "fit"}):
            raise ValueError("parent fields must be id, clipped, source, shape and optional fit")
        object_id = _text(row["id"], f"parents[{index}].id")
        if object_id in seen:
            raise ValueError("parent IDs must be unique")
        seen.add(object_id)
        if type(row["clipped"]) is not bool:
            raise ValueError("parent clipped must be boolean")
        item = dict(row)
        item["source"] = _text(row["source"], f"parents[{index}].source")
        item["polygon"] = _shape(row["shape"], f"parent {object_id}")
        item["fit_config"] = _fit_config(row["fit"], f"parent {object_id}.fit") if "fit" in row else None
        rows.append(item)
    return dict(recipe, frame=frame, tolerances=tolerances, parents=rows)


def _validate_neighbor(value: Any) -> dict[str, Any]:
    required = {"roles", "clearance", "simplify", "max_shift", "max_area_change"}
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("neighbor_fit fields are exact")
    roles = value["roles"]
    if not isinstance(roles, list) or not roles or len(set(roles)) != len(roles) or not all(
            isinstance(role, str) and role.startswith("GREEN") for role in roles):
        raise ValueError("neighbor_fit roles must be unique GREEN* names")
    result = dict(value, roles=list(roles))
    for key in required - {"roles"}:
        result[key] = _number(value[key], "neighbor_fit." + key, positive=True)
    if result["max_area_change"] > 1:
        raise ValueError("neighbor_fit.max_area_change must be <= 1")
    return result


def _validate_detail(recipe: Any) -> dict[str, Any]:
    if not isinstance(recipe, dict) or set(recipe) not in (
            {"schema_version", "module", "operations"},
            {"schema_version", "module", "operations", "neighbor_fit"}):
        raise ValueError("Missing or unknown detail recipe fields")
    if recipe.get("schema_version") != 1 or recipe.get("module") != "detail":
        raise ValueError("detail recipe requires schema_version=1 and module=detail")
    if not isinstance(recipe["operations"], list):
        raise ValueError("operations must be a list")
    operations = []
    seen = set()
    for index, row in enumerate(recipe["operations"]):
        allowed = {"id", "action", "role", "parent", "source", "shape"}
        if not isinstance(row, dict) or set(row) not in (allowed, allowed | {"fit"}):
            raise ValueError("detail operation fields are exact")
        item = dict(row)
        item["id"] = _text(row["id"], f"operations[{index}].id")
        if item["id"] in seen:
            raise ValueError("duplicate operation IDs")
        seen.add(item["id"])
        if row["action"] not in ("replace", "add"):
            raise ValueError("action must be replace or add")
        for field in ("role", "parent", "source"):
            item[field] = _text(row[field], f"operations[{index}].{field}")
        item["polygon"] = _shape(row["shape"], f"operation {item['id']}")
        item["fit_config"] = _fit_config(row["fit"], f"operation {item['id']}.fit") if "fit" in row else None
        operations.append(item)
    neighbor = _validate_neighbor(recipe["neighbor_fit"]) if "neighbor_fit" in recipe else None
    return dict(recipe, operations=operations, neighbor_fit=neighbor)


def _line_parts(geometry):
    if isinstance(geometry, LineString):
        yield geometry
    elif isinstance(geometry, MultiLineString):
        yield from geometry.geoms
    elif isinstance(geometry, GeometryCollection):
        for part in geometry.geoms:
            yield from _line_parts(part)


def _frame_caps(frame_values: list[float], parents: list[Polygon], tolerance: float):
    xmin, ymin, xmax, ymax = frame_values
    sides = [
        (0, ymin, xmin, xmax),
        (1, xmax, ymin, ymax),
        (0, ymax, xmin, xmax),
        (1, xmin, ymin, ymax),
    ]
    result = []
    for axis, constant, side_min, side_max in sides:
        intervals = []
        other = 1 - axis
        for polygon in parents:
            coordinates = list(polygon.exterior.coords)
            for first, second in zip(coordinates, coordinates[1:]):
                if first[other] != constant or second[other] != constant:
                    continue
                lo = max(side_min, min(first[axis], second[axis]))
                hi = min(side_max, max(first[axis], second[axis]))
                if hi > lo:
                    intervals.append((lo, hi))
        intervals.sort()
        merged = []
        for lo, hi in intervals:
            if merged and lo <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
            else:
                merged.append((lo, hi))
        cursor = side_min
        for lo, hi in merged + [(side_max, side_max)]:
            if lo > cursor:
                if axis == 0:
                    result.append(((cursor, constant), (lo, constant)))
                else:
                    result.append(((constant, cursor), (constant, lo)))
            cursor = max(cursor, hi)
    return result


def _source_manifest(source_dxf: Path) -> dict[str, dict[str, Any]]:
    candidate = source_dxf.parent / OUTPUT_NAMES["manifest"]
    if not candidate.is_file():
        return {}
    try:
        data = _json(candidate)
    except ValueError:
        return {}
    rows = data if isinstance(data, list) else data.get("objects", []) if isinstance(data, dict) else []
    return {row["id"]: row for row in rows if isinstance(row, dict) and isinstance(row.get("id"), str)}


def _entity_polygon(entity) -> Polygon:
    if entity.dxftype() != "LWPOLYLINE" or not entity.closed:
        raise ValueError("manifested face must be a native closed LWPOLYLINE")
    return _one_polygon(Polygon([(p[0], p[1]) for p in entity.get_points("xy")]), "source entity")


def _set_polygon(entity, polygon: Polygon) -> None:
    entity.set_points(_points(polygon), format="xy")
    entity.closed = True
    entity.dxf.const_width = 0
    entity.dxf.elevation = 0
    entity.dxf.extrusion = (0, 0, 1)
    entity.dxf.thickness = 0


def _add_polygon(modelspace, polygon: Polygon, layer: str):
    return modelspace.add_lwpolyline(_points(polygon), close=True,
                                     dxfattribs={"layer": layer, "const_width": 0, "elevation": 0})


def _construct_outer(recipe: dict[str, Any], stage: Path):
    doc = ezdxf.new("R2018")
    doc.units = recipe["units"]
    modelspace = doc.modelspace()
    frame_boundary = box(*recipe["frame"]).boundary
    issues = []
    metrics = {}
    face_parents = []
    objects = []
    polygons = []
    for row in recipe["parents"]:
        polygon = row["polygon"]
        fit_metrics = None
        if row["fit_config"]:
            polygon, issue, fit_metrics = _fit_polygon(
                polygon, row["fit_config"], row["id"], frame_boundary if row["clipped"] else None
            )
            if issue:
                issues.append(issue)
        entity = _add_polygon(modelspace, polygon, "PARENT")
        face_parents.append({"id": row["id"], "handle": entity.dxf.handle, "clipped": row["clipped"]})
        objects.append({
            "id": row["id"], "role": "PARENT", "parent": None,
            "handle": entity.dxf.handle, "before_handle": None,
            "source": row["source"], "old_source": None, "status": "added",
            "clipped": row["clipped"], "fit": fit_metrics,
        })
        polygons.append(polygon)
    frame_entities = [modelspace.add_line(a, b, dxfattribs={"layer": "FRAME"})
                      for a, b in _frame_caps(recipe["frame"], polygons,
                                              recipe["tolerances"]["coordinate"])]
    faces = {
        "schema_version": 1, "module": "outer", "units": recipe["units"],
        "frame": recipe["frame"], "tolerances": recipe["tolerances"],
        "expected_road_components": recipe["expected_road_components"],
        "parents": face_parents,
        "frame_handles": [entity.dxf.handle for entity in frame_entities],
        "details": [],
    }
    manifest = objects
    metrics["parent_count"] = len(objects)
    metrics["frame_cap_count"] = len(frame_entities)
    return doc, faces, manifest, issues, metrics, {"status": "not_requested", "implicated_ids": []}


def _construct_detail(recipe: dict[str, Any], source_dxf: Path, face_path: Path, stage: Path):
    face_config = _json(face_path)
    check_face_hierarchy.validate_config(face_config)
    if face_config["module"] not in ("outer", "detail"):
        raise ValueError("face_config module must be outer or detail")
    precheck = check_face_hierarchy.inspect(source_dxf, face_config, face_path.parent)
    if not precheck["geometry_pass"]:
        raise ValueError("source_dxf does not pass its face_config: " +
                         ", ".join(issue["code"] for issue in precheck["issues"]))
    doc = ezdxf.readfile(source_dxf)
    modelspace = doc.modelspace()
    parent_rows = [dict(row) for row in face_config["parents"]]
    detail_rows = [dict(row) for row in face_config["details"]]
    parent_ids = {row["id"] for row in parent_rows}
    detail_by_id = {row["id"]: row for row in detail_rows}
    all_ids = parent_ids | set(detail_by_id)
    old_manifest = _source_manifest(source_dxf)
    issues = []
    fit_records = {}
    changed_buildings = []
    operation_by_id = {row["id"]: row for row in recipe["operations"]}

    for operation in recipe["operations"]:
        object_id = operation["id"]
        if operation["parent"] not in parent_ids:
            raise ValueError(f"operation {object_id} names an unknown parent")
        if operation["action"] == "replace":
            if object_id in parent_ids:
                raise ValueError("replace cannot target parent or frame geometry")
            if object_id not in detail_by_id:
                raise ValueError(f"replace target {object_id} is absent from the face inventory")
            old = detail_by_id[object_id]
            if operation["role"] != old["role"] or operation["parent"] != old["parent"]:
                raise ValueError("replace cannot secretly change role or parent")
            entity = doc.entitydb.get(old["handle"])
            if entity is None:
                raise ValueError(f"replace target {object_id} handle is missing")
            polygon = operation["polygon"]
            if operation["fit_config"]:
                polygon, issue, metrics = _fit_polygon(
                    polygon, operation["fit_config"], object_id
                )
                fit_records[object_id] = metrics
                if issue:
                    issues.append(issue)
            _set_polygon(entity, polygon)
        else:
            if object_id in all_ids:
                raise ValueError(f"add ID {object_id} already exists")
            polygon = operation["polygon"]
            if operation["fit_config"]:
                polygon, issue, metrics = _fit_polygon(
                    polygon, operation["fit_config"], object_id
                )
                fit_records[object_id] = metrics
                if issue:
                    issues.append(issue)
            entity = _add_polygon(modelspace, polygon, operation["role"])
            detail_by_id[object_id] = {
                "id": object_id, "handle": entity.dxf.handle,
                "parent": operation["parent"], "role": operation["role"],
            }
            detail_rows.append(detail_by_id[object_id])
            all_ids.add(object_id)
        if operation["role"].startswith("BUILDING"):
            changed_buildings.append((object_id, operation["parent"], polygon))

    neighbor_record = {"status": "not_requested", "implicated_ids": [], "metrics": {}}
    if recipe["neighbor_fit"] is not None:
        settings = recipe["neighbor_fit"]
        neighbor_record["status"] = "completed"
        by_parent = {}
        for building_id, parent_id, polygon in changed_buildings:
            by_parent.setdefault(parent_id, []).append((building_id, polygon))
        implicated = []
        for row in detail_rows:
            if row["role"] not in settings["roles"]:
                continue
            buildings = by_parent.get(row["parent"], [])
            if not buildings:
                continue
            entity = doc.entitydb.get(row["handle"])
            green = _entity_polygon(entity)
            relevant = [(bid, building) for bid, building in buildings
                        if building.buffer(settings["clearance"]).intersects(green)
                        and not green.covers(building)]
            if not relevant:
                continue
            implicated.append(row["id"])
            exclusion = unary_union([building.buffer(settings["clearance"], join_style=2)
                                     for _, building in relevant])
            candidate = green.difference(exclusion)
            if candidate.equals(green):
                issues.append({"code": "neighbor_fit_unchanged_ambiguity", "id": row["id"]})
                continue
            if candidate.geom_type != "Polygon":
                issues.append({"code": "neighbor_fit_multipart", "id": row["id"],
                               "building_ids": [bid for bid, _ in relevant]})
                continue
            if candidate.is_empty or not candidate.is_valid or candidate.area <= face_config["tolerances"]["area"]:
                issues.append({"code": "neighbor_fit_collapse", "id": row["id"]})
                continue
            if candidate.interiors:
                issues.append({"code": "neighbor_fit_hole", "id": row["id"]})
                continue
            candidate = orient(candidate.simplify(settings["simplify"], preserve_topology=True), sign=1.0)
            if candidate.geom_type != "Polygon" or candidate.interiors or not candidate.is_valid:
                issues.append({"code": "neighbor_fit_invalid", "id": row["id"]})
                continue
            shift = green.boundary.hausdorff_distance(candidate.boundary)
            fraction = green.symmetric_difference(candidate).area / green.area
            gaps = {building_id: candidate.distance(building)
                    for building_id, building in relevant}
            actual_gap = min(gaps.values())
            neighbor_record["metrics"][row["id"]] = {
                "hausdorff": shift, "symmetric_difference_fraction": fraction,
                "building_ids": [bid for bid, _ in relevant],
                "requested_clearance": settings["clearance"], "actual_gap": actual_gap,
            }
            if actual_gap + face_config["tolerances"]["coordinate"] < settings["clearance"]:
                issues.append({
                    "code": "neighbor_fit_clearance_not_met", "id": row["id"],
                    "requested_clearance": settings["clearance"], "actual_gap": actual_gap,
                    "building_ids": [bid for bid, _ in relevant],
                })
                continue
            if shift > settings["max_shift"] or fraction > settings["max_area_change"]:
                issues.append({"code": "neighbor_fit_bounds_exceeded", "id": row["id"],
                               "hausdorff": shift, "area_fraction": fraction})
                continue
            _set_polygon(entity, candidate)
        neighbor_record["implicated_ids"] = sorted(implicated)
        if any(issue["code"].startswith("neighbor_fit_") for issue in issues):
            neighbor_record["status"] = "conflict"

    if face_config["module"] == "outer":
        baseline_path = source_dxf
        baseline_hash = _digest(source_dxf)
    else:
        source_baseline = face_config["baseline"]
        baseline_path = (face_path.parent / source_baseline["dxf"]).resolve()
        baseline_hash = source_baseline["sha256"]
    faces = {
        "schema_version": 1, "module": "detail", "units": face_config["units"],
        "frame": face_config["frame"], "tolerances": face_config["tolerances"],
        "expected_road_components": face_config["expected_road_components"],
        "parents": parent_rows, "frame_handles": list(face_config["frame_handles"]),
        "details": detail_rows,
        "baseline": {"dxf": str(baseline_path), "sha256": baseline_hash},
    }
    objects = []
    for row in parent_rows + detail_rows:
        old = old_manifest.get(row["id"], {})
        operation = operation_by_id.get(row["id"])
        if row in parent_rows:
            role, parent, status = "PARENT", None, "preserved"
        else:
            role, parent = row["role"], row["parent"]
            status = ({"add": "added", "replace": "replaced"}[operation["action"]] if operation else (
                "neighbor_fitted" if row["id"] in neighbor_record["implicated_ids"] else "preserved"
            ))
        before_handle = row["handle"] if row["id"] in {r["id"] for r in face_config["parents"] + face_config["details"]} else None
        objects.append({
            "id": row["id"], "role": role, "parent": parent,
            "handle": row["handle"], "before_handle": before_handle,
            "source": operation["source"] if operation else old.get("source", "preserved from source face config"),
            "old_source": old.get("source") if operation else old.get("old_source"),
            "status": status, "fit": fit_records.get(row["id"]),
        })
    manifest = objects
    metrics = {
        "operation_count": len(recipe["operations"]),
        "preserved_object_count": sum(row["status"] == "preserved" for row in objects),
        "detail_count": len(detail_rows),
    }
    return doc, faces, manifest, issues, metrics, neighbor_record


def _same_file_if_existing(a: Path, b: Path) -> bool:
    try:
        return a.exists() and b.exists() and os.path.samefile(a, b)
    except OSError:
        return False


def construct(recipe_path, output_dir, source_dxf=None, face_config=None) -> dict[str, Any]:
    """Construct an outer or detail DXF and publish a complete validated bundle."""
    recipe_path = Path(recipe_path).resolve()
    output_dir = Path(output_dir).resolve()
    if not recipe_path.is_file():
        raise ValueError("recipe_path must be an existing file")
    input_paths = [recipe_path]
    if source_dxf is not None:
        input_paths.append(Path(source_dxf).resolve())
    if face_config is not None:
        input_paths.append(Path(face_config).resolve())
    if output_dir.exists():
        raise ValueError("output_dir must be fresh and must not alias an input")
    if any(output_dir == path or _same_file_if_existing(output_dir, path) for path in input_paths):
        raise ValueError("output_dir must not alias an input")
    recipe_raw = _json(recipe_path)
    if not isinstance(recipe_raw, dict) or recipe_raw.get("module") not in ("outer", "detail"):
        raise ValueError("recipe module must be outer or detail")
    if recipe_raw["module"] == "outer":
        if source_dxf is not None or face_config is not None:
            raise ValueError("outer construction does not accept source_dxf or face_config")
        recipe = _validate_outer(recipe_raw)
    else:
        if source_dxf is None or face_config is None:
            raise ValueError("detail construction requires source_dxf and face_config")
        source_dxf = Path(source_dxf).resolve()
        face_config = Path(face_config).resolve()
        if not source_dxf.is_file() or not face_config.is_file():
            raise ValueError("source_dxf and face_config must be existing files")
        if any(output_dir == path or _same_file_if_existing(output_dir, path)
               for path in (source_dxf, face_config)):
            raise ValueError("output_dir must not alias an input")
        recipe = _validate_detail(recipe_raw)

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.staging-", dir=output_dir.parent)).resolve()
    try:
        if recipe["module"] == "outer":
            doc, faces, manifest, construction_issues, metrics, neighbor = _construct_outer(recipe, stage)
        else:
            doc, faces, manifest, construction_issues, metrics, neighbor = _construct_detail(
                recipe, source_dxf, face_config, stage
            )
        dxf_path = stage / OUTPUT_NAMES["dxf"]
        faces_path = stage / OUTPUT_NAMES["faces"]
        doc.saveas(dxf_path)
        _write_json(faces_path, faces)
        try:
            checker = check_face_hierarchy.inspect(dxf_path, faces, stage)
        except Exception as exc:
            checker = {
                "schema_version": 1, "module": faces["module"], "geometry_pass": False,
                "dxf": str(dxf_path.resolve()), "sha256": _digest(dxf_path),
                "baseline_sha256": faces.get("baseline", {}).get("sha256"),
                "no_precheck_repairs": True, "metrics": {},
                "issues": [{"code": "checker_exception", "type": type(exc).__name__,
                            "message": str(exc)}],
                "limits": "The native checker could not complete on this invalid candidate.",
            }
        passed = not construction_issues and checker["geometry_pass"]
        validation = dict(checker)
        validation.update({
            "pass": passed,
            "construction_pass": not construction_issues,
            "checker_geometry_pass": checker["geometry_pass"],
            "geometry_pass": passed,
            "dxf": str((output_dir / OUTPUT_NAMES["dxf"]).resolve()),
            "construction_issues": construction_issues,
            "issues": construction_issues + checker["issues"],
        })
        report = {
            "schema_version": 1, "module": recipe["module"], "pass": passed,
            "recipe": str(recipe_path), "recipe_sha256": _digest(recipe_path),
            "source_dxf": str(source_dxf) if source_dxf is not None else None,
            "source_face_config": str(face_config) if face_config is not None else None,
            "metrics": metrics, "operations": [
                {key: value for key, value in row.items() if key not in {"polygon", "fit_config", "shape", "fit"}}
                for row in recipe.get("operations", [])
            ],
            "neighbor_fit": neighbor, "construction_issues": construction_issues,
            "checker_issue_codes": [issue["code"] for issue in checker["issues"]],
            "limits": "Geometry semantics and source-image accuracy are external judgments; this report covers native construction and face validation.",
        }
        _write_json(stage / OUTPUT_NAMES["manifest"], manifest)
        _write_json(stage / OUTPUT_NAMES["report"], report)
        _write_json(stage / OUTPUT_NAMES["validation"], validation)
        if set(path.name for path in stage.iterdir()) != set(OUTPUT_NAMES.values()):
            raise RuntimeError("incomplete staged artifact set")
        os.replace(stage, output_dir)
    except Exception:
        if stage.exists() and stage.parent == output_dir.parent and stage.name.startswith(f".{output_dir.name}.staging-"):
            shutil.rmtree(stage)
        raise
    return {
        "pass": passed,
        **{key: str((output_dir / name).resolve()) for key, name in OUTPUT_NAMES.items()},
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recipe", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--source-dxf", type=Path)
    parser.add_argument("--face-config", type=Path)
    args = parser.parse_args(argv)
    try:
        result = construct(args.recipe, args.output_dir, args.source_dxf, args.face_config)
    except (ValueError, OSError, ezdxf.DXFError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
