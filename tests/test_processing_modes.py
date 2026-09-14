import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


class ProcessingModesTests(unittest.TestCase):
    def run_prompt(self, **changes):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        work = Path(tmp.name)
        Image.new('RGB', (32, 24), 'white').save(work / 'source.png')
        cfg = dict(schema_version=1, source_image='source.png', image_type='satellite')
        cfg.update(changes)
        (work / 'project.json').write_text(json.dumps(cfg), encoding='utf-8')
        out = work / 'prompt.txt'
        run = subprocess.run([sys.executable, '-X', 'utf8', str(ROOT / 'scripts/build_prompt.py'),
                              '--input', str(work / 'project.json'), '--output', str(out)],
                             capture_output=True, text=True, encoding='utf-8')
        return run, out

    def test_district_selects_its_own_complete_template_and_tracks_mode(self):
        run, out = self.run_prompt(processing_mode='district_structure', mode_reason='城区广、单体难辨')
        self.assertEqual(run.returncode, 0, run.stderr)
        meta = json.loads(out.with_suffix('.meta.json').read_text(encoding='utf-8'))
        self.assertEqual(meta['processing_mode'], 'district_structure')
        self.assertEqual(meta['review_stage'], 'complete')
        template = ROOT / 'prompts/district-structure.txt'
        self.assertEqual(Path(meta['template_path']), template)
        self.assertEqual(meta['template_sha256'], hashlib.sha256(template.read_bytes()).hexdigest())
        self.assertIn('片区结构模式', out.read_text(encoding='utf-8'))

    def test_detail_keeps_existing_detail_template(self):
        run, out = self.run_prompt(processing_mode='site_detail')
        self.assertEqual(run.returncode, 0, run.stderr)
        meta = json.loads(out.with_suffix('.meta.json').read_text(encoding='utf-8'))
        self.assertEqual(meta['processing_mode'], 'site_detail')
        self.assertEqual(Path(meta['template_path']), ROOT / 'prompts/simplify-map.txt')

    def test_detail_prompt_keeps_low_clarity_structure_and_road_gap_contract(self):
        run, out = self.run_prompt(processing_mode='site_detail')
        self.assertEqual(run.returncode, 0, run.stderr)
        prompt = out.read_text(encoding='utf-8')
        self.assertIn('【低清晰度通用重构与道路退让】', prompt)
        self.assertIn('间隔放不下就减少或简化细节', prompt)

    def test_legacy_config_remains_usable_without_falsely_claiming_a_mode(self):
        run, out = self.run_prompt()
        self.assertEqual(run.returncode, 0, run.stderr)
        meta = json.loads(out.with_suffix('.meta.json').read_text(encoding='utf-8'))
        self.assertIsNone(meta['processing_mode'])
        self.assertEqual(Path(meta['template_path']), ROOT / 'prompts/simplify-map.txt')

    def test_invalid_mode_fails_without_outputs(self):
        run, out = self.run_prompt(processing_mode='auto')
        self.assertEqual(run.returncode, 2)
        self.assertFalse(out.exists())

    def test_local_detail_is_explicit_and_does_not_create_design_clear(self):
        run, out = self.run_prompt(processing_mode='district_structure', detail_scope='仅东北公园')
        self.assertEqual(run.returncode, 0, run.stderr)
        meta = json.loads(out.with_suffix('.meta.json').read_text(encoding='utf-8'))
        self.assertEqual(meta['detail_scope'], '仅东北公园')
        self.assertEqual(meta['output_variant'], 'existing_condition')
        self.assertIn('仅东北公园', out.read_text(encoding='utf-8'))

    def test_district_clear_still_requires_design_scope_and_base(self):
        run, out = self.run_prompt(processing_mode='district_structure', output_variant='design_clear')
        self.assertEqual(run.returncode, 2)
        self.assertIn('design_scope', run.stderr)
        self.assertFalse(out.exists())

    def test_district_clear_preserves_mode_scope_and_baseline(self):
        run, out = self.run_prompt(processing_mode='district_structure',
                                   output_variant='design_clear', design_scope='南部标注区',
                                   base_plan_image='source.png', detail_scope='东北公园')
        self.assertEqual(run.returncode, 0, run.stderr)
        meta = json.loads(out.with_suffix('.meta.json').read_text(encoding='utf-8'))
        self.assertEqual(meta['output_variant'], 'design_clear')
        self.assertEqual(meta['processing_mode'], 'district_structure')
        self.assertIn('base_plan_image', meta['reference_images'])
        prompt = out.read_text(encoding='utf-8')
        self.assertIn('范围内仅保留道路', prompt)
        self.assertIn('范围外及标注中未覆盖的孔洞区域冻结', prompt)

    def test_explicit_review_stage_remains_separate_from_district_mode(self):
        run, out = self.run_prompt(processing_mode='district_structure', review_stage='urban_structure')
        self.assertEqual(run.returncode, 0, run.stderr)
        meta = json.loads(out.with_suffix('.meta.json').read_text(encoding='utf-8'))
        self.assertEqual(meta['processing_mode'], 'district_structure')
        self.assertEqual(meta['review_stage'], 'urban_structure')
        self.assertEqual(Path(meta['template_path']), ROOT / 'prompts/urban-structure.txt')

    def test_mode_details_cannot_silently_attach_to_legacy(self):
        run, out = self.run_prompt(detail_scope='东北')
        self.assertEqual(run.returncode, 2)
        self.assertFalse(out.exists())


if __name__ == '__main__':
    unittest.main()
