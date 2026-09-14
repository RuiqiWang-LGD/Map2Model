import hashlib,importlib.util,json,math,os,subprocess,sys,tempfile,unittest
from pathlib import Path
import ezdxf

SCRIPT=Path(__file__).resolve().parents[1]/'scripts/triage_buildings.py'

class BuildingTriageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.doc=ezdxf.new();self.doc.units=0;self.m=self.doc.modelspace()
        self.rows=[];self.cfg=dict(min_vertices=20,short_edge=.6,short_fraction=.3,axis_angle_degrees=12,off_axis_fraction=.3)
    def add(self,xy,role='BUILDING'):
        e=self.m.add_lwpolyline(xy,close=True)
        self.rows.append(dict(id=f'B{len(self.rows)+1}',handle=e.dxf.handle,role=role))
        return e
    def run_triage(self):
        path=self.root/'current.dxf';self.doc.saveas(path);before=path.read_bytes()
        spec=importlib.util.spec_from_file_location('triage',SCRIPT);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
        result=mod.triage(path,self.rows,self.cfg)
        self.assertEqual(path.read_bytes(),before)
        return result
    def test_rotated_rectangle_not_flagged(self):
        a=math.radians(31)
        self.add([(x*math.cos(a)-y*math.sin(a),x*math.sin(a)+y*math.cos(a)) for x,y in [(0,0),(20,0),(20,8),(0,8)]])
        r=self.run_triage();self.assertEqual(r['candidate_count'],0);self.assertEqual(r['automatic_edits'],0)
    def test_regular_l_and_u_are_not_forced_to_rectangles(self):
        self.add([(0,0),(20,0),(20,5),(5,5),(5,20),(0,20)])
        self.add([(30,0),(50,0),(50,20),(45,20),(45,5),(35,5),(35,20),(30,20)])
        self.assertEqual(self.run_triage()['candidate_count'],0)
    def test_many_zigzags_trigger_review_only(self):
        self.add([(i,5+.4*(i%2)) for i in range(25)]+[(24,0),(0,0)])
        r=self.run_triage();self.assertEqual(r['candidate_count'],1)
        self.assertEqual(r['objects'][0]['status'],'review_candidate');self.assertEqual(r['automatic_edits'],0)
    def test_nonbuilding_layers_are_outside_triage_scope(self):
        self.add([(0,0),(4,0),(4,4),(0,4)],'GREEN')
        self.assertEqual(self.run_triage()['building_count'],0)
    def test_curve_deferred_without_flattening(self):
        e=self.add([(0,0),(10,0),(10,10),(0,10)]);e.set_points([(0,0,1),(10,0,0),(10,10,0),(0,10,0)],format='xyb')
        r=self.run_triage();self.assertEqual(r['deferred_count'],1);self.assertEqual(r['objects'][0]['status'],'geometry_deferred')
    def test_open_geometry_is_not_classified_as_normal(self):
        e=self.add([(0,0),(10,0),(10,10),(0,10)]);e.closed=False
        self.assertEqual(self.run_triage()['deferred_count'],1)
    def test_bad_config_fails(self):
        self.cfg['short_edge']=float('nan')
        with self.assertRaises(ValueError):self.run_triage()
    def test_duplicate_manifest_handle_fails(self):
        self.add([(0,0),(10,0),(10,10),(0,10)])
        self.rows.append(dict(self.rows[0],id='another'))
        with self.assertRaises(ValueError):self.run_triage()
    def test_missing_handle_is_deferred(self):
        self.rows.append(dict(id='B0',handle='FFFF',role='BUILDING'))
        self.assertEqual(self.run_triage()['deferred_count'],1)
    def test_cli_protects_hardlinked_input(self):
        self.add([(0,0),(10,0),(10,10),(0,10)]);dxf=self.root/'current.dxf';self.doc.saveas(dxf)
        cfg=self.root/'config.json';cfg.write_text(json.dumps(self.cfg));manifest=self.root/'manifest.json';manifest.write_text(json.dumps(self.rows))
        before=dxf.read_bytes();report=self.root/'alias.json';os.link(dxf,report)
        run=subprocess.run([sys.executable,str(SCRIPT),str(dxf),'--config',str(cfg),'--manifest',str(manifest),'--report',str(report),'--overwrite'],capture_output=True)
        self.assertEqual(dxf.read_bytes(),before);self.assertEqual(run.returncode,2)

if __name__=='__main__':unittest.main()
