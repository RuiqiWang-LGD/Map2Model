import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import ezdxf
from PIL import Image

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/cad_review.py'


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.assertTrue(SCRIPT.exists(), 'generic CAD evidence sheet tool is missing')
        spec = importlib.util.spec_from_file_location('review_under_test', SCRIPT)
        self.mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(self.mod)
        doc = ezdxf.new(); doc.units = 0
        self.rows = []
        for i in range(7):
            e = doc.modelspace().add_lwpolyline([(10,10),(30,10),(30,30),(10,30)], close=True)
            self.rows.append(dict(id=f'b{i}', handle=e.dxf.handle, role='BUILDING'))
        doc.saveas(self.p/'before.dxf'); doc.saveas(self.p/'after.dxf')
        Image.new('RGB', (100,100), '#557755').save(self.p/'source.png')
        self.write('manifest.json', self.rows)
        self.write('transform.json', dict(world_to_image=[[1,0,0],[0,-1,100],[0,0,1]],width=100,height=100))

    def write(self, name, value):
        p=self.p/name; p.write_text(json.dumps(value),encoding='utf-8'); return p

    def run_review(self, selection=None, **kw):
        return self.mod.make_review(self.p/'before.dxf',self.p/'after.dxf',self.p/'manifest.json',
                                   self.p/'source.png',self.p/'transform.json',selection,self.p/'out',**kw)

    def test_selected_object_uses_supplied_affine_and_keeps_inputs(self):
        selection=self.write('selection.json',{'ids':['b1']})
        protected={p:p.read_bytes() for p in self.p.iterdir() if p.is_file()}
        result=self.run_review(selection,padding=2)
        index=json.loads(Path(result['index']).read_text(encoding='utf-8'))
        self.assertEqual(index['objects'][0]['pixel_bounds'],[8,68,32,92])
        self.assertEqual([r['id'] for r in index['objects']],['b1'])
        self.assertEqual(len(result['pages']),1)
        self.assertEqual(index['visual_validation'],'pending')
        im=Image.open(result['pages'][0]).convert('RGB')
        colors=[color for count,color in im.getcolors(maxcolors=im.width*im.height)]
        self.assertTrue(any(r>190 and g>190 and b<90 for r,g,b in colors),'old yellow line missing')
        self.assertTrue(any(g>190 and b>190 and r<90 for r,g,b in colors),'new cyan line missing')
        self.assertEqual(protected,{p:p.read_bytes() for p in protected})

    def test_all_objects_paginate_without_omission(self):
        result=self.run_review()
        index=json.loads(Path(result['index']).read_text())
        self.assertEqual(len(result['pages']),2)
        self.assertEqual([r['id'] for r in index['objects']],[f'b{i}' for i in range(7)])

    def test_small_crops_are_enlarged_for_readable_review(self):
        r=self.run_review(self.write('selection.json',{'ids':['b0']}),padding=2)
        im=Image.open(r['pages'][0]).convert('RGB')
        # A 24px square source crop fills the 258px-high panel, centered at x=220.
        red,green,blue=im.getpixel((100,100))
        self.assertLess(red,130);self.assertLess(blue,130)

    def test_line_on_image_right_boundary_remains_visible(self):
        doc=ezdxf.new();e=doc.modelspace().add_line((100,80),(100,20))
        doc.saveas(self.p/'after.dxf');doc.saveas(self.p/'before.dxf')
        self.write('manifest.json',[dict(id='edge',handle=e.dxf.handle,role='PARENT')])
        result=self.run_review()
        im=Image.open(result['pages'][0]).convert('RGB')
        colors=[color for count,color in im.getcolors(maxcolors=im.width*im.height)]
        self.assertTrue(any(g>190 and b>190 and r<90 for r,g,b in colors),'boundary line disappeared')

    def test_default_font_without_size_parameter_is_supported(self):
        from unittest.mock import patch
        from PIL import ImageFont
        original=ImageFont.load_default
        def older_api():return original()
        with patch.object(self.mod.ImageFont,'load_default',older_api):
            result=self.run_review(self.write('selection.json',{'ids':['b0']}))
        self.assertTrue(Path(result['pages'][0]).is_file())

    def test_unknown_selection_rejected(self):
        with self.assertRaises(ValueError):self.run_review(self.write('selection.json',{'ids':['missing']}))
        self.assertFalse((self.p/'out').exists())

    def test_missing_old_selector_not_guessed(self):
        self.rows[0]['before_handle']='BAD';self.write('manifest.json',self.rows)
        with self.assertRaises(ValueError):self.run_review()

    def test_added_roof_explicitly_has_no_old_geometry(self):
        self.rows[0]['before_handles']=[];self.write('manifest.json',self.rows)
        r=self.run_review(self.write('selection.json',{'ids':['b0']}))
        index=json.loads(Path(r['index']).read_text())
        self.assertEqual(index['objects'][0]['before_handles'],[])

    def test_image_dimension_mismatch_rejected(self):
        self.write('transform.json',{'world_to_image':[[1,0,0],[0,-1,100],[0,0,1]],'width':200,'height':100})
        with self.assertRaises(ValueError):self.run_review()

    def test_existing_output_not_overwritten(self):
        (self.p/'out').mkdir();(self.p/'out/sentinel').write_text('keep')
        with self.assertRaises(ValueError):self.run_review()
        self.assertEqual((self.p/'out/sentinel').read_text(),'keep')

    def test_only_candidates_and_deferred_from_triage_report(self):
        s=self.write('selection.json',{'objects':[{'id':'b1','status':'review_candidate'},
                                                {'id':'b2','status':'no_geometry_trigger'},
                                                {'id':'b3','status':'geometry_deferred'}]})
        r=self.run_review(s)
        self.assertEqual(json.loads(Path(r['index']).read_text())['count'],2)


if __name__=='__main__':unittest.main()
