import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

import ezdxf
from PIL import Image

SCRIPTS=Path(__file__).resolve().parents[1]/'scripts'


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.p=Path(self.tmp.name);self.addCleanup(self.tmp.cleanup)
        self.assertTrue((SCRIPTS/'cad_pipeline.py').exists(),'generic staged runner is missing')
        sys.path.insert(0,str(SCRIPTS));self.addCleanup(lambda:sys.path.remove(str(SCRIPTS)))
        spec=importlib.util.spec_from_file_location('pipeline_under_test',SCRIPTS/'cad_pipeline.py')
        self.mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.mod)
        doc=ezdxf.new();doc.units=0;m=doc.modelspace()
        parent=m.add_lwpolyline([(10,10),(90,10),(90,90),(10,90)],close=True)
        caps=[m.add_line(a,b).dxf.handle for a,b in zip([(0,0),(100,0),(100,100),(0,100)],[(100,0),(100,100),(0,100),(0,0)])]
        doc.saveas(self.p/'outer.dxf')
        faces=dict(schema_version=1,module='outer',units=0,frame=[0,0,100,100],
                   tolerances=dict(coordinate=1e-7,area=1e-5,min_edge=.1,min_clearance=.5),
                   expected_road_components=1,parents=[dict(id='p',handle=parent.dxf.handle,clipped=False)],
                   frame_handles=caps,details=[])
        self.write('outer-faces.json',faces)
        self.config=dict(schema_version=1,output_dir='run',outer=dict(dxf='outer.dxf',faces='outer-faces.json'))

    def write(self,name,value):
        p=self.p/name;p.write_text(json.dumps(value),encoding='utf-8');return p

    def run_pipeline(self,resume=False):
        return self.mod.run_pipeline(self.write('project.json',self.config),resume=resume)

    def detail(self,points=None):
        r=dict(schema_version=1,module='detail',operations=[dict(id='b',action='add',role='BUILDING',parent='p',
               source='Synthetic visible roof',shape={'points':points or [[20,20],[40,20],[40,40],[20,40]]})])
        self.write('detail.json',r);self.config['detail']={'recipe':'detail.json'}

    def test_import_checks_and_returns_dxf_without_claiming_dwg(self):
        before=(self.p/'outer.dxf').read_bytes();result=self.run_pipeline()
        self.assertEqual(result['status'],'complete')
        self.assertIsNone(result['dwg'])
        self.assertTrue(Path(result['dxf']).exists())
        self.assertEqual((self.p/'outer.dxf').read_bytes(),before)

    def test_resume_skips_unchanged_verified_stage(self):
        first=self.run_pipeline();second=self.run_pipeline(resume=True)
        self.assertEqual(first['dxf'],second['dxf'])
        self.assertEqual(second['stages'][0]['execution'],'cached')

    def test_tampered_output_rebuilt_into_new_attempt(self):
        first=self.run_pipeline();Path(first['dxf']).write_text('tampered')
        second=self.run_pipeline(resume=True)
        self.assertEqual(second['status'],'complete')
        self.assertNotEqual(first['dxf'],second['dxf'])
        self.assertEqual(Path(first['dxf']).read_text(),'tampered')

    def test_valid_detail_preserves_outer_and_forms_separate_face(self):
        self.detail();r=self.run_pipeline()
        self.assertEqual(r['status'],'complete')
        qa=json.loads(Path(r['validation']).read_text())
        self.assertTrue(qa['metrics']['road_unchanged'])
        self.assertEqual(qa['metrics']['raw_faces'],3)

    def test_changed_detail_recipe_reuses_outer(self):
        self.detail();first=self.run_pipeline()
        self.detail([[25,20],[45,20],[45,40],[25,40]])
        second=self.run_pipeline(resume=True)
        self.assertEqual(second['stages'][0]['execution'],'cached')
        self.assertEqual(second['stages'][1]['execution'],'ran')
        self.assertNotEqual(first['dxf'],second['dxf'])

    def test_bad_detail_stops_with_candidate_and_no_later_stages(self):
        self.detail([[5,20],[25,20],[25,40],[5,40]])
        r=self.run_pipeline()
        self.assertEqual(r['status'],'failed');self.assertEqual(r['failed_stage'],'detail')
        self.assertTrue(r['issues'])
        self.assertIsNone(r['dwg'])
        state=json.loads((self.p/'run/state.json').read_text())
        self.assertEqual(state['status'],'failed')

    def test_outer_geometry_failure_stops(self):
        f=json.loads((self.p/'outer-faces.json').read_text());f['frame_handles']=[];self.write('outer-faces.json',f)
        r=self.run_pipeline();self.assertEqual(r['status'],'failed');self.assertEqual(r['failed_stage'],'outer')

    def test_corrupt_dxf_records_failed_stage(self):
        (self.p/'outer.dxf').write_text('broken dxf')
        r=self.run_pipeline()
        self.assertEqual(r['status'],'failed');self.assertEqual(r['failed_stage'],'outer')
        self.assertTrue(r['issues'])

    def test_import_failed_topology_records_terminal_failure(self):
        self.detail([[5,20],[25,20],[25,40],[5,40]])
        self.run_pipeline()
        state=json.loads((self.p/'run/state.json').read_text())
        candidate=state['stages']['detail']['result']
        self.config.pop('detail')
        self.config.update(output_dir='import_bad',outer={'dxf':candidate['dxf'],'faces':candidate['faces']})
        result=self.run_pipeline()
        self.assertEqual(result['status'],'failed')
        saved=json.loads((self.p/'import_bad/state.json').read_text())
        self.assertEqual(saved['status'],'failed')
        self.assertEqual(saved['stages']['outer']['status'],'failed')

    def test_output_cannot_be_input_directory(self):
        self.config['output_dir']='.'
        with self.assertRaises(ValueError):self.run_pipeline()
        self.assertTrue((self.p/'outer.dxf').exists())

    def test_resume_must_be_explicit(self):
        self.run_pipeline()
        with self.assertRaises(ValueError):self.run_pipeline()

    def test_invalid_converter_tolerance_rejected_before_run(self):
        self.config['delivery']={'core_console':'accoreconsole.exe','tolerance':float('nan')}
        with self.assertRaises(ValueError):self.run_pipeline()
        self.assertFalse((self.p/'run').exists())

    def test_null_outer_is_controlled_input_error(self):
        self.config['outer']=None
        with self.assertRaises(ValueError):self.run_pipeline()
        self.assertFalse((self.p/'run').exists())

    def test_review_can_use_automatic_pre_detail_checkpoint(self):
        self.detail();Image.new('RGB',(100,100),'white').save(self.p/'source.png')
        self.write('transform.json',{'world_to_image':[[1,0,0],[0,-1,100],[0,0,1]],'width':100,'height':100})
        self.config['review']={'source':'source.png','transform':'transform.json'}
        result=self.run_pipeline()
        self.assertEqual(result['status'],'complete')
        index=json.loads(Path(result['review']['index']).read_text())
        before=Path(result['stages'][0]['directory'])/'cad.dxf'
        self.assertEqual(Path(index['inputs']['before_dxf']),before)
        self.assertEqual(index['count'],2)


if __name__=='__main__':unittest.main()
