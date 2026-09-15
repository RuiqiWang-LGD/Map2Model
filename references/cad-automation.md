# CAD 自动执行：构造、复核图与续跑

用于已确认主要对象关系的二维多边形底图，或局部修改已有结果。按“一级道路 → 围合块面 → 二三级道路 → 建筑及其他细节”整理项目配方，再执行；配方记录 Agent 的图像判断，工具负责重复构造和检查。原图判读仍遵循 [建筑细化](building-regularization.md) 和 [分面模块](cad-modules.md)。默认交付还须满足 [SU 前期平面合同](su-plan-contract.md)：源侧对象/同面探针独立保存，生成 manifest 只作导出索引；主路遮挡裁边、岸线单边与规则化落实到项目构造，最终及回读 DXF 另跑 `check_visible_plan.py`。本执行器的 complete 不代替这一步与视觉复核。

## 实际分工

| Agent 提供 | 工具执行 |
|---|---|
| 主路围合哪些父块、真实图框截边、道路连通部分 | 简单父环、唯一图框补边、原始分面和道路检查 |
| 每栋楼的主体、方向、翼部、铺装/连廊关系 | 按点或局部矩形组合构造闭环；拆楼通过多个明确对象表达 |
| 哪些绿地允许接边修正，以及项目容差 | 定位改楼影响的同父块绿地，有限修边，再检查完整图 |
| 原图坐标变换、需要复核的对象及前后对应 | 原图/修改前/修改后三栏分页、实际 CAD 全图叠图 |
| 本轮执行阶段和已有基线 | 保存阶段输入与输出哈希、定位失败、验证后续跑 |

这不是照片中的建筑自动识别器。正常矩形也可能是铺装，复杂楼也可能正确；不能把几何筛选分数直接作为修改决定。看图判断不必逐栋交给用户，在已授权范围内由 Agent 完成，用户按约定验收阶段成果。

## 一个可运行的起点

复制 [示例目录](../examples/automation/project.json) 及其相邻 `outer.json`、`detail.json` 到外部任务工作目录，并复制 [建筑筛选参数](../examples/building-triage.json)，调整项目 JSON 中的路径。示例只是两个截边街坊、旋转 L 形屋面和局部绿地冲突，不代表真实场地。数值为未定单位，不能照搬到实际项目。

```text
python "<Skill绝对目录>/scripts/cad_pipeline.py" work/project.json
python "<Skill绝对目录>/scripts/cad_pipeline.py" work/project.json --resume
```

`output_dir` 相对外部任务工作目录中的项目 JSON，第一次必须是新目录；运行前解析后确认仍在外部工作范围，禁止指向交付项目。`--resume` 校验实际输入、相关工具代码和输出文件哈希；一致才复用该步骤。输入或成果有变动时，将受影响步骤及依赖步骤写入新的 `attempt-*` 目录，保留旧记录。没有自动修改容差、全图缩小或凭已有文件名跳过检查的逻辑。

`state.json` 是运行状态及成果索引。失败返回具体步骤、原检查问题和候选文件位置；先修正对应对象/配方，再使用同一命令续跑。`complete` 只表示本次配置的机械步骤完成，视觉验收仍为 `pending`，SU 为 `not_run`；还须逐项完成 SU 平面合同才能判默认底图可交付。新增源侧证据另存，不向现有 JSON 填入脚本未支持的字段。同一外部任务工作目录一次运行一个进程。

## 配方：外层与内部

`cad_construct.py recipe.json --output-dir new-step-dir` 单独构造外层；内部增加 `--source-dxf accepted.dxf --face-config accepted-faces.json`。每步输出 `cad.dxf`、`faces.json`、完整对象 `manifest.json`、`construction.json` 和原始几何 `validation.json`。几何失败的候选也留证，不能作为已通过成果进入下一步。

外层配方字段见示例：`units` 是 INSUNITS 整数，`frame` 是实际绘图坐标范围，`tolerances` 与分面检查器一致，`expected_road_components` 来自源图；父块须有唯一 `id`、`clipped`、非空 `source` 依据和 `shape`。道路由父块与图框的剩余区域表达，不重复画两套不一致的边。

形体有两种写法：

- `shape: {"points": [[x,y], ...]}`：CAD 平面坐标中的完整多边形，不重复首点。
- `shape: {"origin": [x,y], "angle_degrees": 角度, "rectangles": [[xmin,ymin,xmax,ymax], ...], "polygons": [点数组, ...], "subtract_rectangles": [矩形, ...]}`：局部坐标先组合、减去矩形，再旋转平移。方向由该建筑源图确定，不统一套世界 XY 轴。

一个形体对应一个无孔多边形。真正内院用独立子环表达；独立屋面用多个有 ID 的操作表达。不能丢弃多部件中的小块来凑成单环。

