# 项目输入、单位与证据配置

所有场景默认 review_stage=complete（省略也为 complete），由 Agent 在内部完成结构核验、冻结与细化，不要求用户预先通过结构稿。按 [按需分步规则](complex-urban-two-stage.md) 已选分阶段流程时，urban_structure 选择独立结构模板且 modules 为空；urban_detail 仍要求 base_plan_image 和 structure_approval（实际结构图 sha256 与真实用户意见 evidence）。脚本校验一致性，不认证批准本身。未取消的用户分步要求或真实关键未决问题不得用 complete 绕过；用户明确取消旧的程序性阶段等待时，记录取消依据、移除不适用的 structure_approval，使用 complete 并保留有效基线。用途版本 existing_condition/design_clear 与阶段独立。示例 [结构稿配置](../examples/prompt-urban-structure.json) 仅用于已明确要求分步的情形。

每个项目复制 [project.json](../examples/project.json) 到本次外部任务工作目录，再按实际对象设置；不要把示例参数直接说成已验证阈值。路径默认相对于配置文件目录；原始资料不随 Skill 包复制。

路网与父/子块采用两步独立环流程时，另建 [face-hierarchy.json](../examples/face-hierarchy.json)，字段与运行方式见 [CAD 分面模块](cad-modules.md)。它补充原始线网、完整面对应与外层回归检查，不把这些字段混入本页的 `project.json`，也不替代源图、道路宽度及视觉证据。

## Agent 先记录的项目条件

