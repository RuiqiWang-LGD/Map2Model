#!/usr/bin/env python3
"""Build a complete Map2CAD image-stage prompt from validated project facts."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TEMPLATE = ROOT / "prompts" / "simplify-map.txt"
STRUCTURE_TEMPLATE = ROOT / "prompts" / "urban-structure.txt"
DISTRICT_TEMPLATE = ROOT / "prompts" / "district-structure.txt"
PROCESSING_MODES = {"district_structure": "片区结构模式", "site_detail": "场地详图模式"}
DEFAULT_MODULES = ROOT / "prompts" / "scene-modules.json"
START_MARKER = "【输入与可选项目条件】"
END_MARKER = "【一、按输入类型处理】"
RESERVED_MARKERS = (START_MARKER, END_MARKER)

IMAGE_TYPES = {
    "orthophoto": "正射图",
    "satellite": "卫星图",
    "oblique": "倾斜航拍图",
    "simplified_plan": "已有简化二维图",
}
ALLOWED_FIELDS = {
    "processing_mode",
    "mode_reason",
    "detail_scope",
    "review_stage",
    "structure_approval",
    "schema_version",
    "source_image",
    "image_type",
    "design_scope",
    "output_variant",
    "design_scope_image",
    "base_plan_image",
    "output_scope",
    "detail_level",
    "keep",
    "omit",
    "image_roles",
    "notes",
    "inferences",
    "modules",
    "scale",
}


class PromptConfigError(ValueError):
    """Raised when project facts cannot safely produce a prompt."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_json_bytes(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise PromptConfigError(f"{label}不存在：{path}") from exc
    except OSError as exc:
        raise PromptConfigError(f"无法读取{label}：{path}（{exc}）") from exc
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PromptConfigError(f"{label}不是有效 UTF-8 JSON：{path}（{exc}）") from exc
    if not isinstance(value, dict):
        raise PromptConfigError(f"{label}顶层必须是 JSON 对象：{path}")
    return value, raw


def read_json(path: Path, label: str) -> dict[str, Any]:
    value, _ = read_json_bytes(path, label)
    return value