可选 `fit: {"simplify": 长度, "max_shift": 长度, "max_area_change": 比例}` 控制减点：验证每个对象简化前后边界最大偏移与对称差面积比例，保护截边。真实转弯和曲线用受误差约束的折线表达；原生圆弧、共边体系不在此构造器范围，不偷换成另一种拓扑模型。

内部配方 `module: "detail"`，`operations` 中每项有 `id, action, role, parent, source, shape` 及可选 `fit`。`action` 是 `replace` 或 `add`；替换只匹配已有子对象，不允许借此修改父块、偷偷换归属或类别。语义类别要改变时，先按源图明确处理方案，不能隐式套用同一替换。默认没有整块删除操作。

可选 `neighbor_fit` 必须明确允许的 GREEN 图层角色、净距、简化及最大偏移/面积变化。只处理受改楼影响的同父块绿地；合理包含关系保留。修边超限、产生多部件/孔洞、无法维持有效环时，记录冲突并停止，交给 Agent 重新判断局部形体。不能以无限循环修补代替方案判断。

## 流程配置与交付

项目 JSON 使用 `schema_version: 1`；除 `output_dir` 外，支持以下固定阶段配置，路径相对该文件。不能嵌入任意脚本命令。

| 字段 | 内容 |
|---|---|
| `outer`（必填） | `{"recipe":"outer.json"}`，或 `{"dxf":"accepted.dxf","faces":"accepted-faces.json"}` 导入已有结果并重新检查；后者也可用于已通过内部版的局部更新 |
| `detail` | `{"recipe":"detail.json"}`；在通过的上一步上构造/局部更新 |
| `delivery` | `{"core_console":"本机已有 accoreconsole.exe 路径","tolerance": 正有限数}`；真实保存 R2018 DWG、重新打开、比较并复检 |
| `triage` | `{"config":"building-triage.json"}`；对最终实际 DXF 筛选候选，不代表视觉通过 |
| `review` | `{"source":"original.jpg","transform":"transform.json"}`，可加 `before_dxf` 和 `selection` 文件；默认以内部构造前的自动检查点作对比，输出最终叠图与复核页 |

`review.source` 只配置原始卫星/正射影像，叠图使用最终回读 DXF。先按 [资料角色规则](source-roles.md) 判断适用性：影像为几何来源时，缺影像或可信配准则省略 review 并说明该项未完成；CAD／独立平面为主且影像只辅助时，无影像或可信配准可省略 review，不列为成果缺项，另对主底图独立核验。不要用分析图或未经核验的大致参考变换触发默认 original 定位复核；大致参考叠图须另外明确标注。所有自动输出留过程目录，验证后仅将所请求的 DWG/SKP 和有效成果图片分别复制到交付项目的“成果”和“其他”；不补做二维简图叠图。复核页可以是原图/旧 CAD/新 CAD 的内部对比，不作为额外交付。

未配置转换器时只返回 DXF，`dwg` 为 null，不能改后缀冒充。转换需要现有合法可用的 Windows AutoCAD Core Console，工具不负责安装。最终候选清单和复核图绑定转换回读后的 DXF；不同文件版本不能仅凭 ID 相同混用检查记录。

导入已有 CAD 同样重跑独立多边形分面检查，不能用“已验收”绕过原生曲线或共边的支持范围。此类对象走分面模块规定的等效检查流程，不走当前执行器的导入入口。

`review.selection` 可以是 `{"ids":["对象ID", ...]}` 或建筑筛选报告。省略时优先使用本次筛选候选；未配置筛选则包含全部清单对象。筛选报告中 `review_candidate` 和 `geometry_deferred` 都进入复核。新增对象在完整清单中用空 `before_handles` 或 null `before_handle` 表示没有旧轮廓；其它对象使用实际旧 handle，不能猜测丢失的对应关系。

单独出图使用 `cad_review.py --help`。每页最多六个对象，三栏共用一个裁剪范围和原图变换；小区域可以放大显示，但不增加原图信息。索引记录对象、裁剪范围、输入哈希和分页位置。源图变换须明确提供，禁止重新拟合 CAD 范围去掩盖偏移。

## 换场景时

保留这些脚本，替换项目数据：原图和变换、尺度/精度、道路与父块清单、对象作用和形体配方。先跑外层，再按授权安排二三级道路，随后完成建筑与内部细节。已有成果更新时保留基线，只改本轮对象；二三级道路是否留给人工沿用本轮用户要求，不作为所有场景的省略规则。

工具适用范围与 [现有分面检查合同](cad-modules.md) 一致。模糊影像、倾斜建筑、真实共享边、桥下分层和原生曲线，仍需要相应的源图判断或等效检查；本案例通过不代表这些情况已自动解决。

最终建筑覆盖另按 building-regularization.md 运行 check_building_review.py。执行器的 triage/review 结果不自动完成全部源侧建筑处置，pipeline complete 不能豁免这个门。
