import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]

class UrbanReviewPromptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.source = self.work / "source.png"
        Image.new("RGB", (64, 96), "white").save(self.source)

    def build(self, **kw):
        data = dict(schema_version=1, source_image="source.png", image_type="satellite")
        data.update(kw)
        cfg = self.work / "input.json"
        cfg.write_text(json.dumps(data), encoding="utf-8")
        out = self.work / "out.txt"
        result = subprocess.run([sys.executable, "-X", "utf8", str(ROOT/"scripts/build_prompt.py"),
                                 "--input", str(cfg), "--output", str(out)],
                                capture_output=True, text=True, encoding="utf-8")
        return result, out

    def test_structure_uses_dedicated_template_without_building_work(self):
        result, out = self.build(review_stage="urban_structure")
        self.assertEqual(result.returncode, 0, result.stderr)
        meta = json.loads(out.with_suffix(".meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["review_stage"], "urban_structure")
        self.assertEqual(Path(meta["template_path"]).name, "urban-structure.txt")
        self.assertIn("块面内部统一中性白色", out.read_text(encoding="utf-8"))

    def test_detail_needs_actual_baseline_and_approval(self):
        result, out = self.build(review_stage="urban_detail")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("base_plan_image", result.stderr)
        self.assertFalse(out.exists())

    def test_approval_must_match_baseline_bytes(self):
        result, out = self.build(review_stage="urban_detail", base_plan_image="source.png",
                                 structure_approval={"sha256": "0"*64, "evidence": "User approved stage 1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("sha256", result.stderr)
        self.assertFalse(out.exists())

    def test_detail_carries_verified_baseline_and_constraints(self):
        digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        result, out = self.build(review_stage="urban_detail", base_plan_image="source.png",
                                 structure_approval={"sha256": digest, "evidence": "User approved stage 1"})
        self.assertEqual(result.returncode, 0, result.stderr)
        meta = json.loads(out.with_suffix(".meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["structure_approval"]["sha256"], digest)
        self.assertIn("不得改动已通过", out.read_text(encoding="utf-8"))

    def test_structure_rejects_detail_modules(self):
        result, out = self.build(review_stage="urban_structure", modules=["dense_buildings"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("modules", result.stderr)
        self.assertFalse(out.exists())

    def test_stage_cannot_be_confused_with_design_clear(self):
        result, out = self.build(review_stage="urban_structure", output_variant="design_clear",
                                 design_scope="centre", base_plan_image="source.png")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("review_stage", result.stderr)
        self.assertFalse(out.exists())

if __name__ == "__main__":
    unittest.main()
