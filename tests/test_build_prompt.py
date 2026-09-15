import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_prompt.py"
TEMPLATE = ROOT / "prompts" / "simplify-map.txt"
MODULES = ROOT / "prompts" / "scene-modules.json"
START_MARKER = "【输入与可选项目条件】"
END_MARKER = "【一、按输入类型处理】"

SPEC = importlib.util.spec_from_file_location("map2model_build_prompt", SCRIPT)
BUILD_PROMPT = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(BUILD_PROMPT)


class BuildPromptCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.source = self.work / "source.png"
        Image.new("RGB", (40, 30), "white").save(self.source)

    def write_config(self, **changes):
        config = {"schema_version": 1, "source_image": self.source.name, "image_type": "orthophoto"}
        config.update(changes)
        path = self.work / "prompt-project.json"
        path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        return path

    def write_raw_config(self, config, name="prompt-project.json"):
        path = self.work / name
        path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        return path

    def run_cli(self, *args, ok=True):
        result = subprocess.run(
            [sys.executable, "-X", "utf8", str(SCRIPT), *map(str, args)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
        return result

    @staticmethod
    def condition_block(prompt):
        return prompt.split(START_MARKER, 1)[1].split(END_MARKER, 1)[0]

    def test_relative_defaults_preserve_template_and_write_traceable_metadata(self):
        config = self.write_config()
        output = self.work / "simplify-map.project.txt"
        self.run_cli("--input", config, "--output", output)

        prompt = output.read_text(encoding="utf-8")
        template = TEMPLATE.read_text(encoding="utf-8")
        self.assertEqual(prompt.split(START_MARKER, 1)[0], template.split(START_MARKER, 1)[0])
        self.assertEqual(prompt.split(END_MARKER, 1)[1], template.split(END_MARKER, 1)[1])
        inserted = self.condition_block(prompt)
        self.assertIn("正射图", inserted)
        self.assertIn("相对比例", inserted)
        self.assertIn("40 × 30 px", inserted)
        self.assertNotRegex(inserted, r"(?<!\d)20\s*(?:m|米)(?![A-Za-z])")

        metadata = json.loads(output.with_suffix(".meta.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["source"]["width"], 40)
        self.assertEqual(metadata["source"]["height"], 30)
        self.assertEqual(metadata["source"]["sha256"], hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertEqual(metadata["prompt_sha256"], hashlib.sha256(output.read_bytes()).hexdigest())
        self.assertEqual(metadata["template_sha256"], hashlib.sha256(TEMPLATE.read_bytes()).hexdigest())
        self.assertEqual(metadata["modules_sha256"], hashlib.sha256(MODULES.read_bytes()).hexdigest())
        self.assertEqual(metadata["selected_modules"], [])
        self.assertIs(metadata["prompt_ready"], True)
        self.assertEqual(metadata["generation_status"], "not_started")
        self.assertGreaterEqual(metadata["elapsed_seconds"], 0)

    def test_existing_variant_is_explicit_even_with_design_scope(self):
        config = self.write_config(design_scope="blue courtyard")
        output = self.work / "existing.txt"
        self.run_cli("--input", config, "--output", output)
        metadata = json.loads(output.with_suffix(".meta.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["output_variant"], "existing_condition")
        self.assertIn("现状整理版", output.read_text(encoding="utf-8"))

    def test_clear_variant_tracks_original_scope_and_base_independently(self):
        scope = self.work / "scope.png"
        base = self.work / "base.png"
        Image.new("RGB", (40, 30), "blue").save(scope)
        Image.new("RGB", (80, 60), "gray").save(base)
        config = self.write_config(output_variant="design_clear", design_scope="blue courtyard with unmarked hole",
                                   design_scope_image=scope.name, base_plan_image=base.name)
        output = self.work / "clear.txt"
        self.run_cli("--input", config, "--output", output)
        metadata = json.loads(output.with_suffix(".meta.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["output_variant"], "design_clear")
        self.assertEqual(metadata["source"]["sha256"], hashlib.sha256(self.source.read_bytes()).hexdigest())
        for role, path in (("design_scope_image", scope), ("base_plan_image", base)):
            self.assertEqual(metadata["reference_images"][role]["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(metadata["reference_images"]["base_plan_image"]["width"], 80)
        self.assertIn("范围内仅保留道路", output.read_text(encoding="utf-8"))

    def test_clear_variant_missing_inputs_fail_without_partial_outputs(self):
        cases = [
            ({"output_variant": "design_clear"}, "design_scope"),
            ({"output_variant": "design_clear", "design_scope": "blue"}, "base_plan_image"),
            ({"output_variant": "unknown"}, "output_variant"),
            ({"design_scope_image": "missing.png"}, "design_scope_image"),
        ]
        for index, (changes, field) in enumerate(cases):
            with self.subTest(field=field):
                config = self.write_config(**changes)
                output = self.work / f"invalid-variant-{index}.txt"
                result = self.run_cli("--input", config, "--output", output, ok=False)
                self.assertIn(field, result.stderr)
                self.assertFalse(output.exists())
                self.assertFalse(output.with_suffix(".meta.json").exists())

    def test_reference_images_are_protected_from_output_overwrite(self):
        base = self.work / "base.png"
        Image.new("RGB", (40, 30), "gray").save(base)
        before = base.read_bytes()
        config = self.write_config(output_variant="design_clear", design_scope="blue", base_plan_image=base.name)
        result = self.run_cli("--input", config, "--output", base, ok=False)
        self.assertIn("输出路径", result.stderr)
        self.assertEqual(base.read_bytes(), before)

    def test_grid_scale_reports_image_dimensions_in_real_units(self):
        config = self.write_config(scale={"status": "grid", "grid": {
            "units_per_pixel": 0.5, "unit": "m", "source": "orthophoto export", "crs": "EPSG:3857"}})
        output = self.work / "grid-prompt.txt"
        self.run_cli("--input", config, "--output", output)

        inserted = self.condition_block(output.read_text(encoding="utf-8"))
        self.assertRegex(inserted, r"20(?:\.0)?\s*m.{0,20}15(?:\.0)?\s*m")
        self.assertIn("0.5 m/px", inserted)
        self.assertIn("EPSG:3857", inserted)
        self.assertIn("orthophoto export", inserted)

    def test_reference_scale_preserves_measurement_without_inventing_grid(self):
        config = self.write_config(scale={"status": "reference", "reference": {
            "object": "east road", "location": "between bridge and junction", "value": 12.5,
            "unit": "m", "meaning": "estimated carriageway width", "reliability": "estimated",
            "source": "client markup"}})
        output = self.work / "reference-prompt.txt"
        self.run_cli("--input", config, "--output", output)

        inserted = self.condition_block(output.read_text(encoding="utf-8"))
        for value in ("east road", "between bridge and junction", "12.5 m",
                      "estimated carriageway width", "estimated", "client markup"):
            self.assertIn(value, inserted)
        self.assertNotRegex(inserted, r"\d+(?:\.\d+)?\s*(?:m|mm)\s*(?:/|每)\s*(?:px|pixel|像素)")

    def test_conditions_and_only_selected_deduplicated_modules_are_inserted(self):
        modules = json.loads(MODULES.read_text(encoding="utf-8"))
        config = self.write_config(
            image_type="oblique", design_scope="north courtyard", output_scope="full source frame",
            detail_level="design scope detailed; context simplified", keep=["old bridge", "large trees"],
            omit=["parked cars"], image_roles=["source.png is source", "mark.png is markup"],
            notes=["entry is behind the canopy"], inferences=["continue the visible east lane"],
            modules=["waterfront", "oblique_buildings", "waterfront"])
        output = self.work / "conditions-prompt.txt"
        self.run_cli("--input", config, "--output", output)

        inserted = self.condition_block(output.read_text(encoding="utf-8"))
        for value in ("倾斜航拍图", "north courtyard", "full source frame",
                      "design scope detailed; context simplified", "old bridge", "large trees",
                      "parked cars", "source.png is source", "mark.png is markup",
                      "entry is behind the canopy", "continue the visible east lane",
                      modules["waterfront"]["reminder"], modules["oblique_buildings"]["reminder"]):
            self.assertIn(value, inserted)
        for module_id in ("dense_buildings", "farmland", "site_detail"):
            self.assertNotIn(modules[module_id]["reminder"], inserted)
        metadata = json.loads(output.with_suffix(".meta.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["selected_modules"], ["waterfront", "oblique_buildings"])

    def test_list_modules_does_not_require_project_input(self):
        result = self.run_cli("--list-modules")
        for module_id in json.loads(MODULES.read_text(encoding="utf-8")):
            self.assertIn(module_id, result.stdout)

    def test_invalid_configs_fail_without_outputs(self):
        cases = [
            ({"schema_version": 1, "source_image": self.source.name, "image_type": "orthophoto", "typo": 1}, "未知字段"),
            ({"schema_version": 1, "source_image": self.source.name, "image_type": "orthophoto", "modules": ["not_real"]}, "not_real"),
            ({"schema_version": 1, "source_image": self.source.name, "image_type": "orthophoto",
              "scale": {"status": "grid", "grid": {"units_per_pixel": 0, "unit": "m", "source": "world file"}}},
             "units_per_pixel"),
        ]
        for index, (payload, expected) in enumerate(cases):
            with self.subTest(index=index):
                config = self.write_raw_config(payload, f"bad-{index}.json")
                output = self.work / f"bad-{index}.txt"
                result = self.run_cli("--input", config, "--output", output, ok=False)
                self.assertIn(expected, result.stderr)
                self.assertFalse(output.exists())
                self.assertFalse(output.with_suffix(".meta.json").exists())

    def test_missing_source_fails_cleanly(self):
        config = self.write_raw_config({"schema_version": 1, "source_image": "missing.png", "image_type": "satellite"})
        output = self.work / "missing.txt"
        result = self.run_cli("--input", config, "--output", output, ok=False)
        self.assertIn("源图不存在", result.stderr)
        self.assertFalse(output.exists())

    def test_broken_template_markers_are_rejected(self):
        with self.assertRaisesRegex(BUILD_PROMPT.PromptConfigError, "模板标记"):
            BUILD_PROMPT.replace_conditions("no markers", "conditions")

    def test_refuses_to_overwrite_source_input(self):
        config = self.write_config()
        original = self.source.read_bytes()

        result = self.run_cli("--input", config, "--output", self.source, ok=False)

        self.assertIn("输出路径", result.stderr)
        self.assertEqual(self.source.read_bytes(), original)

    def test_output_temp_name_cannot_overwrite_source(self):
        source = self.work / "prompt.txt.tmp"
        Image.new("RGB", (40, 30), "white").save(source, format="PNG")
        original = source.read_bytes()
        config = self.write_config(source_image=source.name)
        output = self.work / "prompt.txt"

        self.run_cli("--input", config, "--output", output)

        self.assertEqual(source.read_bytes(), original)
        self.assertTrue(output.is_file())
        self.assertTrue(output.with_suffix(".meta.json").is_file())

    def test_existing_prompt_and_metadata_are_refused(self):
        config = self.write_config()
        output = self.work / "existing.txt"
        metadata = output.with_suffix(".meta.json")
        output.write_text("old prompt", encoding="utf-8")
        metadata.write_text("old metadata", encoding="utf-8")

        result = self.run_cli("--input", config, "--output", output, ok=False)

        self.assertIn("已存在", result.stderr)
        self.assertEqual(output.read_text(encoding="utf-8"), "old prompt")
        self.assertEqual(metadata.read_text(encoding="utf-8"), "old metadata")

    def test_metadata_target_conflict_leaves_no_partial_prompt(self):
        config = self.write_config()
        output = self.work / "partial.txt"
        output.with_suffix(".meta.json").mkdir()

        result = self.run_cli("--input", config, "--output", output, ok=False)

        self.assertIn("已存在", result.stderr)
        self.assertFalse(output.exists())

    def test_inactive_scale_branches_are_rejected(self):
        cases = [
            {"status": "relative", "grid": {"units_per_pixel": 1, "unit": "m", "source": "world file"}},
            {"status": "grid", "grid": {"units_per_pixel": 1, "unit": "m", "source": "world file"},
             "reference": {"object": "road", "location": "marked ends", "value": 8, "unit": "m",
                           "meaning": "width", "reliability": "provided", "source": "markup"}},
        ]
        for index, scale in enumerate(cases):
            with self.subTest(index=index):
                config = self.write_raw_config(
                    {"schema_version": 1, "source_image": self.source.name,
                     "image_type": "orthophoto", "scale": scale},
                    f"exclusive-scale-{index}.json",
                )
                output = self.work / f"exclusive-scale-{index}.txt"
                result = self.run_cli("--input", config, "--output", output, ok=False)
                self.assertIn("只能包含", result.stderr)
                self.assertFalse(output.exists())

    def test_reserved_template_markers_are_rejected_in_project_text(self):
        for index, marker in enumerate((START_MARKER, END_MARKER)):
            with self.subTest(marker=marker):
                config = self.write_raw_config(
                    {"schema_version": 1, "source_image": self.source.name,
                     "image_type": "orthophoto", "notes": [marker]},
                    f"marker-{index}.json",
                )
                output = self.work / f"marker-{index}.txt"
                result = self.run_cli("--input", config, "--output", output, ok=False)
                self.assertIn("保留标记", result.stderr)
                self.assertFalse(output.exists())

    def test_cli_cannot_replace_fixed_template_or_scene_modules(self):
        config = self.write_config()
        output = self.work / "fixed.txt"
        for option, path in (("--template", TEMPLATE), ("--modules-file", MODULES)):
            with self.subTest(option=option):
                result = self.run_cli("--input", config, "--output", output, option, path, ok=False)
                self.assertIn("unrecognized arguments", result.stderr)
                self.assertFalse(output.exists())

    def test_copyable_example_keeps_unknown_facts_unknown(self):
        example = json.loads((ROOT / "examples" / "prompt-project.json").read_text(encoding="utf-8"))
        Image.new("RGB", (40, 30), "white").save(self.work / "source.jpg")
        config = self.write_raw_config(example, "prompt-project.json")
        output = self.work / "example.txt"

        self.run_cli("--input", config, "--output", output)

        inserted = self.condition_block(output.read_text(encoding="utf-8"))
        self.assertIn("相对比例", inserted)
        self.assertIn("设计范围：未指定", inserted)
        self.assertNotRegex(inserted, r"(?<!\d)20\s*(?:m|米)(?![A-Za-z])")
        self.assertNotIn("用户提供", inserted)


if __name__ == "__main__":
    unittest.main()
