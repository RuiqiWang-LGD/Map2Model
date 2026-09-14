import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import ezdxf
from PIL import Image
from shapely.geometry import shape

ROOT = Path(__file__).resolve().parents[1]


class RasterToolsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_tool(self, name, *args, ok=True):
        result = subprocess.run([sys.executable, str(ROOT / 'scripts' / name), *map(str, args)], capture_output=True, text=True)
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
        return result

    def test_mask_hole_frame_origin_and_units(self):
        im = Image.new('RGB', (10, 8), '#EEEEEE')
        im.putpixel((4, 3), (255, 255, 255))
        im.save(self.p / 'mask.png')
        for unit, scale in [('unknown', 1), ('m', 2), ('mm', 0.5)]:
            prefix = self.p / unit
            args = ['--units', unit]
            if unit != 'unknown':
                args += ['--units-per-pixel', str(scale)]
            self.run_tool('vectorize_plan.py', '--input', self.p / 'mask.png', '--output-prefix', prefix, *args)
            features = json.loads(prefix.with_suffix('.geojson').read_text())['features']
            poly = shape(features[0]['geometry'])
            self.assertEqual(len(poly.interiors), 1)
            self.assertEqual(poly.bounds, (0, 0, 10 * scale, 8 * scale))
            self.assertAlmostEqual(poly.area, 79 * scale * scale)
            doc = ezdxf.readfile(prefix.with_suffix('.dxf'))
            self.assertEqual(doc.header['$INSUNITS'], {'unknown': 0, 'm': 6, 'mm': 4}[unit])
            frames = list(doc.modelspace().query('LWPOLYLINE[layer=="FRAME"]'))
            self.assertEqual(len(frames), 1)
            self.assertTrue(frames[0].closed)
            self.assertEqual(set((float(x),float(y)) for x,y in frames[0].get_points('xy')), {(0,0),(10*scale,0),(10*scale,8*scale),(0,8*scale)})
            meta = json.loads(prefix.with_suffix('.metadata.json').read_text())
            self.assertEqual(meta['image_to_world'], [[scale, 0, 0], [0, -scale, 8 * scale], [0, 0, 1]])
            self.assertEqual(meta['status'], 'draft')
        self.run_tool('vectorize_plan.py', '--input', self.p/'mask.png', '--output-prefix', self.p/'bad', '--units', 'm', ok=False)
        self.run_tool('vectorize_plan.py', '--input', self.p/'mask.png', '--output-prefix', self.p/'unknown', ok=False)

    def test_overlay_curves_and_transform_validation(self):
        doc = ezdxf.new()
        ms = doc.modelspace()
        ms.add_circle((10, 10), 5)
        ms.add_arc((20, 20), 4, 0, 180)
        ms.add_line((1, 1), (3, 3))
        ms.add_lwpolyline([(2, 10, 1), (8, 10, 0)], format='xyb')
        ms.add_polyline2d([(1, 20), (10, 25)])
        doc.saveas(self.p/'curves.dxf')
        cfg = self.p/'transform.json'
        cfg.write_text(json.dumps({'world_to_image': [[1,0,0],[0,-1,32],[0,0,1]]}))
        out = self.p/'overlay.jpg'
        self.run_tool('render_overlay.py', '--dxf', self.p/'curves.dxf', '--transform', cfg, '--width', 40, '--height', 32, '--output', out)
        im = Image.open(out)
        self.assertEqual(im.size, (40,32))
        self.assertLess(sum(im.getpixel((15,22))), 650)
        self.assertLess(min(sum(im.getpixel((x,y))) for x in range(4,7) for y in range(24,27)), 650)  # Bulge midpoint neighborhood, away from its straight chord.
        im.close()
        meta = json.loads(out.with_suffix('.metadata.json').read_text())
        self.assertEqual(meta['rendered_entities'], 5)
        self.assertEqual(meta['skipped_entities'], [])
        for matrix in [[[1,0,0],[0,0,0],[0,0,1]], [[1,0,0],[0,1,0],[1,0,1]], [[1,0,0],[0,float('nan'),0],[0,0,1]]]:
            cfg.write_text(json.dumps({'world_to_image': matrix}))
            self.run_tool('render_overlay.py', '--dxf', self.p/'curves.dxf', '--transform', cfg, '--width', 40, '--height', 32, '--output', self.p/'bad.jpg', ok=False)

    def test_unsupported_entity_fails(self):
        doc = ezdxf.new()
        doc.modelspace().add_text('unsupported')
        doc.saveas(self.p/'text.dxf')
        cfg = self.p/'transform.json'
        cfg.write_text(json.dumps({'world_to_image': [[1,0,0],[0,1,0],[0,0,1]]}))
        result = self.run_tool('render_overlay.py', '--dxf', self.p/'text.dxf', '--transform', cfg, '--width', 20, '--height', 20, '--output', self.p/'text.jpg', ok=False)
        self.assertIn('TEXT', result.stderr)

    def test_custom_palette_small_objects_and_source_dimensions(self):
        im = Image.new('RGB', (20, 15), 'white')
        im.putpixel((0, 0), (255, 0, 0))
        im.putpixel((19, 14), (255, 0, 0))
        im.save(self.p/'mask.png')
        palette = self.p/'palette.json'
        palette.write_text(json.dumps({'building':'#FF0000','background':'#FFFFFF'}))
        self.run_tool('vectorize_plan.py', '--input', self.p/'mask.png', '--output-prefix', self.p/'draft', '--palette', palette)
        features = json.loads((self.p/'draft.geojson').read_text())['features']
        self.assertEqual(len(features), 2)
        self.assertEqual(sum(shape(f['geometry']).area for f in features), 2)
        self.run_tool('render_overlay.py', '--dxf', self.p/'draft.dxf', '--transform', self.p/'draft.metadata.json', '--source', self.p/'mask.png', '--source-kind', 'analysis', '--output', self.p/'overlay.jpg')
        with Image.open(self.p/'overlay.jpg') as result_image:
            self.assertEqual(result_image.size, (20,15))
        self.run_tool('render_overlay.py', '--dxf', self.p/'draft.dxf', '--transform', self.p/'draft.metadata.json', '--source', self.p/'mask.png', '--width', 99, '--output', self.p/'wrong.jpg', ok=False)
        self.run_tool('vectorize_plan.py', '--input', self.p/'mask.png', '--output-prefix', self.p/'unmatched', ok=False)

    def test_transform_dimensions_must_match_source(self):
        doc = ezdxf.new()
        doc.modelspace().add_line((0,0),(1,1))
        doc.saveas(self.p/'line.dxf')
        Image.new('RGB',(40,30),'white').save(self.p/'source.png')
        (self.p/'transform.json').write_text(json.dumps({'width':20,'height':15,'world_to_image':[[1,0,0],[0,1,0],[0,0,1]]}))
        result = self.run_tool('render_overlay.py', '--dxf', self.p/'line.dxf', '--transform', self.p/'transform.json', '--source', self.p/'source.png', '--output', self.p/'bad.jpg', ok=False)
        self.assertIn('transform dimensions', result.stderr)

    def test_empty_mask_rejected(self):
        Image.new('RGB',(10,10),'white').save(self.p/'empty.png')
        result = self.run_tool('vectorize_plan.py', '--input', self.p/'empty.png', '--output-prefix', self.p/'empty', ok=False)
        self.assertIn('no candidate', result.stderr)

    def test_empty_dxf_rejected(self):
        ezdxf.new().saveas(self.p/'empty.dxf')
        (self.p/'transform.json').write_text(json.dumps({'world_to_image':[[1,0,0],[0,1,0],[0,0,1]]}))
        result = self.run_tool('render_overlay.py', '--dxf', self.p/'empty.dxf', '--transform', self.p/'transform.json', '--width', 10, '--height', 10, '--output', self.p/'bad.jpg', ok=False)
        self.assertIn('no modelspace', result.stderr)

    def test_nearest_palette_tolerance_is_explicit_and_recorded(self):
        Image.new('RGB',(3,3),(235,238,238)).save(self.p/'soft.png')
        self.run_tool('vectorize_plan.py', '--input', self.p/'soft.png', '--output-prefix', self.p/'strict', ok=False)
        self.run_tool('vectorize_plan.py', '--input', self.p/'soft.png', '--output-prefix', self.p/'soft', '--color-tolerance', 3)
        meta = json.loads((self.p/'soft.metadata.json').read_text())
        self.assertEqual(meta['color_tolerance'], 3)
        self.assertEqual(meta['approximate_match_pixels'], 9)
        self.assertEqual(meta['class_pixel_counts']['building'], 9)
        self.run_tool('vectorize_plan.py', '--input', self.p/'soft.png', '--output-prefix', self.p/'too_far', '--color-tolerance', 2, ok=False)


if __name__ == '__main__':
    unittest.main()
