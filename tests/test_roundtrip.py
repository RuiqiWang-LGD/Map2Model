import importlib.util
import pathlib
import tempfile
import unittest

import ezdxf

ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE = ROOT / 'scripts' / 'compare_dxf.py'


class RoundTripTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(MODULE.exists(), 'roundtrip comparator implementation missing')
        spec = importlib.util.spec_from_file_location('roundtrip', MODULE)
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.doc = ezdxf.new('R2018')
        self.doc.units = 6
        self.doc.modelspace().add_lwpolyline(
            [(0, 0, 0), (10, 0, 1), (10, 10, 0), (0, 10, 0)],
            format='xyb', close=True, dxfattribs={'layer': 'BUILDING'})
        self.a = self.root / 'a.dxf'
        self.b = self.root / 'b.dxf'
        self.doc.saveas(self.a)

    def compare(self):
        self.doc.saveas(self.b)
        return self.mod.compare(self.a, self.b, 1e-6)

    def test_same_geometry_passes(self):
        self.assertTrue(self.compare()['geometry_equal'])

    def test_bulge_change_fails(self):
        e = list(self.doc.modelspace())[0]
        points = list(e.get_points('xyb'))
        points[1] = (10, 0, 0)
        e.set_points(points, format='xyb')
        self.assertFalse(self.compare()['geometry_equal'])

    def test_units_change_fails(self):
        self.doc.units = 4
        self.assertFalse(self.compare()['geometry_equal'])

    def test_missing_object_fails(self):
        self.doc.modelspace().delete_entity(list(self.doc.modelspace())[0])
        self.assertFalse(self.compare()['geometry_equal'])

    def test_coordinate_change_fails(self):
        list(self.doc.modelspace())[0].translate(0.1, 0, 0)
        self.assertFalse(self.compare()['geometry_equal'])

    def test_unsupported_is_not_pass(self):
        self.doc.modelspace().add_text('unsupported')
        self.doc.saveas(self.a)
        self.assertFalse(self.compare()['geometry_equal'])

    def test_negative_tolerance_rejected(self):
        self.doc.saveas(self.b)
        with self.assertRaises(ValueError):
            self.mod.compare(self.a, self.b, -1)


if __name__ == '__main__':
    unittest.main()