def require_text(value: Any, field: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.strip():
        raise PromptConfigError(f"{field} 必须是非空字符串")
    return value.strip()


def prompt_text(value: Any, field: str, *, optional: bool = False) -> str | None:
    text = require_text(value, field, optional=optional)
    if text is not None and any(marker in text for marker in RESERVED_MARKERS):
        raise PromptConfigError(f"{field} 不能包含提示词保留标记")
    return text


def text_list(config: dict[str, Any], field: str) -> list[str]:
    value = config.get(field, [])
    if not isinstance(value, list):
        raise PromptConfigError(f"{field} 必须是字符串数组")
    return [prompt_text(item, f"{field}[{index}]") for index, item in enumerate(value)]


def finite_positive(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PromptConfigError(f"{field} 必须是正数")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise PromptConfigError(f"{field} 必须是正数")
    return number


def format_number(value: float) -> str:
    return f"{value:.9f}".rstrip("0").rstrip(".")


def resolve_path(value: str, config_path: Path) -> Path:
    candidate = Path(value).expanduser()
    return candidate.resolve() if candidate.is_absolute() else (config_path.parent / candidate).resolve()


def require_shape(value: dict[str, Any], allowed: set[str], required: set[str], label: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise PromptConfigError(f"{label} 只能包含 {', '.join(sorted(allowed))}；发现：{', '.join(unknown)}")
    missing = sorted(required - set(value))
    if missing:
        raise PromptConfigError(f"{label} 缺少字段：{', '.join(missing)}")


def load_modules(path: Path) -> tuple[dict[str, Any], bytes]:
    modules, raw = read_json_bytes(path, "场景模块文件")
    normalized: dict[str, Any] = {}
    for module_id, value in modules.items():
        if not isinstance(module_id, str) or not module_id.strip():
            raise PromptConfigError("场景模块 ID 必须是非空字符串")
        if not isinstance(value, dict) or set(value) != {"title", "reminder"}:
            raise PromptConfigError(f"场景模块 {module_id} 必须且只能包含 title、reminder")
        normalized[module_id] = {
            "title": prompt_text(value["title"], f"{module_id}.title"),
            "reminder": prompt_text(value["reminder"], f"{module_id}.reminder"),
        }
    return normalized, raw


def validate_config(config: dict[str, Any], config_path: Path, modules: dict[str, Any]) -> dict[str, Any]:
    unknown = sorted(set(config) - ALLOWED_FIELDS)
    if unknown:
        raise PromptConfigError(f"配置含未知字段：{', '.join(unknown)}")
    if config.get("schema_version") != 1:
        raise PromptConfigError("schema_version 必须为 1")

    source_text = require_text(config.get("source_image"), "source_image")
    source = resolve_path(source_text, config_path)
    if not source.is_file():
        raise PromptConfigError(f"源图不存在：{source}")
    image_type = require_text(config.get("image_type"), "image_type")
    if image_type not in IMAGE_TYPES:
        raise PromptConfigError(f"image_type 必须是：{', '.join(IMAGE_TYPES)}")

    result: dict[str, Any] = {
        "source": source,
        "image_type": image_type,
        "design_scope": prompt_text(config.get("design_scope"), "design_scope", optional=True),
        "output_scope": prompt_text(config.get("output_scope"), "output_scope", optional=True),
        "detail_level": prompt_text(config.get("detail_level"), "detail_level", optional=True),
    }
    mode = config.get("processing_mode")
    if "processing_mode" in config and (not isinstance(mode, str) or mode not in PROCESSING_MODES):
        raise PromptConfigError("processing_mode 必须为 district_structure 或 site_detail")
    if mode is None and any(field in config for field in ("mode_reason", "detail_scope")):
        raise PromptConfigError("mode_reason / detail_scope 需要明确 processing_mode")
    result["processing_mode"] = mode
    result["mode_reason"] = prompt_text(config.get("mode_reason"), "mode_reason", optional=True)
    result["detail_scope"] = prompt_text(config.get("detail_scope"), "detail_scope", optional=True)
    for field in ("keep", "omit", "image_roles", "notes", "inferences", "modules"):
        result[field] = text_list(config, field)

    variant = config.get("output_variant", "existing_condition")
    if variant not in ("existing_condition", "design_clear"):
        raise PromptConfigError("output_variant 必须为 existing_condition 或 design_clear")
    result["output_variant"] = variant
    stage = config.get("review_stage", "complete")
    if stage not in ("complete", "urban_structure", "urban_detail"):
        raise PromptConfigError("review_stage 必须为 complete、urban_structure 或 urban_detail")
    result["review_stage"] = stage
    result["structure_approval"] = None
    if stage != "complete" and variant != "existing_condition":
        raise PromptConfigError("review_stage 两步审查先完成现状结构与细化；design_clear 另行生成")
    if stage == "urban_structure" and result["modules"]:
        raise PromptConfigError("urban_structure 使用独立结构模板，modules 须为空以免混入细节要求")
    if stage == "urban_detail" and not config.get("base_plan_image"):
        raise PromptConfigError("urban_detail 需要已通过结构稿 base_plan_image")
    if stage != "urban_detail" and "structure_approval" in config:
        raise PromptConfigError("structure_approval 仅用于 urban_detail")
    if variant == "design_clear":
        if not result["design_scope"]:
            raise PromptConfigError("design_clear 需要明确的 design_scope，不能自行清空全图")
        if not config.get("base_plan_image"):
            raise PromptConfigError("design_clear 需要已整理的 base_plan_image，不能独立重生成布局")
    result["reference_images"] = {}
    for field in ("design_scope_image", "base_plan_image"):
        if field in config:
            path = resolve_path(require_text(config[field], field), config_path)
            try:
                raw = path.read_bytes()
                with Image.open(io.BytesIO(raw)) as reference:
                    width, height = reference.size
                    reference.verify()
            except (OSError, ValueError) as exc:
                raise PromptConfigError(f"{field} 无法读取为图片：{path}（{exc}）") from exc
            result["reference_images"][field] = {
                "path": str(path), "width": width, "height": height, "sha256": sha256_bytes(raw)
            }

    if stage == "urban_detail":
        approval = config.get("structure_approval")
        if not isinstance(approval, dict):
            raise PromptConfigError("urban_detail 需要 structure_approval 用户通过记录")
        require_shape(approval, {"sha256", "evidence"}, {"sha256", "evidence"}, "structure_approval")
        digest = require_text(approval["sha256"], "structure_approval.sha256")
        if digest != result["reference_images"]["base_plan_image"]["sha256"]:
            raise PromptConfigError("structure_approval.sha256 与 base_plan_image 不一致；不得替换已通过基线")
        result["structure_approval"] = {
            "sha256": digest,
            "evidence": prompt_text(approval["evidence"], "structure_approval.evidence"),
        }

    selected_modules: list[str] = []
    for module_id in result["modules"]:
        if module_id not in modules:
            raise PromptConfigError(f"未知场景模块：{module_id}")
        if module_id not in selected_modules:
            selected_modules.append(module_id)
    result["modules"] = selected_modules

    if "scale" not in config:
        scale: Any = {"status": "relative"}
    else:
        scale = config["scale"]
    if not isinstance(scale, dict):
        raise PromptConfigError("scale 必须是 JSON 对象")
    status = scale.get("status")
    if status not in {"relative", "grid", "reference"}:
        raise PromptConfigError("scale.status 必须为 relative、grid 或 reference")

    if status == "relative":
        require_shape(scale, {"status"}, {"status"}, "scale.status=relative 时 scale")
        result["scale"] = {"status": "relative"}
    elif status == "grid":
        require_shape(scale, {"status", "grid"}, {"status", "grid"}, "scale.status=grid 时 scale")
        grid = scale["grid"]
        if not isinstance(grid, dict):
            raise PromptConfigError("scale.grid 必须是 JSON 对象")
        require_shape(
            grid,
            {"units_per_pixel", "unit", "source", "crs"},
            {"units_per_pixel", "unit", "source"},
            "scale.grid",
        )
        result["scale"] = {
            "status": "grid",
            "units_per_pixel": finite_positive(grid["units_per_pixel"], "scale.grid.units_per_pixel"),
            "unit": prompt_text(grid["unit"], "scale.grid.unit"),
            "source": prompt_text(grid["source"], "scale.grid.source"),
            "crs": prompt_text(grid.get("crs"), "scale.grid.crs", optional=True),
        }
    else:
        require_shape(
            scale,
            {"status", "reference"},
            {"status", "reference"},
            "scale.status=reference 时 scale",
        )
        reference = scale["reference"]
        if not isinstance(reference, dict):
            raise PromptConfigError("scale.reference 必须是 JSON 对象")
        fields = {"object", "location", "value", "unit", "meaning", "reliability", "source"}
        require_shape(reference, fields, fields, "scale.reference")
        result["scale"] = {
            "status": "reference",
            "object": prompt_text(reference["object"], "scale.reference.object"),
            "location": prompt_text(reference["location"], "scale.reference.location"),
            "value": finite_positive(reference["value"], "scale.reference.value"),
            "unit": prompt_text(reference["unit"], "scale.reference.unit"),
            "meaning": prompt_text(reference["meaning"], "scale.reference.meaning"),
            "reliability": prompt_text(reference["reliability"], "scale.reference.reliability"),
            "source": prompt_text(reference["source"], "scale.reference.source"),
        }
    return result


def join_or_default(items: list[str], default: str) -> str:
    return "；".join(items) if items else default


def scale_text(scale: dict[str, Any], width: int, height: int) -> str:
    if scale["status"] == "relative":
        return "未提供可靠尺寸；保持源图相对比例，不虚构米制尺寸。"
    if scale["status"] == "grid":
        ratio = scale["units_per_pixel"]
        unit = scale["unit"]
        details = [
            f"{format_number(ratio)} {unit}/px",
            f"整幅约 {format_number(width * ratio)} {unit} × {format_number(height * ratio)} {unit}",
            f"来源：{scale['source']}",
        ]
        if scale.get("crs"):
            details.append(f"坐标参考：{scale['crs']}")
        return "；".join(details) + "。"
    return (
        f"对象：{scale['object']}；位置/两端：{scale['location']}；数值："
        f"{format_number(scale['value'])} {scale['unit']}；测量含义：{scale['meaning']}；"
        f"可靠程度：{scale['reliability']}；来源：{scale['source']}。"
    )


def build_condition_block(data: dict[str, Any], modules: dict[str, Any], width: int, height: int) -> str:
    output_scope = data["output_scope"] or "原图完整范围"
    detail = data["detail_level"] or ("完整块面与一二级道路优先，三级路仅留必要例外，建筑先筛选再规整" if data.get("processing_mode") == "district_structure" else "保留主要空间结构和重要支路，概括微小装饰")
    lines = [
        START_MARKER,
        "以下内容是已经整理的当前项目条件。它们只适用于本次上传图片；未给出的信息保持未知，不自行补造尺寸、范围或对象。",
        f"• 源图类型：{IMAGE_TYPES[data['image_type']]}。",
        f"• 源图像素尺寸：{width} × {height} px（用于文件追溯，不等于真实尺度）。",
        f"• 设计范围：{data['design_scope'] or '未指定；全图按一般前期建模精度整理，不自行指定重点设计区。'}",
        f"• 尺度参照：{scale_text(data['scale'], width, height)}",
        f"• 必须保留：{join_or_default(data['keep'], '未指定；按空间作用和可辨识程度取舍。')}",
        f"• 可以省略：{join_or_default(data['omit'], '未指定；按通用简化规则判断。')}",
        f"• 输出范围和精细程度：{output_scope}；{detail}。",
        f"• 多图用途说明：{join_or_default(data['image_roles'], '只有一张源图，直接以该图为依据。')}",
        f"• 用户或资料已明确的补充条件：{join_or_default(data['notes'], '无。')}",
        f"• 需要保守处理的推定项：{join_or_default(data['inferences'], '无预设推定；只根据可见地面证据和通用规则判断。')}",
    ]
    mode = data.get("processing_mode")
    if mode:
        lines.extend([
            f"• 处理模式：{PROCESSING_MODES[mode]}（{mode}）。模式决定表达深度，与审查阶段及用途版本独立。",
            f"• 模式依据：{data.get('mode_reason') or '未记录；此字段不表示用户已明确确认。'}",
            f"• 用户授权局部详图区：{data.get('detail_scope') or '无；不自行指定。'}",
        ])
        if mode == "district_structure":
            lines.append("• 片区模式最高优先级：完整块面与一二级道路优先；三级道路、园路默认省略，仅保留必要连接或决定主要片区划分的例外。建筑先筛选再规整，只保留有意义的规整大型建筑或规则排列；不重要的小碎、歪扭建筑及干扰边界且不规整的对象可整项舍弃，不为保楼缩路移块。桥头与河岸清除低优先穿线、双边和无用细带。片区是中性背景，不是建筑体量。本取舍高于旧文和场景模块；仅在片区范围生效，场地详图模式和明确局部详图区不套用片区省略策略。用户明确指定的保留对象仍按授权处理，detail_level或模块的泛泛细化要求不自动算作保留授权。清空版范围内仅留道路的规则优先。")
        else:
            lines.append("• 模式表达范围：按源图可辨程度整理建筑基底、内部道路和场地细节；局部模糊不补造，亦不自动降级其余清晰区域。")
    if data["output_variant"] == "design_clear":
        lines.extend([
            "• 本次输出版本：设计范围清空版（design_clear）。编辑已有现状整理版，不重生成布局。",
            "• 本版本优先规则：范围内仅保留道路，其余建筑、树木、绿地、水面、广场、停车分区、铺装及附属物连同轮廓全部清除，统一纯白 #FFFFFF；不补画景观或其他分区。",
            "• 保留条件：已识别道路按原位置、宽度和连续关系保留，真实交通桥面属于道路；普通硬地和停车区不能为保留而改称道路。范围外及标注中未覆盖的孔洞区域冻结沿用现状整理版；跨界对象只清除范围内部分，不改变范围外部分。",
            "• 范围标注只定义清除区域，不代表水体；不扩大、不填实未标注孔洞。全幅、朝向、尺度和道路接边保持不变，清除产生的边界不冒充现状围墙或法定地界。",
            "• 后文及场景模块的现状建筑、水域、绿地、细部保留要求仅适用于范围外；范围内以本版本清空规则为准。项目keep/detail_level中范围内非道路保留描述不适用本版本；若用户另有保留要求，先调整清空范围定义。",
        ])
    elif data.get("review_stage", "complete") != "complete":
        lines.append("• 本次输出版本：现状整理版（existing_condition）的分阶段稿。按当前阶段范围表达现状；如有设计范围，在完整现状细化稿完成后另制清空用途版，不能以结构留白稿代替。无设计范围只做现状。")
    else:
        lines.append("• 本次输出版本：现状整理版（existing_condition）。按源图整理现状；即使已有设计范围，本次仍保留现状内容，另行基于本版制作设计范围清空版。无设计范围时只制作本版。")
    stage = data.get("review_stage", "complete")
    if stage == "urban_structure":
        lines.extend([
            "• 当前审查阶段：第一步 urban_structure，仅主要路网、河道桥梁与围合大块面。",
            "• 这是现状结构审查稿，块面内部中性留白只表示尚未细化，不是设计范围清空版。",
        ])
    elif stage == "urban_detail":
        lines.extend([
            ("• 当前审查阶段：第二步 urban_detail，在已通过结构稿内只加入片区规则筛选后的必要道路和建筑；明确局部详图区按详图处理。" if mode == "district_structure" else "• 当前审查阶段：第二步 urban_detail，在用户已通过的结构稿内部逐块添加二三级道路，再加建筑及必要细节。"),
            "• 不得改动已通过的主路占地、河道桥面和父块外边界；内部道路与细节服从上级边界。真实入口可接续，不为保环堵路；需要改外围时先说明原图依据并重审受影响区域。",
            "• 原图提供定位与对象依据，base_plan_image 提供已通过结构基线；两者冲突先局部查证，不用全图生成或弹性拉伸掩盖。",
            ("• 片区内密集小建筑只作中性背景；三级路与小碎建筑不因进入urban_detail阶段恢复，明确局部详图区除外。保留道路不跨路并楼、不挤窄，斜弯道路维持原图关系。" if mode == "district_structure" else "• 密集小建筑保留街坊自身轴向和有依据形体，同街坊内保守概括，不能跨路并楼或为细节挤占通道；斜向、弯曲道路保持原图转向和法向宽度，不强制横平竖直。"),
            f"• 已通过结构稿 SHA256：{data['structure_approval']['sha256']}；用户依据：{data['structure_approval']['evidence']}",
        ])
    for role, reference in data["reference_images"].items():
        meaning = "范围标注，只定义操作区域" if role == "design_scope_image" else "已复核现状整理版，清空版的编辑基线"
        if role == "base_plan_image" and stage == "urban_detail":
            meaning = "用户已通过的第一步结构稿，本轮冻结的外层基线"
        lines.append(f"• 附图 {role}：{reference['path']}（{meaning}）。")
    if data["modules"]:
        lines.append("• 本图启用的场景强化：")
        for module_id in data["modules"]:
            module = modules[module_id]
            lines.append(f"  - {module['title']}：{module['reminder']}")
    else:
        lines.append("• 本图启用的场景强化：无；执行后续通用规则。")
    return "\n".join(lines) + "\n\n"


def replace_conditions(template: str, condition_block: str) -> str:
    if template.count(START_MARKER) != 1 or template.count(END_MARKER) != 1:
        raise PromptConfigError("提示词模板标记缺失或重复，无法安全替换项目条件")
    before, remainder = template.split(START_MARKER, 1)
    _, after = remainder.split(END_MARKER, 1)
    return before + condition_block + END_MARKER + after


def stage_file(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def write_new_pair(prompt_path: Path, prompt_data: bytes, metadata_path: Path, metadata_data: bytes) -> None:
    existing = [path for path in (prompt_path, metadata_path) if path.exists()]
    if existing:
        raise PromptConfigError("输出已存在；请另存版本：" + "；".join(str(path) for path in existing))

    staged: list[tuple[Path, Path]] = []
    committed: list[Path] = []
    try:
        staged.append((stage_file(prompt_path, prompt_data), prompt_path))
        staged.append((stage_file(metadata_path, metadata_data), metadata_path))
        for temporary, target in staged:
            os.link(temporary, target)
            committed.append(target)
            temporary.unlink()
    except OSError as exc:
        for target in reversed(committed):
            target.unlink(missing_ok=True)
        raise PromptConfigError(f"无法完整写入提示词与元数据：{exc}") from exc
    finally:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="从项目条件快速生成完整、可复制的 Map2CAD 简化二维图提示词。")
    parser.add_argument("--input", type=Path, help="提示词项目 JSON")
    parser.add_argument("--output", type=Path, help="输出完整提示词文本")
    parser.add_argument("--list-modules", action="store_true", help="列出可用场景模块后退出")
    return parser


def run(argv: list[str] | None = None) -> int:
    started = time.perf_counter()
    args = make_parser().parse_args(argv)
    try:
        modules, modules_bytes = load_modules(DEFAULT_MODULES)
        if args.list_modules:
            for module_id, value in modules.items():
                print(f"{module_id}\t{value['title']}\t{value['reminder']}")
            return 0
        if args.input is None or args.output is None:
            raise PromptConfigError("生成提示词需要同时提供 --input 和 --output")

        config_path = args.input.resolve()
        output_path = args.output.resolve()
        config, config_bytes = read_json_bytes(config_path, "项目条件文件")
        data = validate_config(config, config_path, modules)
        if data["review_stage"] == "urban_structure":
            template_path = STRUCTURE_TEMPLATE.resolve()
        elif data["processing_mode"] == "district_structure":
            template_path = DISTRICT_TEMPLATE.resolve()
        else:
            template_path = DEFAULT_TEMPLATE.resolve()
        modules_path = DEFAULT_MODULES.resolve()
        metadata_path = output_path.with_suffix(".meta.json")
        protected = {config_path, data["source"].resolve(), template_path, modules_path}
        protected.update(Path(ref["path"]) for ref in data["reference_images"].values())
        if output_path in protected or metadata_path in protected:
            raise PromptConfigError("输出路径及元数据路径不能覆盖项目配置、源图、模板或场景模块文件")

        try:
            template_bytes = template_path.read_bytes()
        except FileNotFoundError as exc:
            raise PromptConfigError(f"提示词模板不存在：{template_path}") from exc
        except OSError as exc:
            raise PromptConfigError(f"无法读取提示词模板：{template_path}（{exc}）") from exc
        try:
            template = template_bytes.decode("utf-8-sig")
        except UnicodeError as exc:
            raise PromptConfigError(f"提示词模板不是有效 UTF-8：{template_path}（{exc}）") from exc

        try:
            source_bytes = data["source"].read_bytes()
            with Image.open(io.BytesIO(source_bytes)) as image:
                width, height = image.size
                image.verify()
        except Exception as exc:
            raise PromptConfigError(f"源图无法读取：{data['source']}（{exc}）") from exc

        prompt = replace_conditions(template, build_condition_block(data, modules, width, height))
        prompt_bytes = prompt.encode("utf-8")
        metadata = {
            "schema_version": 1,
            "prompt_ready": True,
            "generation_status": "not_started",
            "processing_mode": data["processing_mode"],
            "mode_reason": data["mode_reason"],
            "detail_scope": data["detail_scope"],
            "output_variant": data["output_variant"],
            "review_stage": data["review_stage"],
            "structure_approval": data["structure_approval"],
            "reference_images": data["reference_images"],
            "source": {
                "path": str(data["source"]),
                "width": width,
                "height": height,
                "sha256": sha256_bytes(source_bytes),
            },
            "config_path": str(config_path),
            "config_sha256": sha256_bytes(config_bytes),
            "template_path": str(template_path),
            "template_sha256": sha256_bytes(template_bytes),
            "modules_path": str(modules_path),
            "modules_sha256": sha256_bytes(modules_bytes),
            "selected_modules": data["modules"],
            "prompt_path": str(output_path),
            "prompt_sha256": sha256_bytes(prompt_bytes),
            "elapsed_seconds": round(time.perf_counter() - started, 6),
        }
        metadata_bytes = (json.dumps(metadata, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        write_new_pair(output_path, prompt_bytes, metadata_path, metadata_bytes)
        print(output_path)
        print(metadata_path)
        return 0
    except PromptConfigError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(run())
