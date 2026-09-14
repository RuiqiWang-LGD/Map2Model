import pathlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'export_dwg.ps1'
SHELL = shutil.which('pwsh') or shutil.which('powershell')


@unittest.skipUnless(SHELL, 'PowerShell is optional on this platform')
class DwgGuardTests(unittest.TestCase):
    def test_existing_output_never_overwritten(self):
        self.assertTrue(SCRIPT.exists(), 'DWG exporter not implemented')
        with tempfile.TemporaryDirectory() as d:
            base = pathlib.Path(d)
            source = base / 'input.dxf'
            source.write_text('source')
            dest = base / 'result.dwg'
            dest.write_bytes(b'keep-this-dwg')
            result = subprocess.run([SHELL, '-NoProfile', '-File', str(SCRIPT),
                '-InputDxf', str(source), '-OutputDwg', str(dest),
                '-RoundTripDxf', str(base/'rt.dxf'), '-CoreConsolePath', str(base/'missing.exe')],
                capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(dest.read_bytes(), b'keep-this-dwg')
            self.assertIn(b'Output already exists', result.stderr)

    def test_input_is_protected_even_with_overwrite(self):
        self.assertTrue(SCRIPT.exists(), 'DWG exporter not implemented')
        with tempfile.TemporaryDirectory() as d:
            base = pathlib.Path(d)
            source = base / 'input.dxf'
            source.write_text('source')
            result = subprocess.run([SHELL, '-NoProfile', '-File', str(SCRIPT),
                '-InputDxf', str(source), '-OutputDwg', str(base/'out.dwg'),
                '-RoundTripDxf', str(source), '-CoreConsolePath', str(base/'missing.exe'), '-Overwrite'],
                capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(source.read_text(), 'source')
            self.assertIn(b'Paths must be distinct', result.stderr)


if __name__ == '__main__':
    unittest.main()