| 信息 | 没有提供时 |
|---|---|
| 主底图、辅助影像、生成中间图、风格图的角色 | 按 [资料角色规则](source-roles.md) 记录文件、来源、用途及几何基线；独立 CAD／平面为主时，补图不更换基线。仅角色不清且会改变主要几何时澄清 |
| 项目名称与保存位置 | 按图片主要地物自动命名为“场景主题项目_建模底图”；指定路径下创建项目文件夹，未指定则在系统实际桌面创建。完整项目目录和续作沿用，子目录按需创建，详见 [项目命名与保存位置](output-lifecycle.md#项目命名与保存位置) |
| 处理模式与推荐依据 | 看图按 [两种模式](processing-modes.md) 推荐，已有选择沿用；记录用户指定/建议后执行，不把建议写成明确确认 |
| 局部详图区 | 仅在用户明确指定或已有授权时记录；与设计清空范围独立 |
| 输出范围 | 原图完整画幅 |
| 设计范围 | 无范围只整理现状；有用户文字范围或人工批注图时，另制作范围内仅留道路的清空版。保留未标注原图，范围外及未覆盖孔洞不清除 |
| 尺度对象、位置/两端、数值、单位、测量含义和可靠程度 | 相对尺度，不默认米或固定桥宽；已声明估算的数值不能记录为实测 |
| 必留/省略对象 | 按共用规则与空间作用取舍 |
| 交付用途与可见分区 | 默认 SU 前期平面：连续道路、水域、主要父块及内部块独立选面；详细高差仅按用户明确请求 |
| 已验收底图与本轮范围 | 没有则走新场地流程 |
| 第一阶段内置生成与项目提示词 | 尚待简化时由 Codex 内置生图执行；首次只引导模式、尺度与设计范围，不要求选择路径。完整提示词内部保存，用于生成、修改与追溯 |
| DWG 转换软件路径 | 先检查本机可用工具；缺少时明确保留DXF候选 |

以上条件先记录在项目说明中，不向下方检查脚本的 JSON 填入未经支持的字段。源侧对象、主路/桥面范围、同面组及异面探针、唯一边界与形体预算另按 [SU 前期平面合同](su-plan-contract.md#构造之前独立的源侧期望) 保存为独立 `source-contract.json`，供 `check_visible_plan.py --contract` 使用；它们不属于现有 `project.json` 或 `face-hierarchy.json` 的 CLI 字段。首次引导与提示词整理见 [第一阶段](image-stage.md)。先继承已有条件，只询问缺失的尺度参照与设计范围；用户明确未知或暂不指定时采用表中默认方式。

标尺优先选地面上可辨认的长度。道路要明确横向宽度及包含范围，建筑要确认是基底边长，场地要指出对应边段或端点。单个面积数值不足以确定某条边的长度；不能把倾斜屋顶的投影长度直接当成地面长度。单位或测量对象有歧义时先澄清或保留相对尺度，不猜测后写成已知尺寸。

单张倾斜图没有精确的全局正射坐标。可使用地面锚点建立局部近似，但记录变换与适用范围。分析图和原图分辨率不同时，明确缩放换算；不能把分析图像素坐标直接叠到更大原图。

## 提示词自动化配置

`prompt-project.json` 只用于生成简化二维图提示词，与下方 `check_cad.py` 的 CAD 检查配置分开保存。以 [prompt-project.json](../examples/prompt-project.json) 为字段示例；示例默认保留未知尺度与空项目事实，复制后只填写已有依据的内容。源图相对路径从该 JSON 所在目录解析。

| 字段 | 要求 |
|---|---|
| `schema_version` | 固定为 `1` |
| `source_image` | 必填，现有且可读取的源图路径 |
| `processing_mode` | 新项目填写 `district_structure`（片区结构模式）或 `site_detail`（场地详图模式）；脚本不自动分类。旧配置省略时沿用旧模板，元数据为 null，不代表自动选了详图 |
| `mode_reason` | 可选文字；图像依据及用途判断，须有 processing_mode；选择来源另留过程记录 |
| `detail_scope` | 可选文字；明确授权的局部详图区，须有 processing_mode；不隐含设计清空范围 |
| `image_type` | `orthophoto` / `satellite` / `oblique` / `simplified_plan` |
| `design_scope` | 可选；用户指定的设计区域，记录位置、边界和未覆盖孔洞。用于额外生成清空版；未知时删除 |
| `output_variant` | `existing_condition`（默认，现状整理版）或 `design_clear`（设计范围清空版）；每份提示词只生成一个版本 |
| `design_scope_image` | 可选；范围标注图路径。有上传范围图时填写，脚本读取尺寸与哈希；标注不是地物色类 |
| `base_plan_image` | 清空版必填；已复核现状整理版路径，用作局部编辑基线。脚本不判断是否已视觉复核 |
| `output_scope`、`detail_level` | 可选；默认原图完整范围，精细程度受所选模式及授权局部范围约束 |
| `keep`、`omit` | 字符串数组；只填用户要求或图中有依据的对象 |
| `image_roles` | 多图用途说明；单图可省略 |
| `notes` | 用户或资料已明确的补充条件；主体详图＋局部片区概括时，填写已采用的局部范围、对象取舍及限制，选择依据另留过程记录，不把 Agent 建议伪记为用户明确确认 |
| `inferences` | 模型看图后需要保守处理的推定；不得写成用户已确认事实 |
| `modules` | 场景强化模块 ID 数组；由模型看图选择，脚本只校验和去重 |
| `scale` | 尺度状态，格式见下方 |

`scale` 只能保留当前 `status` 对应的分支：`relative` 只有 `status`，`grid` 只有 `status` 与 `grid`，`reference` 只有 `status` 与 `reference`。不能同时保留多个尺度分支。`grid.source` 必填，用于说明像素网格的可信来源。

没有可靠尺寸时：

```json
{"status":"relative"}
```

用户提供可辨认参照物时，完整记录对象与含义：

```json
{
  "status":"reference",
  "reference":{
    "object":"桥体",
    "location":"图中指定桥段的两侧边界",
    "value":20,
    "unit":"m",
    "meaning":"桥面总宽",
    "reliability":"用户提供",
    "source":"用户文字说明"
  }
}
```

已有可信像素网格时才使用 `grid`。脚本会用源图像素尺寸计算整幅范围；这不等于它校验了坐标或测绘精度：

```json
{
  "status":"grid",
  "grid":{
    "units_per_pixel":0.515,
    "unit":"m",
    "source":"正射图导出参数",
    "crs":"可选坐标参考"
  }
}
```

先用 `output_variant: existing_condition` 生成并复核现状整理版；有设计范围时另存配置，设为 `design_clear`，保留 `source_image` 指向最初原图，填写 `base_plan_image` 和已有的 `design_scope_image`，再输出新的完整提示词。两次调用和输出文件独立，不拼成一张对比图。清空版的范围和基线缺失时脚本拒绝生成；参考图尺寸与哈希写入元数据，不能拿现状版元数据冒充清空版。脚本不识别标注遮罩，不证明两图配准或清除结果已经通过。

场景模块位于 [scene-modules.json](../prompts/scene-modules.json)。可用 ID 为 `oblique_buildings`、`dense_buildings`、`waterfront`、`farmland`、`site_detail`。先看图再选；例如有水域不自动等于需要 `waterfront`，只有水陆边界、跨水对象或高差容易误判时才选。通用规则始终保留，模块只增加本图重点。

运行：

```powershell
python "<Skill绝对目录>/scripts/build_prompt.py" --input "外部工作目录\prompt-project.json" --output "外部工作目录\simplify-map.project.txt"
```

输出文本可直接复制到 ChatGPT image2 或交给 Codex 内置生图。相邻的 `simplify-map.project.meta.json` 记录源图、项目配置、固定模板、场景模块文件和最终提示词的哈希，用于追溯本次内容；`generation_status: not_started` 只说明提示词已经准备好，不表示已经生图或验收。脚本拒绝覆盖已有提示词或元数据，修改条件后使用新的输出文件名。

## check_cad.py 的实际字段

| 字段 | 含义 |
|---|---|
| `schema_version` | 固定 1 |
| `units` | `m` / `mm` / `relative`，对应DXF INSUNITS 6 / 4 / 0 |
| `tolerance` | 正的有限数，绘图单位；影响几何检查和曲线采样 |
| `z_tolerance` | 非负有限数，允许的平面Z偏差 |
| `layers` | `building`、`road`、`frame`、`reference` 各为图层名数组 |
| `required_layers` | 必须存在且有模型空间实体的图层 |
| `required_handles` | 必须实际导出的本项目模型空间实体编号 |
| `forbidden_layers` | 当前项目明确排除的图层；不能预设所有河面线都排除 |
| `baseline`、`frozen_handles` | 可选基线DXF及需保护实体；比较原始标签，含弧和单位；顶点顺序改变也可能报不同 |
| `hole_map` | 本项目已确认外环handle到内环handles的映射；不可凭嵌套就猜成孔洞 |
| `control_points` | 独立源图/实测观察点到CAD指定层边线的距离检查 |
| `road_connections` | 对已声明封闭道路面的若干连接探针，不代表全图语义验证 |
| `width_probes` | 道路面上的法向短截面宽度检查；是构造尺寸，不是地面测绘精度 |
| `required_reviews` | 至少 `cad_visual` 和 `road_semantics`；使用简化图时另加 `stage_one` |
| `review` | 各复核阶段的状态、证据文件、检查者和当前DXF SHA256 |

`building` 和 `road` 是面角色，配置进去的实体必须闭合。共享开放道路边线应另存为参考/边界层，不要当作封闭道路面；需要时保留独立语义面检查模型。`reference` 允许有作用的开放单线，但不能靠把建筑改成reference来规避闭合检查。指定的frame必须构成有效矩形；格式不支持的实体应明确处理。

仅有闭合环且未填写 `hole_map` 时，脚本将每个环按独立面判断；真正孔洞必须根据源图及对象清单建立关联。GeoJSON候选中的每个feature有外环及孔洞和对应dxf_handles，可据此填写，再人工确认。图框内嵌建筑不是图框的孔洞。

独立观察点例子（数字仅说明格式）：

```json
{"id":"junction-01","layer":"road-edge","point":[120,80],"max_distance":0.5,"source":"source-observations.csv: observation-01"}
```

需要有指定层实际CAD边线。`source` 记录来源；脚本不鉴定这个来源是否独立，执行Agent负责选取和核对。取自CAD自身的点不能作源图贴合证据。

道路探针例子：

```json
{
  "road_connections":[{"id":"local-route-01","point_a":[1,35],"point_b":[99,35],"max_snap":0.1}],
  "width_probes":[{"id":"width-01","segment":[[50,25],[50,45]],"expected":10,"tolerance":0.1}]
}
```

两端必须在同一个有面积的道路连通面内或允许吸附距离内；只点接触不通过。宽度探针必须垂直于局部道路，覆盖一条目标道路，不能横穿多条平行道路后把总长度当一条路的宽。

## 视觉证据字段

每个 `review` 阶段填以下字段。初始一律 pending；真实查看后才能改pass：

```json
{
  "status":"pending",
  "evidence":null,
  "dxf_sha256":null,
  "reviewer":null
}
```

通过记录须引用存在的证据文件，填写当前待检DXF字节SHA256及实际检查者。`stage_one` 的图像记录在CAD验收时关联到当前DXF：说明此CAD继承的是哪张已查图像；不是声称生图时已存在CAD。若CAD修改后哈希改变，重新复核改动影响并更新记录，不能直接复制旧hash。

几何通过但视觉待查时 `delivery_ready=false`。使用 `--geometry-only` 仅用于开发/诊断，不能据其退出码向用户宣布完整验收。脚本检查文件和哈希关联，真实性仍由检查者负责。

## 叠图变换

先按 [资料角色与叠图条件](source-roles.md) 判断是否需要配准。以下变换链只在制作叠图时建立；CAD／独立平面为主底图时，不要求为分类而完成全图配准，无影像或无可靠配准不列为成果缺项。大致参考叠图须在图内说明用途与精度限制，不能冒充定位核验图。

`render_overlay.py` 读取JSON里的 `world_to_image` 三乘三仿射矩阵；像素原点在左上，CAD通常Y向上，必须显式处理反向。禁止直接fit CAD包围盒到图片包围盒，这会隐藏原本位移。

相对像素坐标例子：图片宽1024、高768，CAD `x=px,y=768-py`，则矩阵为 `[[1,0,0],[0,-1,768],[0,0,1]]`。有真实尺度/平移时使用该项目实际矩阵。`width`、`height` 与用于叠图的实际图片相符；改变源图分辨率要改矩阵，不能只改尺寸字段。


保存 CAD 到原始影像的 `original-transform.json`（名称可调整）；简图到 CAD 的变换仅作内部构造依据，不再渲染二维简图叠图。记录原始影像、最终简图、CAD 范围之间的缩放、旋转、裁切和页边变换链；继承旧配准时核对源文件哈希及图像版本。简图带标题、图例或留白时，先明确场地视窗，不能把整张排版页当场地比例。不同图片分辨率的比例只有在同一场地视窗和方向已有证据时才能直接换算，不能仅因宽高比相同就假定配准。

缺少既定关系时，用两张源图中可辨识的对应对象建立配准，并用未参与拟合的对象检查；记录不确定性。配准是展示坐标换算，不是修改 CAD 或消除生成图局部偏移。最终交付和缺原图分支见 [CAD 执行](cad-stage.md#叠图与最终交付)。

处理模式与审查阶段独立：district_structure + complete 生成可交付深度的完整片区稿，包含可辨二三级道路和可信大型建筑；不自动进入 urban_structure/urban_detail 两轮用户审核。两用途版本继承同一模式与范围配置，design_clear 仍优先清空范围内非道路内容。
