"""Behavior regressions: flat road clipping, source topology, width, false alarms.
Synthetic geometry is test-only; it never substitutes source evidence in a job.
"""
import copy, json, tempfile, unittest, sys, hashlib,os,subprocess
from pathlib import Path
import ezdxf
from shapely.geometry import box, mapping
from shapely.ops import unary_union
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from check_visible_plan import audit,validate_contract

class VisiblePlanTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
  self.source=self.root/'source-observations.txt';self.source.write_text('Independent fixture: 20-unit central road separates left land and right water.')
  self.cfg={'schema_version':1,'width':100,'height':100,'units':0,'sources':[{'path':str(self.source),'sha256':hashlib.sha256(self.source.read_bytes()).hexdigest()}],
   'same_face_groups':{'road':[[50,10],[50,90]],'land':[[10,10],[10,90]],'water':[[90,10],[90,90]]},'distinct_groups':['road','land','water'],
   'protected_regions':[{'id':'primary','geometry':mapping(box(42,2,58,98)),'inset':0,'evidence':'independent fixture expectation'}],
   'single_boundaries':[{'id':'right_shore','segment':[[55,50],[90,50]],'expected':1}],
   'width_probes':[{'id':'road_width','point':[50,50],'segment':[[0,50],[100,50]],'expected':20,'tolerance':.1,'evidence':'20 units from independent fixture observation'}],
   'boundary_sawtooth':{'short_length':3,'consecutive_alternating_turns':5,'minimum_boundary_edges':8,'roles':['ROAD']},'minimum_face_area':.1,'minimum_native_edge':.01,'tolerance':1e-5}
 def tearDown(self):self.tmp.cleanup()
 def drawing(self,extra=None,right=60,left=40):
  d=ezdxf.new();d.units=0
  for l in ['ROAD','LAND','BUILDING']:d.layers.new(l)
  # Node outer frame at source road endpoints in the native file.
  m=d.modelspace();m.add_lwpolyline([(0,0),(left,0),(right,0),(100,0),(100,100),(right,100),(left,100),(0,100)],close=True,dxfattribs={'layer':'LAND'})
  m.add_line((left,0),(left,100),dxfattribs={'layer':'ROAD'});m.add_line((right,0),(right,100),dxfattribs={'layer':'ROAD'})
  if extra:extra(m)
  p=self.root/'test.dxf';d.saveas(p);return p
 def codes(self,path,cfg=None):return {i['code'] for i in audit(path,contract=cfg or self.cfg)[0]['issues']}
 def test_clean_same_plane_passes(self):
  validate_contract(self.cfg,self.root);self.assertEqual(self.codes(self.drawing()),set())
 def test_bridge_underlay_on_other_layer_is_not_ignored(self):
  codes=self.codes(self.drawing(lambda m:m.add_line((0,50),(100,50),dxfattribs={'layer':'LAND'})))
  self.assertIn('primary_road_interior_lines',codes);self.assertIn('crossing_without_native_endpoint',codes)
 def test_source_width_catches_clean_but_narrow_road(self):
  self.assertIn('source_road_width',self.codes(self.drawing(left=45,right=55)))
 def test_duplicate_boundary_rejected(self):
  self.assertIn('duplicate_visible_boundaries',self.codes(self.drawing(lambda m:m.add_line((60,0),(60,100),dxfattribs={'layer':'LAND'}))))
 def test_parallel_shore_strip_rejected(self):
  self.assertIn('shore_boundary_multiplicity',self.codes(self.drawing(lambda m:m.add_line((62,0),(62,100),dxfattribs={'layer':'LAND'}))))
 def test_tiny_valid_rectangle_not_sawtooth(self):
  self.cfg['boundary_sawtooth']['roles'].append('BUILDING')
  codes=self.codes(self.drawing(lambda m:m.add_lwpolyline([(20,20),(22,20),(22,22),(20,22)],close=True,dxfattribs={'layer':'BUILDING'})))
  self.assertNotIn('visible_boundary_sawtooth',codes)
 def test_extended_alternating_teeth_rejected(self):
  self.cfg['boundary_sawtooth']['roles'].append('BUILDING')
  pts=[(20,20),(21,21),(22,20),(23,21),(24,20),(25,21),(26,20),(27,21),(28,20),(28,30),(20,30)]
  self.assertIn('visible_boundary_sawtooth',self.codes(self.drawing(lambda m:m.add_lwpolyline(pts,close=True,dxfattribs={'layer':'BUILDING'}))))
 def test_closed_teeth_split_into_lines_still_rejected(self):
  self.cfg['boundary_sawtooth']['roles'].append('BUILDING')
  pts=[(20,20),(21,21),(22,20),(23,21),(24,20),(25,21),(26,20),(27,21),(28,20),(28,30),(20,30)]
  def extra(m):
   for a,b in zip(pts,pts[1:]+pts[:1]):m.add_line(a,b,dxfattribs={'layer':'BUILDING'})
  report,_=audit(self.drawing(extra),contract=self.cfg)
  self.assertEqual(report['metrics']['raw_faces'],4)
  self.assertIn('visible_boundary_sawtooth',{i['code'] for i in report['issues']})
 def test_closed_teeth_split_across_lwpolylines_still_rejected(self):
  self.cfg['boundary_sawtooth']['roles'].append('BUILDING')
  pts=[(20,20),(21,21),(22,20),(23,21),(24,20),(25,21),(26,20),(27,21),(28,20),(28,30),(20,30)]
  def extra(m):
   for chain in (pts[:4],pts[3:7],pts[6:]+pts[:1]):m.add_lwpolyline(chain,dxfattribs={'layer':'BUILDING'})
  self.assertIn('visible_boundary_sawtooth',self.codes(self.drawing(extra)))
 def test_small_rectangle_split_into_lines_is_not_sawtooth(self):
  self.cfg['boundary_sawtooth']['roles'].append('BUILDING')
  pts=[(20,20),(22,20),(22,22),(20,22)]
  def extra(m):
   for a,b in zip(pts,pts[1:]+pts[:1]):m.add_line(a,b,dxfattribs={'layer':'BUILDING'})
  self.assertEqual(self.codes(self.drawing(extra)),set())
 def test_unconfigured_land_teeth_do_not_inherit_road_role(self):
  pts=[(20,20),(21,21),(22,20),(23,21),(24,20),(25,21),(26,20),(27,21),(28,20),(28,30),(20,30)]
  def extra(m):
   for a,b in zip(pts,pts[1:]+pts[:1]):m.add_line(a,b,dxfattribs={'layer':'LAND'})
  self.assertNotIn('visible_boundary_sawtooth',self.codes(self.drawing(extra)))
 def u_road_drawing(self):
  # Two ten-unit lanes connect across the top; the target at (50,50) has
  # width ten, even though its face crosses the full ray twice.
  road=unary_union([box(45,0,55,100),box(70,0,80,100),box(45,0,80,10)])
  network=unary_union([box(0,0,100,100).boundary,road.boundary])
  d=ezdxf.new();d.units=0;d.layers.new('ROAD');m=d.modelspace()
  for edge in network.geoms:
   pts=list(edge.coords)
   for a,b in zip(pts,pts[1:]):m.add_line((a[0],100-a[1]),(b[0],100-b[1]),dxfattribs={'layer':'ROAD'})
  p=self.root/'u-road.dxf';d.saveas(p)
  self.cfg['protected_regions'][0]['geometry']=mapping(box(47,2,53,98))
  self.cfg['single_boundaries'][0]['segment']=[[79,50],[90,50]]
  return p
 def test_width_does_not_sum_other_crossing_of_same_road_face(self):
  report,_=audit(self.u_road_drawing(),contract=self.cfg)
  self.assertIn('source_road_width',{i['code'] for i in report['issues']})
  self.assertEqual(report['width_probes'][0]['measured'],10)
 def test_width_can_select_target_interval_of_multi_interval_face(self):
  p=self.u_road_drawing();self.cfg['width_probes'][0]['expected']=10
  self.assertEqual(self.codes(p),set())
 def test_width_source_point_must_lie_on_probe_segment(self):
  self.cfg['width_probes'][0]['point']=[50,51]
  self.assertIn('source_road_width',self.codes(self.drawing()))
  with self.assertRaises(ValueError):validate_contract(self.cfg,self.root)
 def test_wrong_independent_group_rejects_self_consistent_map(self):
  cfg=copy.deepcopy(self.cfg);cfg['same_face_groups']['road'].append([10,50])
  self.assertIn('source_same_face_group_split',self.codes(self.drawing(),cfg))
 def test_source_change_invalidates_contract(self):
  self.source.write_text('changed source')
  with self.assertRaises(ValueError):validate_contract(self.cfg,self.root)
 def test_nonplanar_native_line_rejected(self):
  self.assertIn('nonplanar',self.codes(self.drawing(lambda m:m.add_line((10,20,4),(10,30,4),dxfattribs={'layer':'LAND'}))))
 def test_empty_shape_role_patterns_rejected(self):
  self.cfg['boundary_sawtooth']['roles']=[]
  with self.assertRaises(ValueError):validate_contract(self.cfg,self.root)
 def test_unused_role_pattern_cannot_claim_check(self):
  self.cfg['boundary_sawtooth']['roles']=['MISSING_ROAD_LAYER']
  self.assertIn('configured_boundary_role_not_found',self.codes(self.drawing()))
 def test_missing_width_observation_rejected(self):
  self.cfg['width_probes']=[]
  with self.assertRaises(ValueError):validate_contract(self.cfg,self.root)
 def cli(self,p,report):
  cfg=self.root/'contract.json';cfg.write_text(json.dumps(self.cfg),encoding='utf-8')
  return subprocess.run([sys.executable,str(Path(__file__).resolve().parents[1]/'scripts'/'check_visible_plan.py'),str(p),'--contract',str(cfg),'--report',str(report)],capture_output=True,text=True)
 def test_report_hardlink_cannot_destroy_dxf(self):
  p=self.drawing();before=p.read_bytes();report=self.root/'alias.json';os.link(p,report)
  self.assertEqual(self.cli(p,report).returncode,2);self.assertEqual(p.read_bytes(),before)
 def test_report_cannot_destroy_source(self):
  p=self.drawing();before=self.source.read_bytes()
  self.assertEqual(self.cli(p,self.source).returncode,2);self.assertEqual(self.source.read_bytes(),before)
 def test_candidate_cannot_be_its_own_source(self):
  p=self.drawing();self.cfg['sources']=[{'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}]
  self.assertEqual(self.cli(p,self.root/'report.json').returncode,2)
if __name__=='__main__':unittest.main()
