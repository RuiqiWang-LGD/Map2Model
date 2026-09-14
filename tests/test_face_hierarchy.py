"""Counterexamples for native parent/child face construction, independent of maps."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import ezdxf

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'check_face_hierarchy.py'


class FaceHierarchyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.doc = ezdxf.new('R2018')
        self.doc.units = 4
        self.m = self.doc.modelspace()
        self.parent = self.ring([(20, 20), (80, 20), (80, 80), (20, 80)])
        corners = [(0, 0), (100, 0), (100, 100), (0, 100)]
        self.caps = [self.m.add_line(a, b) for a, b in zip(corners, corners[1:] + corners[:1])]
        self.cfg = dict(schema_version=1, module='outer', units=4, frame=[0, 0, 100, 100],
                        tolerances=dict(coordinate=1e-8, area=1e-6, min_edge=.1, min_clearance=.2),
                        expected_road_components=1,
                        parents=[dict(id='P1', handle=self.parent.dxf.handle, clipped=False)],
                        frame_handles=[e.dxf.handle for e in self.caps], details=[])

    def ring(self, xy):
        return self.m.add_lwpolyline(xy, close=True)

    def inspect(self):
        self.doc.saveas(self.root / 'current.dxf')
        spec = importlib.util.spec_from_file_location('face_checker', SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.inspect(self.root / 'current.dxf', copy.deepcopy(self.cfg), self.root)

    def assertBad(self, code):
        result = self.inspect()
        self.assertFalse(result['geometry_pass'], result)
        self.assertIn(code, [x['code'] for x in result['issues']], result)

    def detailMode(self):
        path = self.root / 'baseline.dxf'
        self.doc.saveas(path)
        self.cfg.update(module='detail', baseline=dict(dxf='baseline.dxf', sha256=hashlib.sha256(path.read_bytes()).hexdigest()))

    def child(self, xy, name='D1', parent='P1'):
        e = self.ring(xy)
        self.cfg['details'].append(dict(id=name, handle=e.dxf.handle, parent=parent, role='BUILDING'))
        return e

    def test_valid_outer_is_two_faces(self):
        r = self.inspect()
        self.assertTrue(r['geometry_pass'], r)
        self.assertEqual(r['metrics']['raw_faces'], 2)

    def test_native_open_ring_fails_even_when_last_point_repeats(self):
        self.parent.set_points([(20,20),(80,20),(80,80),(20,80),(20,20)])
        self.parent.closed = False
        self.assertBad('native_not_closed')

    def test_missing_frame_segment(self):
        self.m.delete_entity(self.caps.pop())
        self.cfg['frame_handles'].pop()
        self.assertBad('frame_coverage')

    def test_duplicate_native_edge(self):
        e = self.m.add_line((0,0),(100,0))
        self.cfg['frame_handles'].append(e.dxf.handle)
        self.assertBad('overlapping_native_edges')

    def test_unknown_entity_cannot_hide_on_unlisted_layer(self):
        self.m.add_line((1,1),(4,4), dxfattribs={'layer':'HIDDEN'})
        self.assertBad('inventory_mismatch')

    def test_point_contact_cannot_count_as_source_clipped_edge(self):
        self.parent.set_points([(20,20),(50,0),(80,20),(80,80),(20,80)])
        self.cfg['parents'][0]['clipped'] = True
        self.assertBad('clipped_parent_without_frame_edge')

    def test_missing_native_t_node_even_if_polygonizer_succeeds(self):
        self.parent.set_points([(20,20),(50,0),(80,20),(80,80),(20,80)])
        self.assertBad('intersection_without_native_endpoint')

    def test_two_road_components_are_project_specific(self):
        self.parent.set_points([(40,0),(60,0),(60,100),(40,100)])
        self.cfg['parents'][0]['clipped'] = True
        for e in self.caps:
            self.m.delete_entity(e)
        paths=[((0,0),(40,0)),((60,0),(100,0)),((100,0),(100,100)),
               ((100,100),(60,100)),((40,100),(0,100)),((0,100),(0,0))]
        self.cfg['frame_handles']=[self.m.add_line(a,b).dxf.handle for a,b in paths]
        self.cfg['expected_road_components']=2
        r=self.inspect()
        self.assertTrue(r['geometry_pass'], r)
        self.assertEqual(r['metrics']['road_components'],2)

    def test_self_intersection(self):
        self.parent.set_points([(20,20),(80,80),(80,20),(20,80)])
        self.assertBad('invalid_ring')

    def test_tiny_native_edge(self):
        self.parent.set_points([(20,20),(20.01,20),(80,20),(80,80),(20,80)])
        self.assertBad('short_native_edge')

    def test_nonplanar(self):
        self.parent.dxf.elevation = 1
        self.assertBad('nonplanar_or_extruded')

    def test_nonfinite_thickness_is_not_planar(self):
        self.parent.dxf.thickness = float('nan')
        self.assertBad('nonplanar_or_extruded')

    def test_native_curve_not_silently_chorded(self):
        self.parent.set_points([(20,20,1),(80,20,0),(80,80,0),(20,80,0)], format='xyb')
        self.assertBad('unsupported_curve_or_width')

    def test_incomplete_parent_inventory(self):
        self.cfg['parents']=[]
        self.assertBad('inventory_mismatch')

    def test_valid_nested_children_keep_parent_and_road(self):
        self.detailMode()
        self.child([(30,30),(70,30),(70,70),(30,70)])
        self.child([(40,40),(60,40),(60,60),(40,60)], 'D2')
        r=self.inspect()
        self.assertTrue(r['geometry_pass'], r)
        self.assertTrue(r['metrics']['road_unchanged'])
        self.assertEqual(r['metrics']['raw_faces'],4)

    def test_crossing_closed_children_are_not_independent(self):
        self.detailMode()
        self.child([(30,30),(55,30),(55,55),(30,55)])
        self.child([(45,45),(65,45),(65,65),(45,65)], 'D2')
        self.assertBad('crossing_or_touching_details')

    def test_child_outside_parent(self):
        self.detailMode()
        self.child([(5,5),(15,5),(15,15),(5,15)])
        self.assertBad('detail_outside_parent')

    def test_child_touching_parent(self):
        self.detailMode()
        self.child([(20,30),(35,30),(35,40),(20,40)])
        self.assertBad('detail_parent_clearance')

    def test_moved_accepted_geometry(self):
        self.detailMode()
        self.parent.translate(1,0,0)
        self.assertBad('accepted_geometry_changed')

    def test_baseline_hash_is_pinned(self):
        self.detailMode()
        self.cfg['baseline']['sha256']='0'*64
        self.assertBad('baseline_hash_mismatch')

    def test_new_unregistered_road_split_is_caught(self):
        self.detailMode()
        self.m.add_line((0,50),(20,50))
        self.assertBad('inventory_mismatch')

    def test_nonfinite_parameter_rejected(self):
        self.cfg['tolerances']['coordinate']=float('nan')
        with self.assertRaises(ValueError):
            self.inspect()

    def test_cli_does_not_overwrite_input(self):
        path=self.root/'current.dxf'
        self.doc.saveas(path)
        before=path.read_bytes()
        cfg=self.root/'config.json'
        cfg.write_text(json.dumps(self.cfg),encoding='utf-8')
        run=subprocess.run([sys.executable,str(SCRIPT),str(path),'--config',str(cfg),'--report',str(path),'--overwrite'],capture_output=True)
        self.assertEqual(run.returncode,2)
        self.assertEqual(path.read_bytes(),before)

    def test_cli_does_not_overwrite_hardlinked_input(self):
        path=self.root/'current.dxf'
        self.doc.saveas(path)
        before=path.read_bytes()
        cfg=self.root/'config.json'
        cfg.write_text(json.dumps(self.cfg),encoding='utf-8')
        alias=self.root/'report-alias.json'
        os.link(path,alias)
        run=subprocess.run([sys.executable,str(SCRIPT),str(path),'--config',str(cfg),'--report',str(alias),'--overwrite'],capture_output=True)
        self.assertEqual(path.read_bytes(),before, 'A report alias must not truncate the input DXF')
        self.assertEqual(run.returncode,2)


if __name__ == '__main__':
    unittest.main()
