import unittest, tempfile, pathlib, json, hashlib, subprocess, sys
import ezdxf
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
try:
 from check_cad import check
except ImportError:
 check=None
class CheckerTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(); self.p=pathlib.Path(self.tmp.name)
  self.doc=ezdxf.new(); self.doc.units=6
  for layer in ['BUILDING','ROAD','FRAME','GRADE']: self.doc.layers.new(layer)
  self.cfg=dict(schema_version=1,units='m',tolerance=.001,z_tolerance=.001,layers=dict(building=['BUILDING'],road=['ROAD'],frame=['FRAME'],reference=['GRADE']),required_layers=['FRAME'],required_handles=[],forbidden_layers=['WEIR'])
  self.poly([(0,0),(20,0),(20,20),(0,20)],'FRAME')
 def tearDown(self): self.tmp.cleanup()
 def poly(self,pts,layer='BUILDING',closed=True): return self.doc.modelspace().add_lwpolyline(pts,close=closed,dxfattribs={'layer':layer})
 def runcheck(self):
  self.assertIsNotNone(check,'checker implementation missing')
  self.doc.saveas(self.p/'a.dxf'); (self.p/'project.json').write_text(json.dumps(self.cfg))
  return check(self.p/'a.dxf',self.p/'project.json')
 def codes(self,r): return [x['id'] for x in r['checks'] if x['status']=='fail']
 def test_valid_geometry_pending_review(self):
  self.poly([(2,2),(4,2),(4,4),(2,4)]); r=self.runcheck(); self.assertTrue(r['geometry_pass']); self.assertFalse(r['delivery_ready'])
 def test_open_building(self):
  self.poly([(2,2),(4,2),(4,4)],closed=False); self.assertIn('building_closed',self.codes(self.runcheck()))
 def test_open_grade_allowed(self):
  self.poly([(2,2),(4,2),(4,4)],'GRADE',False); self.assertTrue(self.runcheck()['geometry_pass'])
 def test_self_crossing(self):
  self.poly([(2,2),(4,4),(2,4),(4,2)]); self.assertIn('valid_2d',self.codes(self.runcheck()))
 def test_z(self):
  self.poly([(2,2),(4,2),(4,4)]).dxf.elevation=2; self.assertIn('planar_z',self.codes(self.runcheck()))
 def test_duplicate_segment(self):
  self.doc.modelspace().add_line((1,1),(2,1)); self.doc.modelspace().add_line((2,1),(1,1)); self.assertIn('duplicate_segments',self.codes(self.runcheck()))
 def test_missing_layer_and_handle(self):
  self.cfg['required_layers']=['ABSENT']; self.cfg['required_handles']=['BAD']; codes=self.codes(self.runcheck()); self.assertIn('required_layers',codes); self.assertIn('required_handles',codes)
 def test_empty_required_layer(self):
  self.cfg['required_layers']=['ROAD']; self.assertIn('required_layers',self.codes(self.runcheck()))
 def test_forbidden(self):
  self.doc.layers.new('WEIR'); self.doc.modelspace().add_line((1,1),(2,2),dxfattribs={'layer':'WEIR'}); self.assertIn('forbidden_layers',self.codes(self.runcheck()))
 def test_road_intrusion(self):
  self.poly([(0,8),(20,8),(20,12),(0,12)],'ROAD'); self.poly([(3,10),(5,10),(5,14),(3,14)]); self.assertIn('road_intrusion',self.codes(self.runcheck()))
 def test_connected_road_and_width(self):
  self.poly([(0,8),(20,8),(20,12),(0,12)],'ROAD'); self.cfg['road_connections']=[dict(id='route',point_a=[1,10],point_b=[19,10],max_snap=.01)]; self.cfg['width_probes']=[dict(id='width',segment=[[5,0],[5,20]],expected=4,tolerance=.01)]; r=self.runcheck(); self.assertTrue(r['geometry_pass']); self.assertEqual(next(x for x in r['checks'] if x['id']=='width:width')['measured'],4)
 def test_point_touch_not_connected(self):
  self.poly([(0,0),(4,0),(4,4),(0,4)],'ROAD'); self.poly([(4,4),(8,4),(8,8),(4,8)],'ROAD'); self.cfg['road_connections']=[dict(id='route',point_a=[2,2],point_b=[6,6],max_snap=.01)]; self.assertIn('connection:route',self.codes(self.runcheck()))
 def test_frozen_bulge_change(self):
  e=self.poly([(2,2,0,0,1),(4,2,0,0,0),(4,4,0,0,0)]); self.doc.saveas(self.p/'baseline.dxf'); self.cfg.update(baseline='baseline.dxf',frozen_handles=[e.dxf.handle]); e.set_points([(2,2,0,0,.5),(4,2,0,0,0),(4,4,0,0,0)]); self.assertIn('frozen_geometry',self.codes(self.runcheck()))
 def test_arc_and_independent_control(self):
  self.doc.modelspace().add_arc((10,10),3,0,180,dxfattribs={'layer':'GRADE'}); self.cfg['control_points']=[dict(id='obs',layer='GRADE',point=[10,13],max_distance=.01,source='survey')]; self.assertTrue(self.runcheck()['geometry_pass']); self.cfg['control_points'][0]['point']=[10,16]; self.assertIn('control:obs',self.codes(self.runcheck()))
 def test_unknown_units(self):
  self.cfg['units']='feet'; self.assertIn('units',self.codes(self.runcheck()))
 def test_frozen_unit_change(self):
  e=self.poly([(2,2),(4,2),(4,4)]); self.doc.saveas(self.p/'baseline.dxf'); self.cfg.update(baseline='baseline.dxf',frozen_handles=[e.dxf.handle]); self.doc.units=4; self.assertIn('frozen_geometry',self.codes(self.runcheck()))
 def test_unsupported(self):
  self.doc.modelspace().add_text('X'); self.assertIn('supported_entities',self.codes(self.runcheck()))
 def test_review_requires_current_hash_existing_file(self):
  self.cfg['review']={k:dict(status='pass',evidence='review.md',dxf_sha256='fake',reviewer='reviewer') for k in ['stage_one','cad_visual','road_semantics']}; self.assertFalse(self.runcheck()['delivery_ready']); (self.p/'review.md').write_text('Reviewed'); self.assertFalse(self.runcheck()['delivery_ready'])
 def test_cli_geometry_only_and_missing_review(self):
  self.runcheck(); args=[sys.executable,str(ROOT/'scripts/check_cad.py'),str(self.p/'a.dxf'),'--project',str(self.p/'project.json'),'--report',str(self.p/'qa.json')]; self.assertEqual(subprocess.run(args,capture_output=True).returncode,1); self.assertEqual(subprocess.run(args+['--geometry-only'],capture_output=True).returncode,0)
 def test_valid_review_current_hash(self):
  self.doc.saveas(self.p/'a.dxf'); digest=hashlib.sha256((self.p/'a.dxf').read_bytes()).hexdigest(); (self.p/'review.md').write_text('Source and CAD inspected')
  self.cfg['required_reviews']=[]; self.cfg['review']={k:dict(status='pass',evidence='review.md',dxf_sha256=digest,reviewer='independent reviewer') for k in ['cad_visual','road_semantics']}
  (self.p/'project.json').write_text(json.dumps(self.cfg)); self.assertTrue(check(self.p/'a.dxf',self.p/'project.json')['delivery_ready'])
 def test_no_road_surface_not_run(self):
  r=self.runcheck(); self.assertEqual(next(x for x in r['checks'] if x['id']=='road_intrusion')['status'],'not_run')
 def test_bulged_boundary_pass_unchanged(self):
  e=self.poly([(5,5,0,0,1),(9,5,0,0,0),(9,9,0,0,0),(5,9,0,0,0)]); self.doc.saveas(self.p/'baseline.dxf'); self.cfg.update(baseline='baseline.dxf',frozen_handles=[e.dxf.handle]); self.assertTrue(self.runcheck()['geometry_pass'])
 def test_cli_must_not_overwrite_baseline(self):
  self.doc.saveas(self.p/'baseline.dxf'); self.cfg.update(baseline='baseline.dxf',frozen_handles=[]); self.runcheck(); before=(self.p/'baseline.dxf').read_bytes()
  args=[sys.executable,str(ROOT/'scripts/check_cad.py'),str(self.p/'a.dxf'),'--project',str(self.p/'project.json'),'--report',str(self.p/'baseline.dxf'),'--geometry-only']; self.assertNotEqual(subprocess.run(args,capture_output=True).returncode,0); self.assertEqual((self.p/'baseline.dxf').read_bytes(),before)
 def test_cross_layer_overlap_reported(self):
  self.doc.modelspace().add_line((1,1),(3,1),dxfattribs={'layer':'GRADE'}); self.doc.modelspace().add_line((1,1),(3,1),dxfattribs={'layer':'ROAD'}); r=self.runcheck(); self.assertIn('cross_layer_overlap',[x['id'] for x in r['checks']]); self.assertEqual(next(x for x in r['checks'] if x['id']=='cross_layer_overlap')['status'],'warning')
 def test_buildings_overlap(self):
  self.poly([(2,2),(6,2),(6,6),(2,6)]); self.poly([(4,4),(8,4),(8,8),(4,8)]); self.assertIn('building_overlap',self.codes(self.runcheck()))
 def test_empty_drawing_fails(self):
  self.doc.modelspace().delete_all_entities(); self.cfg['required_layers']=[]; self.assertIn('nonempty_drawing',self.codes(self.runcheck()))
 def test_nonrectangular_frame_fails(self):
  self.doc.modelspace().delete_all_entities(); self.poly([(0,0),(20,0),(10,20),(0,20)],'FRAME'); self.assertIn('frame_rectangle',self.codes(self.runcheck()))
 def test_rotated_frame_four_lines(self):
  self.doc.modelspace().delete_all_entities(); pts=[(10,0),(20,10),(10,20),(0,10)]
  for a,b in zip(pts,pts[1:]+pts[:1]): self.doc.modelspace().add_line(a,b,dxfattribs={'layer':'FRAME'})
  self.assertTrue(self.runcheck()['geometry_pass'])
 def test_explicit_road_hole_preserves_courtyard(self):
  outer=self.poly([(1,1),(19,1),(19,19),(1,19)],'ROAD'); inner=self.poly([(4,4),(16,4),(16,16),(4,16)],'ROAD'); self.poly([(6,6),(10,6),(10,10),(6,10)]); self.cfg['hole_map']={outer.dxf.handle:[inner.dxf.handle]}; self.assertTrue(self.runcheck()['geometry_pass'])
 def test_hole_missing_handle(self):
  outer=self.poly([(1,1),(19,1),(19,19),(1,19)],'ROAD'); self.cfg['hole_map']={outer.dxf.handle:['MISSING']}; self.assertIn('hole_map',self.codes(self.runcheck()))
 def test_hole_wrong_role_or_parent(self):
  outer=self.poly([(1,1),(9,1),(9,9),(1,9)],'ROAD'); inner=self.poly([(11,11),(14,11),(14,14),(11,14)],'ROAD'); self.cfg['hole_map']={outer.dxf.handle:[inner.dxf.handle]}; self.assertIn('hole_map',self.codes(self.runcheck())); inner.dxf.layer='BUILDING'; self.assertIn('hole_map',self.codes(self.runcheck()))
 def test_hole_open_or_touching(self):
  outer=self.poly([(1,1),(19,1),(19,19),(1,19)],'ROAD'); inner=self.poly([(1,4),(16,4),(16,16),(1,16)],'ROAD'); self.cfg['hole_map']={outer.dxf.handle:[inner.dxf.handle]}; self.assertIn('hole_map',self.codes(self.runcheck())); inner.closed=False; self.assertIn('hole_map',self.codes(self.runcheck()))
 def test_hole_multiple_parents(self):
  a=self.poly([(1,1),(19,1),(19,19),(1,19)],'ROAD'); b=self.poly([(2,2),(18,2),(18,18),(2,18)],'ROAD'); inner=self.poly([(4,4),(16,4),(16,16),(4,16)],'ROAD'); self.cfg['hole_map']={a.dxf.handle:[inner.dxf.handle],b.dxf.handle:[inner.dxf.handle]}; self.assertIn('hole_map',self.codes(self.runcheck()))
 def test_reviewer_requires_nonblank_string(self):
  self.doc.saveas(self.p/'a.dxf'); digest=hashlib.sha256((self.p/'a.dxf').read_bytes()).hexdigest(); (self.p/'review.md').write_text('Reviewed current CAD')
  for reviewer in [None, [], 123, '   ']:
   with self.subTest(reviewer=reviewer):
    self.cfg['review']={k:dict(status='pass',evidence='review.md',dxf_sha256=digest,reviewer=reviewer) for k in ['cad_visual','road_semantics']}
    (self.p/'project.json').write_text(json.dumps(self.cfg)); self.assertFalse(check(self.p/'a.dxf',self.p/'project.json')['delivery_ready'])
if __name__=='__main__': unittest.main()
