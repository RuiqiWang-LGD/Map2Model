"""Behavior tests for recipe-driven native CAD construction."""
import importlib.util
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import ezdxf
from shapely.geometry import Polygon


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "cad_construct.py"


def load_module():
    spec = importlib.util.spec_from_file_location("cad_construct", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TOLERANCES = {
    "coordinate": 1e-8,
    "area": 1e-6,
    "min_edge": 0.1,
    "min_clearance": 0.2,
}


def point_shape(points):
    return {"points": [list(p) for p in points]}


def local_shape(origin, angle, rectangles, polygons=(), subtract=()):
    return {
        "origin": list(origin),
        "angle_degrees": angle,
        "rectangles": [list(r) for r in rectangles],
        "polygons": [[list(p) for p in ring] for ring in polygons],
        "subtract_rectangles": [list(r) for r in subtract],
    }


class CadConstructTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.module = load_module()

    def write_recipe(self, value, name="recipe.json"):
        path = self.root / name
        path.write_text(json.dumps(value, allow_nan=True), encoding="utf-8")
        return path

    def outer_recipe(self, parents=None, expected=1):
        if parents is None:
            parents = [
                {
                    "id": "P1",
                    "clipped": False,
                    "source": "hand-derived parent one",
                    "shape": point_shape([(10, 10), (45, 10), (45, 90), (10, 90)]),
                },
                {
                    "id": "P2",
                    "clipped": False,
                    "source": "hand-derived parent two",
                    "shape": point_shape([(55, 10), (90, 10), (90, 90), (55, 90)]),
                },
            ]
        return {
            "schema_version": 1,
            "module": "outer",
            "units": 4,
            "frame": [0, 0, 100, 100],
            "tolerances": dict(TOLERANCES),
            "expected_road_components": expected,
            "parents": parents,
        }

    def construct_outer(self, name="outer"):
        return self.module.construct(self.write_recipe(self.outer_recipe(), name + ".json"), self.root / name)

    def detail_recipe(self, operations, neighbor_fit=None):
        value = {
            "schema_version": 1,
            "module": "detail",
            "operations": operations,
        }
        if neighbor_fit is not None:
            value["neighbor_fit"] = neighbor_fit
        return value

    def add_op(self, object_id, role, parent, shape, source="hand-derived detail", fit=None):
        value = {
            "id": object_id,
            "action": "add",
            "role": role,
            "parent": parent,
            "source": source,
            "shape": shape,
        }
        if fit is not None:
            value["fit"] = fit
        return value

    def detail_construct(self, source_result, operations, name, neighbor_fit=None):
        recipe = self.write_recipe(self.detail_recipe(operations, neighbor_fit), name + ".json")
        return self.module.construct(
            recipe,
            self.root / name,
            source_dxf=source_result["dxf"],
            face_config=source_result["faces"],
        )

    def read_ring(self, dxf_path, handle):
        doc = ezdxf.readfile(dxf_path)
        entity = doc.entitydb[handle]
        return Polygon([(p[0], p[1]) for p in entity.get_points("xy")])

    def test_outer_writes_native_rings_unique_frame_caps_and_simplifies_collinear_runs(self):
        parents = [{
            "id": "EDGE",
            "clipped": True,
            "source": "visible crop edge from y=20 to y=80",
            "shape": point_shape([(0, 20), (10, 20), (20, 20), (40, 20),
                                  (40, 80), (20, 80), (10, 80), (0, 80)]),
            "fit": {"simplify": 0.5, "max_shift": 0.01, "max_area_change": 0.01},
        }]
        result = self.module.construct(
            self.write_recipe(self.outer_recipe(parents)), self.root / "constructed"
        )
        self.assertTrue(result["pass"], result)
        self.assertEqual(set(result), {"pass", "dxf", "faces", "manifest", "report", "validation"})
        self.assertTrue(all(Path(result[k]).is_absolute() for k in result if k != "pass"))
        doc = ezdxf.readfile(result["dxf"])
        rings = list(doc.modelspace().query("LWPOLYLINE"))
        lines = list(doc.modelspace().query("LINE"))
        self.assertEqual(len(rings), 1)
        self.assertTrue(rings[0].closed)
        self.assertEqual(rings[0].dxf.const_width, 0)
        self.assertEqual(len(list(rings[0].get_points("xy"))), 4)
        self.assertEqual(len(lines), 5)  # left side is split around the real parent edge
        self.assertEqual(len({e.dxf.handle for e in lines}), 5)
        faces = json.loads(Path(result["faces"]).read_text(encoding="utf-8"))
        self.assertEqual(faces["parents"][0]["handle"], rings[0].dxf.handle)
        self.assertEqual(set(faces["frame_handles"]), {e.dxf.handle for e in lines})
        validation = json.loads(Path(result["validation"]).read_text(encoding="utf-8"))
        checked_dxf = Path(validation["dxf"])
        self.assertTrue(checked_dxf.is_file())
        self.assertEqual(checked_dxf, Path(result["dxf"]))
        self.assertEqual(hashlib.sha256(checked_dxf.read_bytes()).hexdigest(), validation["sha256"])

    def test_fractional_clipped_parent_and_frame_cap_share_the_exact_native_endpoint(self):
        recipe = self.outer_recipe([{
            "id": "FRACTIONAL",
            "clipped": True,
            "source": "fractional source contact on the bottom frame edge",
            "shape": point_shape([(3.3, 0), (20, 0), (20, 40), (3.3, 40)]),
        }])
        recipe["tolerances"]["coordinate"] = 1e-12
        result = self.module.construct(
            self.write_recipe(recipe, "fractional.json"), self.root / "fractional"
        )
        self.assertTrue(result["pass"], result)
        doc = ezdxf.readfile(result["dxf"])
        parent = next(iter(doc.modelspace().query("LWPOLYLINE")))
        parent_points = {(p[0], p[1]) for p in parent.get_points("xy")}
        cap_points = {
            (float(point.x), float(point.y))
            for line in doc.modelspace().query("LINE")
            for point in (line.dxf.start, line.dxf.end)
        }
        self.assertIn((3.3, 0.0), parent_points & cap_points)
        validation = json.loads(Path(result["validation"]).read_text(encoding="utf-8"))
        self.assertEqual(validation["metrics"]["raw_cuts"], 0.0)
        self.assertEqual(validation["metrics"]["raw_dangles"], 0.0)

    def test_rotated_l_union_preserves_concavity_instead_of_using_a_bounding_box(self):
        parent = {
            "id": "L",
            "clipped": False,
            "source": "two visible orthogonal wings",
            "shape": local_shape((30, 30), 30, [(0, 0, 10, 4), (0, 0, 4, 10)]),
        }
        result = self.module.construct(
            self.write_recipe(self.outer_recipe([parent])), self.root / "l-shape"
        )
        self.assertTrue(result["pass"], result)
        faces = json.loads(Path(result["faces"]).read_text(encoding="utf-8"))
        poly = self.read_ring(result["dxf"], faces["parents"][0]["handle"])
        self.assertAlmostEqual(poly.area, 64.0, places=6)
        self.assertGreater(len(poly.exterior.coords) - 1, 4)

    def test_shape_that_produces_two_components_is_rejected(self):
        parent = {
            "id": "SPLIT",
            "clipped": False,
            "source": "two roofs must be separate operations",
            "shape": local_shape((0, 0), 0, [(10, 10, 20, 20), (40, 40, 50, 50)]),
        }
        with self.assertRaisesRegex(ValueError, "one polygon"):
            self.module.construct(self.write_recipe(self.outer_recipe([parent])), self.root / "bad")
        self.assertFalse((self.root / "bad").exists())

    def test_detail_add_then_replace_keeps_baseline_and_unmodified_handles(self):
        outer = self.construct_outer()
        first = self.detail_construct(
            outer,
            [
                self.add_op("B1", "BUILDING", "P1", point_shape([(15, 20), (25, 20), (25, 30), (15, 30)])),
                self.add_op("G1", "GREEN_MAIN", "P1", point_shape([(30, 20), (40, 20), (40, 40), (30, 40)])),
            ],
            "detail-one",
        )
        self.assertTrue(first["pass"], first)
        before_faces = json.loads(Path(first["faces"]).read_text(encoding="utf-8"))
        before = {row["id"]: row["handle"] for row in before_faces["parents"] + before_faces["details"]}
        replacement = {
            "id": "B1",
            "action": "replace",
            "role": "BUILDING",
            "parent": "P1",
            "source": "corrected ground-contact evidence",
            "shape": point_shape([(16, 20), (27, 20), (27, 31), (16, 31)]),
        }
        second = self.detail_construct(
            first,
            [replacement, self.add_op("B2", "BUILDING", "P2", point_shape([(60, 20), (70, 20), (70, 30), (60, 30)]))],
            "detail-two",
        )
        self.assertTrue(second["pass"], second)
        after_faces = json.loads(Path(second["faces"]).read_text(encoding="utf-8"))
        after = {row["id"]: row["handle"] for row in after_faces["parents"] + after_faces["details"]}
        self.assertEqual(after["P1"], before["P1"])
        self.assertEqual(after["P2"], before["P2"])
        self.assertEqual(after["G1"], before["G1"])
        self.assertEqual(after["B1"], before["B1"])
        self.assertIn("B2", after)
        manifest = json.loads(Path(second["manifest"]).read_text(encoding="utf-8"))
        self.assertIsInstance(manifest, list)
        objects = {row["id"]: row for row in manifest}
        self.assertEqual(objects["B1"]["before_handle"], before["B1"])
        self.assertEqual(objects["B1"]["source"], "corrected ground-contact evidence")
        self.assertEqual(objects["B2"]["status"], "added")
        self.assertIsNone(objects["B2"]["before_handle"])
        self.assertEqual(objects["G1"]["status"], "preserved")

    def test_parent_target_and_secret_role_change_are_rejected(self):
        outer = self.construct_outer()
        parent_replace = {
            "id": "P1", "action": "replace", "role": "BUILDING", "parent": "P1",
            "source": "invalid", "shape": point_shape([(1, 1), (2, 1), (2, 2), (1, 2)]),
        }
        with self.assertRaisesRegex(ValueError, "parent|frame"):
            self.detail_construct(outer, [parent_replace], "parent-target")
        detail = self.detail_construct(
            outer,
            [self.add_op("B1", "BUILDING", "P1", point_shape([(15, 20), (25, 20), (25, 30), (15, 30)]))],
            "valid-detail",
        )
        bad = dict(parent_replace, id="B1", role="GREEN", parent="P1")
        with self.assertRaisesRegex(ValueError, "role|parent"):
            self.detail_construct(detail, [bad], "role-change")

    def test_neighbor_fit_repairs_only_spatially_implicated_green_in_same_parent(self):
        outer = self.construct_outer()
        initial = self.detail_construct(
            outer,
            [
                self.add_op("B1", "BUILDING", "P1", point_shape([(15, 35), (23, 35), (23, 48), (15, 48)])),
                self.add_op("G1", "GREEN_MAIN", "P1", point_shape([(28, 20), (42, 20), (42, 70), (28, 70)])),
                self.add_op("G2", "GREEN_MAIN", "P2", point_shape([(60, 20), (80, 20), (80, 70), (60, 70)])),
            ],
            "green-source",
        )
        self.assertTrue(initial["pass"], initial)
        before_faces = json.loads(Path(initial["faces"]).read_text(encoding="utf-8"))
        handles = {r["id"]: r["handle"] for r in before_faces["details"]}
        g1_before = self.read_ring(initial["dxf"], handles["G1"])
        g2_before = self.read_ring(initial["dxf"], handles["G2"])
        replacement = {
            "id": "B1", "action": "replace", "role": "BUILDING", "parent": "P1",
            "source": "reconstructed building footprint",
            "shape": point_shape([(18, 35), (32, 35), (32, 50), (18, 50)]),
        }
        result = self.detail_construct(
            initial,
            [replacement],
            "green-fit",
            neighbor_fit={
                "roles": ["GREEN_MAIN"], "clearance": 1.0, "simplify": 0.1,
                "max_shift": 10.0, "max_area_change": 0.4,
            },
        )
        self.assertTrue(result["pass"], result)
        faces = json.loads(Path(result["faces"]).read_text(encoding="utf-8"))
        after = {r["id"]: r["handle"] for r in faces["details"]}
        self.assertEqual(after["G1"], handles["G1"])
        self.assertEqual(after["G2"], handles["G2"])
        g1_after = self.read_ring(result["dxf"], after["G1"])
        g2_after = self.read_ring(result["dxf"], after["G2"])
        self.assertLess(g1_after.area, g1_before.area)
        self.assertTrue(g2_after.equals_exact(g2_before, 1e-8))
        report = json.loads(Path(result["report"]).read_text(encoding="utf-8"))
        self.assertEqual(report["neighbor_fit"]["implicated_ids"], ["G1"])

    def test_neighbor_fit_that_splits_green_publishes_failed_candidate(self):
        outer = self.construct_outer()
        initial = self.detail_construct(
            outer,
            [
                self.add_op("B1", "BUILDING", "P1", point_shape([(15, 15), (20, 15), (20, 20), (15, 20)])),
                self.add_op("G1", "GREEN", "P1", point_shape([(12, 40), (42, 40), (42, 60), (12, 60)])),
            ],
            "split-source",
        )
        replacement = {
            "id": "B1", "action": "replace", "role": "BUILDING", "parent": "P1",
            "source": "crosses entire green strip",
            "shape": point_shape([(25, 30), (29, 30), (29, 70), (25, 70)]),
        }
        result = self.detail_construct(
            initial, [replacement], "split-fit",
            neighbor_fit={"roles": ["GREEN"], "clearance": 1, "simplify": 0.1,
                          "max_shift": 20, "max_area_change": 0.5},
        )
        self.assertFalse(result["pass"])
        self.assertTrue(all(Path(result[k]).exists() for k in result if k != "pass"))
        validation = json.loads(Path(result["validation"]).read_text(encoding="utf-8"))
        self.assertIn("neighbor_fit_multipart", [x["code"] for x in validation["construction_issues"]])

    def test_neighbor_fit_repairs_permitted_green_added_in_the_same_recipe(self):
        outer = self.construct_outer()
        result = self.detail_construct(
            outer,
            [
                self.add_op("B1", "BUILDING", "P1", point_shape([(15, 20), (30, 20), (30, 40), (15, 40)])),
                self.add_op("G1", "GREEN", "P1", point_shape([(28, 15), (42, 15), (42, 70), (28, 70)])),
            ],
            "same-recipe-fit",
            neighbor_fit={"roles": ["GREEN"], "clearance": 1, "simplify": 0.1,
                          "max_shift": 10, "max_area_change": 0.4},
        )
        self.assertTrue(result["pass"], result)
        report = json.loads(Path(result["report"]).read_text(encoding="utf-8"))
        self.assertEqual(report["neighbor_fit"]["implicated_ids"], ["G1"])
        faces = json.loads(Path(result["faces"]).read_text(encoding="utf-8"))
        handles = {r["id"]: r["handle"] for r in faces["details"]}
        building = self.read_ring(result["dxf"], handles["B1"])
        green = self.read_ring(result["dxf"], handles["G1"])
        self.assertGreaterEqual(building.boundary.distance(green.boundary), 1.0 - 1e-8)

    def test_neighbor_fit_fails_when_simplification_erases_requested_clearance(self):
        outer_recipe = self.outer_recipe([{
            "id": "P1",
            "clipped": False,
            "source": "single synthetic parent",
            "shape": point_shape([(10, 10), (90, 10), (90, 90), (10, 90)]),
        }])
        outer = self.module.construct(
            self.write_recipe(outer_recipe, "clearance-outer.json"),
            self.root / "clearance-outer",
        )
        initial = self.detail_construct(
            outer,
            [self.add_op("G1", "GREEN", "P1", point_shape([(50, 20), (80, 20), (80, 80), (50, 80)]))],
            "clearance-source",
        )
        result = self.detail_construct(
            initial,
            [self.add_op("B1", "BUILDING", "P1", point_shape([(40, 40), (49.5, 40), (49.5, 60), (40, 60)]))],
            "clearance-fit",
            neighbor_fit={"roles": ["GREEN"], "clearance": 2, "simplify": 2,
                          "max_shift": 10, "max_area_change": 0.5},
        )
        self.assertFalse(result["pass"])
        validation = json.loads(Path(result["validation"]).read_text(encoding="utf-8"))
        issue = next(row for row in validation["construction_issues"]
                     if row["code"] == "neighbor_fit_clearance_not_met")
        self.assertEqual(issue["id"], "G1")
        self.assertEqual(issue["requested_clearance"], 2.0)
        self.assertAlmostEqual(issue["actual_gap"], 0.5)
        self.assertEqual(issue["building_ids"], ["B1"])

    def test_neighbor_fit_preserves_deliberate_nested_green_relationship(self):
        outer_recipe = self.outer_recipe([{
            "id": "P1", "clipped": False, "source": "single synthetic parent",
            "shape": point_shape([(10, 10), (90, 10), (90, 90), (10, 90)]),
        }])
        outer = self.module.construct(
            self.write_recipe(outer_recipe, "nested-outer.json"), self.root / "nested-outer"
        )
        result = self.detail_construct(
            outer,
            [
                self.add_op("G1", "GREEN", "P1", point_shape([(20, 20), (80, 20), (80, 80), (20, 80)])),
                self.add_op("B1", "BUILDING", "P1", point_shape([(40, 40), (60, 40), (60, 60), (40, 60)])),
            ],
            "nested-fit",
            neighbor_fit={"roles": ["GREEN"], "clearance": 2, "simplify": 2,
                          "max_shift": 10, "max_area_change": 0.5},
        )
        self.assertTrue(result["pass"], result)
        report = json.loads(Path(result["report"]).read_text(encoding="utf-8"))
        self.assertEqual(report["neighbor_fit"]["implicated_ids"], [])
        faces = json.loads(Path(result["faces"]).read_text(encoding="utf-8"))
        handles = {row["id"]: row["handle"] for row in faces["details"]}
        self.assertAlmostEqual(self.read_ring(result["dxf"], handles["G1"]).area, 3600.0)

    def test_unknown_fields_nan_duplicate_ids_and_output_collision_are_input_errors(self):
        bad_unknown = self.outer_recipe()
        bad_unknown["surprise"] = True
        with self.assertRaises(ValueError):
            self.module.construct(self.write_recipe(bad_unknown, "unknown.json"), self.root / "unknown")
        bad_nan = self.outer_recipe()
        bad_nan["frame"][0] = math.nan
        with self.assertRaises(ValueError):
            self.module.construct(self.write_recipe(bad_nan, "nan.json"), self.root / "nan")
        bad_duplicate = self.outer_recipe()
        bad_duplicate["parents"][1]["id"] = "P1"
        with self.assertRaises(ValueError):
            self.module.construct(self.write_recipe(bad_duplicate, "duplicate.json"), self.root / "duplicate")
        existing = self.root / "existing"
        existing.mkdir()
        with self.assertRaises(ValueError):
            self.module.construct(self.write_recipe(self.outer_recipe(), "collision.json"), existing)

    def test_output_hardlink_alias_and_cli_exit_codes_protect_inputs(self):
        recipe = self.write_recipe(self.outer_recipe(), "cli-recipe.json")
        alias = self.root / "alias.json"
        os.link(recipe, alias)
        with self.assertRaises(ValueError):
            self.module.construct(recipe, alias)
        good = subprocess.run(
            [sys.executable, str(SCRIPT), str(recipe), "--output-dir", str(self.root / "cli-good")],
            capture_output=True, text=True,
        )
        self.assertEqual(good.returncode, 0, good.stderr)
        geometric = self.outer_recipe(expected=2)
        geometric_path = self.write_recipe(geometric, "geometry.json")
        failed = subprocess.run(
            [sys.executable, str(SCRIPT), str(geometric_path), "--output-dir", str(self.root / "cli-fail")],
            capture_output=True, text=True,
        )
        self.assertEqual(failed.returncode, 1, failed.stderr)
        malformed = self.root / "malformed.json"
        malformed.write_text("{}", encoding="utf-8")
        invalid = subprocess.run(
            [sys.executable, str(SCRIPT), str(malformed), "--output-dir", str(self.root / "cli-invalid")],
            capture_output=True, text=True,
        )
        self.assertEqual(invalid.returncode, 2)


if __name__ == "__main__":
    unittest.main()
